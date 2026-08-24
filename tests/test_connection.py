# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import httpx
import mcp.types as types
import pytest

from dosbox_mcp.client import DosboxClient, DosboxError
from dosbox_mcp.config import Config
from dosbox_mcp.connection import Connection, EngineRestarted, NotConnected, guard


TOKEN = "0" * 64


def _refused(request):
    raise httpx.ConnectError("connection refused")


def _make_conn(token=None, features=None, capabilities=None):
    config = Config(base_url="http://127.0.0.1:8386", token=token)
    # Unit tests never touch the network: even the hello probe runs
    # against a transport that simulates "nothing is listening".
    conn = Connection(config, transport=httpx.MockTransport(_refused))
    if features is not None:
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"ok": True})
        )
        conn._client = DosboxClient(
            config.base_url, token or TOKEN, transport=transport
        )
        conn._features = features
        if capabilities is not None:
            conn._capabilities = capabilities
    return conn


def _routes(info=None, hello=None, require_token=TOKEN):
    """MockTransport standing in for a running engine: /info behind the
    bearer token, /hello unauthenticated (404 when the engine predates
    the route)."""
    def handler(request):
        if request.url.path == "/api/v1/hello":
            if hello is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=hello)
        if request.url.path == "/api/v1/dosbox/info":
            if request.headers.get("authorization") != f"Bearer {require_token}":
                return httpx.Response(401, json={"error": "unauthorized"})
            return httpx.Response(200, json=info)
        return httpx.Response(404, json={"error": "not found"})
    return httpx.MockTransport(handler)


def _connectable(info, token=TOKEN, hello=None, protocol_pin=None):
    config = Config(base_url="http://127.0.0.1:8386", token=token,
                    protocol=protocol_pin)
    return Connection(config, transport=_routes(info=info, hello=hello))


def test_starts_disconnected():
    conn = _make_conn()
    assert not conn.connected
    assert conn.features == {}


def test_no_token_raises_not_connected():
    conn = _make_conn(token=None)
    with pytest.raises(NotConnected, match="No API token"):
        conn.ensure_connected()


def test_connected_with_features():
    conn = _make_conn(token="0" * 64, features={"memory": True, "freeze": True})
    assert conn.connected
    assert conn.features["memory"] is True


def test_detach_clears_state():
    conn = _make_conn(token="0" * 64, features={"memory": True},
                      capabilities={"memory": {"state": "on"}})
    assert conn.connected
    conn.detach()
    assert not conn.connected
    assert conn.features == {}
    assert conn.capabilities == {}


def test_guard_returns_error_when_not_connected():
    conn = _make_conn()

    def handler(args):
        return [types.TextContent(type="text", text="ok")]

    guarded = guard(conn, handler)
    result = guarded({})
    assert isinstance(result, types.CallToolResult)
    assert result.isError
    assert len(result.content) == 1
    assert "No API token" in result.content[0].text


def test_guard_returns_error_when_feature_disabled():
    conn = _make_conn(token="0" * 64, features={"memory": True, "freeze": False})

    def handler(args):
        return [types.TextContent(type="text", text="ok")]

    guarded = guard(conn, handler, feature="freeze")
    result = guarded({})
    assert isinstance(result, types.CallToolResult)
    assert result.isError
    assert len(result.content) == 1
    assert "not enabled" in result.content[0].text


def test_guard_passes_through_when_feature_enabled():
    conn = _make_conn(token="0" * 64, features={"memory": True})

    def handler(args):
        return [types.TextContent(type="text", text="ok")]

    guarded = guard(conn, handler, feature="memory")
    result = guarded({})
    assert result[0].text == "ok"


def test_guard_passes_through_when_no_feature_gate():
    conn = _make_conn(token="0" * 64, features={})

    def handler(args):
        return [types.TextContent(type="text", text="ok")]

    guarded = guard(conn, handler, feature=None)
    result = guarded({})
    assert result[0].text == "ok"


