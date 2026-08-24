# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.freeze import _freeze_clear, _freeze_list, _freeze_set


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


def test_freeze_set_posts_address_value_and_default_width():
    client = _FakeClient({"status": "ok"})

    _freeze_set(client, {"address": 0x1000, "value": 99})

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/memory/freeze"
    assert client.last_kwargs["json"] == {
        "address": 0x1000, "value": 99, "width": 1}


def test_freeze_set_forwards_explicit_width():
    client = _FakeClient({"status": "ok"})

    _freeze_set(client, {"address": 0x1000, "value": 99, "width": 4})

    assert client.last_kwargs["json"] == {
        "address": 0x1000, "value": 99, "width": 4}


def test_freeze_list_gets_the_freeze_route_with_no_body():
    response = {"freezes": [{"address": 0x1000, "value": 99, "width": 1}]}
    client = _FakeClient(response)

    result = _freeze_list(client)

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/memory/freeze"
    assert json.loads(result[0].text) == response


def test_freeze_clear_with_address_sends_only_that_address():
    client = _FakeClient({"status": "removed"})

    _freeze_clear(client, {"address": 0x1000})

    assert client.last_method == "delete"
    assert client.last_path == "/api/v1/memory/freeze"
    assert client.last_kwargs["json"] == {"address": 0x1000}


def test_freeze_clear_with_no_address_sends_no_body():
    client = _FakeClient({"status": "cleared"})

    _freeze_clear(client, {})

    assert client.last_method == "delete"
    assert "json" not in client.last_kwargs
