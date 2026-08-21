# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

"""Spawn and manage a dosbox instance owned by this bridge.

Security posture (design doc, self-audit 2026-07-17):
- The binary path comes from the human-authored config only; nothing
  here accepts a path from tool arguments.
- Spawn success is an authenticated attach with a token this manager
  generates itself and hands to the child via DOSBOX_API_TOKEN - a port
  squatter cannot pass that check.
- The child is held as a Popen handle, never a raw PID, so stop() is
  immune to PID reuse.
- The token never appears on a command line; it travels via the child's
  environment only, never a file or an argv element.
"""

import logging
import os
import secrets
import shutil
import subprocess  # nosec B404 - spawning the engine is this module's purpose
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

_TRUNCATION_MARK = "..."
_STOP_GRACE_SECONDS = 5.0
_POLL_INTERVAL = 0.05


class LifecycleError(Exception):
    pass


class SpawnError(LifecycleError):
    pass


class RingLog:
    """Bounded line buffer for child output: capped per line, in line
    count, and in total bytes, so a chatty guest cannot grow memory."""

    def __init__(self, max_lines: int = 200, max_line_bytes: int = 2048,
                 max_total_bytes: int = 131072):
        if max_lines < 1 or max_line_bytes < 1 or max_total_bytes < 1:
            raise ValueError("RingLog caps must be positive")
        self._max_line_bytes = max_line_bytes
        self._max_total_bytes = max_total_bytes
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._total = 0
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        data = line.encode("utf-8")
        if len(data) > self._max_line_bytes:
            line = (data[: self._max_line_bytes].decode("utf-8", errors="ignore")
                    + _TRUNCATION_MARK)
        with self._lock:
            if len(self._lines) == self._lines.maxlen:
                self._total -= len(self._lines[0].encode("utf-8"))
            self._lines.append(line)
            self._total += len(line.encode("utf-8"))
            while self._total > self._max_total_bytes and len(self._lines) > 1:
                self._total -= len(self._lines.popleft().encode("utf-8"))

    def tail(self, n: int | None = None) -> list[str]:
        with self._lock:
            lines = list(self._lines)
        return lines if n is None else lines[-n:]


