# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

import httpx

from dosbox_mcp.config import Config
from dosbox_mcp.connection import Connection
from dosbox_mcp.tools.script import (
    _script_load,
    _script_log,
    _script_start,
    _script_status,
    _script_stop,
)


class _FakeClient:
    def __init__(self, response=None):
        self._response = response
        self.text_calls = []
        self.json_calls = []
        self.get_calls = []

    def post_text(self, path, text, content_type="text/plain", params=None):
        self.text_calls.append((path, text, content_type, params))
        return {"status": "loaded", "name": (params or {}).get("name", "unnamed")}

    def post(self, path, json=None):
        self.json_calls.append((path, json))
        return self._response if self._response is not None else {"status": "started"}

    def get(self, path, params=None):
        self.get_calls.append(path)
        return self._response


def test_script_load_sends_lua_as_text_not_json():
    # aug-bt7n: script/load wants a text/plain body; the old JSON post
    # 415'd. Verify the handler uses post_text with the raw source.
    client = _FakeClient()
    _script_load(client, {"script": "dosbox.log('hi')"})

    assert len(client.text_calls) == 1
    path, text, ctype, params = client.text_calls[0]
    assert path == "/api/v1/script/load"
    assert text == "dosbox.log('hi')"
    assert ctype == "text/plain"
    assert params == {}


def test_script_load_starts_by_default():
    client = _FakeClient()
    _script_load(client, {"script": "dosbox.log('hi')"})

    assert client.json_calls == [("/api/v1/script/start", None)]


def test_script_load_with_start_false_does_not_start():
    client = _FakeClient()
    result = _script_load(client, {"script": "dosbox.log('hi')", "start": False})

    assert client.json_calls == []
    assert json.loads(result[0].text)["status"] == "loaded"


def test_script_load_forwards_name_seed_and_debug_as_query_params():
    client = _FakeClient()
    _script_load(client, {
        "script": "dosbox.log('hi')",
        "name": "installer",
        "seed": 42,
        "debug": True,
        "start": False,
    })

    _, _, _, params = client.text_calls[0]
    assert params == {"name": "installer", "seed": "42", "debug": "true"}


def test_script_load_seed_as_integral_float_is_sent_as_a_clean_integer():
    # The schema's type:"integer" also accepts a JSON float with a zero
    # fractional part (5.0) - str(5.0) == "5.0" would fail the engine's
    # std::from_chars parse with a confusing "seed is not a valid
    # integer" for a value that passed schema validation.
    client = _FakeClient()
    _script_load(client, {"script": "x", "seed": 5.0, "start": False})

    _, _, _, params = client.text_calls[0]
    assert params["seed"] == "5"


def test_script_load_default_response_preserves_the_loaded_name():
    # Regression: /script/start's own response has no "name" field, so
    # the default start:true path used to silently drop the load
    # confirmation entirely.
    client = _FakeClient()
    result = _script_load(client, {"script": "x", "name": "installer-run-3"})

    body = json.loads(result[0].text)
    assert body["name"] == "installer-run-3"
    assert body["status"] == "started"


def test_script_load_debug_false_is_sent_as_the_string_false():
    client = _FakeClient()
    _script_load(client, {"script": "x", "debug": False, "start": False})

    _, _, _, params = client.text_calls[0]
    assert params == {"debug": "false"}


def test_script_load_omits_params_not_given():
    client = _FakeClient()
    _script_load(client, {"script": "x", "start": False})

    _, _, _, params = client.text_calls[0]
    assert params == {}


def test_script_load_works_through_a_real_connection():
    # In production, script.register passes `conn` (a Connection) as
    # `client`, not a DosboxClient or the fake above. Connection had no
    # post_text, so every real script_run call raised AttributeError; the
    # fake in the tests above has its own post_text and never caught it.
    calls = []

    def handler(request):
        if request.url.path == "/api/v1/dosbox/info":
            return httpx.Response(
                200, json={"version": "0.84-da3", "features": {},
                          "mcp_protocol": "1.0"})
        if request.url.path == "/api/v1/script/load":
            calls.append(("load", request.content,
                          request.headers.get("content-type"),
                          dict(request.url.params)))
            return httpx.Response(200, json={"status": "loaded", "name": "test"})
        if request.url.path == "/api/v1/script/start":
            calls.append(("start", request.content))
            return httpx.Response(200, json={"status": "started"})
        return httpx.Response(404, json={"error": "not found"})

    config = Config(base_url="http://127.0.0.1:8386", token="0" * 64)
    conn = Connection(config, transport=httpx.MockTransport(handler))

    result = _script_load(conn, {"script": "dosbox.log('hi')", "name": "test"})

    assert calls[0] == ("load", b"dosbox.log('hi')", "text/plain", {"name": "test"})
    assert calls[1][0] == "start"
    assert json.loads(result[0].text) == {"status": "started", "name": "test"}


def test_script_start_posts_with_no_body():
    client = _FakeClient({"status": "running"})
    result = _script_start(client)

    assert client.json_calls == [("/api/v1/script/start", None)]
    assert json.loads(result[0].text) == {"status": "running"}


def test_script_status_gets_the_status_route():
    client = _FakeClient({"state": "idle", "frame": 0, "name": "unnamed"})
    result = _script_status(client)

    assert client.get_calls == ["/api/v1/script/status"]
    assert json.loads(result[0].text)["state"] == "idle"


def test_script_log_gets_the_log_route():
    client = _FakeClient({"path": "/tmp/x.log", "truncated": False, "content": "hi\n"})
    result = _script_log(client)

    assert client.get_calls == ["/api/v1/script/log"]
    assert json.loads(result[0].text)["content"] == "hi\n"


def test_script_stop_posts_with_no_body():
    client = _FakeClient({"status": "stopped"})
    result = _script_stop(client)

    assert client.json_calls == [("/api/v1/script/stop", None)]
    assert json.loads(result[0].text) == {"status": "stopped"}
