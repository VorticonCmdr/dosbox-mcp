# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.session import _session_info, _shutdown, _status

_STATUS_RESPONSE = {
    "running": True,
    "shutdown_requested": False,
    "is_booted": True,
    "program": "GAME",
    "canonical_name": "C:\\GAMES\\GAME.EXE",
    "is_shell": False,
    "last_tick_ms_ago": 4,
    "frame": 12345,
    "emulation": "running",
}

_DRIVE_RESPONSE = {
    "drives": [
        {"letter": "A", "mounted": False},
        {"letter": "C", "mounted": True, "type": "fat", "info": "/games",
         "read_only": False, "removable": False},
        {"letter": "D", "mounted": True, "type": "iso", "info": "/iso/game.iso",
         "read_only": True, "removable": True},
    ] + [{"letter": chr(ord("E") + i), "mounted": False} for i in range(22)],
}

_INFO_RESPONSE = {
    "version": "dosbox-automation 0.84-da3",
    "features": {"memory": True, "debugger": True},
    "capabilities": {"memory": {"state": "on", "reason": "always available",
                                "limits": {"max_transfer_bytes": 65536}}},
    "limits": {"max_request_body_bytes": 1048576},
}


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self.paths = []

    def get(self, path, **kwargs):
        self.paths.append(path)
        return self._responses[path]


def _client():
    return _FakeClient({
        "/api/v1/status": _STATUS_RESPONSE,
        "/api/v1/drive": _DRIVE_RESPONSE,
        "/api/v1/dosbox/info": _INFO_RESPONSE,
    })


def test_status_never_hits_the_redundant_program_state_route():
    client = _client()
    _status(client, {})
    assert "/api/v1/program/state" not in client.paths


def test_status_summary_has_the_program_and_version_fields():
    client = _client()
    result = _status(client, {})
    body = json.loads(result[0].text)

    assert body["running"] is True
    assert body["emulation"] == "running"
    assert body["program"] == "GAME"
    assert body["canonical_name"] == "C:\\GAMES\\GAME.EXE"
    assert body["is_shell"] is False
    assert body["is_booted"] is True
    assert body["frame"] == 12345
    assert body["version"] == "dosbox-automation 0.84-da3"


def test_status_summary_lists_only_mounted_drives():
    client = _client()
    result = _status(client, {})
    body = json.loads(result[0].text)

    assert set(body["mounted_drives"]) == {"C", "D"}
    assert body["mounted_drives"]["D"] == {
        "type": "iso", "info": "/iso/game.iso",
        "read_only": True, "removable": True,
    }


def test_status_summary_omits_raw_payloads_by_default():
    client = _client()
    result = _status(client, {})
    body = json.loads(result[0].text)

    assert "status" not in body
    assert "drives" not in body
    assert "info" not in body


def test_status_detail_true_includes_the_full_raw_payloads():
    client = _client()
    result = _status(client, {"detail": True})
    body = json.loads(result[0].text)

    assert body["status"] == _STATUS_RESPONSE
    assert body["drives"] == _DRIVE_RESPONSE["drives"]
    assert len(body["drives"]) == 25
    assert body["info"] == _INFO_RESPONSE


# ---------------------------------------------------------------------------
# dosbox_shutdown
# ---------------------------------------------------------------------------


class _FakeShutdownClient:
    def __init__(self, response):
        self._response = response
        self.last_method = None
        self.last_path = None

    def post(self, path, **kwargs):
        self.last_method = "post"
        self.last_path = path
        return self._response


def test_shutdown_posts_the_shutdown_route():
    client = _FakeShutdownClient({"status": "shutting_down"})

    result = _shutdown(client)

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/dosbox/shutdown"
    assert json.loads(result[0].text) == {"status": "shutting_down"}


# ---------------------------------------------------------------------------
# session_info
# ---------------------------------------------------------------------------


class _FakeInfoClient:
    def __init__(self, base_url="http://127.0.0.1:8386"):
        self.base_url = base_url


def test_session_info_reports_absent_token_with_no_curl_example():
    # conftest.py's isolate_token fixture points DOSBOX_TOKEN_FILE at a
    # nonexistent path and clears DOSBOX_API_TOKEN, so no token is
    # available here by default.
    result = _session_info(_FakeInfoClient())
    body = json.loads(result[0].text)

    assert body["token"] == "absent"
    assert "example" not in body
    assert "note" in body


def test_session_info_reports_the_token_file_read_token_actually_used(
        monkeypatch, tmp_path):
    # Regression: this used to call default_token_path(), which ignores
    # DOSBOX_TOKEN_FILE - so a token read via the env override still got
    # reported (and curl-exampled) against the wrong file.
    token_file = tmp_path / "custom_token"
    token_file.write_text("s3cr3t", encoding="utf-8")
    monkeypatch.setenv("DOSBOX_TOKEN_FILE", str(token_file))

    result = _session_info(_FakeInfoClient())
    body = json.loads(result[0].text)

    assert body["token"] == "present"
    assert body["token_file"] == str(token_file)
    assert str(token_file) in body["example"]
    # The token value itself must never appear - only its file path.
    assert "s3cr3t" not in json.dumps(body)