def test_guard_off_capability_refuses_with_the_engines_reason():
    conn = _make_conn(
        token="0" * 64,
        features={"debugger": False},
        capabilities={"debugger": {"state": "off",
                                   "reason": "debugger not built into this binary"}},
    )

    def handler(args):
        return [types.TextContent(type="text", text="ok")]

    guarded = guard(conn, handler, feature="debugger")
    result = guarded({})
    assert isinstance(result, types.CallToolResult)
    assert result.isError
    assert "debugger not built into this binary" in result.content[0].text


def test_guard_degraded_capability_still_passes_through():
    conn = _make_conn(
        token="0" * 64,
        features={"debugger": True},
        capabilities={"debugger": {"state": "degraded",
                                   "reason": "memory breakpoints unavailable"}},
    )

    def handler(args):
        return [types.TextContent(type="text", text="ok")]

    guarded = guard(conn, handler, feature="debugger")
    result = guarded({})
    assert result[0].text == "ok"


def test_guard_falls_back_to_features_boolean_without_a_capabilities_block():
    # An engine older than 1.2 sends 'features' but no 'capabilities' at
    # all - guard() must behave exactly as it did before this existed.
    conn = _make_conn(token="0" * 64, features={"freeze": False})

    def handler(args):
        return [types.TextContent(type="text", text="ok")]

    guarded = guard(conn, handler, feature="freeze")
    result = guarded({})
    assert isinstance(result, types.CallToolResult)
    assert result.isError
    assert "not enabled" in result.content[0].text


class TestVersionNegotiation:
    def test_connect_computes_effective_version(self):
        conn = _connectable({"version": "0.84-da3", "features": {"memory": True},
                             "mcp_protocol": "1.0"})
        conn.ensure_connected()
        assert conn.effective_protocol == "1.0"
        assert conn.engine_info["version"] == "0.84-da3"

    def test_legacy_info_without_advertisement_is_implicit_1_0(self):
        conn = _connectable({"version": "0.84-da2", "features": {"memory": True}})
        conn.ensure_connected()
        assert conn.effective_protocol == "1.0"

    def test_non_peer_rejected(self):
        conn = _connectable({"version": "not ours"})
        with pytest.raises(NotConnected, match="protocol peer"):
            conn.ensure_connected()

    def test_major_mismatch_rejected(self):
        conn = _connectable({"features": {}, "mcp_protocol": "99.0"})
        with pytest.raises(NotConnected, match="major"):
            conn.ensure_connected()

    def test_bad_pin_reported(self):
        conn = _connectable({"features": {}, "mcp_protocol": "1.0"},
                            protocol_pin="99.9")
        with pytest.raises(NotConnected, match="pin"):
            conn.ensure_connected()


class TestNoTokenDiagnostics:
    def test_hello_makes_the_error_precise(self):
        conn = _connectable(
            {"features": {}},
            token=None,
            hello={"name": "dosbox-automation", "version": "0.84-da3",
                   "mcp_protocol": "1.0"},
        )
        with pytest.raises(NotConnected, match="0.84-da3") as exc:
            conn.ensure_connected()
        assert "token" in str(exc.value).lower()

    def test_listener_without_hello_still_mentions_token(self):
        conn = _connectable({"features": {}}, token=None)
        with pytest.raises(NotConnected, match="token"):
            conn.ensure_connected()

    def test_nothing_listening_keeps_the_generic_message(self):
        conn = _make_conn(token=None)
        with pytest.raises(NotConnected, match="No API token"):
            conn.ensure_connected()


