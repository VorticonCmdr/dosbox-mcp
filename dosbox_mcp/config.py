# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import ipaddress
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import tomlkit
from platformdirs import user_config_dir

_LOOPBACK_NAMES = {"localhost"}

MODES = ("observe", "interact", "full")

# Protocol pins are "major.minor"; the patch grade is spec-document-only
# and never appears on the wire (design doc, protocol levels).
_PROTOCOL_RE = re.compile(r"^\d+\.\d+$")

DEFAULT_PORT = 8386


def validate_base_url(url: str) -> str:
    """Accept only http(s) URLs pointing at a loopback address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"scheme must be http or https, got {parsed.scheme!r}")
    host = parsed.hostname
    if host is None:
        raise ValueError(f"no host in URL {url!r}")
    if host in _LOOPBACK_NAMES:
        return url
    try:
        if ipaddress.ip_address(host).is_loopback:
            return url
    except ValueError:
        pass
    raise ValueError(f"host {host!r} is not a loopback address")


def default_config_path() -> Path:
    """The bridge's own config file. DOSBOX_MCP_CONFIG overrides for
    tests and unusual setups."""
    env = os.environ.get("DOSBOX_MCP_CONFIG")
    if env:
        return Path(env)
    return Path(user_config_dir("dosbox-mcp")) / "config.toml"


def default_ghidra_map_path() -> Path:
    """Where debug_map_set_base/debug_map_auto persist Ghidra<->live
    address-mapping ranges (delta and label only, never live_segment -
    see tools/ghidra.py for why). DOSBOX_MCP_GHIDRA_MAP overrides for
    tests and unusual setups."""
    env = os.environ.get("DOSBOX_MCP_GHIDRA_MAP")
    if env:
        return Path(env)
    return Path(user_config_dir("dosbox-mcp")) / "ghidra_map.json"


def default_token_path() -> Path:
    """Where the engine's launcher writes the API token by default.

    Mirrors dosbox-automation's own per-OS config directory choice
    (get_or_create_config_dir() in src/misc/cross.cpp), which is not
    the same directory platformdirs would pick: on macOS the engine
    uses ~/Library/Preferences, not ~/Library/Application Support.
    """
    name = "dosbox-automation"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Preferences" / name
    elif os.name == "nt":
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            base = Path(xdg) / name
        else:
            appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            base = Path(appdata or Path.home()) / name
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) / name if xdg else Path.home() / ".config" / name
    return base / "webserver" / "api_token"


def engine_config_dir(home: Path) -> Path:
    """Where the engine resolves its primary config directory
    (get_or_create_config_dir(), src/misc/cross.cpp) when both HOME and
    XDG_CONFIG_HOME are set to `home` - the isolation InstanceManager
    always applies to a spawned instance so it never touches the
    operator's real home directory.

    macOS ignores XDG_CONFIG_HOME entirely and resolves through HOME.
    Windows and Linux/BSD both honor an explicit XDG_CONFIG_HOME, which
    here is `home` itself (not home/.config - that's a choice specific
    to dosbox-automation's own integration test harness, which points
    XDG_CONFIG_HOME at work_dir/.config instead), so both collapse to
    the same home/dosbox-automation.
    """
    name = "dosbox-automation"
    if sys.platform == "darwin":
        return home / "Library" / "Preferences" / name
    return home / name


def resolved_token_path(token_file: Path | None = None) -> Path:
    """Where read_token() will actually look: the given path, else
    DOSBOX_TOKEN_FILE, else the engine's default location. Exposed so
    callers that report the token file (e.g. session_info) show the
    same path read_token() uses, even when not going through
    Config.load()."""
    if token_file is not None:
        return token_file
    env = os.environ.get("DOSBOX_TOKEN_FILE")
    return Path(env) if env else default_token_path()


def read_token(token_file: Path | None = None) -> str | None:
    """Token from DOSBOX_API_TOKEN, else the given file, else
    DOSBOX_TOKEN_FILE, else the default token file. Returns None if no
    token is available (dosbox not running yet)."""
    env = os.environ.get("DOSBOX_API_TOKEN")
    if env:
        return env
    path = resolved_token_path(token_file)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_port(value: object) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool),
             f"port must be an integer, got {value!r}")
    _require(1 <= value <= 65535, f"port out of range: {value}")
    return value


def _validate_mode(value: object) -> str:
    _require(isinstance(value, str) and value in MODES,
             f"mode must be one of {MODES}, got {value!r}")
    return value


def _validate_protocol(value: object) -> str:
    _require(isinstance(value, str) and bool(_PROTOCOL_RE.match(value)),
             f'protocol pin must be "major.minor" (e.g. "1.0"), got {value!r}')
    return value


def _validate_bool(key: str):
    def check(value: object) -> bool:
        _require(isinstance(value, bool), f"{key} must be true or false, got {value!r}")
        return value
    return check


def _validate_path(key: str):
    def check(value: object) -> Path:
        _require(isinstance(value, str) and value != "",
                 f"{key} must be a non-empty string path, got {value!r}")
        return Path(value)
    return check


# Mirrors max_entries in ParsePolicyConfig (src/dos/programs/mount_policy.cpp):
# the engine silently drops entries past this cap with only a log warning,
# so rejecting here at config-load time gives a loud error instead.
_MAX_POLICY_ENTRIES = 5


def _validate_path_list(key: str):
    def check(value: object) -> list[Path]:
        _require(isinstance(value, list),
                 f"{key} must be a list of path strings, got {value!r}")
        _require(len(value) <= _MAX_POLICY_ENTRIES,
                 f"{key} accepts at most {_MAX_POLICY_ENTRIES} entries "
                 f"(the engine's own per-key cap), got {len(value)}")
        paths = []
        for item in value:
            _require(isinstance(item, str) and item != "",
                     f"{key} entries must be non-empty strings, got {item!r}")
            path = Path(item)
            # Absolute and an existing directory: the engine's own
            # ParsePathList canonicalizes each entry and silently drops
            # (log warning only) anything that doesn't resolve - an
            # operator typo would otherwise surface only as
            # drive_mount/drive_swap failing with outside_whitelist,
            # with no clearer signal than a line buried in bridge_logs.
            _require(path.is_absolute(),
                     f"{key} entry {item!r} must be an absolute path")
            _require(path.is_dir(),
                     f"{key} entry {item!r} does not exist or is not a directory")
            # The engine's own drive_mount/drive_swap check
            # (HasSymlinkComponent) inspects the raw path a caller
            # supplies, not where it resolves to - a symlinked spelling
            # here would canonicalize fine into the primary config but
            # then never actually be usable, since a caller would have
            # to pass the resolved form anyway to get past that check.
            resolved = path.resolve()
            _require(resolved == path,
                     f"{key} entry {item!r} contains a symlink component - "
                     f"a drive_mount/drive_swap call must supply the "
                     f"already-resolved path to get past the engine's own "
                     f"symlink check, so this entry would never match a "
                     f"caller's request; use the resolved path instead: "
                     f"{resolved}")
            paths.append(path)
        return paths
    return check


_TOML_KEYS = {
    "binary": _validate_path("binary"),
    "port": _validate_port,
    "headless": _validate_bool("headless"),
    "protocol": _validate_protocol,
    "mode": _validate_mode,
    "token_file": _validate_path("token_file"),
    "mount_allowed_bases": _validate_path_list("mount_allowed_bases"),
    "mount_allowed_image_roots": _validate_path_list("mount_allowed_image_roots"),
}


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"malformed config {path}: {e}") from e
    unknown = sorted(set(raw) - set(_TOML_KEYS))
    if unknown:
        raise ValueError(
            f"unknown key(s) in {path}: {', '.join(unknown)} "
            f"(known: {', '.join(sorted(_TOML_KEYS))})"
        )
    return {key: _TOML_KEYS[key](value) for key, value in raw.items()}


@dataclass
class Config:
    base_url: str
    token: str | None = None
    binary: Path | None = None
    port: int = DEFAULT_PORT
    headless: bool = False
    protocol: str | None = None
    mode: str = "full"
    token_file: Path | None = None
    mount_allowed_bases: list[Path] = field(default_factory=list)
    mount_allowed_image_roots: list[Path] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Resolve configuration with precedence env > toml > defaults."""
        data = _read_toml(path if path is not None else default_config_path())

        port = data.get("port", DEFAULT_PORT)
        base = os.environ.get("DOSBOX_API_URL") or f"http://127.0.0.1:{port}"

        env_token_file = os.environ.get("DOSBOX_TOKEN_FILE")
        token_file = Path(env_token_file) if env_token_file else data.get("token_file")

        cfg = cls(
            base_url=validate_base_url(base),
            binary=data.get("binary"),
            port=port,
            headless=data.get("headless", False),
            protocol=data.get("protocol"),
            mode=data.get("mode", "full"),
            token_file=token_file,
            mount_allowed_bases=data.get("mount_allowed_bases", []),
            mount_allowed_image_roots=data.get("mount_allowed_image_roots", []),
        )
        cfg.token = read_token(cfg.token_file)
        return cfg


