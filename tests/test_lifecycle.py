# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import os
import stat
import textwrap
import time

import pytest

from dosbox_mcp.config import Config
from dosbox_mcp.lifecycle import InstanceManager, LifecycleError, RingLog, SpawnError

FAKE_TOKEN = "fake-token-123"


def _write_script(path, body):
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body),
                    encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def fake_engine(tmp_path):
    """A stand-in engine: writes its token file where the launcher would,
    prints a line, then waits to be terminated."""
    return _write_script(tmp_path / "fake-engine", f"""
        import os, pathlib, signal, sys, time
        cfg = (pathlib.Path(os.environ["XDG_CONFIG_HOME"])
               / "dosbox-automation" / "webserver")
        cfg.mkdir(parents=True)
        (cfg / "api_token").write_text("{FAKE_TOKEN}")
        print("fake engine started", flush=True)
        print("MOUNT_POLICY: something", file=sys.stderr, flush=True)
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        while True:
            time.sleep(0.05)
    """)


@pytest.fixture
def silent_engine(tmp_path):
    """Never writes a token: the spawn must time out and clean up."""
    return _write_script(tmp_path / "silent-engine", """
        import signal, sys, time
        print("starting but never ready", file=sys.stderr, flush=True)
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        while True:
            time.sleep(0.05)
    """)


def _config(binary=None, port=8386):
    return Config(base_url=f"http://127.0.0.1:{port}", binary=binary, port=port)


def _ok_attach(calls):
    def attach(base_url, token):
        calls.append((base_url, token))
        return {"version": "0.84-test", "features": {}}
    return attach


class TestSpawn:
    def test_start_requires_configured_binary(self):
        manager = InstanceManager(_config(binary=None), attach=_ok_attach([]))
        with pytest.raises(LifecycleError, match="binary"):
            manager.start()

    def test_start_spawns_and_attaches_with_child_token(self, fake_engine):
        calls = []
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach(calls))
        try:
            info = manager.start(token_deadline=5.0)
            assert info["version"] == "0.84-test"
            assert calls == [("http://127.0.0.1:8386", FAKE_TOKEN)]
            assert manager.running
            assert manager.pid is not None
        finally:
            if manager.running:
                manager.stop()
        assert not manager.running

    def test_token_never_in_argv(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        # The exact-argv assertion is the strongest form: the command is
        # the binary alone, so no token (and no anything) can ride along.
        assert manager.build_argv() == [str(fake_engine)]

    def test_double_start_refused(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        try:
            manager.start(token_deadline=5.0)
            with pytest.raises(LifecycleError, match="already"):
                manager.start(token_deadline=5.0)
        finally:
            manager.stop()

    def test_attach_failure_stops_child(self, fake_engine):
        def bad_attach(base_url, token):
            raise RuntimeError("401: nope")

        manager = InstanceManager(_config(binary=fake_engine), attach=bad_attach)
        with pytest.raises(SpawnError, match="401"):
            manager.start(token_deadline=5.0)
        assert not manager.running

    def test_token_timeout_kills_child_and_reports_stderr(self, silent_engine):
        manager = InstanceManager(_config(binary=silent_engine),
                                  attach=_ok_attach([]))
        with pytest.raises(SpawnError, match="never ready"):
            manager.start(token_deadline=0.5)
        assert not manager.running

    def test_child_that_dies_immediately_reports_spawn_error(self, tmp_path):
        dead = _write_script(tmp_path / "dead-engine", """
            import sys
            print("cannot bind port", file=sys.stderr, flush=True)
            sys.exit(1)
        """)
        manager = InstanceManager(_config(binary=dead), attach=_ok_attach([]))
        with pytest.raises(SpawnError, match="cannot bind port"):
            manager.start(token_deadline=5.0)
        assert not manager.running


class TestStop:
    def test_stop_without_managed_child_refused(self):
        manager = InstanceManager(_config(), attach=_ok_attach([]))
        with pytest.raises(LifecycleError, match="no managed instance"):
            manager.stop()

    def test_stop_terminates_child(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        manager.start(token_deadline=5.0)
        pid = manager.pid
        manager.stop()
        assert not manager.running
        # The child must actually be gone, not just forgotten.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"child {pid} still alive after stop()")

    def test_stop_twice_refused(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        manager.start(token_deadline=5.0)
        manager.stop()
        with pytest.raises(LifecycleError):
            manager.stop()


class TestLogs:
    def test_child_output_captured(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        try:
            manager.start(token_deadline=5.0)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                lines = manager.logs()
                if any("fake engine started" in line for line in lines) and \
                   any("MOUNT_POLICY" in line for line in lines):
                    break
                time.sleep(0.05)
            else:
                pytest.fail(f"expected output not captured, got: {manager.logs()}")
        finally:
            manager.stop()

    def test_logs_without_spawn_refused(self):
        manager = InstanceManager(_config(), attach=_ok_attach([]))
        with pytest.raises(LifecycleError):
            manager.logs()


class TestRingLog:
    def test_keeps_last_lines(self):
        ring = RingLog(max_lines=3, max_line_bytes=100, max_total_bytes=1000)
        for i in range(5):
            ring.append(f"line {i}")
        assert ring.tail() == ["line 2", "line 3", "line 4"]

    def test_long_line_truncated(self):
        ring = RingLog(max_lines=10, max_line_bytes=10, max_total_bytes=1000)
        ring.append("x" * 50)
        (line,) = ring.tail()
        assert len(line.encode("utf-8")) <= 10 + len("...")

    def test_total_byte_cap_evicts_old_lines(self):
        ring = RingLog(max_lines=1000, max_line_bytes=100, max_total_bytes=50)
        for i in range(20):
            ring.append(f"line number {i}")
        assert sum(len(line.encode("utf-8")) for line in ring.tail()) <= 50

    def test_tail_n(self):
        ring = RingLog(max_lines=10, max_line_bytes=100, max_total_bytes=1000)
        for i in range(5):
            ring.append(f"line {i}")
        assert ring.tail(2) == ["line 3", "line 4"]
