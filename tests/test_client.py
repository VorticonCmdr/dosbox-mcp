# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import httpx
import pytest

from dosbox_mcp.client import DosboxClient, DosboxError


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return DosboxClient(
        base_url="http://127.0.0.1:8386",
        token="0" * 64,
        transport=transport,
    )


def test_get_sends_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["client"] = request.headers.get("x-client")
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    body = client.get("/api/v1/status")
    assert body == {"ok": True}
    assert seen["auth"] == "Bearer " + "0" * 64
    assert seen["client"] == "mcp"


def test_post_sends_json_body():
    seen = {}

    def handler(request):
        seen["body"] = request.content
        seen["ctype"] = request.headers.get("content-type")
        return httpx.Response(200, json={"status": "ok"})

    client = make_client(handler)
    result = client.post("/api/v1/input/type", json={"text": "hi"})
    assert result == {"status": "ok"}
    assert b'"text"' in seen["body"]


def test_error_status_raises():
    def handler(request):
        return httpx.Response(400, json={"error": "bad"})

    client = make_client(handler)
    with pytest.raises(RuntimeError, match="bad"):
        client.get("/api/v1/status")


def test_binary_response_returned_as_bytes():
    def handler(request):
        return httpx.Response(
            200,
            content=b"\x89PNG",
            headers={"content-type": "image/png"},
        )

    client = make_client(handler)
    data = client.get("/api/v1/video/frame")
    assert data == b"\x89PNG"


def test_error_status_raises_typed_error_with_engine_fields():
    # The engine's error_handler (webserver.cpp, since protocol 1.2)
    # sends {error, error_code, retryable}; DosboxError carries all
    # three plus the status and the route that failed.
    def handler(request):
        return httpx.Response(400, json={
            "error": "port must be 0x0000..0xFFFF",
            "error_code": "invalid_argument",
            "retryable": False,
        })

    client = make_client(handler)
    with pytest.raises(DosboxError) as exc_info:
        client.get("/api/v1/io/port")

    e = exc_info.value
    assert e.status == 400
    assert e.code == "invalid_argument"
    assert e.message == "port must be 0x0000..0xFFFF"
    assert e.retryable is False
    assert e.route == "GET /api/v1/io/port"


def test_bridge_timeout_status_is_retryable():
    def handler(request):
        return httpx.Response(503, json={
            "error": "Command execution timed out - the emulator may be paused",
            "error_code": "bridge_timeout",
            "retryable": True,
        })

    client = make_client(handler)
    with pytest.raises(DosboxError) as exc_info:
        client.post("/api/v1/cpu/state")

    assert exc_info.value.retryable is True
    assert exc_info.value.code == "bridge_timeout"


def test_error_status_falls_back_gracefully_against_an_older_engine():
    # An engine older than protocol 1.2 sends only {"error": "..."} -
    # code and retryable must default rather than raise.
    def handler(request):
        return httpx.Response(500, json={"error": "Internal server error"})

    client = make_client(handler)
    with pytest.raises(DosboxError) as exc_info:
        client.get("/api/v1/status")

    e = exc_info.value
    assert e.status == 500
    assert e.code == "unknown"
    assert e.retryable is False
    assert e.message == "Internal server error"


def test_error_status_with_non_json_body_falls_back_to_raw_text():
    def handler(request):
        return httpx.Response(502, text="Bad Gateway", headers={
            "content-type": "text/plain",
        })

    client = make_client(handler)
    with pytest.raises(DosboxError) as exc_info:
        client.get("/api/v1/status")

    e = exc_info.value
    assert e.status == 502
    assert e.code == "unknown"
    assert e.message == "Bad Gateway"


def test_429_captures_the_retry_after_header():
    # script/load's rate limiter (lua_bridge_commands.cpp) sends this on
    # every 429 - whole seconds, rounded up, no {error_code, retryable}.
    def handler(request):
        return httpx.Response(429, json={"error": "too many requests"},
                              headers={"Retry-After": "2"})

    client = make_client(handler)
    with pytest.raises(DosboxError) as exc_info:
        client.post_text("/api/v1/script/load", "print(1)")

    e = exc_info.value
    assert e.status == 429
    assert e.retry_after == "2"


def test_error_without_a_retry_after_header_leaves_it_none():
    def handler(request):
        return httpx.Response(400, json={"error": "bad"})

    client = make_client(handler)
    with pytest.raises(DosboxError) as exc_info:
        client.get("/api/v1/status")

    assert exc_info.value.retry_after is None


def test_per_call_timeout_override_actually_overrides():
    # Required by 1.8 (server-side wait): a caller doing a long poll
    # needs a longer client-side timeout than the 30s default without
    # that default changing for every other call.
    seen = {}

    def handler(request):
        seen["extensions"] = dict(request.extensions)
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    assert client.get("/api/v1/status", timeout=1.5) == {"ok": True}
    assert seen["extensions"]["timeout"]["read"] == 1.5

    assert client.post("/api/v1/cpu/state", timeout=5.0) == {"ok": True}
    assert seen["extensions"]["timeout"]["read"] == 5.0


def test_omitted_timeout_does_not_pass_none_to_httpx():
    # httpx treats an explicit timeout=None as "no timeout at all", not
    # "use the client default" - passing that through by default would
    # silently disable the 30s default for every ordinary call.
    seen = {}

    def handler(request):
        seen["extensions"] = dict(request.extensions)
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    client.get("/api/v1/status")
    # httpx stamps the resolved timeout onto request.extensions; the
    # client's own 30s default must have been used, not an infinite one.
    timeout = seen["extensions"].get("timeout")
    if timeout is not None:
        assert timeout.get("read") == 30.0
