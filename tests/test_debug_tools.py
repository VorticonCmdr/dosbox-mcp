# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.debug import (
    _breakpoint_add,
    _breakpoint_delete,
    _run_to,
    _step,
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
