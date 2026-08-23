# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#
# Symbol table + address annotation, built entirely in the bridge (no
# engine involvement, no protocol dependency - same reasoning as
# tools/ghidra.py's own docstring): the symbol data originates from
# Ghidra, which the agent already talks to directly, so there is
# nothing for the engine to validate or store. An engine-side symbol
# store would mean a large, untrusted-name payload retained in
# emulator memory for metadata with zero emulator-side semantics, and
# would stop working on a stock (non-debugger) build for no reason -
# this module works everywhere, same as tools/ghidra.py's four
# pure-arithmetic tools.
#
# debug_symbols_load accepts whatever text an agent got back from
# calling the Ghidra MCP server's own list_functions/list_globals/
# list_functions_enhanced tools, unmodified - no reformatting asked of
# the agent. Three shapes are recognized:
#   - list_functions_enhanced's JSON: {"functions": [{"address", "name",
#     ...}, ...]} (a bare JSON list of {"address", "name"} objects is
#     also accepted, in case a different Ghidra MCP server structures
#     its own enhanced output that way).
#   - "<name> at <address>" (list_functions' plain-text line format).
#   - "<name> @ <address> [...] ..." (list_globals' plain-text line
#     format, both its named-symbol and unnamed-but-xrefed shapes).
# This is deliberately permissive rather than tied to one exact Ghidra
# MCP server's byte-for-byte formatting: a line/entry this doesn't
# recognize is skipped, not a load failure, since there's no contract
# guaranteeing every Ghidra MCP server formats these identically (unlike
# this project's own routes, which PROTOCOL.md pins down exactly).
#
# `address` itself is either a segmented "SSSS:OOOO" pair (Ghidra's
# x86:LE:16:Real Mode rendering, hex, no "0x" prefix - see ghidra.py's
# own docstring for why a segmented address space is the norm here) or
# a plain hex integer (a flat address space, or an already-linearized
# one) - both resolve to the single ghidra_address integer
# ghidra.py's own range model already uses, "SSSS:OOOO" via the same
# seg*16+off real-mode convention as everything else in this bridge.

import bisect
import json
import re

import mcp.types as types

from .ghidra import find_ghidra_address

# Bytes past the nearest preceding symbol beyond which "inside this
# symbol" stops being a useful claim - a huge, uncapped "+0x1234"
# offset past the last known function is more misleading than no
# annotation at all. Generous for a DOS-sized program.
MaxSymbolDistance = 0x10000

# Backstop against an unreasonably large paste - a real Ghidra
# function+global list for even a large DOS program rarely reaches
# this; large enough that no genuine load should ever hit it.
MaxSymbols = 20000

# Schema-level cap on the raw pasted text itself, checked by the MCP
# SDK before the handler runs - same convention as mem_write's
# maxLength (memory.py). Without this, a load large enough to still be
# under MaxSymbols entries but pathologically long per line (or with
# huge amounts of unparseable filler) would cost unbounded CPU/memory
# in _parse_text_lines/_parse_json_entries before that cap is ever
# reached. 8 MiB is generously above any real Ghidra function+global
# listing, which runs low single-digit MB even for a large program.
MaxTextLength = 8 * 1024 * 1024

_LINE_RE = re.compile(
    r"^(?P<name>\S+)\s+(?:at|@)\s+(?P<addr>[0-9A-Fa-f]{1,4}:[0-9A-Fa-f]{1,8}"
    r"|0[xX][0-9A-Fa-f]+|[0-9A-Fa-f]+)\b"
)


def register(server, client, add_tool, feature=None):
    state = {"symbols": {}, "sorted_addrs": []}

    add_tool(
        name="debug_symbols_load",
        description=(
            "Load function/global names from Ghidra so addresses "
            "elsewhere in this bridge (disassembly, pause/step/wait "
            "stop records, backtrace frames, the MCB map) get a "
            "'symbol' field alongside the raw number. Paste the text "
            "the Ghidra MCP server's own list_functions, list_globals, "
            "or list_functions_enhanced tool returned, unmodified - "
            "this parses their output directly, not a reformatted "
            "version of it. Requires an anchored debug_map_set_base or "
            "debug_map_auto range first (2.16): a symbol is only "
            "reachable through the same Ghidra<->live translation "
            "those establish, so load the range(s) covering these "
            "addresses before or after loading symbols, either order "
            "works. Calling this again adds to the existing table "
            "(re-loading the same name/address updates it); it does "
            "not replace what's already loaded. Lines/entries this "
            "can't parse are silently skipped, not a load failure - "
            "check the returned 'loaded'/'skipped_lines' counts, and "
            "'dropped_at_cap' (present only when nonzero) for entries "
            "dropped because the table already holds "
            f"{MaxSymbols} symbols."
        ),
        risk="mutate_host",
        title="Load Symbols",
        idempotent=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {
                    "type": "string",
                    "maxLength": MaxTextLength,
                    "description": (
                        "Raw output from the Ghidra MCP server's "
                        "list_functions, list_globals, or "
                        "list_functions_enhanced tool."
                    ),
                },
            },
            "required": ["text"],
        },
        handler=lambda args: _load(state, args),
        feature=None,
        needs_connection=False,
    )

    add_tool(
        name="debug_symbols_status",
        description=(
            "How many symbols are currently loaded (debug_symbols_load) "
            "and available for address annotation."
        ),
        risk="read",
        title="Symbol Table Status",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _status(state),
        feature=None,
        needs_connection=False,
    )

    return state