class TestStatus:
    def test_status_when_connected(self):
        conn = _connectable({"version": "0.84-da3", "features": {"memory": True},
                             "mcp_protocol": "1.0"})
        conn.ensure_connected()
        status = conn.status()
        assert status["connected"] is True
        assert status["engine_version"] == "0.84-da3"
        assert status["protocol"] == "1.0"
        assert status["features"] == {"memory": True}
        assert status["token"] == "present"

    def test_status_passes_through_the_capabilities_block(self):
        conn = _connectable({
            "version": "0.84-da3", "mcp_protocol": "1.0",
            "features": {"debugger": True},
            "capabilities": {"debugger": {"state": "degraded",
                                          "reason": "memory breakpoints unavailable"}},
        })
        conn.ensure_connected()
        status = conn.status()
        assert status["capabilities"]["debugger"]["state"] == "degraded"

    def test_status_capabilities_is_empty_dict_against_an_older_engine(self):
        conn = _connectable({"version": "0.84-da2", "features": {"memory": True}})
        conn.ensure_connected()
        assert conn.status()["capabilities"] == {}

    def test_status_when_disconnected(self):
        conn = _make_conn()
        status = conn.status()
        assert status["connected"] is False
        assert status["token"] == "absent"

    def test_status_never_contains_the_token_value(self):
        conn = _connectable({"features": {}, "mcp_protocol": "1.0"})
        conn.ensure_connected()
        assert TOKEN not in repr(conn.status())

    def test_status_reports_instance_id(self):
        conn = _connectable({"features": {}, "mcp_protocol": "1.0",
                             "instance_id": "a" * 32})
        conn.ensure_connected()
        assert conn.status()["instance_id"] == "a" * 32

    def test_status_instance_id_is_none_against_an_engine_that_predates_3_6(self):
        conn = _connectable({"features": {}, "mcp_protocol": "1.0"})
        conn.ensure_connected()
        assert conn.status()["instance_id"] is None

    def test_status_instance_id_is_none_while_disconnected(self):
        conn = _make_conn()
        assert conn.status()["instance_id"] is None


class TestStaleTokenRetry:
    def test_retries_on_status_not_message_substring(self, tmp_path, monkeypatch):
        """Regression test for the exact bug 1.5 fixes: detecting a
        stale token used to check '"401" in str(e)'. A 401 whose
        message never contains the digits '401' must still trigger the
        reattach."""
        monkeypatch.delenv("DOSBOX_API_TOKEN", raising=False)
        fresh_token = "1" * 64
        token_file = tmp_path / "api_token"
        token_file.write_text(fresh_token)

        calls = []

        def handler(request):
            auth = request.headers.get("authorization")
            calls.append(auth)
            if auth == f"Bearer {fresh_token}":
                return httpx.Response(200, json={
                    "version": "0.84-test", "features": {},
                    "mcp_protocol": "1.0",
                })
            # No literal "401" substring anywhere in this body.
            return httpx.Response(401, json={
                "error": "token rejected", "error_code": "unauthorized",
            })

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN,
                        token_file=token_file)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        conn.ensure_connected()

        assert conn.connected
        assert calls == [f"Bearer {TOKEN}", f"Bearer {fresh_token}"]

    def test_non_401_error_does_not_retry(self):
        # A 4xx/5xx that is not literally a stale-token 401 must not be
        # swallowed into a silent reattach loop.
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(403, json={
                "error": "forbidden", "error_code": "forbidden_host",
            })

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        with pytest.raises(NotConnected):
            conn.ensure_connected()
        assert len(calls) == 1


