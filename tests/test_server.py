# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

import httpx

from dosbox_mcp.config import Config
from dosbox_mcp.connection import Connection
from dosbox_mcp.server import build_server
from dosbox_mcp.tools.memory import _mem_read, _mem_write


def _make_conn():
    config = Config(base_url="http://127.0.0.1:8386", token=None)
    return Connection(config)


def _build(mode="full"):
    return build_server(_make_conn(), mode=mode)


def test_always_on_tools_present():
    server = _build()
    names = server.registered_tool_names()
    assert "dosbox_status" in names
    assert "screen_text" in names
    assert "script_run" in names
    assert "video_capture_status" in names


def test_all_tools_registered_regardless_of_features():
    server = _build()
    names = server.registered_tool_names()
    assert "mem_read" in names
    assert "mem_write" in names
    assert "input_type" in names
    assert "input_key" in names
    assert "mem_search" in names
    assert "dos_memory_map" in names
    assert "freeze_set" in names
    assert "freeze_list" in names
    assert "freeze_clear" in names
    assert "port_read" in names
    assert "port_write" in names
    assert "cpu_write_register" in names
    assert "cpu_read_registers" in names
    for t in ("debug_status", "debug_pause", "debug_continue", "debug_step",
              "debug_breakpoint_add", "debug_breakpoint_list", "debug_breakpoint_delete"):
        assert t in names
    for t in ("debug_map_set_base", "debug_map_to_live", "debug_map_to_ghidra",
              "debug_map_status"):
        assert t in names


class TestCapabilityModes:
    """The mode is the operator's constraint on the agent, gated at
    registration: a tool outside the mode does not exist for the client."""

    def test_observe_registers_only_read_only_tools(self):
        names = _build(mode="observe").registered_tool_names()
        assert "screen_text" in names
        assert "mem_read" in names
        assert "dosbox_status" in names
        assert "cpu_read_registers" in names
        assert "debug_map_to_live" in names
        assert "mem_write" not in names
        assert "input_key" not in names
        assert "script_run" not in names
        assert "drive_swap" not in names
        assert "port_write" not in names
        assert "debug_map_set_base" not in names

    def test_interact_adds_input_media_script_but_not_surgery(self):
        names = _build(mode="interact").registered_tool_names()
        assert "input_key" in names
        assert "input_type" in names
        assert "script_run" in names
        assert "video_capture_start" in names
        assert "mem_write" not in names
        assert "freeze_set" not in names
        assert "port_write" not in names
        assert "cpu_write_register" not in names

    def test_full_registers_everything(self):
        names = _build(mode="full").registered_tool_names()
        assert "mem_write" in names
        assert "port_write" in names
        assert "freeze_set" in names

    def test_unknown_mode_rejected(self):
        import pytest
        with pytest.raises(ValueError, match="mode"):
            _build(mode="root")


# ---------------------------------------------------------------------------
# Tool handlers must build the right REST calls. The registration tests
# above cannot catch a wrong route or missing Accept header; these do
# (aug-df86: mem_read hit the wrong route and got binary back).
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None, headers=None):
        self.calls.append(("get", path, params, headers))
        return {"memory": {"data": "3q2+7w==", "addr": 4660}, "registers": {}}

    def put(self, path, json=None):
        self.calls.append(("put", path, json))
        return {"status": "ok"}


def test_mem_read_uses_linear_offset_route_and_json_accept():
    client = _FakeClient()
    result = _mem_read(client, {"offset": 0x1234, "length": 100})

    method, path, params, headers = client.calls[0]
    assert method == "get"
    # Linear offset and length in the path, nothing split into segments
    assert path == "/api/v1/memory/4660/100"
    # JSON (base64 payload) is selected by the Accept header
    assert headers == {"accept": "application/json"}
    # The base64 data must survive into the tool output
    assert "3q2+7w==" in result[0].text


def test_mem_write_uses_single_offset_route():
    client = _FakeClient()
    _mem_write(client, {"offset": 0x1234, "data": "AAECAw=="})

    method, path, body = client.calls[0]
    assert method == "put"
    assert path == "/api/v1/memory/4660"
    assert body == {"data": "AAECAw=="}


def test_session_info_registered():
    server = _build()
    assert "session_info" in server.registered_tool_names()


def test_session_info_never_reveals_the_token_value(monkeypatch, tmp_path):
    # Self-audit 2026-07-17: the bearer token must not enter transcripts.
    # session_info reports presence and where a human finds it, nothing more.
    from dosbox_mcp.tools.session import _session_info

    monkeypatch.setenv("DOSBOX_API_TOKEN", "a" * 64)

    class _FakeConn:
        base_url = "http://127.0.0.1:8386"

    result = _session_info(_FakeConn())
    import json as _json
    info = _json.loads(result[0].text)
    assert info["base_url"] == "http://127.0.0.1:8386"
    assert info["token"] == "present"
    assert "a" * 64 not in result[0].text


