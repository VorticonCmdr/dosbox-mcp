# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import logging
import threading

import httpx
import mcp.types as types

from .client import DosboxClient, DosboxError
from .config import Config, default_token_path, read_token
from .protocol import IncompatiblePeer, effective_version

log = logging.getLogger(__name__)

HELLO_PATH = "/api/v1/hello"
INFO_PATH = "/api/v1/dosbox/info"


class Connection:
    """Lazy connector to a running dosbox instance.

    Starts disconnected. On the first tool call (or an explicit
    bridge_connect), reads the token, probes the instance, and
    negotiates the effective protocol version. Reconnects automatically
    if the instance restarts (new token) or comes back after going away.

    `transport` is an httpx transport injection point so tests never
    touch the network.
    """

    def __init__(self, config: Config, transport=None):
        self._config = config
        self._transport = transport
        self._client: DosboxClient | None = None
        self._features: dict = {}
        self._capabilities: dict = {}
        self._engine_info: dict = {}
        self._effective: tuple[int, int] | None = None
        # Identifies the engine process behind the last successful
        # attach (None against an engine that predates 3.6, or before
        # the first attach). Used to tell a genuinely stale token apart
        # from the token now belonging to a different process - see
        # EngineRestarted.
        self._instance_id: str | None = None
        # 3.7 (anyio.to_thread.run_sync) means concurrent tool calls
        # genuinely run in parallel OS threads now, not just nominally
        # concurrent asyncio tasks serialized by a blocking call. Two
        # threads hitting a 401 at once must not each run their own
        # detach()/reconnect independently: the second one would read
        # _instance_id as None (the first already cleared it) and
        # compare it against the reconnected id, raising a spurious
        # EngineRestarted for an engine that never restarted. _lock
        # guards state transitions only - never the request itself, so
        # a slow call still can't stall an unrelated one.
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def config(self) -> Config:
        return self._config

    @property
    def connected(self) -> bool:
        return self._client is not None

    @property
    def base_url(self) -> str:
        return self._config.base_url

    @property
    def features(self) -> dict:
        return self._features

    @property
    def capabilities(self) -> dict:
        """{group: {state, reason, limits}} from an engine new enough to
        send it (1.2+); {} against an older engine, in which case guard()
        falls back to the plain features boolean."""
        return self._capabilities

    @property
    def engine_info(self) -> dict:
        return self._engine_info

    @property
    def effective_protocol(self) -> str | None:
        if self._effective is None:
            return None
        return f"{self._effective[0]}.{self._effective[1]}"

    def status(self) -> dict:
        """Connection state for bridge_status. Reports token presence,
        never the token value (design rule: tokens stay out of
        transcripts)."""
        token = self._config.token or read_token(self._config.token_file)
        return {
            "connected": self.connected,
            "base_url": self.base_url,
            "engine_name": self._engine_info.get("name"),
            "engine_version": self._engine_info.get("version"),
            "instance_id": self._instance_id,
            "protocol": self.effective_protocol,
            "features": dict(self._features),
            "capabilities": dict(self._capabilities),
            "token": "present" if token else "absent",
        }

    def probe_hello(self) -> tuple[bool, dict | None]:
        """Unauthenticated hello probe: (something listens, hello payload
        or None when the engine predates the route)."""
        try:
            with httpx.Client(transport=self._transport, timeout=5.0) as probe:
                resp = probe.get(self._config.base_url.rstrip("/") + HELLO_PATH)
        except httpx.HTTPError:
            return False, None
        if resp.status_code == 200:
            try:
                return True, resp.json()
            except ValueError:
                return True, None
        return True, None

    def _no_token_message(self) -> str:
        looked_in = (
            "looked in DOSBOX_API_TOKEN and "
            f"{self._config.token_file or default_token_path()}"
        )
        listening, hello = self.probe_hello()
        if hello is not None:
            return (
                f"Instance found at {self.base_url} "
                f"(version {hello.get('version', '?')}, "
                f"protocol {hello.get('mcp_protocol', '?')}) "
                f"but no usable token - {looked_in}."
            )
        if listening:
            return (
                f"Something answers at {self.base_url} but no usable "
                f"token - {looked_in}."
            )
        return f"No API token available and nothing answers at {self.base_url} - is dosbox running?"

    def _try_connect(self):
        token = self._config.token or read_token(self._config.token_file)
        if token is None:
            raise NotConnected(self._no_token_message())

        client = DosboxClient(self._config.base_url, token,
                              transport=self._transport)
        try:
            info = client.get(INFO_PATH)
        except httpx.TransportError as e:
            raise NotConnected(
                f"Cannot reach dosbox at {self._config.base_url}: {e}"
            ) from None
        except DosboxError as e:
            if e.status == 401 and self._config.token:
                # A remembered token (e.g. from a stopped spawned
                # instance) went stale: drop it and retry once via the
                # normal token lookup.
                self._config.token = None
                return self._try_connect()
            raise NotConnected(
                f"attach to {self._config.base_url} failed: {e.message}"
            ) from e

        try:
            effective = effective_version(info, pin=self._config.protocol)
        except (IncompatiblePeer, ValueError) as e:
            raise NotConnected(str(e)) from e

        self._client = client
        self._engine_info = info
        self._features = info.get("features", {})
        self._capabilities = info.get("capabilities", {})
        self._effective = effective
        self._instance_id = info.get("instance_id")
        self._generation += 1
        log.info("attached to %s (%s, protocol %s)", self._config.base_url,
                 info.get("version", "?"), self.effective_protocol)

    def ensure_connected(self):
        if self._client is not None:
            return
        with self._lock:
            # Double-checked: another thread may have connected while
            # this one was waiting for the lock.
            if self._client is None:
                self._try_connect()

    def detach(self):
        self._client = None
        self._features = {}
        self._capabilities = {}
        self._engine_info = {}
        self._effective = None
        self._instance_id = None
        log.info("detached from dosbox")

    def call(self, method, path, **kwargs):
        """Execute an HTTP call, reconnecting once on failure."""
        self.ensure_connected()
        old_id = self._instance_id
        my_generation = self._generation
        fn = getattr(self._client, method)
        try:
            return fn(path, **kwargs)
        except httpx.TransportError as e:
            self.detach()
            raise NotConnected(
                f"dosbox went away or stopped responding during the call: {e}"
            ) from e
        except DosboxError as e:
            if e.status == 401:
                with self._lock:
                    # If another thread already reconnected on our
                    # behalf (it hit the same 401 and got the lock
                    # first), _generation has moved past what we
                    # observed before the call - reuse its result
                    # instead of detaching and reattaching again,
                    # which is what produced a spurious EngineRestarted
                    # under concurrent load: the second thread would
                    # otherwise read _instance_id as None (the first
                    # thread's detach() already cleared it) and compare
                    # that against the freshly reconnected id.
                    if self._generation == my_generation:
                        self.detach()
                        self._try_connect()
                    new_id = self._instance_id
                # Only "both None" is ambiguous (neither side supports
                # instance_id, so we genuinely cannot tell). Any other
                # change - including one side None and the other set,
                # which can only happen if the responding binary
                # changed - is a restart signal: the 401 was not a
                # stale token on the same process, it was a different
                # process behind the same URL. Replaying a mutating
                # request into a fresh guest session is worse than
                # surfacing the restart, so don't retry the call at all.
                if (old_id is not None or new_id is not None) \
                        and old_id != new_id:
                    raise EngineRestarted(old_id, new_id) from e
                fn = getattr(self._client, method)
                return fn(path, **kwargs)
            raise

    def get(self, path, **kwargs):
        return self.call("get", path, **kwargs)

    def post(self, path, **kwargs):
        return self.call("post", path, **kwargs)

    def post_text(self, path, text, **kwargs):
        return self.call("post_text", path, text=text, **kwargs)

    def put(self, path, **kwargs):
        return self.call("put", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.call("delete", path, **kwargs)


class NotConnected(Exception):
    pass


class EngineRestarted(Exception):
    """Raised instead of replaying a request when a mid-call 401 turns
    out to be a different engine process (a restart), not a stale
    token on the same one. Everything the old process held - freeze
    registry, loaded script, breakpoints, guest state - is gone; the
    caller must not assume the request it was making ran.

    Either id can be None: that means the engine on that side of the
    restart predates 3.6 and never sent instance_id at all - still a
    provable restart, since a running process's support for the field
    cannot change without the binary itself changing."""

    def __init__(self, old_instance_id: str | None, new_instance_id: str | None):
        super().__init__(
            f"engine restarted (was {old_instance_id or 'unknown, pre-3.6 engine'}, "
            f"now {new_instance_id or 'unknown, pre-3.6 engine'}) - "
            "the request was not retried"
        )
        self.old_instance_id = old_instance_id
        self.new_instance_id = new_instance_id


def _hint_for(code: str | None, retryable: bool) -> str | None:
    """A short, actionable suggestion for the agent - only for cases
    where the message text alone does not already make the remedy
    obvious (a retryable engine message like bridge_timeout's already
    says "the emulator may be paused" itself)."""
    if retryable:
        return "This may be transient - the same call can succeed on retry."
    if code == "unauthorized":
        return "The token may be stale; bridge_connect will re-attach with a fresh one."
    if code == "not_connected":
        return "Call bridge_status to check reachability, or bridge_start to spawn an instance."
    return None


def to_error_result(message: str, *, tool: str | None = None,
                    route: str | None = None, code: str | None = None,
                    retryable: bool = False,
                    retry_after: str | None = None) -> types.CallToolResult:
    """Build a CallToolResult(isError=True). This is the only way to get
    isError set at all: the MCP SDK's call_tool wrapper hardcodes
    isError=False for any handler return value that is not already a
    CallToolResult (see mcp.server.lowlevel.server.Server.call_tool).
    That passthrough needs mcp>=1.19 (pyproject.toml's floor) - below
    it, call_tool has no CallToolResult special case, and pydantic's
    own BaseModel.__iter__ makes one look like an iterable of
    (field_name, value) tuples instead, which the wrapper then tries to
    rebuild into content blocks and fails with a wall of validation
    errors instead of the clean message built here."""
    bits = [b for b in (tool, route) if b]
    prefix = f"[{' '.join(bits)}] " if bits else ""
    text = f"{prefix}{message}"
    if retry_after:
        # More specific than the generic retryable hint below, so it
        # replaces rather than joins it: "may be transient" is a much
        # weaker signal than an exact wait time from the server itself.
        text = f"{text} Retry after {retry_after}s."
    else:
        hint = _hint_for(code, retryable)
        if hint:
            text = f"{text} {hint}"
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        isError=True,
    )


