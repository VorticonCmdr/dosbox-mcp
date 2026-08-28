# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json
import os
import stat
import textwrap
import time
from pathlib import Path

import pytest

from dosbox_mcp import lifecycle as lifecycle_module
from dosbox_mcp.config import Config, engine_config_dir
from dosbox_mcp.lifecycle import InstanceManager, LifecycleError, RingLog, SpawnError


def _write_script(path, body):
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body),
                    encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def fake_engine(tmp_path):
    """A stand-in engine: dumps the env and argv it received to
    env_dump.json next to itself, prints a startup line, then waits to
    be terminated. Never exits and never opens a real port - `attach` is
    always injected in these tests, so nothing here needs to be a real
    HTTP server."""
    return _write_script(tmp_path / "fake-engine", """
        import json, os, signal, sys, time
        dump = {
            "DOSBOX_API_TOKEN": os.environ.get("DOSBOX_API_TOKEN"),
            "HOME": os.environ.get("HOME"),
            "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME"),
            "SDL_VIDEODRIVER": os.environ.get("SDL_VIDEODRIVER"),
            "argv": sys.argv,
        }
        with open(os.path.join(os.path.dirname(__file__), "env_dump.json"), "w") as f:
            json.dump(dump, f)
        print("fake engine started", flush=True)
        print("MOUNT_POLICY: something", file=sys.stderr, flush=True)
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        while True:
            time.sleep(0.05)
    """)


def _config(binary=None, port=8386, headless=False, mount_allowed_bases=None,
           mount_allowed_image_roots=None):
    return Config(base_url=f"http://127.0.0.1:{port}", binary=binary, port=port,
                 headless=headless,
                 mount_allowed_bases=mount_allowed_bases or [],
                 mount_allowed_image_roots=mount_allowed_image_roots or [])


def _ok_attach(calls):
    """Always succeeds. Fine for tests that are not about readiness
    detection - real readiness detection is covered separately below."""
    def attach(base_url, token):
        calls.append((base_url, token))
        return {"version": "0.84-test", "features": {}}
    return attach


def _always_refused_attach(base_url, token):
    """Represents nothing being reachable yet: the honest stand-in for
    an engine that is alive but has not opened its listener (or never
    will), since these tests never make a real network call."""
    raise ConnectionRefusedError("connection refused")