class ToolProtectedKey(ValueError):
    """Raised when a model-facing caller tries to write a key reserved
    for the human-edited config."""


# binary: whoever sets it decides what code the bridge executes.
# mode: the operator's constraint on the agent - an agent that can raise
# it escalates its own privileges. Both are human-only (self-audit
# 2026-07-17).
# mount_allowed_bases/mount_allowed_image_roots: the filesystem
# whitelist drive_mount/drive_swap enforce - an agent that could widen
# it would be granting itself access, defeating the whitelist entirely.
# bridge_setup's tool schema already excludes all four keys
# (additionalProperties: False, only port/headless/protocol listed);
# this set is the second gate, exercised if that schema is ever loosened.
_TOOL_PROTECTED_KEYS = frozenset({
    "binary", "mode", "mount_allowed_bases", "mount_allowed_image_roots",
})

_CONFIG_TEMPLATE = """\
# dosbox-mcp configuration
# Read at bridge startup. Precedence: environment > this file > defaults.
# Uncomment a line to set it; commented lines show the defaults.

# Path to the dosbox binary that bridge_start spawns.
# Deliberately not settable through any MCP tool.
#binary = "/usr/local/bin/dosbox"

# Port of the instance's webserver on 127.0.0.1 (used to connect, and
# passed to a spawned instance).
#port = 8386

# Spawn without a window (SDL dummy video driver).
#headless = false

# Pin the bridge to a lower protocol version, "major.minor" form.
#protocol = "1.0"

# Capability mode: what a connected agent may do.
#   observe  - read-only tools only
#   interact - adds input, video capture, script, instance lifecycle
#   full     - everything (memory writes, port IO, cpu control, debugger)
# Human-edited only: no tool can change this.
#mode = "full"

# Token file of an already-running instance to attach to. Leave unset
# to use the engine's default location.
#token_file = "~/.config/dosbox-automation/webserver/api_token"

# Directories a bridge_start-spawned instance allows drive_mount to
# mount, and image roots it allows drive_swap to pull disk images from
# - written into the spawned instance's own primary config, since the
# engine reads its mount policy from there regardless of
# --noprimaryconf. TOML arrays of absolute paths, max 5 entries each
# (the engine's own per-key cap). Unset means every drive_mount/
# drive_swap call against a spawned instance is refused by policy
# regardless of path - the out-of-the-box state. Human-edited only: no
# tool can change this, the same reasoning as binary and mode.
#mount_allowed_bases = ["/home/user/games"]
#mount_allowed_image_roots = ["/home/user/images"]
"""


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     delete=False) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
    if path.exists():
        os.chmod(tmp.name, path.stat().st_mode)
    os.replace(tmp.name, path)