def test_session_info_without_token(monkeypatch, tmp_path):
    from dosbox_mcp.tools.session import _session_info

    monkeypatch.delenv("DOSBOX_API_TOKEN", raising=False)
    # Point the token file lookup at an empty directory
    monkeypatch.setenv("DOSBOX_TOKEN_FILE", str(tmp_path / "no_token"))

    class _FakeConn:
        base_url = "http://127.0.0.1:8386"

    result = _session_info(_FakeConn())
    import json as _json
    info = _json.loads(result[0].text)
    assert info["token"] == "absent"
    assert "note" in info


def test_cpu_read_registers_hits_state_route():
    from dosbox_mcp.tools.cpu import _cpu_state

    class _FakeClient:
        def get(self, path, params=None, headers=None):
            return {"registers": {"cs": 0x2000, "eip": 0x100}}

    result = _cpu_state(_FakeClient())
    body = json.loads(result[0].text)
    assert body["registers"]["cs"] == 0x2000


class TestGhidraAddressMap:
    """Bridge-side arithmetic, no engine call involved."""

    def _fresh_state(self):
        from dosbox_mcp.tools import ghidra
        state = {"base_segment": None, "delta": None, "ghidra_anchor": None}
        return ghidra, state

    def test_translations_fail_before_a_base_is_set(self):
        ghidra, state = self._fresh_state()
        result = ghidra._to_live(state, {"ghidra_address": 0x150})
        assert "No mapping set" in result[0].text

    def test_set_base_then_roundtrip(self):
        ghidra, state = self._fresh_state()
        # .COM-style anchor: entry point 0x100 in both spaces, live CS 0x2000
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x2000, "live_offset": 0x100,
        })

        live = json.loads(ghidra._to_live(state, {"ghidra_address": 0x150})[0].text)
        assert live == {"segment": 0x2000, "offset": 0x150, "linear": 0x2000 * 16 + 0x150}

        back = json.loads(ghidra._to_ghidra(state, {
            "live_segment": 0x2000, "live_offset": 0x150,
        })[0].text)
        assert back == {"ghidra_address": 0x150}

    def test_to_ghidra_refuses_a_different_segment(self):
        ghidra, state = self._fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x2000, "live_offset": 0x100,
        })
        result = json.loads(ghidra._to_ghidra(state, {
            "live_segment": 0x3000, "live_offset": 0x150,
        })[0].text)
        assert "error" in result
        assert "0x3000" in result["error"]

    def test_status_reports_unset_and_set(self):
        ghidra, state = self._fresh_state()
        assert json.loads(ghidra._status(state)[0].text) == {"set": False}
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x2000, "live_offset": 0x100,
        })
        status = json.loads(ghidra._status(state)[0].text)
        assert status == {
            "set": True, "base_segment": 0x2000, "ghidra_anchor": 0x100, "delta": 0,
        }


def test_script_run_sends_lua_as_text_not_json():
    # aug-bt7n: script/load wants a text/plain body; the old JSON post
    # 415'd. Verify the handler uses post_text with the raw source.
    from dosbox_mcp.tools.script import _script_run

    class _FakeClient:
        def __init__(self):
            self.text_calls = []
            self.json_calls = []

        def post_text(self, path, text, content_type="text/plain", params=None):
            self.text_calls.append((path, text, content_type))
            return {"status": "loaded"}

        def post(self, path, json=None):
            self.json_calls.append((path, json))
            return {"status": "running"}

    client = _FakeClient()
    _script_run(client, {"script": "dosbox.log('hi')"})

    # The script goes through post_text as raw source, not post(json=)
    assert len(client.text_calls) == 1
    path, text, ctype = client.text_calls[0]
    assert path == "/api/v1/script/load"
    assert text == "dosbox.log('hi')"
    assert ctype == "text/plain"
    # start is a plain POST with no body
    assert client.json_calls == [("/api/v1/script/start", None)]


def test_script_run_works_through_a_real_connection():
    # In production, script.register passes `conn` (a Connection) as
    # `client`, not a DosboxClient or the fake above. Connection had no
    # post_text, so every real script_run call raised AttributeError; the
    # fake in the previous test has its own post_text and never caught it.
    from dosbox_mcp.tools.script import _script_run

    calls = []

    def handler(request):
        if request.url.path == "/api/v1/dosbox/info":
            return httpx.Response(
                200, json={"version": "0.84-da3", "features": {},
                          "mcp_protocol": "1.0"})
        if request.url.path == "/api/v1/script/load":
            calls.append(("load", request.content,
                          request.headers.get("content-type")))
            return httpx.Response(200, json={"status": "loaded"})
        if request.url.path == "/api/v1/script/start":
            calls.append(("start", request.content))
            return httpx.Response(200, json={"status": "running"})
        return httpx.Response(404, json={"error": "not found"})

    config = Config(base_url="http://127.0.0.1:8386", token="0" * 64)
    conn = Connection(config, transport=httpx.MockTransport(handler))

    result = _script_run(conn, {"script": "dosbox.log('hi')"})

    assert calls[0] == ("load", b"dosbox.log('hi')", "text/plain")
    assert calls[1][0] == "start"
    assert json.loads(result[0].text) == {"status": "running"}
