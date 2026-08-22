# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

import pytest

from dosbox_mcp.tools import ghidra


@pytest.fixture(autouse=True)
def _isolated_map_file(monkeypatch, tmp_path):
    # _set_base/_auto persist on every call (register.py: state changes
    # always saved) - point that at a disposable file so tests never
    # touch the real user config dir or leak state between tests.
    monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(tmp_path / "ghidra_map.json"))


def _fresh_state():
    return {"ranges": []}


class _FakeClient:
    """Routes get/post by path prefix - debug_map_auto needs two
    different engine responses (scan, dos/internals) in one call."""

    def __init__(self, scan_response=None, internals_response=None):
        self.scan_response = scan_response
        self.internals_response = internals_response
        self.calls = []

    def post(self, path, json=None):
        self.calls.append(("post", path, json))
        if path == "/api/v1/memory/scan":
            return self.scan_response
        raise AssertionError(f"unexpected post path {path!r}")

    def get(self, path):
        self.calls.append(("get", path, None))
        if path == "/api/v1/dos/internals":
            return self.internals_response
        raise AssertionError(f"unexpected get path {path!r}")


class TestSetBaseAndTranslate:
    def test_translations_fail_before_any_range_is_set(self):
        state = _fresh_state()
        result = json.loads(ghidra._to_live(state, {"ghidra_address": 0x150})[0].text)
        assert "error" in result
        assert "no range covers" in result["error"]

    def test_set_base_then_roundtrip(self):
        state = _fresh_state()
        # .COM-style anchor: entry point 0x100 in both spaces, live CS 0x2000
        entry = json.loads(ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x2000, "live_offset": 0x100,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "main",
        })[0].text)
        assert entry == {
            "label": "main", "ghidra_start": 0, "ghidra_end": 0x1000,
            "delta": 0, "live_segment": 0x2000,
        }

        live = json.loads(ghidra._to_live(state, {"ghidra_address": 0x150})[0].text)
        assert live == {
            "segment": 0x2000, "offset": 0x150,
            "linear": 0x2000 * 16 + 0x150, "label": "main",
        }

        back = json.loads(ghidra._to_ghidra(state, {
            "live_segment": 0x2000, "live_offset": 0x150,
        })[0].text)
        assert back == {"ghidra_address": 0x150, "label": "main"}

    def test_to_ghidra_refuses_an_uncovered_segment(self):
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x2000, "live_offset": 0x100,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "main",
        })
        result = json.loads(ghidra._to_ghidra(state, {
            "live_segment": 0x3000, "live_offset": 0x150,
        })[0].text)
        assert "error" in result
        assert "0x3000" in result["error"]

    def test_re_anchoring_the_same_label_replaces_the_range_not_duplicates_it(self):
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x2000, "live_offset": 0x100,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "main",
        })
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x3000, "live_offset": 0x100,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "main",
        })
        assert len(state["ranges"]) == 1
        assert state["ranges"][0]["live_segment"] == 0x3000

    def test_status_reports_empty_and_set(self):
        state = _fresh_state()
        assert json.loads(ghidra._status(state)[0].text) == {"ranges": []}
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x2000, "live_offset": 0x100,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "main",
        })
        status = json.loads(ghidra._status(state)[0].text)
        assert status == {"ranges": [{
            "label": "main", "ghidra_start": 0, "ghidra_end": 0x1000,
            "delta": 0, "live_segment": 0x2000,
        }]}


class TestMultipleRanges:
    def _two_range_state(self):
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x1000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "seg_a",
        })
        ghidra._set_base(state, {
            "ghidra_address": 0x1000, "live_segment": 0x4000, "live_offset": 0,
            "ghidra_start": 0x1000, "ghidra_end": 0x2000, "label": "seg_b",
        })
        return state

    def test_translation_routes_to_the_range_that_covers_the_address(self):
        state = self._two_range_state()
        a = json.loads(ghidra._to_live(state, {"ghidra_address": 0x50})[0].text)
        assert a == {"segment": 0x1000, "offset": 0x50,
                     "linear": 0x1000 * 16 + 0x50, "label": "seg_a"}

        b = json.loads(ghidra._to_live(state, {"ghidra_address": 0x1050})[0].text)
        assert b == {"segment": 0x4000, "offset": 0x50,
                     "linear": 0x4000 * 16 + 0x50, "label": "seg_b"}

    def test_address_outside_every_range_is_refused(self):
        state = self._two_range_state()
        result = json.loads(ghidra._to_live(state, {"ghidra_address": 0x5000})[0].text)
        assert "error" in result
        assert "no range covers" in result["error"]

    def test_to_ghidra_only_matches_the_range_with_the_right_live_segment(self):
        state = self._two_range_state()
        result = json.loads(ghidra._to_ghidra(state, {
            "live_segment": 0x4000, "live_offset": 0x50,
        })[0].text)
        assert result == {"ghidra_address": 0x1050, "label": "seg_b"}


