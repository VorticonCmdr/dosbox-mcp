# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.input import (
    _input_sequence,
    _record_pause,
    _record_start,
    _record_status,
    _record_stop,
    _recording_delete,
    _recordings_list,
    _replay_cancel,
    _replay_status,
)


class _FakeClient:
    def __init__(self, response=None):
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


def test_input_sequence_forwards_events():
    client = _FakeClient({"status": "ok", "events_scheduled": 1})

    _input_sequence(client, {"events": [{"type": "key", "key": "KBD_a"}]})

    assert client.last_kwargs["json"] == {
        "events": [{"type": "key", "key": "KBD_a"}]}


def test_input_sequence_forwards_recording_not_events():
    client = _FakeClient({"status": "ok", "events_scheduled": 3})

    _input_sequence(client, {"recording": "install-run-1"})

    assert client.last_kwargs["json"] == {"recording": "install-run-1"}


def test_input_sequence_forwards_both_when_both_given():
    # The bridge is a thin pass-through here - the engine is the one
    # that rejects the conflict (400), not this handler.
    client = _FakeClient({"error": "conflict"})

    _input_sequence(client, {"events": [], "recording": "x"})

    assert client.last_kwargs["json"] == {"events": [], "recording": "x"}


def test_replay_status_gets_the_status_route():
    response = {"active": False, "engine": "none", "total": 0,
                "dispatched": 0, "remaining": 0, "elapsed_ms": 0,
                "drift_ms": 0, "current_frame": 1234}
    client = _FakeClient(response)

    result = _replay_status(client)

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/input/replay/status"
    assert json.loads(result[0].text) == response


def test_replay_cancel_deletes_with_no_body():
    client = _FakeClient({"cancelled": True})

    result = _replay_cancel(client)

    assert client.last_method == "delete"
    assert client.last_path == "/api/v1/input/replay"
    assert "json" not in client.last_kwargs
    assert json.loads(result[0].text) == {"cancelled": True}


def test_record_start_posts_with_no_body():
    client = _FakeClient({"status": "recording"})

    result = _record_start(client)

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/input/record/start"
    assert "json" not in client.last_kwargs
    assert json.loads(result[0].text) == {"status": "recording"}


def test_record_pause_posts_with_no_body():
    client = _FakeClient({"status": "paused"})

    _record_pause(client)

    assert client.last_path == "/api/v1/input/record/pause"


def test_record_status_gets_the_status_route():
    client = _FakeClient({"recording": False})

    _record_status(client)

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/input/record/status"


def test_record_stop_with_no_args_has_no_query_string():
    client = _FakeClient({"event_count": 0})

    _record_stop(client, {})

    assert client.last_path == "/api/v1/input/record/stop"


def test_record_stop_encodes_name_in_the_query_string():
    client = _FakeClient({"event_count": 0, "name": "run-1"})

    _record_stop(client, {"name": "run-1"})

    assert client.last_path == "/api/v1/input/record/stop?name=run-1"


def test_record_stop_include_events_false_adds_query_param():
    client = _FakeClient({"event_count": 0})

    _record_stop(client, {"name": "run-1", "include_events": False})

    assert "name=run-1" in client.last_path
    assert "include_events=false" in client.last_path


def test_record_stop_include_events_true_omits_the_param():
    client = _FakeClient({"event_count": 0})

    _record_stop(client, {"include_events": True})

    assert client.last_path == "/api/v1/input/record/stop"


def test_recordings_list_gets_the_recordings_route():
    response = {"recordings": [{"name": "a", "event_count": 5}]}
    client = _FakeClient(response)

    result = _recordings_list(client)

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/input/recordings"
    assert json.loads(result[0].text) == response


def test_recording_delete_url_encodes_the_name():
    client = _FakeClient({"status": "deleted", "name": "a b"})

    _recording_delete(client, {"name": "a b"})

    assert client.last_method == "delete"
    assert client.last_path == "/api/v1/input/recordings/a%20b"
