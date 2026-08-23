# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import logging

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
        log.info("attached to %s (%s, protocol %s)", self._config.base_url,
                 info.get("version", "?"), self.effective_protocol)

    def ensure_connected(self):
        if self._client is None:
            self._try_connect()

    def detach(self):
        self._client = None
        self._features = {}
        self._capabilities = {}
        self._engine_info = {}
        self._effective = None
        log.info("detached from dosbox")

    def call(self, method, path, **kwargs):
        """Execute an HTTP call, reconnecting once on failure."""
        self.ensure_connected()
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
                self.detach()
                self._try_connect()
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
                    retryable: bool = False) -> types.CallToolResult:
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
        except DosboxError as e:
            return to_error_result(e.message, tool=tool_name, route=e.route,
                                   code=e.code, retryable=e.retryable)
    return guarded
