# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.input import _replay_cancel, _replay_status


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

    def delete(self, path, **kwargs):
        self.last_method = "delete"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response


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
