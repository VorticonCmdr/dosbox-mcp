# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import httpx
import mcp.types as types
import pytest

from dosbox_mcp.client import DosboxClient, DosboxError
from dosbox_mcp.config import Config
from dosbox_mcp.connection import Connection, NotConnected, guard


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