class TestRestartDetection:
    """3.6: a mid-call 401 can mean either a stale token on the same
    engine process (replay is safe) or a different process behind the
    same URL, i.e. a restart (replay would run a mutating request
    against a fresh guest session - worse than an error)."""

    def _restart_transport(self, old_token, new_token, token_file, *,
                           old_instance="aaaa" * 8, new_instance="bbbb" * 8):
        # The first request to any authenticated path other than the
        # initial /info attach 401s once (simulating the restart);
        # after that, both /info and the retried path key their
        # response on which token the caller now presents.
        state = {"restarted": False}

        def handler(request):
            auth = request.headers.get("authorization")
            path = request.url.path
            if path == "/api/v1/dosbox/info":
                if not state["restarted"] and auth == f"Bearer {old_token}":
                    return httpx.Response(200, json={
                        "version": "0.84-test", "features": {},
                        "mcp_protocol": "1.0", "instance_id": old_instance,
                    })
                if state["restarted"] and auth == f"Bearer {new_token}":
                    return httpx.Response(200, json={
                        "version": "0.84-test", "features": {},
                        "mcp_protocol": "1.0", "instance_id": new_instance,
                    })
                return httpx.Response(401, json={
                    "error": "unauthorized", "error_code": "unauthorized",
                })
            if not state["restarted"]:
                state["restarted"] = True
                return httpx.Response(401, json={
                    "error": "unauthorized", "error_code": "unauthorized",
                })
            if auth == f"Bearer {new_token}":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(401, json={
                "error": "unauthorized", "error_code": "unauthorized",
            })

        return httpx.MockTransport(handler)

    def test_call_raises_engine_restarted_when_the_instance_id_changes(
            self, tmp_path, monkeypatch):
        monkeypatch.delenv("DOSBOX_API_TOKEN", raising=False)
        old_token, new_token = "0" * 64, "1" * 64
        token_file = tmp_path / "api_token"
        token_file.write_text(new_token)

        config = Config(base_url="http://127.0.0.1:8386", token=old_token,
                        token_file=token_file)
        conn = Connection(config, transport=self._restart_transport(
            old_token, new_token, token_file))
        conn.ensure_connected()
        assert conn.status()["instance_id"] == "aaaa" * 8

        with pytest.raises(EngineRestarted) as exc_info:
            conn.get("/api/v1/status")
        assert exc_info.value.old_instance_id == "aaaa" * 8
        assert exc_info.value.new_instance_id == "bbbb" * 8
        # The failed call was not silently retried into success.
        assert conn.status()["instance_id"] == "bbbb" * 8

    def test_guard_turns_engine_restarted_into_an_error_result(
            self, tmp_path, monkeypatch):
        monkeypatch.delenv("DOSBOX_API_TOKEN", raising=False)
        old_token, new_token = "0" * 64, "1" * 64
        token_file = tmp_path / "api_token"
        token_file.write_text(new_token)

        config = Config(base_url="http://127.0.0.1:8386", token=old_token,
                        token_file=token_file)
        conn = Connection(config, transport=self._restart_transport(
            old_token, new_token, token_file))
        conn.ensure_connected()

        guarded = guard(conn, lambda args: conn.get("/api/v1/status"),
                        tool_name="dosbox_status")
        result = guarded({})
        assert result.isError
        text = result.content[0].text
        assert "engine_restarted" not in text  # code, not echoed in text
        assert "restarted" in text
        assert "dosbox_status" in text

    def test_same_instance_id_across_reattach_still_replays_normally(self):
        # No restart: same process, same instance_id both times - the
        # pre-3.6 stale-token-retry behavior must be unchanged.
        calls = []

        def handler(request):
            auth = request.headers.get("authorization")
            calls.append((request.url.path, auth))
            if request.url.path == "/api/v1/dosbox/info":
                return httpx.Response(200, json={
                    "version": "0.84-test", "features": {},
                    "mcp_protocol": "1.0", "instance_id": "same" * 8,
                })
            if len(calls) == 2:  # first hit on /status: simulate a stale token
                return httpx.Response(401, json={
                    "error": "unauthorized", "error_code": "unauthorized",
                })
            return httpx.Response(200, json={"ok": True})

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        conn.ensure_connected()

        result = conn.get("/api/v1/status")
        assert result == {"ok": True}

    def test_no_restart_detection_against_an_engine_that_predates_3_6(self):
        # Neither info response carries instance_id - both stay None,
        # so the "different id" check never fires and the pre-3.6
        # replay-on-401 behavior is preserved for older engines.
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if request.url.path == "/api/v1/dosbox/info":
                return httpx.Response(200, json={
                    "version": "0.84-da2", "features": {}, "mcp_protocol": "1.0",
                })
            if calls.count("/api/v1/status") == 1:
                return httpx.Response(401, json={
                    "error": "unauthorized", "error_code": "unauthorized",
                })
            return httpx.Response(200, json={"ok": True})

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        conn.ensure_connected()

        assert conn.get("/api/v1/status") == {"ok": True}

    def test_engine_restarted_when_instance_id_appears_across_the_boundary(self):
        # An engine straddling the 1.13.0 boundary (upgraded and
        # restarted) is still a restart even though the *old* side has
        # no instance_id to compare against: the field going from
        # absent to present can only happen if the responding binary
        # changed.
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if request.url.path == "/api/v1/dosbox/info":
                if len(calls) == 1:  # initial attach: pre-3.6 engine
                    return httpx.Response(200, json={
                        "version": "0.84-da2", "features": {}, "mcp_protocol": "1.0",
                    })
                return httpx.Response(200, json={  # reattach: upgraded engine
                    "version": "0.84-da3", "features": {}, "mcp_protocol": "1.0",
                    "instance_id": "cccc" * 8,
                })
            if calls.count("/api/v1/status") == 1:
                return httpx.Response(401, json={
                    "error": "unauthorized", "error_code": "unauthorized",
                })
            return httpx.Response(200, json={"ok": True})

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        conn.ensure_connected()
        assert conn.status()["instance_id"] is None

        with pytest.raises(EngineRestarted) as exc_info:
            conn.get("/api/v1/status")
        assert exc_info.value.old_instance_id is None
        assert exc_info.value.new_instance_id == "cccc" * 8

    def test_engine_restarted_when_instance_id_disappears_across_the_boundary(self):
        # Symmetric case: a downgrade/rollback to a pre-3.6 build.
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if request.url.path == "/api/v1/dosbox/info":
                if len(calls) == 1:  # initial attach: 3.6+ engine
                    return httpx.Response(200, json={
                        "version": "0.84-da3", "features": {}, "mcp_protocol": "1.0",
                        "instance_id": "dddd" * 8,
                    })
                return httpx.Response(200, json={  # reattach: rolled back
                    "version": "0.84-da2", "features": {}, "mcp_protocol": "1.0",
                })
            if calls.count("/api/v1/status") == 1:
                return httpx.Response(401, json={
                    "error": "unauthorized", "error_code": "unauthorized",
                })
            return httpx.Response(200, json={"ok": True})

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        conn.ensure_connected()
        assert conn.status()["instance_id"] == "dddd" * 8

        with pytest.raises(EngineRestarted) as exc_info:
            conn.get("/api/v1/status")
        assert exc_info.value.old_instance_id == "dddd" * 8
        assert exc_info.value.new_instance_id is None

    def _make_stale_then_tokenless_conn(self, tmp_path, monkeypatch):
        # A connected Connection whose remembered token has just gone
        # bad (simulating a restart with a fresh token) and whose
        # token file is also gone by the time the reattach falls back
        # to it - the reattach has nothing left to try.
        monkeypatch.delenv("DOSBOX_API_TOKEN", raising=False)
        token_file = tmp_path / "api_token"
        token_file.write_text(TOKEN)
        state = {"stale": False}

        def handler(request):
            auth = request.headers.get("authorization")
            if request.url.path == "/api/v1/dosbox/info":
                if not state["stale"] and auth == f"Bearer {TOKEN}":
                    return httpx.Response(200, json={
                        "version": "0.84-test", "features": {}, "mcp_protocol": "1.0",
                    })
                return httpx.Response(401, json={
                    "error": "unauthorized", "error_code": "unauthorized",
                })
            state["stale"] = True
            return httpx.Response(401, json={
                "error": "unauthorized", "error_code": "unauthorized",
            })

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN,
                        token_file=token_file)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        conn.ensure_connected()
        token_file.unlink()  # the reattach's fallback will find nothing
        return conn

    def test_reattach_failing_for_lack_of_a_token_still_surfaces_as_not_connected(
            self, tmp_path, monkeypatch):
        # If the 401-triggered reattach inside call() can't find any
        # token at all, _try_connect() raises NotConnected from inside
        # call()'s except block. That must propagate as NotConnected,
        # not get swallowed or turned into something confusing.
        conn = self._make_stale_then_tokenless_conn(tmp_path, monkeypatch)
        with pytest.raises(NotConnected):
            conn.get("/api/v1/status")

    def test_guard_turns_that_not_connected_into_a_clean_error_result(
            self, tmp_path, monkeypatch):
        conn = self._make_stale_then_tokenless_conn(tmp_path, monkeypatch)
        guarded = guard(conn, lambda args: conn.get("/api/v1/status"),
                        tool_name="dosbox_status")
        result = guarded({})
        assert result.isError
        assert "dosbox_status" in result.content[0].text