class TestOverlap:
    def test_set_base_rejects_a_span_overlapping_an_existing_different_labeled_range(self):
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x1000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "a",
        })
        result = json.loads(ghidra._set_base(state, {
            "ghidra_address": 0x800, "live_segment": 0x2000, "live_offset": 0,
            "ghidra_start": 0x800, "ghidra_end": 0x1800, "label": "b",
        })[0].text)
        assert "error" in result
        assert "overlaps range 'a'" in result["error"]
        assert len(state["ranges"]) == 1

    def test_auto_rejects_an_overlapping_span_before_calling_the_client(self):
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x1000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "a",
        })
        client = _FakeClient()
        result = json.loads(ghidra._auto(client, state, {
            "pattern": "AA BB", "ghidra_address": 0x800,
            "ghidra_start": 0x800, "ghidra_end": 0x1800, "label": "b",
        })[0].text)
        assert "error" in result
        assert "overlaps range 'a'" in result["error"]
        assert client.calls == []

    def test_re_anchoring_the_same_label_to_a_disjoint_span_is_not_an_overlap_with_itself(self):
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x1000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "a",
        })
        result = json.loads(ghidra._set_base(state, {
            "ghidra_address": 0x2000, "live_segment": 0x3000, "live_offset": 0,
            "ghidra_start": 0x2000, "ghidra_end": 0x3000, "label": "a",
        })[0].text)
        assert "error" not in result
        assert len(state["ranges"]) == 1
        assert state["ranges"][0]["ghidra_start"] == 0x2000

    def test_adjacent_non_overlapping_spans_are_allowed(self):
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x1000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "a",
        })
        result = json.loads(ghidra._set_base(state, {
            "ghidra_address": 0x1000, "live_segment": 0x2000, "live_offset": 0,
            "ghidra_start": 0x1000, "ghidra_end": 0x2000, "label": "b",
        })[0].text)
        assert "error" not in result
        assert len(state["ranges"]) == 2


class TestValidation:
    def test_set_base_rejects_an_out_of_range_live_segment(self):
        state = _fresh_state()
        result = json.loads(ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x10000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x10, "label": "x",
        })[0].text)
        assert "error" in result
        assert state["ranges"] == []

    def test_set_base_rejects_an_out_of_range_live_offset(self):
        state = _fresh_state()
        result = json.loads(ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0, "live_offset": -1,
            "ghidra_start": 0, "ghidra_end": 0x10, "label": "x",
        })[0].text)
        assert "error" in result

    def test_set_base_rejects_an_empty_or_too_long_label(self):
        state = _fresh_state()
        for label in ("", "x" * (ghidra.MaxLabelLength + 1)):
            result = json.loads(ghidra._set_base(state, {
                "ghidra_address": 0, "live_segment": 0, "live_offset": 0,
                "ghidra_start": 0, "ghidra_end": 0x10, "label": label,
            })[0].text)
            assert "error" in result

    def test_set_base_rejects_a_backwards_range(self):
        state = _fresh_state()
        result = json.loads(ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0, "live_offset": 0,
            "ghidra_start": 0x100, "ghidra_end": 0x100, "label": "x",
        })[0].text)
        assert "error" in result

    def test_set_base_rejects_an_anchor_outside_its_own_range(self):
        state = _fresh_state()
        result = json.loads(ghidra._set_base(state, {
            "ghidra_address": 0x2000, "live_segment": 0, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "x",
        })[0].text)
        assert "error" in result
        assert state["ranges"] == []

    def test_to_ghidra_rejects_an_out_of_range_segment(self):
        state = _fresh_state()
        result = json.loads(ghidra._to_ghidra(state, {
            "live_segment": -1, "live_offset": 0,
        })[0].text)
        assert "error" in result

    def test_to_live_rejects_a_translated_offset_outside_16_bits(self):
        state = _fresh_state()
        # Deliberately oversized span (larger than 64K) so a far address
        # inside it translates to an offset > 0xFFFF - the "validate the
        # sum, not just the parts" case: live_segment/live_offset were
        # fine at anchor time, but this ghidra_address wasn't.
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0, "live_offset": 0x100,
            "ghidra_start": 0, "ghidra_end": 0x20000, "label": "huge",
        })
        result = json.loads(ghidra._to_live(state, {"ghidra_address": 0x1FFFF})[0].text)
        assert "error" in result
        assert "outside 0x0000..0xFFFF" in result["error"]

    def test_to_live_reports_an_unanchored_range_distinctly(self):
        # Simulates a range loaded from disk (live_segment None) that
        # hasn't been re-anchored this session yet.
        state = {"ranges": [{
            "label": "stale", "ghidra_start": 0, "ghidra_end": 0x1000,
            "delta": 0, "live_segment": None,
        }]}
        result = json.loads(ghidra._to_live(state, {"ghidra_address": 0x10})[0].text)
        assert "error" in result
        assert "no live segment yet this session" in result["error"]


