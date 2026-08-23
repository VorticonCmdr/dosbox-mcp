# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import asyncio
import json

import httpx
import mcp.types as types

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
    assert "mem_alloc" in names
    assert "mem_free" in names
    assert "mem_allocations" in names
    assert "freeze_set" in names
    assert "freeze_list" in names
    assert "freeze_clear" in names
    assert "port_read" in names
    assert "port_write" in names
    assert "cpu_write_register" in names
    assert "cpu_read_registers" in names
    for t in ("debug_status", "debug_pause", "debug_continue", "debug_step",
              "debug_breakpoint_add", "debug_breakpoint_list", "debug_breakpoint_delete",
              "debug_backtrace", "debug_step_out"):
        assert t in names
    for t in ("debug_map_set_base", "debug_map_auto", "debug_map_to_live",
              "debug_map_to_ghidra", "debug_map_status"):
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
        assert "debug_backtrace" in names
        assert "mem_write" not in names
        assert "input_key" not in names
        assert "script_run" not in names
        assert "drive_swap" not in names
        assert "port_write" not in names
        assert "mem_allocations" in names
        assert "mem_alloc" not in names
        assert "mem_free" not in names
        # debug_map_set_base mutates only the bridge's own local
        # address-mapping bookkeeping, never the connected engine or
        # guest, so it's available in every mode - see
        # _LOCAL_ONLY_GROUPS. debug_map_auto looks similar but reads
        # live engine memory as part of deriving what to persist, so it
        # does NOT get that exemption and needs full mode like any
        # other engine-reaching mutation.
        assert "debug_map_set_base" in names
        assert "debug_map_auto" not in names
        assert "debug_step_out" not in names

    def test_interact_still_requires_full_for_debug_map_auto(self):
        names = _build(mode="interact").registered_tool_names()
        assert "debug_map_set_base" in names
        assert "debug_map_auto" not in names

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
        assert "debug_step_out" not in names

    def test_full_registers_everything(self):
        names = _build(mode="full").registered_tool_names()
        assert "mem_write" in names
        assert "port_write" in names
        assert "freeze_set" in names
        assert "debug_map_auto" in names

    def test_unknown_mode_rejected(self):
        import pytest
        with pytest.raises(ValueError, match="mode"):
            _build(mode="root")


def _call(server, name, args):
    """Dispatch through the real MCP call_tool path (guard()/add_tool's
    needs_connection included) rather than calling a tool module's
    handler function directly - a bug in that wiring itself (e.g.
    needs_connection left at its default) is invisible to a test that
    bypasses it."""
    handler = server.request_handlers[types.CallToolRequest]

    async def go():
        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=args),
        )
        result = await handler(req)
        ctr = result.root
        return ctr.isError, ctr.content[0].text if ctr.content else None

    return asyncio.run(go())


class TestGhidraToolsDontNeedAConnection:
    """debug_map_set_base/to_live/to_ghidra/status are pure client-side
    arithmetic - unlike every other tool in this bridge, they must work
    with no dosbox instance reachable at all (aug-2.16: these silently
    required one anyway because needs_connection was left at add_tool's
    default True)."""

    def _disconnected_server(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DOSBOX_API_TOKEN", raising=False)
        monkeypatch.setenv("DOSBOX_TOKEN_FILE", str(tmp_path / "no_token"))
        monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(tmp_path / "ghidra_map.json"))
        config = Config(base_url="http://127.0.0.1:8386", token=None)
        return build_server(Connection(config), mode="full")

    def test_status_works_with_no_connection(self, monkeypatch, tmp_path):
        server = self._disconnected_server(monkeypatch, tmp_path)
        is_error, text = _call(server, "debug_map_status", {})
        assert not is_error
        assert json.loads(text) == {"ranges": []}

    def test_set_base_works_with_no_connection(self, monkeypatch, tmp_path):
        server = self._disconnected_server(monkeypatch, tmp_path)
        is_error, text = _call(server, "debug_map_set_base", {
            "ghidra_address": 0, "live_segment": 0x1000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x10, "label": "x",
        })
        assert not is_error, text

    def test_debug_map_auto_does_need_a_connection(self, monkeypatch, tmp_path):
        # The one tool in this module that genuinely talks to the engine
        # - confirms the fix didn't just blanket-disable the guard.
        server = self._disconnected_server(monkeypatch, tmp_path)
        is_error, text = _call(server, "debug_map_auto", {
            "pattern": "AA BB", "ghidra_address": 0,
            "ghidra_start": 0, "ghidra_end": 0x10, "label": "x",
        })
        assert is_error
        assert "not_connected" in text or "token" in text.lower()


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
