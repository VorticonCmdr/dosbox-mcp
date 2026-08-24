# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import pytest

from dosbox_mcp.protocol import (
    BRIDGE_PROTOCOL,
    IncompatiblePeer,
    effective_version,
    known_route_prefixes,
    negotiate,
    parse_version,
)


class TestParseVersion:
    def test_parses_major_minor(self):
        assert parse_version("1.0") == (1, 0)
        assert parse_version("2.13") == (2, 13)

    @pytest.mark.parametrize("bad", ["", "1", "1.0.0", "banana", "1.x", None, 1.0])
    def test_rejects_garbage(self, bad):
        with pytest.raises(ValueError):
            parse_version(bad)


class TestNegotiate:
    def test_effective_is_min_minor_on_same_major(self):
        assert negotiate((1, 2), (1, 0)) == (1, 0)
        assert negotiate((1, 0), (1, 3)) == (1, 0)
        assert negotiate((1, 1), (1, 1)) == (1, 1)

    @pytest.mark.parametrize("bridge,engine", [
        ((1, 0), (2, 0)),
        ((2, 1), (1, 9)),
    ])
    def test_major_mismatch_is_incompatible(self, bridge, engine):
        with pytest.raises(IncompatiblePeer):
            negotiate(bridge, engine)


class TestEffectiveVersion:
    def test_advertised_version_negotiated(self):
        info = {"features": {"memory": True}, "mcp_protocol": "1.0"}
        assert effective_version(info) == (1, 0)

    def test_features_without_advertisement_is_implicit_1_0(self):
        # da1/da2 ship the features block but predate the level field.
        info = {"features": {"memory": True}}
        assert effective_version(info) == (1, 0)

    def test_no_features_no_advertisement_is_incompatible(self):
        with pytest.raises(IncompatiblePeer):
            effective_version({"version": "something else entirely"})

    def test_unparseable_advertisement_is_incompatible(self):
        info = {"features": {}, "mcp_protocol": "latest"}
        with pytest.raises(IncompatiblePeer):
            effective_version(info)

    def test_future_major_is_incompatible(self):
        info = {"features": {}, "mcp_protocol": "99.0"}
        with pytest.raises(IncompatiblePeer):
            effective_version(info)

    def test_pin_lowers_bridge_side(self):
        info = {"features": {}, "mcp_protocol": "1.9"}
        assert effective_version(info, pin="1.0") == (1, 0)

    def test_pin_above_bridge_rejected(self):
        bridge_major, bridge_minor = parse_version(BRIDGE_PROTOCOL)
        too_high = f"{bridge_major}.{bridge_minor + 1}"
        with pytest.raises(ValueError):
            effective_version({"features": {}}, pin=too_high)

    def test_pin_wrong_major_rejected(self):
        with pytest.raises(ValueError):
            effective_version({"features": {}}, pin="99.0")


class TestKnownRoutePrefixes:
    def test_minor_0_excludes_hello(self):
        assert "hello" not in known_route_prefixes(0)

    def test_minor_1_includes_everything_minor_0_has_plus_hello(self):
        assert known_route_prefixes(1) == known_route_prefixes(0) | {"hello"}

    def test_debug_control_batch_wait_are_baseline_not_a_later_addition(self):
        # These routes existed and were already unconditionally
        # recognized before minor-versioning existed for this table;
        # they must not have silently moved to a later minor, or an
        # engine still negotiating implicit 1.0 would start seeing its
        # already-real routes misreported as unknown. "wait" was a real
        # gap caught live against a running engine while building this -
        # POST /api/v1/wait was never in the pre-4.2 flat set at all.
        for prefix in ("debug", "control", "batch", "wait"):
            assert prefix in known_route_prefixes(0)

    def test_a_future_minor_beyond_what_this_bridge_knows_still_returns_everything_defined(self):
        # Cumulative by construction: an unrecognized (future) minor
        # number just means "no additional entries beyond what's
        # defined," not an error - callers only ever pass minors this
        # bridge itself understands (BRIDGE_PROTOCOL's own minor, or a
        # peer's negotiated minor which can never exceed it).
        assert known_route_prefixes(99) == known_route_prefixes(1)