class TestPersistence:
    def test_set_base_persists_delta_and_label_but_not_live_segment(self, monkeypatch, tmp_path):
        map_file = tmp_path / "map.json"
        monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(map_file))
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0x100, "live_segment": 0x2000, "live_offset": 0x100,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "main",
        })
        on_disk = json.loads(map_file.read_text())
        assert on_disk == [{
            "label": "main", "ghidra_start": 0, "ghidra_end": 0x1000, "delta": 0,
        }]

    def test_a_range_loaded_from_disk_has_no_live_segment(self, monkeypatch, tmp_path):
        map_file = tmp_path / "map.json"
        map_file.write_text(json.dumps([
            {"label": "main", "ghidra_start": 0, "ghidra_end": 0x1000, "delta": 5},
        ]))
        monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(map_file))

        ranges = ghidra._load_ranges()
        assert ranges == [{
            "label": "main", "ghidra_start": 0, "ghidra_end": 0x1000,
            "delta": 5, "live_segment": None,
        }]

    def test_missing_map_file_loads_as_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(tmp_path / "does_not_exist.json"))
        assert ghidra._load_ranges() == []

    def test_malformed_map_file_loads_as_empty_not_a_crash(self, monkeypatch, tmp_path):
        map_file = tmp_path / "map.json"
        map_file.write_text("not json at all {{{")
        monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(map_file))
        assert ghidra._load_ranges() == []

    def test_re_anchoring_updates_the_persisted_file_too(self, monkeypatch, tmp_path):
        map_file = tmp_path / "map.json"
        monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(map_file))
        state = _fresh_state()
        ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x1000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x10, "label": "x",
        })
        ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x2000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x20, "label": "x",
        })
        on_disk = json.loads(map_file.read_text())
        assert on_disk == [{
            "label": "x", "ghidra_start": 0, "ghidra_end": 0x20, "delta": 0,
        }]