def update_config_file(path: Path, changes: dict,
                       tool_facing: bool = False) -> None:
    """Apply validated changes to the config file, preserving the
    human's comments and layout (tomlkit round-trip, atomic replace).

    tool_facing=True enforces the model-side boundary: protected keys
    are rejected loudly instead of written.
    """
    if not changes:
        raise ValueError("no changes given")
    unknown = sorted(set(changes) - set(_TOML_KEYS))
    if unknown:
        raise ValueError(f"unknown key(s): {', '.join(unknown)}")
    if tool_facing:
        protected = sorted(set(changes) & _TOOL_PROTECTED_KEYS)
        if protected:
            raise ToolProtectedKey(
                f"key(s) {', '.join(protected)} are set in the human-edited "
                "config file only - not writable through a tool"
            )
    for key, value in changes.items():
        _TOML_KEYS[key](value)

    if path.is_file():
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()
    for key, value in changes.items():
        doc[key] = value
    _atomic_write(path, tomlkit.dumps(doc))


def write_config_template(path: Path) -> None:
    """Write the commented default config (`dosbox-mcp setup --init`).
    Refuses to clobber an existing file."""
    if path.exists():
        raise FileExistsError(f"{path} already exists - edit it instead")
    _atomic_write(path, _CONFIG_TEMPLATE)
