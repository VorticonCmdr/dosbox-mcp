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
    assert "script_load" in names
    assert "script_start" in names
    assert "script_log" in names
    assert "video_capture_status" in names


def test_all_tools_registered_regardless_of_features():
    server = _build()
    names = server.registered_tool_names()
    assert "mem_read" in names
    assert "mem_write" in names
    assert "input_type" in names
    assert "input_key" in names
    assert "replay_status" in names
    assert "replay_cancel" in names
    assert "record_start" in names
    assert "record_pause" in names
    assert "record_stop" in names
    assert "record_status" in names
    assert "recordings_list" in names
    assert "recording_delete" in names
    assert "mouse_position" in names
    assert "mouse_set_position" in names
    assert "mem_search" in names
    assert "dos_memory_map" in names
    assert "dos_ems_status" in names
    assert "dos_xms_status" in names
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
              "debug_watch_add", "debug_watch_list", "debug_watch_delete",
              "debug_backtrace", "debug_step_out"):
        assert t in names
    for t in ("debug_map_set_base", "debug_map_auto", "debug_map_to_live",
              "debug_map_to_ghidra", "debug_map_status"):
        assert t in names
    assert "debug_symbols_load" in names
    assert "debug_symbols_status" in names
    assert "drive_list" in names
    assert "mount_status" in names
    assert "mount_images" in names
    assert "drive_swap" in names
    assert "drive_mount" in names
    assert "mount_lock" in names
    assert "batch_execute" in names


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
        assert "replay_status" in names
        assert "replay_cancel" not in names
        assert "record_start" not in names
        assert "record_status" in names
        assert "recordings_list" in names
        assert "recording_delete" not in names
        assert "mouse_position" in names
        assert "mouse_set_position" not in names
        assert "script_load" not in names
        assert "script_start" not in names
        assert "script_log" in names
        assert "drive_swap" not in names
        assert "drive_mount" not in names
        assert "mount_lock" not in names
        assert "port_write" not in names
        assert "mem_allocations" in names
        assert "mem_alloc" not in names
        assert "mem_free" not in names
        # mem_snapshot/mem_diff mutate a shared, engine-side snapshot
        # registry (allocate/narrow/evict entries) despite reading like
        # a pure query - an adversarial review of 3.1 caught these
        # mislabeled risk="read" (readOnlyHint=True), which let a
        # mutating operation slip past observe mode's one hard
        # guarantee (never touch the engine). Confirmed full mode still
        # has them, in test_full_registers_everything below.
        assert "mem_snapshot" not in names
        assert "mem_diff" not in names
        assert "drive_list" in names
        assert "mount_status" in names
        assert "mount_images" in names
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
        # debug_symbols_load/status (2.17) are the same kind of
        # bridge-local bookkeeping as debug_map_set_base - never reaches
        # the engine, so they survive observe mode too.
        assert "debug_symbols_load" in names
        assert "debug_symbols_status" in names
        # batch_execute can perform mem_write/cpu_write_register/
        # port_write/freeze_set - the strictest of its constituent
        # single ops (all full-mode-only) governs the whole tool.
        assert "batch_execute" not in names

    def test_interact_still_requires_full_for_debug_map_auto(self):
        names = _build(mode="interact").registered_tool_names()
        assert "debug_map_set_base" in names
        assert "debug_map_auto" not in names

    def test_interact_adds_input_media_script_but_not_surgery(self):
        names = _build(mode="interact").registered_tool_names()
        assert "input_key" in names
        assert "input_type" in names
        assert "replay_cancel" in names
        assert "record_start" in names
        assert "record_stop" in names
        assert "recording_delete" in names
        assert "mouse_set_position" in names
        assert "script_load" in names
        assert "script_start" in names
        assert "video_capture_start" in names
        assert "drive_swap" in names
        assert "drive_mount" in names
        assert "mount_lock" in names
        assert "mem_write" not in names
        assert "freeze_set" not in names
        assert "port_write" not in names
        assert "cpu_write_register" not in names
        assert "mem_snapshot" not in names
        assert "mem_diff" not in names
        assert "debug_step_out" not in names
        assert "batch_execute" not in names

    def test_full_registers_everything(self):
        names = _build(mode="full").registered_tool_names()
        assert "mem_write" in names
        assert "port_write" in names
        assert "freeze_set" in names
        assert "debug_map_auto" in names
        assert "mem_snapshot" in names
        assert "mem_diff" in names
        assert "batch_execute" in names

    def test_unknown_mode_rejected(self):
        import pytest
        with pytest.raises(ValueError, match="mode"):
            _build(mode="root")