class TestAuto:
    def test_derives_segment_and_delta_from_a_unique_signature_match(self):
        # A self-owned .COM-style block: segment 7's own data segment
        # (7+1=8) equals the pspSegment it's tagged with - the actual
        # invariant debug_map_auto requires (see the ownership check's
        # own comment). Data spans physical [128, 1424); matched_addr
        # 400 sits inside that.
        state = _fresh_state()
        client = _FakeClient(
            scan_response={"matches": [400], "total": 1, "truncated": False},
            internals_response={"memoryMap": [
                {"segment": 7, "pspSegment": 8, "sizeBytes": 1296,
                 "filename": "TEST", "type": 77, "isLast": False, "sizeParas": 81},
            ]},
        )
        result = json.loads(ghidra._auto(client, state, {
            "pattern": "55 FE 38 29",
            "ghidra_address": 0, "ghidra_start": 0, "ghidra_end": 0x1000,
            "label": "test",
        })[0].text)

        assert result["live_segment"] == 8 + 0x10  # pspSegment + 0x10
        live_offset = 400 - (8 + 0x10) * 16
        assert live_offset == 16
        assert result["delta"] == 0 - live_offset
        assert result["matched_addr"] == 400
        assert result["psp_segment"] == 8
        assert state["ranges"][0]["label"] == "test"

        # scan is called with the tool's default conventional-memory
        # bounds when scan_start/scan_end are omitted.
        scan_call = next(c for c in client.calls if c[1] == "/api/v1/memory/scan")
        assert scan_call[2]["start"] == 0
        assert scan_call[2]["end"] == 0xA0000
        assert scan_call[2]["limit"] == 2

    def test_refuses_a_match_inside_a_block_that_isnt_self_owned(self):
        # segment 391's own data segment is 392, not the pspSegment (8)
        # it's tagged with - an environment block or similar, owned by
        # PSP 8 but not PSP 8's own code. This was the SD-driver
        # scenario debug_map_auto used to (wrongly) accept - see the
        # ownership check's own comment in _auto.
        state = _fresh_state()
        client = _FakeClient(
            scan_response={"matches": [6272], "total": 1, "truncated": False},
            internals_response={"memoryMap": [
                {"segment": 391, "pspSegment": 8, "sizeBytes": 1296,
                 "filename": "SD", "type": 77, "isLast": False, "sizeParas": 81},
            ]},
        )
        result = json.loads(ghidra._auto(client, state, {
            "pattern": "55 FE 38 29",
            "ghidra_address": 0, "ghidra_start": 0, "ghidra_end": 0x1000,
            "label": "sd",
        })[0].text)
        assert "error" in result
        assert "not that PSP's own code block" in result["error"]
        assert state["ranges"] == []

    def test_refuses_a_match_inside_a_free_block(self):
        # pspSegment=0 (MCB_FREE) can never equal segment+1 for a real
        # segment value, so this is rejected by the same ownership check.
        state = _fresh_state()
        client = _FakeClient(
            scan_response={"matches": [100], "total": 1, "truncated": False},
            internals_response={"memoryMap": [
                {"segment": 5, "pspSegment": 0, "sizeBytes": 1000,
                 "filename": "", "type": 77, "isLast": False, "sizeParas": 62},
            ]},
        )
        result = json.loads(ghidra._auto(client, state, {
            "pattern": "AA BB", "ghidra_address": 0, "ghidra_start": 0,
            "ghidra_end": 0x10, "label": "x",
        })[0].text)
        assert "error" in result
        assert state["ranges"] == []

    def test_refuses_when_the_signature_is_not_found(self):
        state = _fresh_state()
        client = _FakeClient(scan_response={"matches": [], "total": 0, "truncated": False})
        result = json.loads(ghidra._auto(client, state, {
            "pattern": "AA BB", "ghidra_address": 0, "ghidra_start": 0,
            "ghidra_end": 0x10, "label": "x",
        })[0].text)
        assert "error" in result
        assert "not found" in result["error"]
        assert state["ranges"] == []

    def test_refuses_when_the_signature_is_ambiguous(self):
        state = _fresh_state()
        client = _FakeClient(scan_response={"matches": [10, 20], "total": 2, "truncated": False})
        result = json.loads(ghidra._auto(client, state, {
            "pattern": "AA BB", "ghidra_address": 0, "ghidra_start": 0,
            "ghidra_end": 0x10, "label": "x",
        })[0].text)
        assert "error" in result
        assert "ambiguous" in result["error"]
        assert state["ranges"] == []

    def test_refuses_when_the_match_is_not_inside_any_mcb_block(self):
        state = _fresh_state()
        client = _FakeClient(
            scan_response={"matches": [999999], "total": 1, "truncated": False},
            internals_response={"memoryMap": [
                {"segment": 391, "pspSegment": 8, "sizeBytes": 1296,
                 "filename": "SD", "type": 77, "isLast": False, "sizeParas": 81},
            ]},
        )
        result = json.loads(ghidra._auto(client, state, {
            "pattern": "AA BB", "ghidra_address": 0, "ghidra_start": 0,
            "ghidra_end": 0x10, "label": "x",
        })[0].text)
        assert "error" in result
        assert "MCB chain" in result["error"]
        assert state["ranges"] == []

    def test_respects_explicit_scan_bounds(self):
        state = _fresh_state()
        client = _FakeClient(
            scan_response={"matches": [100], "total": 1, "truncated": False},
            internals_response={"memoryMap": [
                {"segment": 5, "pspSegment": 6, "sizeBytes": 1000,
                 "filename": "", "type": 77, "isLast": False, "sizeParas": 62},
            ]},
        )
        ghidra._auto(client, state, {
            "pattern": "AA BB", "ghidra_address": 0, "ghidra_start": 0,
            "ghidra_end": 0x10, "label": "x", "scan_start": 50, "scan_end": 200,
        })
        scan_call = next(c for c in client.calls if c[1] == "/api/v1/memory/scan")
        assert scan_call[2]["start"] == 50
        assert scan_call[2]["end"] == 200

    def test_validates_range_bounds_before_ever_calling_the_client(self):
        state = _fresh_state()
        client = _FakeClient()
        result = json.loads(ghidra._auto(client, state, {
            "pattern": "AA BB", "ghidra_address": 0x2000, "ghidra_start": 0,
            "ghidra_end": 0x1000, "label": "x",
        })[0].text)
        assert "error" in result
        assert client.calls == []
