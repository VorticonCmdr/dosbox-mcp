# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

"""Protocol version negotiation.

The spec (PROTOCOL.md) is versioned MAJOR.MINOR.PATCH; only
"major.minor" travels on the wire. Peers are compatible when the major
matches; the effective contract is the smaller minor. Engines that ship
a features block but predate the mcp_protocol field (da1, da2) are
implicit 1.0, because 1.0 is defined as the surface those releases ship.
"""

import re

# Highest protocol version this bridge implements. PROTOCOL.md's
# changelog already documents everything this bridge's tools cover
# (mouse, batch, script/log, instance_id, ...) up through 1.13.0
# (draft); 1.14.0 is this item (4.2) itself - dosbox/info finally sends
# mcp_protocol/name for real, and GET /api/v1/hello is implemented on
# the reference engine for the first time.
BRIDGE_PROTOCOL = "1.14"

# Route groups (first path segment after /api/v1/) each protocol minor
# adds, on top of everything every earlier minor already knew - nothing
# is ever removed from the protocol, so this is purely additive.
# bridge_swagger sums prefixes up through the negotiated minor to decide
# what's "known to the protocol" for the peer it's actually talking to;
# see known_route_prefixes() below. GET /api/v1/hello itself only exists
# as of 1.1, so "hello" is the one genuinely new prefix here - everything
# else (including debug/control/batch/wait) was already unconditionally
# recognized before minor versioning existed for this table, and staying
# in the 1.0 baseline keeps an engine that still only advertises implicit
# 1.0 (predates mcp_protocol) from having its already-real routes newly
# misreported as unknown. "wait" was missing from the pre-4.2 flat set
# entirely - a real, live false positive against a running engine's
# openapi.json (POST /api/v1/wait), caught during this item's live
# verification, not something 4.2 introduced.
KNOWN_ROUTE_PREFIXES_BY_MINOR: dict[int, frozenset[str]] = {
    0: frozenset({
        "status", "program", "dosbox", "video", "capture", "input", "memory",
        "dos", "cpu", "io", "script", "drive", "mount", "debug",
        "control", "batch", "wait", "openapi.json",
    }),
    1: frozenset({"hello"}),
}


def known_route_prefixes(minor: int) -> frozenset[str]:
    """Route prefixes known as of protocol 1.<minor>, cumulative over
    every minor up to and including it."""
    known: set[str] = set()
    for m, prefixes in KNOWN_ROUTE_PREFIXES_BY_MINOR.items():
        if m <= minor:
            known |= prefixes
    return frozenset(known)

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


class IncompatiblePeer(Exception):
    pass


def parse_version(value: object) -> tuple[int, int]:
    """Parse a "major.minor" wire version into a comparable tuple."""
    if not isinstance(value, str):
        raise ValueError(f"protocol version must be a string, got {value!r}")
    match = _VERSION_RE.match(value)
    if match is None:
        raise ValueError(f'protocol version must be "major.minor", got {value!r}')
    return int(match.group(1)), int(match.group(2))


def negotiate(bridge: tuple[int, int], engine: tuple[int, int]) -> tuple[int, int]:
    """Effective contract between two peers: major must match, minor is
    the smaller of the two."""
    if bridge[0] != engine[0]:
        raise IncompatiblePeer(
            f"protocol major mismatch: bridge speaks {bridge[0]}.x, "
            f"engine speaks {engine[0]}.x"
        )
    return bridge[0], min(bridge[1], engine[1])


def effective_version(info: dict, pin: str | None = None) -> tuple[int, int]:
    """Determine the effective protocol version from an /info payload.

    pin (config `protocol`) lowers the bridge's own side for testing and
    compatibility; it can never exceed what the bridge implements.
    """
    bridge = parse_version(BRIDGE_PROTOCOL)
    if pin is not None:
        pinned = parse_version(pin)
        if pinned[0] != bridge[0] or pinned[1] > bridge[1]:
            raise ValueError(
                f"protocol pin {pin!r} exceeds what this bridge implements "
                f"({BRIDGE_PROTOCOL})"
            )
        bridge = pinned

    advertised = info.get("mcp_protocol")
    if advertised is not None:
        try:
            engine = parse_version(advertised)
        except ValueError as e:
            raise IncompatiblePeer(f"engine advertises {advertised!r}: {e}") from e
    elif "features" in info:
        engine = (1, 0)
    else:
        raise IncompatiblePeer(
            "peer advertises no protocol version and no features block - "
            "not a dosbox-mcp protocol peer"
        )
    return negotiate(bridge, engine)
