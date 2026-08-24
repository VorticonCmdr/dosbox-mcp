# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.cpu import _cpu_state, _cpu_write


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


def test_cpu_state_gets_the_state_route_with_no_body():
    response = {"registers": {"cs": 0x2000, "eip": 0x100}}
    client = _FakeClient(response)

    result = _cpu_state(client)

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/cpu/state"
    assert json.loads(result[0].text) == response


def test_cpu_write_puts_register_and_value():
    client = _FakeClient({"status": "ok"})

    _cpu_write(client, {"register": "eax", "value": 0x1234})

    assert client.last_method == "put"
    assert client.last_path == "/api/v1/cpu/register"
    assert client.last_kwargs["json"] == {"register": "eax", "value": 0x1234}
