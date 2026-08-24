# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.io import _port_read, _port_write


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

    def put(self, path, **kwargs):
        self.last_method = "put"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response


def test_port_read_gets_the_port_route_with_default_width():
    response = {"port": 0x3D4, "value": 0x0E, "width": 1}
    client = _FakeClient(response)

    result = _port_read(client, {"port": 0x3D4})

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/io/port"
    assert client.last_kwargs["params"] == {"port": 0x3D4, "width": 1}
    assert json.loads(result[0].text) == response


def test_port_read_forwards_explicit_width():
    client = _FakeClient({"port": 0x3D4, "value": 0x0E, "width": 2})

    _port_read(client, {"port": 0x3D4, "width": 2})

    assert client.last_kwargs["params"] == {"port": 0x3D4, "width": 2}


def test_port_write_puts_port_value_and_default_width():
    client = _FakeClient({"status": "ok"})

    _port_write(client, {"port": 0x3D4, "value": 0x0E})

    assert client.last_method == "put"
    assert client.last_path == "/api/v1/io/port"
    assert client.last_kwargs["json"] == {
        "port": 0x3D4, "value": 0x0E, "width": 1}


def test_port_write_forwards_explicit_width():
    client = _FakeClient({"status": "ok"})

    _port_write(client, {"port": 0x3D4, "value": 0x0E, "width": 2})

    assert client.last_kwargs["json"] == {
        "port": 0x3D4, "value": 0x0E, "width": 2}