def _list_tools(server):
    handler = server.request_handlers[types.ListToolsRequest]

    async def go():
        req = types.ListToolsRequest(method="tools/list")
        result = await handler(req)
        return result.root.tools

    return asyncio.run(go())


class TestRiskTaxonomy:
    """3.1: every tool declares a title and a risk class (server.py's
    RISK_LEVELS), and annotations are derived mechanically from it - a
    read tool gets only readOnlyHint, everything else gets
    destructiveHint/idempotentHint set (never left to the spec's
    default, which is destructiveHint=true for any non-read-only
    tool)."""

    def test_every_tool_has_a_title_and_consistent_annotations(self):
        tools = _list_tools(_build(mode="full"))
        assert tools, "expected at least one tool registered in full mode"
        for t in tools:
            assert t.title, f"{t.name}: missing title"
            a = t.annotations
            assert a is not None, f"{t.name}: missing annotations"
            if a.readOnlyHint:
                assert a.destructiveHint is None, (
                    f"{t.name}: read-only tool should not set destructiveHint"
                )
                assert a.idempotentHint is None, (
                    f"{t.name}: read-only tool should not set idempotentHint"
                )
            else:
                assert a.destructiveHint is not None, (
                    f"{t.name}: non-read-only tool must state destructiveHint "
                    "explicitly - the spec default (true) would call it as "
                    "dangerous as dosbox_shutdown"
                )
                assert a.idempotentHint is not None, (
                    f"{t.name}: non-read-only tool must state idempotentHint explicitly"
                )

    def test_destructive_tools_are_flagged(self):
        tools = {t.name: t for t in _list_tools(_build(mode="full"))}
        for name in ("dosbox_shutdown", "mount_lock"):
            assert tools[name].annotations.destructiveHint is True, name

    def test_non_destructive_mutators_are_not_flagged_destructive(self):
        tools = {t.name: t for t in _list_tools(_build(mode="full"))}
        for name in ("mem_write", "freeze_set", "input_type",
                     "cpu_write_register", "batch_execute",
                     "mouse_set_position"):
            assert tools[name].annotations.destructiveHint is False, name

    def test_idempotent_hints_match_the_documented_examples(self):
        tools = {t.name: t for t in _list_tools(_build(mode="full"))}
        assert tools["mem_write"].annotations.idempotentHint is True
        assert tools["input_type"].annotations.idempotentHint is False
        # Warping to the same (x, y) twice leaves the same end state -
        # genuinely idempotent, unlike input_type/input_key.
        assert tools["mouse_set_position"].annotations.idempotentHint is True
        # A batch mixing an idempotent op (e.g. mem_write) with a
        # deliberately non-idempotent one (port_write - see io.py's own
        # reasoning) can't honestly claim idempotency for the whole call.
        assert tools["batch_execute"].annotations.idempotentHint is False

    def test_mem_snapshot_and_diff_are_not_read_only(self):
        # An adversarial review of this item caught these mislabeled
        # risk="read": both mutate a shared, engine-side snapshot
        # registry (allocate/narrow/evict entries), which let a
        # mutating operation slip past observe mode's one hard
        # guarantee. See TestCapabilityModes for the mode-reachability
        # side of this fix.
        tools = {t.name: t for t in _list_tools(_build(mode="full"))}
        assert tools["mem_snapshot"].annotations.readOnlyHint is False
        assert tools["mem_diff"].annotations.readOnlyHint is False


