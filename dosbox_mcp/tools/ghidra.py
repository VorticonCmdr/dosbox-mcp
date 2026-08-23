# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#
# Address translation between a Ghidra static analysis address space and
# live DOSBox segment:offset addresses. Almost all of this is pure
# client-side arithmetic that never talks to the engine and has no
# protocol dependency - debug_map_auto is the one exception, using
# mem_scan (2.9b) and dos_memory_map to derive a mapping automatically
# instead of the caller pausing and reading cpu_read_registers by hand.
# It exists to support the common real-mode workflow: analyze a program
# in Ghidra, then set breakpoints or interpret a paused CPU state
# against the exact same addresses.
#
# The model: a list of *ranges*, each `{label, ghidra_start, ghidra_end,
# delta, live_segment}`, resolved by containment on the Ghidra side.
# Multiple ranges let multi-segment .EXE programs work (one range per
# segment) - a Ghidra address outside every range's [ghidra_start,
# ghidra_end) yields "no range covers this" rather than a wrong number.
#
# `delta` is exact for .COM programs (CS is constant for the whole
# program) and for small, single-segment .EXE programs; it breaks down
# across multiple code segments or unresolved relocations, which is
# exactly what multiple ranges are for.
#
# Don't expect the raw numbers to already match before anchoring: a
# Ghidra real-mode (x86:LE:16:Real Mode) import of a .COM file can't be
# rebased to the conventional segment:0x0100 layout - Ghidra requires a
# segmented image base to have a zero segment offset, so it typically
# lands at 0000:0000 instead, 0x100 below every live address. That
# constant offset is exactly what anchoring absorbs; the two address
# spaces are not expected to agree on numbering on their own.
#
# Only `delta` and `label` are ever persisted to disk. `live_segment`
# depends on what DOS happens to have resident this boot and is never
# trustworthy across a restart, so a range loaded from disk starts with
# live_segment unset ("no live segment yet this session") until the
# caller re-anchors it - via debug_map_set_base or debug_map_auto -
# rather than silently answering translations with a stale segment from
# a previous run.

import json
import logging
import os
import tempfile

import mcp.types as types

from ..config import default_ghidra_map_path

log = logging.getLogger(__name__)

MaxLabelLength = 64

# The convention debug_map_auto uses to pick a segment for a .COM: DOS
# loads a .COM's PSP and code into the same segment, code starting at
# offset 0x100 (paragraph 0x10) into it. Representing the code's own
# segment as pspSegment+0x10 instead of pspSegment makes that live
# offset equal the file's own byte offset (Ghidra's own numbering for a
# .COM import), so live_offset == ghidra_address at the file's first
# byte and delta comes out clean rather than carrying the constant 0x100
# the module docstring above describes.
ComCodeSegPara = 0x10


