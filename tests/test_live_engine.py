# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

"""Live-engine smoke tests: spawn a real dosbox-automation binary and
talk to it over the real HTTP API, instead of a FakeClient.

Every other test in this suite exercises request-shape correctness
against a stand-in transport. These prove the seam between the bridge
and a real engine actually works: InstanceManager can spawn it,
Connection can authenticate against it, a real tool round-trips through
real dispatch, and stop() actually terminates the process.

Skipped unless DOSBOX_BIN points at a dosbox-automation binary (same
convention as the engine repo's own tests/run-e2e.py) - CI here has no
way to build or fetch one, see docs/mcp-plan.md item 4.3.
"""

import os
import socket
from pathlib import Path

import pytest

from dosbox_mcp.config import Config
from dosbox_mcp.connection import Connection
from dosbox_mcp.lifecycle import InstanceManager

pytestmark = pytest.mark.skipif(
    not os.environ.get("DOSBOX_BIN"),
    reason="live engine tests need DOSBOX_BIN set to a dosbox-automation binary",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_attach(conn):
    """Same authenticated spawn-identity check server.py wires into
    bridge_start - a live fixture should prove the real path, not a
    simplified stand-in for it."""
    def attach(base_url, token):
        conn.detach()
        conn.config.token = token
        conn.ensure_connected()
        return conn.engine_info
    return attach


def _live_config(port: int) -> Config:
    return Config(
        base_url=f"http://127.0.0.1:{port}",
        binary=Path(os.environ["DOSBOX_BIN"]),
        port=port,
        headless=True,
    )


@pytest.fixture
def live():
    """Spawns an isolated dosbox-automation instance via the same
    InstanceManager bridge_start uses, and returns (conn, manager) both
    already attached.

    Headless via lifecycle.py's own SDL_VIDEODRIVER=dummy - the engine
    has no dedicated --headless flag (confirmed against both repos).
    Individual tests may call manager.stop() themselves (e.g. to assert
    on teardown); the fixture only stops what is still running after.
    """
    config = _live_config(_free_port())
    conn = Connection(config)
    manager = InstanceManager(config, attach=_make_attach(conn))
    manager.start(deadline_seconds=30.0)
    yield conn, manager
    if manager.running:
        manager.stop()
    conn.detach()


class TestLiveEngine:
    def test_connect_authenticates_against_the_real_engine(self, live):
        conn, _manager = live
        status = conn.status()
        assert status["connected"] is True
        assert status["token"] == "present"
        assert status["engine_name"]

    def test_a_real_read_tool_round_trips_through_real_dispatch(self, live):
        conn, _manager = live
        state = conn.get("/api/v1/cpu/state")
        assert "registers" in state
        assert "eax" in state["registers"]

    def test_stop_actually_terminates_the_spawned_process(self, live):
        _conn, manager = live
        assert manager.running is True
        pid = manager.pid
        manager.stop()
        assert manager.running is False
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