class TestInputSequenceSchema:
    """Schema-level regressions an adversarial review of 3.1 caught in
    input_sequence's tightened event schema - validated the same way
    the MCP SDK itself validates (jsonschema against the real
    inputSchema), not just by calling the handler function."""

    def _schema(self):
        tools = {t.name: t for t in _list_tools(_build(mode="full"))}
        return tools["input_sequence"].inputSchema

    def _validates(self, instance):
        import jsonschema
        try:
            jsonschema.validate(instance=instance, schema=self._schema())
            return True
        except jsonschema.ValidationError:
            return False

    def test_recorded_mouse_move_round_trips(self):
        # record_stop's response includes only x_rel/y_rel on a
        # mouse_move event, the fields that actually drive replay -
        # feeding that straight back into input_sequence must not fail
        # schema validation.
        event = {"type": "mouse_move", "t": 10.0, "frame": 1,
                 "x_rel": 5.0, "y_rel": -2.0}
        assert self._validates({"events": [event]})

    def test_recorded_mouse_move_host_fields_do_not_round_trip(self):
        # record_stop actually serializes a recorded mouse_move's
        # absolute position as host_x_abs/host_y_abs (item 2.14), host
        # window pixels - a different coordinate space from
        # input_sequence's own x_abs/y_abs (guest DOS screen pixels).
        # Reposting a dump verbatim must fail schema validation loudly
        # rather than silently warp the cursor with the wrong numbers.
        event = {"type": "mouse_move", "t": 10.0, "frame": 1,
                 "x_rel": 5.0, "y_rel": -2.0,
                 "host_x_abs": 320.0, "host_y_abs": 180.0}
        assert not self._validates({"events": [event]})

    def test_mouse_move_x_abs_and_y_abs_validate_together(self):
        event = {"type": "mouse_move", "x_abs": 160, "y_abs": 100}
        assert self._validates({"events": [event]})

    def test_mouse_move_x_abs_without_y_abs_does_not_validate(self):
        event = {"type": "mouse_move", "x_abs": 160}
        assert not self._validates({"events": [event]})

    def test_mouse_move_x_abs_out_of_range_does_not_validate(self):
        event = {"type": "mouse_move", "x_abs": 70000, "y_abs": 100}
        assert not self._validates({"events": [event]})

    def test_mouse_move_x_abs_negative_does_not_validate(self):
        event = {"type": "mouse_move", "x_abs": -1, "y_abs": 100}
        assert not self._validates({"events": [event]})

    def test_event_time_and_frame_have_upper_bounds(self):
        assert not self._validates(
            {"events": [{"type": "key", "key": "KBD_a", "delay_ms": 1e12}]})
        assert not self._validates(
            {"events": [{"type": "key", "key": "KBD_a", "t": 1e12}]})
        assert not self._validates(
            {"events": [{"type": "key", "key": "KBD_a", "frame": 10**12}]})
        assert self._validates(
            {"events": [{"type": "key", "key": "KBD_a", "delay_ms": 1000}]})

    def test_recording_name_pattern_matches_the_engine(self):
        assert not self._validates({"recording": "my recording!"})
        assert self._validates({"recording": "my-recording_1"})
        assert not self._validates({"recording": ""})


