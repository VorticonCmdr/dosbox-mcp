# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

import mcp.types as types
import pytest

from dosbox_mcp.config import Config
from dosbox_mcp.connection import Connection, NotConnected
from dosbox_mcp.lifecycle import LifecycleError, SpawnError
from dosbox_mcp.protocol import BRIDGE_PROTOCOL
from dosbox_mcp.server import build_server
from dosbox_mcp.tools.bridge import (
    _connect,
    _logs,
    _setup,
    _start,
    _status,
    _stop,
    _swagger,
)


class FakeConn:
    def __init__(self, connected=True, fail_with=None, effective_protocol=None):
        self._connected = connected
        self._fail_with = fail_with
        self.detached = 0
        self.spec = {"paths": {}}
        # None (the default) means "not connected / no negotiation yet" -
        # _known_prefixes_for falls back to the bridge's own highest
        # implemented minor in that case, same as production Connection
        # before its first successful attach.
        self.effective_protocol = effective_protocol

    def status(self):
        return {"connected": self._connected, "base_url": "http://127.0.0.1:8386",
                "protocol": "1.0" if self._connected else None,
                "token": "present"}

    def ensure_connected(self):
        if self._fail_with:
            raise NotConnected(self._fail_with)
        self._connected = True

    def detach(self):
        self.detached += 1
        self._connected = False

    def get(self, path, **kwargs):
        assert path == "/openapi.json"
        return self.spec


class FakeManager:
    def __init__(self, running=False):
        self._running = running
        self.calls = []
        self._logs = ["fake engine started", "MOUNT_POLICY: x"]

    @property
    def running(self):
        return self._running

    @property
    def pid(self):
        return 4242 if self._running else None

    def start(self, deadline_seconds=30.0):
        self.calls.append("start")
        if self._running:
            raise LifecycleError("an instance is already managed")
        self._running = True
        return {"version": "0.84-test", "features": {}}

    def stop(self):
        self.calls.append("stop")
        if not self._running:
            raise LifecycleError("no managed instance to stop")
        self._running = False

    def logs(self, n=None):
        if not self.calls:
            raise LifecycleError("no output captured")
        return self._logs if n is None else self._logs[-n:]


def _text(result):
    # Success paths still return a plain content list; failure paths
    # (since 1.5) return CallToolResult(isError=True) - the only shape
    # the MCP SDK will actually mark as an error (see
    # dosbox_mcp.connection.to_error_result).
    if isinstance(result, types.CallToolResult):
        assert len(result.content) == 1
        return result.content[0].text
    assert len(result) == 1
    return result[0].text


def _is_error(result) -> bool:
    return isinstance(result, types.CallToolResult) and result.isError


class TestStatus:
    def test_status_works_while_disconnected(self):
        conn = FakeConn(connected=False)
        manager = FakeManager()
        payload = json.loads(_text(_status(conn, manager, "observe")))
        assert payload["connected"] is False
        assert payload["mode"] == "observe"
        assert payload["managed_instance"] == {"running": False, "pid": None}

    def test_status_shows_managed_pid(self):
        payload = json.loads(_text(_status(FakeConn(), FakeManager(running=True),
                                           "full")))
        assert payload["managed_instance"] == {"running": True, "pid": 4242}

    def test_status_reports_bridge_version_and_protocol_ceiling(self):
        # Folded in from the deleted bridge_version tool - the one thing
        # it reported that bridge_status didn't already have: the
        # bridge's own package version, and the highest protocol it
        # implements (distinct from `protocol`, the negotiated version,
        # which is None while disconnected).
        conn = FakeConn(connected=False)
        payload = json.loads(_text(_status(conn, FakeManager(), "full")))
        assert payload["bridge_protocol"] == BRIDGE_PROTOCOL
        assert isinstance(payload["bridge_version"], str) and payload["bridge_version"]
        assert payload["protocol"] is None


class TestConnectDisconnect:
    def test_connect_returns_status_on_success(self):
        conn = FakeConn(connected=False)
        payload = json.loads(_text(_connect(conn, FakeManager(), "full")))
        assert payload["connected"] is True
        assert conn.detached == 1

    def test_connect_reports_precise_failure(self):
        conn = FakeConn(fail_with="Instance found at X but no usable token")
        result = _connect(conn, FakeManager(), "full")
        assert _is_error(result)
        assert "no usable token" in _text(result)


class TestStartStop:
    def test_start_spawns_and_reports(self):
        manager = FakeManager()
        text = _text(_start(FakeConn(), manager))
        assert manager.calls == ["start"]
        assert "0.84-test" in text

    def test_start_error_text_when_already_running(self):
        manager = FakeManager(running=True)
        result = _start(FakeConn(), manager)
        assert _is_error(result)
        assert "already managed" in _text(result)

    def test_start_reports_spawn_failure(self):
        manager = FakeManager()

        def boom(deadline_seconds=30.0):
            raise SpawnError("engine produced no token within 30.0s")
        manager.start = boom
        result = _start(FakeConn(), manager)
        assert _is_error(result)
        assert "no token within" in _text(result)

    def test_stop_stops_and_detaches(self):
        conn = FakeConn()
        manager = FakeManager(running=True)
        manager.calls.append("start")
        text = _text(_stop(conn, manager))
        assert "stop" in manager.calls
        assert conn.detached == 1
        assert "stopped" in text.lower()

    def test_stop_refusal_passes_through(self):
        result = _stop(FakeConn(), FakeManager(running=False))
        assert _is_error(result)
        assert "no managed instance" in _text(result)


class TestLogs:
    def test_logs_framed_as_untrusted(self):
        manager = FakeManager(running=True)
        manager.calls.append("start")
        text = _text(_logs(manager, {}))
        assert "fake engine started" in text
        assert "untrusted" in text.lower()

    def test_logs_error_passes_through(self):
        result = _logs(FakeManager(), {})
        assert _is_error(result)
        assert "no output captured" in _text(result)