def register(server, client, add_tool, feature=None):
    """Registers the four pure client-side tools (set_base, to_live,
    to_ghidra, status) - no engine call, no connection needed, safe in
    every capability mode (see server.py's _LOCAL_ONLY_GROUPS). Returns
    the shared range-list state for register_auto to extend, since
    debug_map_auto reads and writes the exact same ranges these do."""
    state = {"ranges": _load_ranges()}

    add_tool(
        name="debug_map_set_base",
        description=(
            "Anchor a Ghidra <-> live address mapping range at one known "
            "correspondence point: the Ghidra static address of an "
            "instruction and the live segment:offset of that same "
            "instruction (get the latter from cpu_read_registers - cs "
            "and eip - while paused on it), plus the [ghidra_start, "
            "ghidra_end) span this anchor covers and a label for it. "
            "Re-anchoring with the same label replaces that range (its "
            "live segment after a restart, or a bad anchor); a new "
            "label adds another range, so a multi-segment .EXE can have "
            "one range per segment. Exact for .COM programs and "
            "single-segment .EXE programs; see debug_map_to_ghidra for "
            "what happens outside every range's bounds. See also "
            "debug_map_auto, which derives segment and delta from a "
            "byte signature instead of a manual pause."
        ),
        risk="mutate_host",
        title="Anchor Ghidra Mapping",
        idempotent=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "ghidra_address": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Static address of the anchor instruction in Ghidra.",
                },
                "live_segment": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 0xFFFF,
                    "description": "Segment register (usually cs) at the anchor instruction.",
                },
                "live_offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 0xFFFF,
                    "description": "Offset (usually eip) at the anchor instruction, in that segment.",
                },
                "ghidra_start": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Start (inclusive) of the Ghidra address span this range covers.",
                },
                "ghidra_end": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "End (exclusive) of the Ghidra address span this range covers.",
                },
                "label": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MaxLabelLength,
                    "description": (
                        "Name for this range, e.g. a segment or module "
                        "name. Re-using a label replaces that range."
                    ),
                },
            },
            "required": ["ghidra_address", "live_segment", "live_offset",
                         "ghidra_start", "ghidra_end", "label"],
        },
        handler=lambda args: _set_base(state, args),
        feature=None,
        needs_connection=False,
    )

    add_tool(
        name="debug_map_to_live",
        description=(
            "Translate a Ghidra static address to a live segment:offset, "
            "using whichever anchored range covers it. Handy for turning "
            "a function address found in Ghidra into an execute "
            "breakpoint's segment/offset."
        ),
        risk="read",
        title="Ghidra Address to Live",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "ghidra_address": {"type": "integer", "minimum": 0},
            },
            "required": ["ghidra_address"],
        },
        handler=lambda args: _to_live(state, args),
        feature=None,
        needs_connection=False,
    )

    add_tool(
        name="debug_map_to_ghidra",
        description=(
            "Translate a live segment:offset to a Ghidra static address, "
            "using whichever anchored range's live segment matches and "
            "whose Ghidra span the translated address falls into. Handy "
            "for looking up what function a breakpoint hit inside, or "
            "what a paused cs:eip corresponds to in the decompilation. "
            "Refuses (rather than guessing) when no range covers it."
        ),
        risk="read",
        title="Live Address to Ghidra",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "live_segment": {"type": "integer", "minimum": 0, "maximum": 0xFFFF},
                "live_offset": {"type": "integer", "minimum": 0, "maximum": 0xFFFF},
            },
            "required": ["live_segment", "live_offset"],
        },
        handler=lambda args: _to_ghidra(state, args),
        feature=None,
        needs_connection=False,
    )

    add_tool(
        name="debug_map_status",
        description=(
            "List every anchored Ghidra <-> live address mapping range. "
            "A range whose 'live_segment' is null was loaded from disk "
            "from a previous session and needs debug_map_set_base or "
            "debug_map_auto to re-anchor it before it can translate "
            "anything - its live segment isn't safe to assume across a "
            "restart even though its delta and label survive."
        ),
        risk="read",
        title="Ghidra Mapping Status",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _status(state),
        feature=None,
        needs_connection=False,
    )

    return state


def register_auto(server, client, add_tool, state, feature=None):
    """Registers debug_map_auto, the one tool in this module that talks
    to the engine (mem_scan, dos_memory_map) - takes the state register()
    already built, since it reads and writes the exact same ranges.
    Deliberately NOT grouped with register()'s tools in server.py: unlike
    those, its mutation is a side effect of live engine reads across up
    to 640 KB of guest memory, not pure local bookkeeping, so it follows
    the normal mode gate (full mode) rather than bypassing it - see
    server.py's _LOCAL_ONLY_GROUPS comment."""
    add_tool(
        name="debug_map_auto",
        description=(
            "Anchor a Ghidra <-> live address mapping range automatically: "
            "give a byte signature (mem_scan pattern syntax) and the "
            "Ghidra address it starts at, and this finds it in live "
            "memory (mem_scan, 2.9b) and derives the live segment from "
            "the DOS MCB chain (dos_memory_map) - no manual pausing or "
            "cpu_read_registers needed. Only correct for a .COM-style "
            "program, where DOS loads the PSP and code into the same "
            "segment: the derived segment is that program's PSP segment "
            "plus 0x10 paragraphs (where its code starts). Refuses "
            "rather than guessing when the signature isn't found, is "
            "ambiguous (matches more than once in the scanned range), "
            "its match address isn't inside any block the MCB chain "
            "walk reached (the walk caps at 1000 blocks and truncates "
            "silently past that), or the derived segment/offset don't "
            "fit the .COM convention this tool assumes (e.g. a PSP "
            "segment high enough that +0x10 paragraphs overflows past "
            "0xFFFF) - none of these guess a plausible-looking wrong "
            "answer, they all refuse outright. Same range/label "
            "semantics as debug_map_set_base - re-using a label "
            "replaces that range. Requires full capability mode: unlike "
            "the other debug_map_* tools, this one reads live engine "
            "memory, not just local bookkeeping."
        ),
        risk="mutate_guest",
        title="Auto-Anchor Ghidra Mapping",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Byte signature to search for, mem_scan syntax "
                        "(space-separated hex bytes, '?' or '??' for a "
                        "wildcard byte)."
                    ),
                },
                "ghidra_address": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Static address in Ghidra where 'pattern' starts.",
                },
                "ghidra_start": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Start (inclusive) of the Ghidra address span this range covers.",
                },
                "ghidra_end": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "End (exclusive) of the Ghidra address span this range covers.",
                },
                "label": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MaxLabelLength,
                    "description": (
                        "Name for this range, e.g. a segment or module "
                        "name. Re-using a label replaces that range."
                    ),
                },
                "scan_start": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Start of the live memory range to search (default 0).",
                },
                "scan_end": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "End (exclusive) of the live memory range to "
                        "search (default 0xA0000, conventional memory)."
                    ),
                },
            },
            "required": ["pattern", "ghidra_address", "ghidra_start",
                         "ghidra_end", "label"],
        },
        handler=lambda args: _auto(client, state, args),
        feature=feature,
    )