def make_annotator(ghidra_state, symbol_state):
    """Returns annotate(live_segment, live_offset) -> symbol string or
    None, bound to the given ghidra/symbol state - what debug.py/
    memory.py's response-building code calls per address. Cheap to call
    when nothing is loaded yet (the overwhelmingly common case before
    an agent has run debug_map_set_base/debug_symbols_load): both
    early-exit before touching ghidra_state at all."""
    def annotate(live_segment, live_offset):
        if not symbol_state["symbols"]:
            return None
        ghidra_address, _label = find_ghidra_address(
                ghidra_state, live_segment, live_offset)
        if ghidra_address is None:
            return None
        return _nearest_symbol(symbol_state, ghidra_address)
    return annotate


def _nearest_symbol(symbol_state, ghidra_address):
    sorted_addrs = symbol_state["sorted_addrs"]
    if not sorted_addrs:
        return None
    idx = bisect.bisect_right(sorted_addrs, ghidra_address) - 1
    if idx < 0:
        return None
    base = sorted_addrs[idx]
    delta = ghidra_address - base
    if delta > MaxSymbolDistance:
        return None
    name = symbol_state["symbols"][base]
    return name if delta == 0 else f"{name}+{delta:#x}"


def _parse_address(token):
    token = token.strip()
    if ":" in token:
        seg_str, off_str = token.split(":", 1)
        try:
            return int(seg_str, 16) * 16 + int(off_str, 16)
        except ValueError:
            return None
    try:
        return int(token, 16 if not token.lower().startswith("0x") else 0)
    except ValueError:
        return None


def _parse_json_entries(text):
    try:
        data = json.loads(text)
    except Exception:
        # Not just json.JSONDecodeError/TypeError - pathologically deep
        # nesting (e.g. a truncated/malformed paste with many
        # unbalanced brackets) makes CPython's decoder raise
        # RecursionError instead, since it recurses per nesting level.
        # Either way this text isn't usable as JSON, so fall back to
        # line-based parsing rather than let the error escape and turn
        # a load this module promises degrades gracefully into an
        # opaque tool failure.
        return None

    if isinstance(data, dict):
        entries = data.get("functions")
        if entries is None:
            entries = data.get("globals")
        if entries is None:
            return None
    elif isinstance(data, list):
        entries = data
    else:
        return None

    if not isinstance(entries, list):
        return None

    parsed = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        addr_raw = entry.get("address")
        if not isinstance(name, str) or not isinstance(addr_raw, str):
            continue
        addr = _parse_address(addr_raw)
        if addr is not None:
            parsed.append((addr, name))
    return parsed


def _parse_text_lines(text):
    parsed = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        addr = _parse_address(m.group("addr"))
        if addr is not None:
            parsed.append((addr, m.group("name")))
    return parsed


def _load(state, args):
    text = args["text"]
    parsed = _parse_json_entries(text)
    used_json = parsed is not None
    if not used_json:
        parsed = _parse_text_lines(text)

    loaded = 0
    dropped_at_cap = 0
    for addr, name in parsed:
        if len(state["symbols"]) >= MaxSymbols and addr not in state["symbols"]:
            # Only a NEW address counts against the cap - skip just this
            # one entry, not the rest of the batch, so a later entry in
            # the same call that updates an address already in the
            # table (explicitly exempt from the cap, same condition
            # above) still gets applied.
            dropped_at_cap += 1
            continue
        state["symbols"][addr] = name
        loaded += 1
    state["sorted_addrs"] = sorted(state["symbols"].keys())

    result = {
        "loaded": loaded,
        "total_symbols": len(state["symbols"]),
    }
    if not used_json:
        total_lines = sum(1 for line in text.splitlines() if line.strip())
        result["skipped_lines"] = max(0, total_lines - len(parsed))
    if dropped_at_cap:
        result["dropped_at_cap"] = dropped_at_cap
    return [types.TextContent(type="text", text=json.dumps(result))]


def _status(state):
    return [types.TextContent(type="text", text=json.dumps({
        "total_symbols": len(state["symbols"]),
    }))]