class TestScriptLoadSchema:
    """script_load's name/seed/debug/start fields, validated against the
    real inputSchema the same way the MCP SDK validates it."""

    def _schema(self):
        tools = {t.name: t for t in _list_tools(_build(mode="full"))}
        return tools["script_load"].inputSchema

    def _validates(self, instance):
        import jsonschema
        try:
            jsonschema.validate(instance=instance, schema=self._schema())
            return True
        except jsonschema.ValidationError:
            return False

    def test_script_only_validates(self):
        assert self._validates({"script": "print('hi')"})

    def test_missing_script_does_not_validate(self):
        assert not self._validates({"name": "x"})

    def test_name_pattern_matches_the_engine(self):
        assert self._validates({"script": "x", "name": "install-run_1"})
        assert not self._validates({"script": "x", "name": "my script!"})
        assert not self._validates({"script": "x", "name": "x" * 65})

    def test_seed_accepts_a_large_negative_or_positive_int64(self):
        assert self._validates({"script": "x", "seed": -9223372036854775808})
        assert self._validates({"script": "x", "seed": 9223372036854775807})

    def test_seed_out_of_int64_range_does_not_validate(self):
        assert not self._validates({"script": "x", "seed": 9223372036854775808})

    def test_debug_and_start_must_be_boolean(self):
        assert self._validates({"script": "x", "debug": True, "start": False})
        assert not self._validates({"script": "x", "debug": "true"})

    def test_unknown_property_does_not_validate(self):
        assert not self._validates({"script": "x", "content_type": "text/lua"})


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


class TestConcurrentDispatch:
    """3.7: call_tool used to invoke each sync handler inline, blocking
    the single event loop thread for the full duration of its httpx
    call. Two tool calls dispatched together must actually overlap,
    not run back to back."""

    def test_two_slow_calls_dispatched_together_run_concurrently_not_serially(self):
        import time

        def handler(request):
            if request.url.path == "/api/v1/dosbox/info":
                return httpx.Response(200, json={
                    "version": "0.84-test", "features": {}, "mcp_protocol": "1.0",
                    "instance_id": "a" * 32,
                })
            if request.url.path == "/api/v1/status":
                time.sleep(0.2)
                return httpx.Response(200, json={"running": True})
            return httpx.Response(404, json={"error": "not found"})

        config = Config(base_url="http://127.0.0.1:8386", token="0" * 64)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        server = build_server(conn, mode="full")
        call_tool_handler = server.request_handlers[types.CallToolRequest]

        async def one_call():
            req = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(name="dosbox_status", arguments={}),
            )
            return await call_tool_handler(req)

        async def go():
            start = time.monotonic()
            await asyncio.gather(one_call(), one_call(), one_call())
            return time.monotonic() - start

        elapsed = asyncio.run(go())
        # Serialized, three 0.2s calls take ~0.6s; concurrent, ~0.2s.
        # Generous margin for CI jitter without letting a regression
        # back to serial dispatch pass.
        assert elapsed < 0.45, f"calls ran serially, not concurrently: {elapsed:.2f}s"


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