class TestTransportErrorHandling:
    def test_try_connect_treats_timeout_like_connect_error(self):
        def handler(request):
            raise httpx.ReadTimeout("timed out", request=request)

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        with pytest.raises(NotConnected, match="Cannot reach dosbox"):
            conn.ensure_connected()

    def test_call_detaches_and_raises_not_connected_on_timeout(self):
        conn = _connectable({"features": {}, "mcp_protocol": "1.0"})
        conn.ensure_connected()
        assert conn.connected

        def timeout_handler(request):
            raise httpx.ReadTimeout("timed out", request=request)
        conn._client = DosboxClient(conn.base_url, TOKEN,
                                    transport=httpx.MockTransport(timeout_handler))

        with pytest.raises(NotConnected, match="stopped responding"):
            conn.get("/api/v1/status")
        assert not conn.connected


class TestDosboxErrorThroughGuard:
    def test_guard_converts_dosbox_error_into_error_result(self):
        conn = _make_conn(token=TOKEN, features={})

        def handler(args):
            raise DosboxError(400, "invalid_argument",
                              "port must be 0x0000..0xFFFF",
                              route="GET /api/v1/io/port")

        guarded = guard(conn, handler, tool_name="port_read")
        result = guarded({})
        assert isinstance(result, types.CallToolResult)
        assert result.isError
        text = result.content[0].text
        assert "port_read" in text
        assert "GET /api/v1/io/port" in text
        assert "port must be 0x0000..0xFFFF" in text

    def test_guard_surfaces_retryable_hint_for_bridge_timeout(self):
        conn = _make_conn(token=TOKEN, features={})

        def handler(args):
            raise DosboxError(503, "bridge_timeout",
                              "Command execution timed out", retryable=True)

        guarded = guard(conn, handler)
        result = guarded({})
        assert result.isError
        assert "retry" in result.content[0].text.lower()

    def test_guard_quotes_retry_after_for_a_429(self):
        conn = _make_conn(token=TOKEN, features={})

        def handler(args):
            raise DosboxError(429, "unknown", "too many requests",
                              retry_after="2")

        guarded = guard(conn, handler, tool_name="script_load")
        result = guarded({})
        assert result.isError
        text = result.content[0].text
        assert "Retry after 2s" in text

    def test_retry_after_takes_priority_over_the_generic_retryable_hint(self):
        conn = _make_conn(token=TOKEN, features={})

        def handler(args):
            raise DosboxError(429, "unknown", "too many requests",
                              retryable=True, retry_after="2")

        guarded = guard(conn, handler)
        result = guarded({})
        text = result.content[0].text
        assert "Retry after 2s" in text
        assert "may be transient" not in text

    def test_a_401_that_persists_through_reconnect_still_surfaces_as_an_error(self):
        # call() detaches and retries once on 401 (Connection.call);
        # that retry goes through ensure_connected() -> _try_connect(),
        # which drops the stale token and requires a fresh one to
        # proceed at all. With none available it raises NotConnected
        # rather than looping forever - either way (NotConnected here,
        # or a DosboxError had a fresh token also been rejected), guard()
        # must not let a persistent auth failure read as success.
        def handler(request):
            return httpx.Response(401, json={
                "error": "still unauthorized", "error_code": "unauthorized",
            })

        config = Config(base_url="http://127.0.0.1:8386", token=TOKEN)
        conn = Connection(config, transport=httpx.MockTransport(handler))

        def handler_fn(args):
            return conn.get("/api/v1/status")

        guarded = guard(conn, handler_fn, tool_name="dosbox_status")
        result = guarded({})
        assert isinstance(result, types.CallToolResult)
        assert result.isError
        assert "dosbox_status" in result.content[0].text
