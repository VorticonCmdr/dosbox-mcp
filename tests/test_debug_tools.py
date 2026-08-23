# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.debug import (
    _backtrace,
    _breakpoint_add,
    _breakpoint_delete,
    _disassemble,
    _pause,
    _run_to,
    _status,
    _step,
    _step_out,
    _step_over,
    _wait,
)


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.last_method = None
        self.last_path = None
        self.last_kwargs = None

    def get(self, path, **kwargs):
        self.last_method = "get"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response

    def post(self, path, **kwargs):
        self.last_method = "post"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response

    def delete(self, path, **kwargs):
        self.last_method = "delete"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response


def test_posts_the_args_straight_through_as_query_params():
    client = _FakeClient({"satisfied": True, "reason": "breakpoint", "stop_id": 2})
    args = {"since_stop_id": 1, "timeout_ms": 3000}

    _wait(client, args)

    assert client.last_path == "/api/v1/debug/wait"
    assert client.last_kwargs["params"] == args


def test_default_timeout_gives_five_seconds_of_transport_slack():
    client = _FakeClient({"satisfied": False, "reason": "timeout"})

    _wait(client, {"since_stop_id": 0})

    assert client.last_kwargs["timeout"] == 10.0


def test_explicit_timeout_ms_is_converted_to_seconds_plus_slack():
    client = _FakeClient({"satisfied": False})

    _wait(client, {"since_stop_id": 0, "timeout_ms": 12000})

    assert client.last_kwargs["timeout"] == 17.0


def test_returns_the_response_as_readable_json():
    response = {"satisfied": True, "reason": "step", "stop_id": 5}
    client = _FakeClient(response)

    result = _wait(client, {"since_stop_id": 4})

    assert len(result) == 1
    parsed = json.loads(result[0].text)
    assert parsed == response


def test_step_posts_args_straight_through_as_the_json_body():
    client = _FakeClient({"status": "ok", "debugging": True})

    _step(client, {"count": 10})

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/debug/step"
    assert client.last_kwargs["json"] == {"count": 10}


def test_step_with_no_args_posts_an_empty_body():
    client = _FakeClient({"status": "ok", "debugging": True})

    _step(client, {})

    assert client.last_kwargs["json"] == {}


def test_step_over_posts_with_no_body():
    client = _FakeClient({"status": "ok", "stepped_over": True})

    _step_over(client)

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/debug/step_over"


def test_run_to_posts_segment_and_offset_straight_through():
    client = _FakeClient({"status": "ok", "resumed_from_stop_id": 3})
    args = {"segment": 0x1000, "offset": 0x50}

    _run_to(client, args)

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/debug/run_to"
    assert client.last_kwargs["json"] == args


def test_breakpoint_add_posts_args_straight_through_including_condition_and_ignore_count():
    client = _FakeClient({"status": "ok", "id": 1})
    args = {
        "type": "execute",
        "segment": 0x1000,
        "offset": 0x50,
        "ignore_count": 5,
        "condition": {"register": "eax", "op": "eq", "value": 0x4C00},
    }

    _breakpoint_add(client, args)

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/debug/breakpoints"
    assert client.last_kwargs["json"] == args


def test_disassemble_builds_the_path_from_segment_offset_and_count():
    client = _FakeClient({"instructions": [], "truncated": False})

    _disassemble(client, {"segment": 0xF000, "offset": 0x100, "count": 10})

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/debug/disassemble/61440/256/10"


def test_step_out_posts_with_no_body():
    client = _FakeClient({"status": "ok", "resumed_from_stop_id": 3})

    _step_out(client)

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/debug/step_out"


def test_backtrace_passes_max_frames_as_a_query_param():
    client = _FakeClient({"frames": [], "stopped_reason": "bp_zero"})
    args = {"max_frames": 8}

    _backtrace(client, args)

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/debug/backtrace"
    assert client.last_kwargs["params"] == args


def test_backtrace_with_no_args_sends_no_query_params():
    client = _FakeClient({"frames": [], "stopped_reason": "max_frames"})

    _backtrace(client, {})

    assert client.last_kwargs["params"] == {}


def test_breakpoint_delete_with_id_sends_only_id():
    client = _FakeClient({"status": "removed", "id": 7})

    _breakpoint_delete(client, {"id": 7})

    assert client.last_kwargs["json"] == {"id": 7}


def test_breakpoint_delete_with_index_sends_only_index():
    client = _FakeClient({"status": "removed", "index": 0})

    _breakpoint_delete(client, {"index": 0})

    assert client.last_kwargs["json"] == {"index": 0}