class TestSymbolAnnotationEndToEnd:
    """Full 2.17 wiring, exercised through the real MCP call_tool
    dispatch path rather than by calling tool module functions
    directly: debug_map_set_base anchors a range, debug_symbols_load
    loads a name, and every route the plan names (status, disassemble,
    backtrace, dos_memory_map) comes back with a 'symbol' field. This is
    the only test that would catch a build_server wiring mistake -
    wrong registration order, annotate_fn built from the wrong state,
    or not threaded into debug.register/memory.register at all."""

    def _server(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(tmp_path / "ghidra_map.json"))

        def handler(request):
            path = request.url.path
            if path == "/api/v1/dosbox/info":
                return httpx.Response(200, json={
                    "version": "0.84-da3",
                    "features": {"debugger": True, "disassemble": True,
                                "backtrace": True, "memory": True},
                    "mcp_protocol": "1.0",
                })
            if path == "/api/v1/debug/status":
                return httpx.Response(200, json={
                    "debugging": True,
                    "stop": {"stop_id": 1, "reason": "paused",
                             "registers": {"cs": 0x2000, "eip": 0x10}},
                })
            if path == "/api/v1/dos/internals":
                return httpx.Response(200, json={"memoryMap": [
                    # segment is the MCB header paragraph, one below the
                    # block's own owned memory (0x2000, the anchor below) -
                    # dos_memory_map's annotation accounts for that offset.
                    {"segment": 0x1FFF, "type": 77, "pspSegment": 1,
                     "sizeParas": 10, "sizeBytes": 160, "filename": "TEST",
                     "isLast": True},
                ]})
            if path.startswith("/api/v1/debug/disassemble/"):
                return httpx.Response(200, json={
                    "segment": 0x2000, "offset": 0x10, "truncated": False,
                    "instructions": [
                        {"offset": 0x2000 * 16 + 0x10, "length": 1,
                         "text": "nop", "target": None, "bytes": "kA=="},
                    ],
                })
            if path == "/api/v1/debug/backtrace":
                return httpx.Response(200, json={
                    "frames": [{"bp": 0, "segment": 0x2000, "offset": 0x10,
                               "confidence": "high"}],
                    "stopped_reason": "chain_ended",
                })
            return httpx.Response(404, json={"error": "not found"})

        config = Config(base_url="http://127.0.0.1:8386", token="0" * 64)
        conn = Connection(config, transport=httpx.MockTransport(handler))
        return build_server(conn, mode="full")

    def _anchor_and_load(self, server):
        is_error, _ = _call(server, "debug_map_set_base", {
            "ghidra_address": 0, "live_segment": 0x2000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "main",
        })
        assert not is_error
        # "base" sits at the segment's own start (live offset 0) - what
        # dos_memory_map's block annotation looks up, since an MCB entry
        # has no offset of its own. "entry" sits at 0x10, matching every
        # other route's CS:EIP/frame/instruction fixture below.
        is_error, _ = _call(server, "debug_symbols_load",
                            {"text": "base at 0x00\nentry at 0x10\n"})
        assert not is_error

    def test_debug_status_gets_a_symbol_on_its_stop_record(self, monkeypatch, tmp_path):
        server = self._server(monkeypatch, tmp_path)
        self._anchor_and_load(server)

        is_error, text = _call(server, "debug_status", {})

        assert not is_error
        assert json.loads(text)["stop"]["symbol"] == "entry"

    def test_dos_memory_map_gets_a_symbol_on_its_matching_block(self, monkeypatch, tmp_path):
        server = self._server(monkeypatch, tmp_path)
        self._anchor_and_load(server)

        is_error, text = _call(server, "dos_memory_map", {})

        assert not is_error
        assert json.loads(text)["blocks"][0]["symbol"] == "base"

    def test_debug_disassemble_gets_a_symbol_on_its_instruction(self, monkeypatch, tmp_path):
        server = self._server(monkeypatch, tmp_path)
        self._anchor_and_load(server)

        is_error, text = _call(server, "debug_disassemble",
                               {"segment": 0x2000, "offset": 0x10, "count": 1})

        assert not is_error
        assert json.loads(text)["instructions"][0]["symbol"] == "entry"

    def test_debug_backtrace_gets_a_symbol_on_its_frame(self, monkeypatch, tmp_path):
        server = self._server(monkeypatch, tmp_path)
        self._anchor_and_load(server)

        is_error, text = _call(server, "debug_backtrace", {})

        assert not is_error
        assert json.loads(text)["frames"][0]["symbol"] == "entry"

    def test_no_symbol_field_before_anything_is_loaded(self, monkeypatch, tmp_path):
        server = self._server(monkeypatch, tmp_path)

        is_error, text = _call(server, "debug_status", {})

        assert not is_error
        assert "symbol" not in json.loads(text)["stop"]


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
            assert path == "/api/v1/cpu/state"
            return {"registers": {"cs": 0x2000, "eip": 0x100}}

    result = _cpu_state(_FakeClient())
    body = json.loads(result[0].text)
    assert body["registers"]["cs"] == 0x2000
