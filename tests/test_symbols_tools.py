# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools import symbols


def _fresh_state():
    return {"symbols": {}, "sorted_addrs": []}


def _load(state, text):
    return json.loads(symbols._load(state, {"text": text})[0].text)


class TestParsePlainText:
    def test_parses_list_functions_style_lines(self):
        state = _fresh_state()
        result = _load(state, "main at 0100:0010\nhelper at 0100:0020\n")
        assert result == {"loaded": 2, "total_symbols": 2, "skipped_lines": 0}
        assert state["symbols"] == {0x1010: "main", 0x1020: "helper"}

    def test_parses_list_globals_style_lines_with_trailing_metadata(self):
        state = _fresh_state()
        result = _load(state, (
            "DAT_1234 @ 0100:1234 [Label] (byte) xrefs=3\n"
            "g_counter @ 0100:2000 [int] xrefs=5\n"
        ))
        assert result["loaded"] == 2
        assert state["symbols"] == {
            0x1000 + 0x1234: "DAT_1234",
            0x1000 + 0x2000: "g_counter",
        }

    def test_parses_flat_hex_addresses_without_a_segment(self):
        state = _fresh_state()
        _load(state, "entry_point at 401000\n")
        assert state["symbols"] == {0x401000: "entry_point"}

    def test_parses_0x_prefixed_addresses(self):
        state = _fresh_state()
        _load(state, "entry_point at 0x401000\n")
        assert state["symbols"] == {0x401000: "entry_point"}

    def test_skips_unparseable_lines_without_failing_the_whole_load(self):
        state = _fresh_state()
        result = _load(state, (
            "main at 0100:0010\n"
            "this is not a symbol line\n"
            "helper at 0100:0020\n"
        ))
        assert result["loaded"] == 2
        assert result["skipped_lines"] == 1
        assert len(state["symbols"]) == 2

    def test_blank_lines_are_not_counted_as_skipped(self):
        state = _fresh_state()
        result = _load(state, "main at 0100:0010\n\n\n")
        assert result["skipped_lines"] == 0

    def test_empty_text_loads_nothing(self):
        state = _fresh_state()
        result = _load(state, "")
        assert result == {"loaded": 0, "total_symbols": 0, "skipped_lines": 0}


class TestParseJson:
    def test_parses_list_functions_enhanced_shape(self):
        state = _fresh_state()
        text = json.dumps({
            "functions": [
                {"address": "0100:0010", "name": "main", "isThunk": False},
                {"address": "0100:0020", "name": "helper", "isThunk": False},
            ],
            "count": 2, "offset": 0, "limit": 100,
        })
        result = _load(state, text)
        assert result == {"loaded": 2, "total_symbols": 2}
        assert "skipped_lines" not in result
        assert state["symbols"] == {0x1010: "main", 0x1020: "helper"}

    def test_parses_a_bare_json_list_of_entries(self):
        state = _fresh_state()
        text = json.dumps([
            {"address": "401000", "name": "entry"},
            {"address": "401010", "name": "loop"},
        ])
        result = _load(state, text)
        assert result["loaded"] == 2
        assert state["symbols"] == {0x401000: "entry", 0x401010: "loop"}

    def test_skips_entries_missing_a_usable_name_or_address(self):
        state = _fresh_state()
        text = json.dumps({"functions": [
            {"address": "0100:0010", "name": "main"},
            {"address": "0100:0020"},
            {"name": "no_address"},
            "not even an object",
        ]})
        result = _load(state, text)
        assert result["loaded"] == 1
        assert state["symbols"] == {0x1010: "main"}

    def test_unrecognized_json_shape_falls_back_to_text_parsing(self):
        state = _fresh_state()
        result = _load(state, json.dumps({"unrelated": "shape"}))
        assert result["loaded"] == 0
        assert "skipped_lines" in result

    def test_pathologically_deep_json_nesting_degrades_instead_of_raising(self):
        # CPython's json decoder recurses per nesting level - malformed
        # or truncated JSON with many unbalanced brackets (a garbled
        # paste, not necessarily a deliberate attack) raises
        # RecursionError, not json.JSONDecodeError. This must still
        # degrade to a graceful "nothing usable here" rather than an
        # uncaught exception escaping the tool call.
        state = _fresh_state()
        result = _load(state, "[" * 2000 + "]" * 2000)
        assert result["loaded"] == 0
        assert "skipped_lines" in result