class InstanceManager:
    """Lifecycle of at most one engine instance spawned by this bridge.

    `attach` is injected (a callable (base_url, token) -> info dict that
    performs the authenticated probe) so the process handling stays
    testable without a network.
    """

    def __init__(self, config: Config, attach):
        self._config = config
        self._attach = attach
        self._proc: subprocess.Popen | None = None
        self._state_dir: Path | None = None
        self._ring: RingLog | None = None
        self._readers: list[threading.Thread] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def build_argv(self) -> list[str]:
        if self._config.binary is None:
            raise LifecycleError(
                "no binary configured - set `binary` in the config file "
                "(human-edited; it is deliberately not settable via tools)"
            )
        return [
            str(self._config.binary),
            # Ignore whatever primary/local config the operator's own
            # dosbox-automation setup has: a spawned instance is fully
            # specified by the --set overrides below, never by config-dir
            # discovery (get_or_create_config_dir() in src/misc/cross.cpp
            # resolves differently per platform - notably on macOS it
            # ignores XDG_CONFIG_HOME entirely - so this manager cannot
            # rely on it to find a conf file it wrote).
            "--noprimaryconf",
            "--nolocalconf",
            "--set", "webserver_enabled=true",
            "--set", f"webserver_port={self._config.port}",
        ]

    def start(self, deadline_seconds: float = 30.0) -> dict:
        """Spawn the configured binary and complete an authenticated
        attach with a token generated up front and handed to the child
        via DOSBOX_API_TOKEN. Returns the /info payload."""
        with self._lock:
            if self.running:
                raise LifecycleError(
                    f"an instance is already managed (pid {self.pid}) - "
                    "stop it before starting another"
                )
            argv = self.build_argv()

            self._state_dir = Path(tempfile.mkdtemp(prefix="dosbox-mcp-"))
            token = secrets.token_hex(32)
            env = dict(os.environ)
            # HOME redirects the engine's config dir on POSIX,
            # XDG_CONFIG_HOME does it on Windows too
            # (get_or_create_config_dir() in src/misc/cross.cpp). Neither
            # is actually read for configuration given --noprimaryconf
            # --nolocalconf above; both are still set so a spawned
            # instance never touches the operator's real home directory
            # for the other paths it resolves outside config (mapper
            # file, save states).
            env["HOME"] = str(self._state_dir)
            env["XDG_CONFIG_HOME"] = str(self._state_dir)
            # A launcher-supplied token via env var (webserver.cpp
            # "channel A"): nothing here needs to read a file back from
            # the child or scrape its log output for one.
            env["DOSBOX_API_TOKEN"] = token
            if self._config.headless:
                # The engine has no dedicated headless flag; SDL's dummy
                # video driver is the portable way to run windowless.
                # Verified against a real build: SDL3's "offscreen"
                # driver, which dosbox-automation's own integration
                # harness uses on Linux, aborts on macOS
                # (SDL_CreateRenderer fails to get a GL context with no
                # real display) - "dummy" is the one that actually
                # starts here.
                env["SDL_VIDEODRIVER"] = "dummy"

            self._ring = RingLog()
            try:
                self._proc = subprocess.Popen(  # nosec B603 - argv list, binary from human-authored config only
                    argv,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as e:
                self._cleanup()
                raise SpawnError(f"cannot spawn {argv[0]}: {e}") from e
            self._readers = [
                self._start_reader(self._proc.stdout),
                self._start_reader(self._proc.stderr),
            ]

        try:
            return self._attach_with_retry(token, deadline_seconds)
        except SpawnError:
            raise
        except Exception as e:
            self._terminate_child()
            self._cleanup()
            raise SpawnError(
                f"spawned pid did not pass the authenticated attach: {e}. "
                f"Engine output:\n{self._stderr_tail()}"
            ) from e

    def stop(self) -> None:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise LifecycleError(
                    "no managed instance to stop - this bridge only stops "
                    "what it spawned, never an instance it merely attached to"
                )
            self._terminate_child()
            self._cleanup()

    def logs(self, n: int | None = None) -> list[str]:
        if self._ring is None:
            raise LifecycleError(
                "no output captured - logs cover only an instance this "
                "bridge spawned"
            )
        return self._ring.tail(n)

    def _start_reader(self, pipe) -> threading.Thread:
        def drain():
            for line in pipe:
                self._ring.append(line.rstrip("\n"))
        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
        return thread

    def _attach_with_retry(self, token: str, deadline_seconds: float) -> dict:
        """Retry the authenticated attach until it succeeds or
        `deadline_seconds` elapses: the child needs a moment after
        spawning before its listener is up, and there is no separate
        readiness signal to wait on."""
        deadline = time.monotonic() + deadline_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                code = self._proc.returncode
                self._cleanup()
                raise SpawnError(
                    f"engine exited during startup (code {code}). "
                    f"Engine output:\n{self._stderr_tail()}"
                )
            try:
                return self._attach(self._config.base_url, token)
            except Exception as e:  # noqa: BLE001
                # Broad on purpose: `attach` raises NotConnected for both
                # "not listening yet" (expected, retry) and "answered but
                # rejected the token" (should not happen - we generated
                # it - but is not worth a special case here). Either way
                # the deadline below is the actual bound.
                last_error = e
                time.sleep(_POLL_INTERVAL)
        self._terminate_child()
        self._cleanup()
        raise SpawnError(
            f"engine did not accept the authenticated attach within "
            f"{deadline_seconds:.1f}s ({last_error}). "
            f"Engine output:\n{self._stderr_tail()}"
        )

    def _stderr_tail(self) -> str:
        # Give the reader threads a beat to drain the pipes after exit.
        for thread in self._readers:
            thread.join(timeout=1.0)
        return "\n".join(self._ring.tail(20)) if self._ring else ""

    def _terminate_child(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            log.warning("child %d ignored terminate, killing", proc.pid)
            proc.kill()
            proc.wait()

    def _cleanup(self) -> None:
        for thread in self._readers:
            thread.join(timeout=1.0)
        self._readers = []
        if self._proc is not None:
            for pipe in (self._proc.stdout, self._proc.stderr):
                if pipe is not None:
                    pipe.close()
        self._proc = None
        if self._state_dir is not None:
            shutil.rmtree(self._state_dir, ignore_errors=True)
            self._state_dir = None