class TestSetup:
    def test_setup_writes_safe_keys(self, tmp_path, monkeypatch):
        path = tmp_path / "config.toml"
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(path))
        text = _text(_setup({"port": 9000, "headless": True}))
        assert "saved" in text.lower()
        content = path.read_text(encoding="utf-8")
        assert "port = 9000" in content
        assert "headless = true" in content

    @pytest.mark.parametrize("args", [
        {"binary": "/some/other/binary"},
        {"mode": "full"},
    ])
    def test_setup_rejects_protected_keys(self, args, tmp_path, monkeypatch):
        path = tmp_path / "config.toml"
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(path))
        result = _setup(args)
        assert _is_error(result)
        assert "human-edited" in _text(result)
        assert not path.exists()

    def test_setup_rejects_bad_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(tmp_path / "c.toml"))
        result = _setup({"port": 99999})
        assert _is_error(result)
        assert "out of range" in _text(result)


class TestSwagger:
    def test_digest_counts_and_flags_unknown_routes(self):
        conn = FakeConn()
        conn.spec = {"paths": {
            "/api/v1/status": {},
            "/api/v1/memory/{offset}/{length}": {},
            "/api/v1/teleporter/engage": {},
        }}
        payload = json.loads(_text(_swagger(conn)))
        assert payload["routes"] == 3
        assert "/api/v1/teleporter/engage" in payload["unknown_to_protocol"]
        assert "/api/v1/status" not in payload["unknown_to_protocol"]

    def test_control_debug_and_batch_prefixes_are_known(self):
        # /api/v1/control/shutdown and /api/v1/batch were both real,
        # live false positives against the actual engine's openapi.json
        # before this item (confirmed live); /api/v1/debug/* joined them
        # once 4.1 made openapi.json document the debugger routes. All
        # three stay in the 1.0 baseline (4.2) rather than moving to the
        # 1.1 addition, precisely so this stays true even against an
        # engine that only ever negotiates implicit 1.0.
        conn = FakeConn()
        conn.spec = {"paths": {
            "/api/v1/control/shutdown": {},
            "/api/v1/debug/status": {},
            "/api/v1/debug/breakpoints": {},
            "/api/v1/batch": {},
        }}
        payload = json.loads(_text(_swagger(conn)))
        assert payload["unknown_to_protocol"] == []

    def test_hello_is_unknown_against_a_peer_that_only_negotiated_1_0(self):
        # GET /api/v1/hello is what 4.2 adds - it only exists once both
        # sides speak 1.1. An engine an older bridge (or an older
        # engine's implicit-1.0 fallback) negotiated down to 1.0 with
        # has no business being credited with a route that protocol
        # minor never promised.
        conn = FakeConn(effective_protocol="1.0")
        conn.spec = {"paths": {"/api/v1/hello": {}}}
        payload = json.loads(_text(_swagger(conn)))
        assert payload["unknown_to_protocol"] == ["/api/v1/hello"]

    def test_hello_is_known_against_a_peer_that_negotiated_1_1(self):
        conn = FakeConn(effective_protocol="1.1")
        conn.spec = {"paths": {"/api/v1/hello": {}}}
        payload = json.loads(_text(_swagger(conn)))
        assert payload["unknown_to_protocol"] == []

    def test_wait_prefix_is_known(self):
        # POST /api/v1/wait was a real, live false positive against the
        # actual engine's openapi.json (confirmed live) - missing from
        # the pre-4.2 flat set entirely, not just a version-gating gap.
        conn = FakeConn()
        conn.spec = {"paths": {"/api/v1/wait": {}}}
        payload = json.loads(_text(_swagger(conn)))
        assert payload["unknown_to_protocol"] == []

    def test_falls_back_to_bridge_protocol_when_not_yet_negotiated(self):
        # effective_protocol defaults to None on a fresh FakeConn, same
        # as a real Connection before its first successful attach.
        conn = FakeConn()
        conn.spec = {"paths": {"/api/v1/hello": {}}}
        payload = json.loads(_text(_swagger(conn)))
        assert payload["unknown_to_protocol"] == []


class TestRegistration:
    def _names(self, mode):
        config = Config(base_url="http://127.0.0.1:8386", token=None)
        return build_server(Connection(config), mode=mode).registered_tool_names()

    def test_full_registers_all_bridge_tools(self):
        names = self._names("full")
        for tool in ("bridge_status", "bridge_connect", "bridge_disconnect",
                     "bridge_start", "bridge_stop", "bridge_logs",
                     "bridge_setup", "bridge_swagger"):
            assert tool in names, tool

    def test_observe_gets_read_only_bridge_tools_only(self):
        names = self._names("observe")
        for tool in ("bridge_status", "bridge_logs", "bridge_swagger"):
            assert tool in names, tool
        for tool in ("bridge_start", "bridge_stop", "bridge_setup"):
            assert tool not in names, tool

    def test_bridge_version_and_bridge_help_are_gone(self):
        # bridge_version folded into bridge_status (3.4); bridge_help
        # deleted outright - it re-sent a tool list the client already
        # has, via a one-liner extraction that mangled multi-sentence
        # descriptions.
        for tool in ("bridge_version", "bridge_help"):
            assert tool not in self._names("full"), tool

    def test_interact_includes_lifecycle(self):
        names = self._names("interact")
        for tool in ("bridge_start", "bridge_stop", "bridge_connect",
                     "bridge_setup"):
            assert tool in names, tool

    def test_no_bridge_token_tool_exists(self):
        # Design rule: the token never enters a transcript.
        assert "bridge_token" not in self._names("full")