class TestAccumulation:
    def test_repeated_loads_add_to_the_existing_table(self):
        state = _fresh_state()
        _load(state, "main at 0100:0010\n")
        _load(state, "helper at 0100:0020\n")
        assert state["symbols"] == {0x1010: "main", 0x1020: "helper"}

    def test_reloading_the_same_address_updates_the_name(self):
        state = _fresh_state()
        _load(state, "old_name at 0100:0010\n")
        _load(state, "new_name at 0100:0010\n")
        assert state["symbols"] == {0x1010: "new_name"}

    def test_status_reports_the_running_total(self):
        state = _fresh_state()
        _load(state, "main at 0100:0010\n")
        status = json.loads(symbols._status(state)[0].text)
        assert status == {"total_symbols": 1}

    def test_max_symbols_caps_further_loading(self, monkeypatch):
        monkeypatch.setattr(symbols, "MaxSymbols", 2)
        state = _fresh_state()
        result = _load(state, (
            "a at 0100:0001\nb at 0100:0002\nc at 0100:0003\n"
        ))
        assert result["total_symbols"] == 2
        assert len(state["symbols"]) == 2
        assert result["dropped_at_cap"] == 1

    def test_dropped_at_cap_is_omitted_when_nothing_was_dropped(self, monkeypatch):
        monkeypatch.setattr(symbols, "MaxSymbols", 2)
        state = _fresh_state()
        result = _load(state, "a at 0100:0001\nb at 0100:0002\n")
        assert "dropped_at_cap" not in result

    def test_updates_to_already_loaded_addresses_still_apply_after_the_cap_is_hit(
            self, monkeypatch):
        # A cap-triggering new entry earlier in the same batch must not
        # abort processing of later entries that only update an address
        # already in the table - those are explicitly exempt from the
        # cap (the same len()>=MaxSymbols check only fires for a brand
        # new address), so they must still take effect.
        monkeypatch.setattr(symbols, "MaxSymbols", 2)
        state = _fresh_state()
        _load(state, "a at 0100:0001\nb at 0100:0002\n")

        result = _load(state, (
            "a_new_name at 0100:0001\n"  # update, no growth
            "c at 0100:0003\n"           # new address, hits the cap
            "b_new_name at 0100:0002\n"  # update, no growth - must still apply
        ))

        assert state["symbols"] == {0x1001: "a_new_name", 0x1002: "b_new_name"}
        assert result["loaded"] == 2
        assert result["dropped_at_cap"] == 1


class TestNearestSymbolLookup:
    def test_exact_match_has_no_offset_suffix(self):
        state = _fresh_state()
        _load(state, "main at 0100:0010\n")
        assert symbols._nearest_symbol(state, 0x1010) == "main"

    def test_address_inside_a_symbol_gets_a_plus_offset_suffix(self):
        state = _fresh_state()
        _load(state, "main at 0100:0010\n")
        assert symbols._nearest_symbol(state, 0x1010 + 0x20) == "main+0x20"

    def test_picks_the_nearest_preceding_symbol_not_the_next_one(self):
        state = _fresh_state()
        _load(state, "a at 0100:0010\nb at 0100:0100\n")
        assert symbols._nearest_symbol(state, 0x1010 + 0x50) == "a+0x50"

    def test_address_before_every_known_symbol_resolves_to_nothing(self):
        state = _fresh_state()
        _load(state, "main at 0100:0100\n")
        assert symbols._nearest_symbol(state, 0x1000) is None

    def test_beyond_max_symbol_distance_resolves_to_nothing(self):
        state = _fresh_state()
        _load(state, "main at 0000:0000\n")
        assert symbols._nearest_symbol(state, symbols.MaxSymbolDistance) == (
            "main+" + hex(symbols.MaxSymbolDistance)
        )
        assert symbols._nearest_symbol(state, symbols.MaxSymbolDistance + 1) is None

    def test_empty_table_resolves_to_nothing(self):
        state = _fresh_state()
        assert symbols._nearest_symbol(state, 0x1234) is None


class TestMakeAnnotator:
    def _anchored_ghidra_state(self):
        from dosbox_mcp.tools import ghidra
        state = {"ranges": []}
        ghidra._set_base(state, {
            "ghidra_address": 0, "live_segment": 0x2000, "live_offset": 0,
            "ghidra_start": 0, "ghidra_end": 0x1000, "label": "main",
        })
        return state

    def test_resolves_a_live_address_through_ghidra_mapping_and_symbols(self):
        ghidra_state = self._anchored_ghidra_state()
        symbol_state = _fresh_state()
        _load(symbol_state, "entry at 0x10\n")
        annotate = symbols.make_annotator(ghidra_state, symbol_state)
        assert annotate(0x2000, 0x10) == "entry"
        assert annotate(0x2000, 0x30) == "entry+0x20"

    def test_returns_none_when_no_symbols_are_loaded(self):
        ghidra_state = self._anchored_ghidra_state()
        symbol_state = _fresh_state()
        annotate = symbols.make_annotator(ghidra_state, symbol_state)
        assert annotate(0x2000, 0x10) is None

    def test_returns_none_when_the_live_address_is_outside_every_ghidra_range(self):
        ghidra_state = self._anchored_ghidra_state()
        symbol_state = _fresh_state()
        _load(symbol_state, "entry at 0x10\n")
        annotate = symbols.make_annotator(ghidra_state, symbol_state)
        assert annotate(0x9000, 0x10) is None

    def test_returns_none_on_an_out_of_range_live_offset_rather_than_raising(self):
        ghidra_state = self._anchored_ghidra_state()
        symbol_state = _fresh_state()
        _load(symbol_state, "entry at 0x10\n")
        annotate = symbols.make_annotator(ghidra_state, symbol_state)
        assert annotate(0x2000, -1) is None
        assert annotate(0x2000, 0x100000) is None