def test_breakpoint_delete_with_neither_sends_no_body():
    client = _FakeClient({"status": "cleared"})

    _breakpoint_delete(client, {})

    assert "json" not in client.last_kwargs


def test_breakpoint_delete_with_both_passes_both_through_for_the_engine_to_reject():
    # A confused caller sending both isn't silently resolved here - the
    # engine's own validation gives a clearer error than picking one.
    client = _FakeClient({"error": "specify only one of 'id' or 'index'"})

    _breakpoint_delete(client, {"id": 7, "index": 0})

    assert client.last_kwargs["json"] == {"id": 7, "index": 0}


def _stub_annotate(mapping):
    """A minimal annotate(segment, offset) stand-in for symbols.
    make_annotator's real one: mapping is {(segment, offset): "symbol"},
    anything not in it resolves to None - same contract the real
    annotate has for an address no loaded symbol covers."""
    return lambda segment, offset: mapping.get((segment, offset))


class TestSymbolAnnotation:
    def test_status_adds_a_symbol_when_annotate_resolves_the_position(self):
        response = {"debugging": True, "stop": {
            "stop_id": 1, "reason": "paused",
            "registers": {"cs": 0x1000, "eip": 0x50},
        }}
        client = _FakeClient(response)
        annotate = _stub_annotate({(0x1000, 0x50): "main+0x10"})

        result = _status(client, annotate)

        assert json.loads(result[0].text)["stop"]["symbol"] == "main+0x10"

    def test_status_omits_symbol_when_annotate_resolves_nothing(self):
        response = {"debugging": True, "stop": {
            "stop_id": 1, "reason": "paused",
            "registers": {"cs": 0x1000, "eip": 0x50},
        }}
        client = _FakeClient(response)

        result = _status(client, _stub_annotate({}))

        assert "symbol" not in json.loads(result[0].text)["stop"]

    def test_status_with_no_annotate_leaves_the_response_untouched(self):
        response = {"debugging": True, "stop": {
            "stop_id": 1, "reason": "paused",
            "registers": {"cs": 0x1000, "eip": 0x50},
        }}
        client = _FakeClient(response)

        result = _status(client)

        assert "symbol" not in json.loads(result[0].text)["stop"]

    def test_pause_adds_a_symbol_to_its_stop_record(self):
        response = {"status": "ok", "debugging": True,
                    "stop": {"registers": {"cs": 0x2000, "eip": 0x10}}}
        client = _FakeClient(response)

        result = _pause(client, _stub_annotate({(0x2000, 0x10): "entry"}))

        assert json.loads(result[0].text)["stop"]["symbol"] == "entry"

    def test_step_adds_a_symbol_to_its_stop_record(self):
        response = {"status": "ok", "debugging": True,
                    "stop": {"registers": {"cs": 0x2000, "eip": 0x10}}}
        client = _FakeClient(response)

        result = _step(client, {}, _stub_annotate({(0x2000, 0x10): "entry"}))

        assert json.loads(result[0].text)["stop"]["symbol"] == "entry"

    def test_step_over_annotates_the_stop_record_when_present(self):
        response = {"status": "ok", "stepped_over": True, "debugging": True,
                    "resumed_from_stop_id": 1,
                    "stop": {"stop_id": 2,
                             "registers": {"cs": 0x2000, "eip": 0x10}}}
        client = _FakeClient(response)

        result = _step_over(client, _stub_annotate({(0x2000, 0x10): "entry"}))

        assert json.loads(result[0].text)["stop"]["symbol"] == "entry"

    def test_step_over_with_no_stop_record_does_not_crash(self):
        # The common plant-and-resume path: no "stop" key at all.
        response = {"status": "ok", "stepped_over": True, "debugging": True,
                    "resumed_from_stop_id": 1}
        client = _FakeClient(response)

        result = _step_over(client, _stub_annotate({(0x2000, 0x10): "entry"}))

        assert "stop" not in json.loads(result[0].text)

    def test_wait_annotates_its_flat_response_in_place(self):
        response = {"satisfied": True, "debugging": True, "stop_id": 3,
                    "reason": "breakpoint",
                    "registers": {"cs": 0x3000, "eip": 0x20}}
        client = _FakeClient(response)

        result = _wait(client, {"since_stop_id": 0},
                       _stub_annotate({(0x3000, 0x20): "handler"}))

        assert json.loads(result[0].text)["symbol"] == "handler"

    def test_disassemble_annotates_each_instruction_and_its_relative_target(self):
        response = {
            "segment": 0x1000, "offset": 0x10, "truncated": False,
            "instructions": [
                {"offset": 0x1000 * 16 + 0x10, "length": 2, "text": "jmp 0x20",
                 "target": 0x1000 * 16 + 0x20, "bytes": "AAA="},
            ],
        }
        client = _FakeClient(response)
        annotate = _stub_annotate({
            (0x1000, 0x10): "main",
            (0x1000, 0x20): "loop_start",
        })

        result = _disassemble(
            client, {"segment": 0x1000, "offset": 0x10, "count": 1}, annotate)

        inst = json.loads(result[0].text)["instructions"][0]
        assert inst["symbol"] == "main"
        assert inst["target_symbol"] == "loop_start"

    def test_disassemble_omits_target_symbol_when_target_is_null(self):
        response = {
            "segment": 0x1000, "offset": 0x10, "truncated": False,
            "instructions": [
                {"offset": 0x1000 * 16 + 0x10, "length": 1, "text": "nop",
                 "target": None, "bytes": "kA=="},
            ],
        }
        client = _FakeClient(response)

        result = _disassemble(
            client, {"segment": 0x1000, "offset": 0x10, "count": 1},
            _stub_annotate({(0x1000, 0x10): "main"}))

        inst = json.loads(result[0].text)["instructions"][0]
        assert inst["symbol"] == "main"
        assert "target_symbol" not in inst

    def test_backtrace_annotates_each_frame_independently(self):
        response = {"frames": [
            {"bp": 0x100, "segment": 0x1000, "offset": 0x10, "confidence": "high"},
            {"bp": 0x0FE, "segment": 0x1000, "offset": 0x200, "confidence": "low"},
        ], "stopped_reason": "chain_ended"}
        client = _FakeClient(response)

        result = _backtrace(client, {}, _stub_annotate({(0x1000, 0x10): "main"}))

        frames = json.loads(result[0].text)["frames"]
        assert frames[0]["symbol"] == "main"
        assert "symbol" not in frames[1]

    def test_status_does_not_annotate_the_never_stopped_placeholder_record(self):
        # DebugStopInfo's own default before the debugger has ever
        # stopped: reason stays "never_stopped" but registers is still
        # a real (all-zero) dict, not empty/None - annotating cs=0/
        # eip=0 as if it were a genuine position would be misleading.
        response = {"debugging": False, "stop": {
            "stop_id": 0, "reason": "never_stopped",
            "registers": {"cs": 0, "eip": 0},
        }}
        client = _FakeClient(response)

        result = _status(client, _stub_annotate({(0, 0): "would_be_wrong"}))

        assert "symbol" not in json.loads(result[0].text)["stop"]

    def test_wait_does_not_annotate_a_never_stopped_timeout_response(self):
        response = {"satisfied": False, "debugging": False, "stop_id": 0,
                    "reason": "never_stopped", "registers": {"cs": 0, "eip": 0}}
        client = _FakeClient(response)

        result = _wait(client, {"since_stop_id": 0},
                       _stub_annotate({(0, 0): "would_be_wrong"}))

        assert "symbol" not in json.loads(result[0].text)

    def test_status_annotates_an_execute_or_memory_breakpoint_hits_own_location(self):
        # The breakpoint's armed segment:offset is a distinct address
        # from CS:EIP (the write can happen anywhere for a memory
        # watchpoint) and gets its own 'symbol', nested on the
        # breakpoint sub-object rather than the top-level 'symbol'.
        response = {"debugging": True, "stop": {
            "stop_id": 5, "reason": "breakpoint",
            "registers": {"cs": 0x1000, "eip": 0x50},
            "breakpoint": {"type": "memory", "segment": 0x2000, "offset": 0x30,
                          "id": 1},
        }}
        client = _FakeClient(response)
        annotate = _stub_annotate({
            (0x1000, 0x50): "unrelated_code",
            (0x2000, 0x30): "g_health",
        })

        result = _status(client, annotate)

        stop = json.loads(result[0].text)["stop"]
        assert stop["symbol"] == "unrelated_code"
        assert stop["breakpoint"]["symbol"] == "g_health"

    def test_interrupt_breakpoint_hit_gets_no_breakpoint_symbol(self):
        # An interrupt breakpoint's segment/offset fields are meaningless
        # (always 0) - annotating them could spuriously match whatever
        # symbol happens to sit at ghidra address 0.
        response = {"debugging": True, "stop": {
            "stop_id": 5, "reason": "breakpoint",
            "registers": {"cs": 0x1000, "eip": 0x50},
            "breakpoint": {"type": "interrupt", "segment": 0, "offset": 0,
                          "int": 0x21, "id": 1},
        }}
        client = _FakeClient(response)
        annotate = _stub_annotate({(0, 0): "would_be_spurious"})

        result = _status(client, annotate)

        assert "symbol" not in json.loads(result[0].text)["stop"]["breakpoint"]