def guard(connection: Connection, handler, feature=None, tool_name=None):
    """Wrap a tool handler with connection and feature checks. Failures
    return CallToolResult(isError=True): an agent must be able to tell
    a refusal from a success without parsing the text."""
    def guarded(args):
        try:
            connection.ensure_connected()
            if feature:
                cap = connection.capabilities.get(feature)
                if cap is not None:
                    # 1.2+ engine: the capability's own state is
                    # authoritative. 'degraded' still allows the call
                    # through (the group is at least partially usable,
                    # matching what features[feature] would already say);
                    # only 'off' refuses.
                    if cap.get("state") == "off":
                        reason = cap.get("reason") or "not enabled in the running instance"
                        return to_error_result(
                            f"Feature '{feature}' is off: {reason}",
                            tool=tool_name, code="feature_disabled",
                        )
                elif not connection.features.get(feature):
                    return to_error_result(
                        f"Feature '{feature}' is not enabled in the running instance.",
                        tool=tool_name, code="feature_disabled",
                    )
            return handler(args)
        except NotConnected as e:
            return to_error_result(str(e), tool=tool_name, code="not_connected")
        except EngineRestarted as e:
            return to_error_result(
                f"{e} - guest state (breakpoints, freezes, loaded "
                "script, recording) is gone; call bridge_status or "
                "dosbox_status before continuing.",
                tool=tool_name, code="engine_restarted",
            )
        except DosboxError as e:
            return to_error_result(e.message, tool=tool_name, route=e.route,
                                   code=e.code, retryable=e.retryable,
                                   retry_after=e.retry_after)
    return guarded
