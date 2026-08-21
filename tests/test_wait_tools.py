# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.wait import _wait_for


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.last_path = None
        self.last_kwargs = None

    def post(self, path, **kwargs):
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response


def test_posts_the_args_straight_through_as_the_json_body():
    client = _FakeClient({"satisfied": True, "reason": "matched", "for": "text"})
    args = {"for": "text", "pattern": "C:\\>"}

    _wait_for(client, args)

    assert client.last_path == "/api/v1/wait"
    assert client.last_kwargs["json"] == args


def test_default_timeout_gives_five_seconds_of_transport_slack():
    client = _FakeClient({"satisfied": False, "reason": "timeout", "for": "frames"})

    _wait_for(client, {"for": "frames", "count": 1})

    assert client.last_kwargs["timeout"] == 10.0


def test_explicit_timeout_ms_is_converted_to_seconds_plus_slack():
    client = _FakeClient({"satisfied": False, "reason": "timeout", "for": "text"})

    _wait_for(client, {"for": "text", "pattern": "x", "timeout_ms": 12000})

    assert client.last_kwargs["timeout"] == 17.0


def test_returns_the_response_as_readable_json():
    response = {"satisfied": True, "reason": "matched", "for": "replay_done"}
    client = _FakeClient(response)

    result = _wait_for(client, {"for": "replay_done"})

    assert len(result) == 1
    parsed = json.loads(result[0].text)
    assert parsed == response