def _error(message):
    return [types.TextContent(type="text", text=json.dumps({"error": message}))]


def _validate_u16(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or not (0 <= value <= 0xFFFF):
        return f"{name} must be 0x0000..0xFFFF, got {value!r}"
    return None


def _validate_seg_off(segment, offset):
    return _validate_u16(segment, "live_segment") or _validate_u16(offset, "live_offset")


def _validate_range_bounds(ghidra_start, ghidra_end, ghidra_address, label):
    if not isinstance(label, str) or not label or len(label) > MaxLabelLength:
        return f"label must be a non-empty string of at most {MaxLabelLength} characters"
    if ghidra_start >= ghidra_end:
        return "ghidra_start must be less than ghidra_end"
    if not (ghidra_start <= ghidra_address < ghidra_end):
        return (
            f"ghidra_address {ghidra_address:#x} is outside this range's "
            f"own span [{ghidra_start:#x}, {ghidra_end:#x}) - the anchor "
            "must lie inside the range it anchors"
        )
    return None


def _check_no_overlap(state, ghidra_start, ghidra_end, label):
    # _find_covering_range returns the first range (insertion order)
    # that covers a given address - silently ambiguous, and exactly the
    # "wrong number instead of an explicit refusal" failure mode this
    # item's multi-range model exists to eliminate, if two ranges were
    # ever allowed to cover the same ghidra address. Enforced here,
    # once, at the only two places a range's span is ever set - not
    # re-checked at lookup time, so a hand-edited persisted file could
    # still smuggle an overlap in; that file isn't attacker-facing input
    # the way this handler's arguments are.
    for r in state["ranges"]:
        if r["label"] == label:
            continue
        if ghidra_start < r["ghidra_end"] and r["ghidra_start"] < ghidra_end:
            return (
                f"[{ghidra_start:#x}, {ghidra_end:#x}) overlaps range "
                f"'{r['label']}' [{r['ghidra_start']:#x}, "
                f"{r['ghidra_end']:#x}) - a ghidra address in the "
                "overlap would translate ambiguously"
            )
    return None


def _upsert_range(state, entry):
    for i, existing in enumerate(state["ranges"]):
        if existing["label"] == entry["label"]:
            state["ranges"][i] = entry
            break
    else:
        state["ranges"].append(entry)
    _save_ranges(state["ranges"])


def _find_covering_range(state, ghidra_address):
    for r in state["ranges"]:
        if r["ghidra_start"] <= ghidra_address < r["ghidra_end"]:
            return r
    return None


def _set_base(state, args):
    ghidra_address = args["ghidra_address"]
    live_segment = args["live_segment"]
    live_offset = args["live_offset"]
    ghidra_start = args["ghidra_start"]
    ghidra_end = args["ghidra_end"]
    label = args["label"]

    err = _validate_seg_off(live_segment, live_offset)
    if err:
        return _error(err)
    err = _validate_range_bounds(ghidra_start, ghidra_end, ghidra_address, label)
    if err:
        return _error(err)
    err = _check_no_overlap(state, ghidra_start, ghidra_end, label)
    if err:
        return _error(err)

    entry = {
        "label": label,
        "ghidra_start": ghidra_start,
        "ghidra_end": ghidra_end,
        "delta": ghidra_address - live_offset,
        "live_segment": live_segment,
    }
    _upsert_range(state, entry)
    return [types.TextContent(type="text", text=json.dumps(entry))]


def _auto(client, state, args):
    pattern = args["pattern"]
    ghidra_address = args["ghidra_address"]
    ghidra_start = args["ghidra_start"]
    ghidra_end = args["ghidra_end"]
    label = args["label"]
    scan_start = args.get("scan_start", 0)
    scan_end = args.get("scan_end", 0xA0000)

    err = _validate_range_bounds(ghidra_start, ghidra_end, ghidra_address, label)
    if err:
        return _error(err)
    err = _check_no_overlap(state, ghidra_start, ghidra_end, label)
    if err:
        return _error(err)

    scan = client.post("/api/v1/memory/scan", json={
        "pattern": pattern, "start": scan_start, "end": scan_end, "limit": 2,
    })
    total = scan.get("total", 0)
    if total == 0:
        return _error(
            f"signature not found in the scanned range "
            f"[{scan_start:#x}, {scan_end:#x})"
        )
    if total > 1:
        return _error(
            f"signature is ambiguous: {total} matches found in the "
            f"scanned range [{scan_start:#x}, {scan_end:#x}) - it isn't "
            "selective enough to anchor a mapping on"
        )
    matched_addr = scan["matches"][0]

    internals = client.get("/api/v1/dos/internals")
    owning_block = None
    for block in internals.get("memoryMap", []):
        block_start = (block["segment"] + 1) * 16
        block_end = block_start + block["sizeBytes"]
        if block_start <= matched_addr < block_end:
            owning_block = block
            break
    if owning_block is None:
        return _error(
            f"matched address {matched_addr:#x} isn't inside any block "
            "in the MCB chain - cannot derive a PSP segment for it. The "
            "chain walk caps at 1000 blocks and truncates silently past "
            "that, so a deep chain may simply not have reached the "
            "owning block."
        )
    # A .COM's PSP and code share one block, self-owned: the block's own
    # data segment (segment+1) equals the pspSegment it's tagged with.
    # DOS_Execute tags OTHER blocks it allocates for the same program
    # (e.g. the environment block) with that same pspSegment too, and
    # never clears a freed block's owner byte, so pspSegment alone
    # doesn't tell "this is the program's own code" apart from "this
    # happens to carry that PSP's number for an unrelated reason" - a
    # signature match inside one of those would silently derive a
    # segment that has nothing to do with where the matched bytes
    # actually live. This also rejects a free block for free: a free
    # block's pspSegment is 0 (MCB_FREE), which segment+1 can never
    # equal for a real segment value.
    if owning_block["segment"] + 1 != owning_block["pspSegment"]:
        return _error(
            f"matched address {matched_addr:#x} is inside a block owned "
            f"by PSP {owning_block['pspSegment']:#x} but not that PSP's "
            "own code block (its segment isn't self-owned - likely an "
            "environment block, or free memory still carrying a stale "
            "owner tag) - cannot derive a .COM code segment from it"
        )

    live_segment = owning_block["pspSegment"] + ComCodeSegPara
    err = _validate_u16(live_segment, "derived live_segment")
    if err:
        return _error(
            f"{err} - pspSegment {owning_block['pspSegment']:#x} is too "
            "high for the .COM convention this tool assumes"
        )
    live_offset = matched_addr - live_segment * 16
    err = _validate_u16(live_offset, "derived live_offset")
    if err:
        return _error(
            f"{err} - the matched address doesn't fit the .COM "
            "convention this tool assumes (PSP segment plus 0x10 "
            "paragraphs); use debug_map_set_base with a "
            "manually-confirmed anchor instead"
        )

    entry = {
        "label": label,
        "ghidra_start": ghidra_start,
        "ghidra_end": ghidra_end,
        "delta": ghidra_address - live_offset,
        "live_segment": live_segment,
    }
    _upsert_range(state, entry)
    result = dict(entry)
    result["matched_addr"] = matched_addr
    result["psp_segment"] = owning_block["pspSegment"]
    return [types.TextContent(type="text", text=json.dumps(result))]


def _to_live(state, args):
    ghidra_address = args["ghidra_address"]
    r = _find_covering_range(state, ghidra_address)
    if r is None:
        return _error(f"no range covers ghidra address {ghidra_address:#x}")
    if r["live_segment"] is None:
        return _error(
            f"range '{r['label']}' has no live segment yet this session "
            "- call debug_map_set_base or debug_map_auto to re-anchor it"
        )
    offset = ghidra_address - r["delta"]
    if not (0 <= offset <= 0xFFFF):
        return _error(
            f"translated offset {offset:#x} is outside 0x0000..0xFFFF "
            f"for range '{r['label']}' - its delta may be stale or wrong"
        )
    result = {
        "segment": r["live_segment"],
        "offset": offset,
        "linear": r["live_segment"] * 16 + offset,
        "label": r["label"],
    }
    return [types.TextContent(type="text", text=json.dumps(result))]


def find_ghidra_address(state, live_segment, live_offset):
    """The pure lookup _to_ghidra wraps: (ghidra_address, label) for
    whichever anchored range's live_segment matches and whose span the
    translated address falls into, or (None, None) if none does or the
    inputs aren't valid 0x0000..0xFFFF values. Never raises and never
    builds an error message - shared with symbols.py's best-effort
    address annotation, which has nothing useful to say about *why* an
    address didn't resolve and would rather skip it than fail the
    response it's annotating."""
    if _validate_seg_off(live_segment, live_offset):
        return None, None
    for r in state["ranges"]:
        if r["live_segment"] != live_segment:
            continue
        candidate = live_offset + r["delta"]
        if r["ghidra_start"] <= candidate < r["ghidra_end"]:
            return candidate, r["label"]
    return None, None


def _to_ghidra(state, args):
    live_segment = args["live_segment"]
    live_offset = args["live_offset"]
    err = _validate_seg_off(live_segment, live_offset)
    if err:
        return _error(err)

    ghidra_address, label = find_ghidra_address(state, live_segment, live_offset)
    if ghidra_address is None:
        return _error(
            f"no anchored range covers live {live_segment:#06x}:"
            f"{live_offset:#06x} - translating it would silently produce a "
            "wrong Ghidra address"
        )
    return [types.TextContent(type="text", text=json.dumps({
        "ghidra_address": ghidra_address, "label": label,
    }))]


def _status(state):
    return [types.TextContent(type="text", text=json.dumps({"ranges": state["ranges"]}))]


def _persisted_fields(entry):
    return {
        "label": entry["label"],
        "ghidra_start": entry["ghidra_start"],
        "ghidra_end": entry["ghidra_end"],
        "delta": entry["delta"],
    }


def _load_ranges():
    path = default_ghidra_map_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("top-level JSON must be a list")
    except (OSError, ValueError) as e:
        log.warning("ignoring unreadable ghidra map at %s: %s", path, e)
        return []

    ranges = []
    seen_labels = set()
    for i, entry in enumerate(raw):
        try:
            label = entry["label"]
            if label in seen_labels:
                raise ValueError(f"duplicate label {label!r}")
            ranges.append({
                "label": label,
                "ghidra_start": entry["ghidra_start"],
                "ghidra_end": entry["ghidra_end"],
                "delta": entry["delta"],
                "live_segment": None,
            })
            seen_labels.add(label)
        except (KeyError, TypeError, ValueError) as e:
            # One bad entry (hand-edited, or corrupted by an
            # interrupted write) must not cost every other,
            # perfectly-good persisted range - skip just this one.
            log.warning("skipping malformed entry %d in ghidra map at %s: %s",
                       i, path, e)
    return ranges


def _save_ranges(ranges):
    path = default_ghidra_map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps([_persisted_fields(r) for r in ranges], indent=2)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     delete=False) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
    if path.exists():
        os.chmod(tmp.name, path.stat().st_mode)
    os.replace(tmp.name, path)
