# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.debug import _wait


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.last_path = None
        self.last_kwargs = None

    def get(self, path, **kwargs):
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