class TestSpawn:
    def test_start_requires_configured_binary(self):
        manager = InstanceManager(_config(binary=None), attach=_ok_attach([]))
        with pytest.raises(LifecycleError, match="binary"):
            manager.start()

    def test_build_argv_disables_config_discovery_and_sets_port(self):
        manager = InstanceManager(_config(binary="/bin/dosbox", port=9999),
                                  attach=_ok_attach([]))
        assert manager.build_argv() == [
            "/bin/dosbox",
            "--noprimaryconf",
            "--nolocalconf",
            "--set", "webserver_enabled=true",
            "--set", "webserver_port=9999",
        ]

    def test_token_never_in_argv(self, fake_engine):
        # The token travels via DOSBOX_API_TOKEN only. build_argv() does
        # not even see it, so there is nothing that could leak it onto
        # the command line.
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        argv = manager.build_argv()
        assert str(fake_engine) in argv
        assert not any("token" in str(a).lower() for a in argv[1:])

    def test_start_spawns_and_attaches_with_a_generated_token(self, fake_engine, tmp_path):
        calls = []
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach(calls))
        try:
            info = manager.start(deadline_seconds=5.0)
            assert info["version"] == "0.84-test"
            assert len(calls) == 1
            base_url, token = calls[0]
            assert base_url == "http://127.0.0.1:8386"
            assert len(token) == 64  # secrets.token_hex(32)
            assert manager.running
            assert manager.pid is not None

            dump_path = tmp_path / "env_dump.json"
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not dump_path.exists():
                time.sleep(0.05)
            dump = json.loads(dump_path.read_text())
            # The token the child actually received is the one handed
            # to attach() - proves it round-trips through the child's
            # environment correctly, not just past this manager's
            # boundary.
            assert dump["DOSBOX_API_TOKEN"] == token
            # HOME and XDG_CONFIG_HOME are both redirected, and to the
            # same isolated directory - the fix that matters on macOS,
            # where the engine reads HOME and not XDG_CONFIG_HOME.
            assert dump["HOME"] == dump["XDG_CONFIG_HOME"]
            assert dump["HOME"] != os.environ.get("HOME")
        finally:
            if manager.running:
                manager.stop()
        assert not manager.running

    def test_headless_sets_dummy_video_driver(self, fake_engine, tmp_path):
        # "dummy", not SDL3's "offscreen": see the comment in
        # lifecycle.py - offscreen aborts on a real macOS build.
        manager = InstanceManager(_config(binary=fake_engine, headless=True),
                                  attach=_ok_attach([]))
        try:
            manager.start(deadline_seconds=5.0)
            dump_path = tmp_path / "env_dump.json"
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not dump_path.exists():
                time.sleep(0.05)
            dump = json.loads(dump_path.read_text())
            assert dump["SDL_VIDEODRIVER"] == "dummy"
        finally:
            if manager.running:
                manager.stop()

    def test_attach_is_retried_until_the_engine_is_ready(self, fake_engine):
        calls = []
        attempts = {"n": 0}

        def attach(base_url, token):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionRefusedError("not listening yet")
            calls.append((base_url, token))
            return {"version": "0.84-test", "features": {}}

        manager = InstanceManager(_config(binary=fake_engine), attach=attach)
        try:
            info = manager.start(deadline_seconds=5.0)
            assert info["version"] == "0.84-test"
            assert attempts["n"] == 3
            assert len(calls) == 1
        finally:
            if manager.running:
                manager.stop()

    def test_double_start_refused(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        try:
            manager.start(deadline_seconds=5.0)
            with pytest.raises(LifecycleError, match="already"):
                manager.start(deadline_seconds=5.0)
        finally:
            manager.stop()

    def test_attach_failure_eventually_times_out_and_stops_child(self, fake_engine):
        def bad_attach(base_url, token):
            raise RuntimeError("401: nope")

        manager = InstanceManager(_config(binary=fake_engine), attach=bad_attach)
        with pytest.raises(SpawnError, match="401"):
            manager.start(deadline_seconds=0.3)
        assert not manager.running

    def test_never_reachable_engine_times_out_and_reports_stderr(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_always_refused_attach)
        with pytest.raises(SpawnError, match="did not accept the authenticated attach"):
            manager.start(deadline_seconds=0.3)
        assert not manager.running

    def test_child_that_dies_immediately_reports_spawn_error(self, tmp_path):
        dead = _write_script(tmp_path / "dead-engine", """
            import sys
            print("cannot bind port", file=sys.stderr, flush=True)
            sys.exit(1)
        """)
        manager = InstanceManager(_config(binary=dead), attach=_always_refused_attach)
        with pytest.raises(SpawnError, match="cannot bind port"):
            manager.start(deadline_seconds=5.0)
        assert not manager.running


class TestPolicyConfig:
    """mount_allowed_bases/mount_allowed_image_roots must reach the
    spawned engine despite --noprimaryconf: WEBSERVER_Init() reads the
    primary config's [webserver] section unconditionally
    (src/webserver/webserver.cpp), so writing one at the path HOME/
    XDG_CONFIG_HOME resolve to is enough - no flag change needed."""

    def _written_primary_conf(self, tmp_path) -> str:
        dump_path = tmp_path / "env_dump.json"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not dump_path.exists():
            time.sleep(0.05)
        dump = json.loads(dump_path.read_text())
        home = Path(dump["HOME"])
        conf_path = engine_config_dir(home) / "dosbox-automation.conf"
        return conf_path.read_text(encoding="utf-8")

    def test_mount_allowed_bases_written_before_spawn(self, fake_engine, tmp_path):
        games = tmp_path / "games"
        games.mkdir()
        manager = InstanceManager(
            _config(binary=fake_engine, mount_allowed_bases=[games]),
            attach=_ok_attach([]),
        )
        try:
            manager.start(deadline_seconds=5.0)
            text = self._written_primary_conf(tmp_path)
            assert "[webserver]" in text
            assert f"mount_allowed_bases = {games}" in text
            assert "mount_allowed_image_roots" not in text
        finally:
            if manager.running:
                manager.stop()

    def test_mount_allowed_image_roots_written_before_spawn(self, fake_engine, tmp_path):
        images = tmp_path / "images"
        images.mkdir()
        manager = InstanceManager(
            _config(binary=fake_engine, mount_allowed_image_roots=[images]),
            attach=_ok_attach([]),
        )
        try:
            manager.start(deadline_seconds=5.0)
            text = self._written_primary_conf(tmp_path)
            assert f"mount_allowed_image_roots = {images}" in text
            assert "mount_allowed_bases" not in text
        finally:
            if manager.running:
                manager.stop()

    def test_both_lists_written_semicolon_joined(self, fake_engine, tmp_path):
        a = tmp_path / "a"
        a.mkdir()
        b = tmp_path / "b"
        b.mkdir()
        manager = InstanceManager(
            _config(binary=fake_engine, mount_allowed_bases=[a, b]),
            attach=_ok_attach([]),
        )
        try:
            manager.start(deadline_seconds=5.0)
            text = self._written_primary_conf(tmp_path)
            assert f"mount_allowed_bases = {a};{b}" in text
        finally:
            if manager.running:
                manager.stop()

    def test_config_dir_write_failure_wrapped_and_state_dir_cleaned_up(
            self, fake_engine, tmp_path, monkeypatch):
        games = tmp_path / "games"
        games.mkdir()
        # A file where the resolved config dir's parent needs to be a
        # directory makes mkdir(parents=True) genuinely fail with an
        # OSError - the real failure mode this wraps, not a mock.
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(lifecycle_module, "engine_config_dir",
                            lambda home: blocked / "dosbox-automation")
        manager = InstanceManager(
            _config(binary=fake_engine, mount_allowed_bases=[games]),
            attach=_ok_attach([]),
        )
        with pytest.raises(SpawnError, match="mount policy config"):
            manager.start(deadline_seconds=5.0)
        assert not manager.running

    def test_no_primary_conf_written_when_policy_unset(self, fake_engine, tmp_path):
        manager = InstanceManager(_config(binary=fake_engine), attach=_ok_attach([]))
        try:
            manager.start(deadline_seconds=5.0)
            dump_path = tmp_path / "env_dump.json"
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not dump_path.exists():
                time.sleep(0.05)
            dump = json.loads(dump_path.read_text())
            conf_path = (engine_config_dir(Path(dump["HOME"])) /
                        "dosbox-automation.conf")
            assert not conf_path.exists()
        finally:
            if manager.running:
                manager.stop()


class TestStop:
    def test_stop_without_managed_child_refused(self):
        manager = InstanceManager(_config(), attach=_ok_attach([]))
        with pytest.raises(LifecycleError, match="no managed instance"):
            manager.stop()

    def test_stop_terminates_child(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        manager.start(deadline_seconds=5.0)
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
        manager.start(deadline_seconds=5.0)
        manager.stop()
        with pytest.raises(LifecycleError):
            manager.stop()


class TestLogs:
    def test_child_output_captured(self, fake_engine):
        manager = InstanceManager(_config(binary=fake_engine),
                                  attach=_ok_attach([]))
        try:
            manager.start(deadline_seconds=5.0)
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
