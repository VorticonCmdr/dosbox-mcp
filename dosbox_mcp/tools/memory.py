# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import base64
import json

from ..client import DosboxError
from ..connection import to_error_result

# Bridge-chosen, deliberately below the engine's real 128 MiB cap - large
# enough for a real read, small enough that even base64 of it stays a
# sane size in an agent's transcript. Matches what mem_read's own
# description has always claimed, now actually enforced.
MAX_LENGTH_BYTES = 65536
# Rendered views (anything but base64) cost far more per byte than
# base64 - a 64 KiB hex dump is ~4000 lines, a bigger bomb than the
# base64 it replaces - so they get their own, much tighter cap.
MAX_RENDERED_VIEW_BYTES = 4096
DEFAULT_LENGTH = 256

_VIEWS = ("base64", "hex", "bytes", "words", "dwords", "text")
_SEGMENT_REGISTERS = ("cs", "ds", "es", "fs", "gs", "ss")
# Base64 is 4 chars per 3 bytes, rounded up, plus up to 2 padding chars -
# a generous schema-level ceiling on the encoded string length, checked
# fast by the MCP SDK before the handler even runs. The handler itself
# decodes and checks the real byte count against MAX_LENGTH_BYTES, which
# is the authoritative bound (mem_write has no analogous check to
# mem_read's length cap otherwise, despite writing the same class of
# oversized payload into the bridge process and onto the wire).
_MAX_WRITE_DATA_CHARS = -(-MAX_LENGTH_BYTES // 3) * 4

# Mirrors the engine's own DefaultSearchLimit/MaxSearchLimit
# (src/webserver/private/memory.h), shared by mem_search/mem_scan/
# mem_diff's 'limit' field - duplicated here for the same reason as
# input.py's constants: schemas are built once at startup, before any
# live capabilities response exists to read the real numbers from.
DEFAULT_SEARCH_LIMIT = 256
MAX_SEARCH_LIMIT = 4096


def register(server, client, add_tool, feature=None):
    add_tool(
        name="mem_read",
        description=(
            "Read bytes from guest memory. Use 'segment' for real-mode-"
            "style addressing - a register name (cs/ds/es/fs/gs/ss, "
            "resolved live on the emulation thread at read time) or a "
            "fixed paragraph value 0x0000..0xFFFF (resolved once, up "
            "front) - with 'offset' as the segment-relative offset; "
            "omit 'segment' for a plain linear physical offset. 'view' "
            "picks how the bytes come back: 'base64' (default, for "
            "bulk/binary data), 'hex' (an offset/hex/ASCII dump), "
            "'bytes'/'words'/'dwords' (little-endian integer arrays), "
            "or 'text' (CP437-decoded, DOS's native character set). "
            f"Rendered views cap at {MAX_RENDERED_VIEW_BYTES} bytes - "
            "they cost far more per byte than base64; use 'base64' for "
            "a larger read. 'include_registers' (default false) adds "
            "the engine's full register snapshot to the response - this "
            "is filtering on the bridge side, not an engine-side "
            "optimization, since the engine loads them unconditionally "
            "either way."
        ),
        risk="read",
        title="Read Memory",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "segment": {
                    "type": ["string", "integer"],
                    "description": (
                        "Register name (cs/ds/es/fs/gs/ss, case-"
                        "insensitive) or a paragraph value 0x0000.."
                        "0xFFFF. Omit for a plain linear offset."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Offset - segment-relative if 'segment' is "
                        "given, otherwise a linear physical offset."
                    ),
                    "minimum": 0,
                    "maximum": 0xFFFFFFFF,
                },
                "length": {
                    "type": "integer",
                    "description": (
                        f"Bytes to read (1-{MAX_LENGTH_BYTES}, default "
                        f"{DEFAULT_LENGTH}; rendered views cap at "
                        f"{MAX_RENDERED_VIEW_BYTES})."
                    ),
                    "minimum": 1,
                    "maximum": MAX_LENGTH_BYTES,
                },
                "view": {
                    "type": "string",
                    "enum": list(_VIEWS),
                    "description": "How to render the bytes (default 'base64').",
                },
                "include_registers": {
                    "type": "boolean",
                    "description": "Include the full CPU register snapshot (default false).",
                },
            },
            "required": ["offset"],
        },
        handler=lambda args: _mem_read(client, args),
        feature=feature,
    )

    add_tool(
        name="mem_write",
        description=(
            "Write bytes to guest memory. 'segment'/'offset' work like "
            "mem_read (segment optional; a register name resolves live, "
            "a numeric paragraph value is fixed at request time; 'data' "
            f"decodes to at most {MAX_LENGTH_BYTES} bytes, matching "
            "mem_read's own read cap). 'expected' (base64) makes this a "
            "compare-and-swap: the write only happens if the bytes "
            "currently at the address exactly match 'expected' first - "
            "safe against something else changing the value between an "
            "earlier read and this write. On success the response is "
            "{status: 'ok', addr: <int>}; on a mismatch it's {conflict: "
            "true, addr: <int>, actual_data: <base64>} (not an error) "
            "with the real current bytes, so a caller can re-read and "
            "retry instead of guessing or blindly clobbering. Omit "
            "'expected' for an unconditional write."
        ),
        risk="mutate_guest",
        title="Write Memory",
        idempotent=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "segment": {
                    "type": ["string", "integer"],
                    "description": (
                        "Register name (cs/ds/es/fs/gs/ss, case-"
                        "insensitive) or a paragraph value 0x0000.."
                        "0xFFFF. Omit for a plain linear offset."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Offset - segment-relative if 'segment' is "
                        "given, otherwise a linear physical offset."
                    ),
                    "minimum": 0,
                    "maximum": 0xFFFFFFFF,
                },
                "data": {
                    "type": "string",
                    "description": f"Base64-encoded data to write (decodes to at most {MAX_LENGTH_BYTES} bytes).",
                    "maxLength": _MAX_WRITE_DATA_CHARS,
                },
                "expected": {
                    "type": "string",
                    "description": (
                        "Base64-encoded bytes the address must "
                        "currently hold for the write to happen "
                        "(compare-and-swap). Omit for an unconditional "
                        "write."
                    ),
                },
            },
            "required": ["offset", "data"],
        },
        handler=lambda args: _mem_write(client, args),
        feature=feature,
    )


def _resolve_segment(value):
    """Validate a segment (register name or 0..0xFFFF) before it's
    interpolated into a URL path - a bad value here should be a clear,
    immediate error, not a malformed request or a confusing engine 400."""
    if isinstance(value, str):
        if value.lower() in _SEGMENT_REGISTERS:
            return value.lower()
        try:
            value = int(value, 0)
        except ValueError:
            raise ValueError(
                f"segment must be a register name "
                f"({'/'.join(_SEGMENT_REGISTERS)}) or an integer "
                f"0x0000..0xFFFF, got {value!r}"
            ) from None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"segment must be a string or integer, got {value!r}")
    if not (0 <= value <= 0xFFFF):
        raise ValueError(f"segment must be 0x0000..0xFFFF, got {value!r}")
    return str(value)


def _resolve_offset(value):
    """Validate an offset before it's interpolated into a URL path - the
    same reasoning as _resolve_segment: a bad value here should be a
    clear, immediate bridge-side error, not a confusing engine 400 for a
    malformed path segment (a float, a negative number, or a value
    outside the engine's uint32_t offset range)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"offset must be an integer, got {value!r}")
    if not (0 <= value <= 0xFFFFFFFF):
        raise ValueError(f"offset must be 0x00000000..0xFFFFFFFF, got {value!r}")
    return value


def _mem_path(offset, segment=None, length=None):
    offset = _resolve_offset(offset)
    prefix = f"{_resolve_segment(segment)}/" if segment is not None else ""
    if length is None:
        return f"/api/v1/memory/{prefix}{offset}"
    return f"/api/v1/memory/{prefix}{offset}/{length}"


def _validate_read_args(length, view):
    if view not in _VIEWS:
        raise ValueError(f"view must be one of {list(_VIEWS)}, got {view!r}")
    if not isinstance(length, int) or isinstance(length, bool) or length < 1:
        raise ValueError(f"length must be a positive integer, got {length!r}")
    if length > MAX_LENGTH_BYTES:
        raise ValueError(f"length must be 1..{MAX_LENGTH_BYTES}, got {length}")
    if view != "base64" and length > MAX_RENDERED_VIEW_BYTES:
        raise ValueError(
            f"length must be 1..{MAX_RENDERED_VIEW_BYTES} for view={view!r} "
            "(rendered views cost far more per byte than base64) - use "
            "view='base64' for a larger read"
        )
    if view == "words" and length % 2:
        raise ValueError("length must be a multiple of 2 for view='words'")
    if view == "dwords" and length % 4:
        raise ValueError("length must be a multiple of 4 for view='dwords'")


def _validate_write_data(data):
    """mem_read's length has a client-side cap enforced before any
    request goes out; mem_write's data - the same class of payload,
    just flowing the other direction - previously had none at all, so a
    caller could make the bridge build, encode and transmit an
    arbitrarily large body before the engine's own (much larger, 128
    MiB) cap ever got a chance to reject it. Also rejects malformed
    base64 here, with a clear message, instead of letting it reach the
    engine as one."""
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as e:
        raise ValueError(f"data must be valid base64: {e}") from e
    if len(raw) > MAX_LENGTH_BYTES:
        raise ValueError(
            f"data must decode to at most {MAX_LENGTH_BYTES} bytes, "
            f"got {len(raw)}"
        )


def _render_hex(raw):
    lines = []
    for i in range(0, len(raw), 16):
        chunk = raw[i:i + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        # CP437 decodes every byte 0-255 to some glyph, but control
        # characters still render as odd/invisible ones - substitute the
        # classic hex-dump dot for anything outside printable ASCII.
        text_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{i:04x}  {hex_part:<47}  {text_part}")
    return "\n".join(lines)


def _render_view(raw, view):
    if view == "bytes":
        return {"bytes": list(raw)}
    if view == "words":
        return {"words": [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]}
    if view == "dwords":
        return {"dwords": [int.from_bytes(raw[i:i + 4], "little")
                           for i in range(0, len(raw), 4)]}
    if view == "text":
        return {"text": raw.decode("cp437")}
    return {"hex": _render_hex(raw)}


def _mem_read(client, args):
    import mcp.types as types

    offset = args["offset"]
    segment = args.get("segment")
    length = args.get("length", DEFAULT_LENGTH)
    view = args.get("view", "base64")
    include_registers = bool(args.get("include_registers", False))

    try:
        path = _mem_path(offset, segment, length)
        _validate_read_args(length, view)
    except ValueError as e:
        return to_error_result(str(e), tool="mem_read", code="invalid_argument")

    result = client.get(path, headers={"accept": "application/json"})

    out = {"addr": result["memory"]["addr"]}
    if view == "base64":
        out["data"] = result["memory"]["data"]
    else:
        raw = base64.b64decode(result["memory"]["data"])
        if len(raw) != length:
            # Only reachable against a non-conforming or compromised
            # connection target - the shipped engine always returns
            # exactly the requested length. Reject explicitly rather
            # than let a size-mismatched response reach _render_view,
            # whose words/dwords branches index past the end of a
            # shorter-than-expected buffer.
            return to_error_result(
                f"engine returned {len(raw)} bytes, expected {length} - "
                "refusing to render a size-mismatched response",
                tool="mem_read", code="unexpected_response",
            )
        out.update(_render_view(raw, view))
    if include_registers:
        out["registers"] = result.get("registers", {})

    return [types.TextContent(type="text", text=json.dumps(out, indent=2))]


def _mem_write(client, args):
    import mcp.types as types

    offset = args["offset"]
    segment = args.get("segment")
    data = args["data"]
    expected = args.get("expected")

    try:
        path = _mem_path(offset, segment)
        _validate_write_data(data)
    except ValueError as e:
        return to_error_result(str(e), tool="mem_write", code="invalid_argument")

    kwargs = {"json": {"data": data}}
    if expected is not None:
        kwargs["headers"] = {"If-Match": expected}

    try:
        result = client.put(path, **kwargs)
    except DosboxError as e:
        if e.status == 412:
            # A CAS conflict is an expected, actionable outcome for a
            # caller doing a compare-and-swap loop, not an error -
            # surfaced as normal data (matching debug_wait's timeout
            # convention), with the conflicting bytes pulled straight
            # out of the exception's parsed body instead of left
            # stringified inside the message (the 412 body has no
            # top-level 'error' key for _handle's usual fallback to
            # find). e.body is untrusted response data (could come from
            # a proxy, gateway, or a future engine version) - checked
            # for the shape actually expected rather than trusted.
            memory = e.body.get("memory")
            if (not isinstance(memory, dict)
                    or "addr" not in memory or "data" not in memory):
                return to_error_result(
                    "engine returned a 412 conflict with an unexpected "
                    f"body shape: {e.body!r}",
                    tool="mem_write", code="unexpected_response",
                )
            out = {
                "conflict": True,
                "addr": memory["addr"],
                "actual_data": memory["data"],
            }
            return [types.TextContent(type="text", text=json.dumps(out, indent=2))]
        raise

    # Flattened the same way the conflict shape above is, rather than
    # the engine's raw {"memory": {"addr": ...}} passthrough - the two
    # outcomes of this same tool call otherwise put 'addr' in different
    # places depending on which one happened.
    out = {"status": "ok", "addr": result.get("memory", {}).get("addr")}
    return [types.TextContent(type="text", text=json.dumps(out))]


def register_search(server, client, add_tool, feature=None, annotate=None):
    """annotate, when given, is symbols.make_annotator's bound
    (live_segment, live_offset) -> symbol-or-None function (2.17),
    threaded through to dos_memory_map only - mem_scan's addresses are
    physical, not segment:offset, and have no single segment to
    translate them through the way a disassembly batch does."""
    add_tool(
        name="mem_search",
        description=(
            "Scan a range of guest memory for a value. Width is 1 (byte), "
            "2 (word), or 4 (dword), little-endian. Returns 'matches' "
            "(up to 'limit' physical addresses), 'total' (the real match "
            "count, which can exceed what's returned) and 'truncated' "
            "(whether it did) - a common byte value over a large range "
            "can match far more times than are useful to see at once, so "
            "check 'truncated' rather than assume 'matches' is complete."
        ),
        risk="read",
        title="Search Memory",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "start": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Start of search range (physical address).",
                },
                "end": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "End of search range (exclusive).",
                },
                "value": {
                    "type": "integer",
                    "description": "Value to search for.",
                },
                "width": {
                    "type": "integer",
                    "enum": [1, 2, 4],
                    "description": "Width in bytes: 1, 2, or 4 (default 1).",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                    "description": f"Max matches to return (1-{MAX_SEARCH_LIMIT}, default {DEFAULT_SEARCH_LIMIT}).",
                },
            },
            "required": ["start", "end", "value"],
        },
        handler=lambda args: _mem_search(client, args),
        feature=feature,
    )

    add_tool(
        name="mem_scan",
        description=(
            "Scan a range of guest memory for a masked byte signature, "
            "e.g. Ghidra's copyable byte string '8B 46 ?? 50 E8' - space-"
            "separated hex-pair bytes and '??' wildcards, 1-256 tokens, "
            "at least one fixed byte. The engine rejects a pattern that "
            "isn't specific enough for the requested range (too many "
            "wildcards would make the scan match almost everywhere) and "
            "one that's too specific for a large range (would risk "
            "exceeding the scan time budget) - narrow the range or "
            "adjust the fixed-byte count if it does. Returns 'matches' "
            "(up to 'limit' physical addresses), 'total' (the real match "
            "count, which can exceed what's returned) and 'truncated' "
            "(whether it did). If an execute breakpoint is active inside "
            "the range, the scan reads through its 0xCC trap byte to the "
            "real instruction underneath - a plain mem_read over the "
            "same address would see the trap."
        ),
        risk="read",
        title="Scan Memory Pattern",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Space-separated hex-pair bytes and '??' "
                        "wildcards, e.g. '8B 46 ?? 50 E8'."
                    ),
                },
                "start": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Start of scan range (physical address).",
                },
                "end": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "End of scan range (exclusive).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                    "description": f"Max matches to return (1-{MAX_SEARCH_LIMIT}, default {DEFAULT_SEARCH_LIMIT}).",
                },
            },
            "required": ["pattern", "start", "end"],
        },
        handler=lambda args: _mem_scan(client, args),
        feature=feature,
    )

    add_tool(
        name="dos_memory_map",
        description=(
            "Walk the DOS MCB chain and report which PSP owns which memory "
            "block. 'blocks' is the conventional memory layout; each "
            "block gets a 'symbol' field when a loaded symbol covers its "
            "segment (debug_symbols_load) - omitted otherwise. "
            "'free_bytes'/'largest_free_bytes' summarize the free blocks "
            "so a caller doesn't have to walk 'blocks' just to answer "
            "'is there room'. 'truncated' is true if the walk was cut "
            "short (corrupt chain, or a 1000-block cap) - 'blocks' is "
            "then incomplete and free_bytes/largest_free_bytes may be an "
            "undercount, the same caveat mem_allocations documents for "
            "its own analogous fields. Set detail:true to also get "
            "'list_of_lists', 'dos_swappable_area', and 'first_shell' - "
            "raw physical addresses this route reads but drops by "
            "default, since nothing else here interprets them; each is "
            "readable with mem_read (omit 'segment' - these are already "
            "linear offsets)."
        ),
        risk="read",
        title="DOS Memory Map",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "detail": {
                    "type": "boolean",
                    "description": (
                        "Also return list_of_lists/dos_swappable_area/"
                        "first_shell (default false)."
                    ),
                },
            },
        },
        handler=lambda args: _dos_memory_map(client, args, annotate),
        feature=feature,
    )


def register_snapshot(server, client, add_tool, feature=None):
    add_tool(
        name="mem_snapshot",
        description=(
            "Capture a range of guest memory (up to 16 MB) so it can be "
            "compared against later with mem_diff - the first step of "
            "the snapshot-and-refine workflow for finding an unknown "
            "value's address (e.g. a game's health or gold counter): "
            "snapshot, change the value in-game, mem_diff with "
            "op='changed' (or 'increased'/'decreased' if you know the "
            "direction) to get a candidate list, change the value "
            "again, mem_diff the same handle again to narrow further. "
            "Returns a 'handle' to pass to mem_diff."
        ),
        # Not "read": this allocates and mutates an entry in the
        # engine's process-wide SnapshotRegistry (up to 32 MiB total,
        # LRU-evicted - a call here can evict another session's
        # entries), which observe mode's "never touch the engine"
        # guarantee is supposed to rule out.
        risk="mutate_guest",
        title="Snapshot Memory",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "start": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Start of the range to capture (physical address).",
                },
                "end": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "End of the range (exclusive).",
                },
            },
            "required": ["start", "end"],
        },
        handler=lambda args: _mem_snapshot(client, args),
        feature=feature,
    )

    add_tool(
        name="mem_diff",
        description=(
            "Compare current guest memory against a mem_snapshot handle "
            "and narrow it to whatever survives. The first call on a "
            "handle compares the whole captured range against fresh "
            "memory; every call after that only re-checks the "
            "addresses that survived the previous call, so repeated "
            "calls on the same handle progressively refine toward the "
            "value's real address - each call re-baselines: 'increased' "
            "means increased since the *previous* mem_diff call, not "
            "since the original snapshot. 'op' is 'changed', "
            "'unchanged' (or its synonym 'equals'), 'increased', or "
            "'decreased'. 'width' (1, 2, or 4 bytes, default 1) is only "
            "settable on a handle's first mem_diff call - it locks in "
            "for every later refine call on that handle. Returns "
            "'matches' (up to 'limit' {addr, value} pairs), 'total' "
            "(the true number of survivors this round), 'truncated' "
            "(whether 'matches' is incomplete), and 'candidates' (how "
            "many addresses are being tracked for the next refine call "
            "- can be less than 'total' if there were more than 65536 "
            "true survivors, in which case the ones kept are arbitrary "
            "and may not include the address you're actually looking "
            "for - treat a heavily-truncated first round as a sign to "
            "narrow the op or range and take a new snapshot, not as "
            "something to keep refining). A handle that narrows to zero "
            "candidates is gone; the next mem_diff on it fails."
        ),
        # Not "read" either: mutates the snapshot entry in place
        # (narrows its candidate set, bumps its generation counter) and
        # can delete it outright once nothing survives - see
        # mem_snapshot's own comment.
        risk="mutate_guest",
        title="Diff Memory Snapshot",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "handle": {
                    "type": "integer",
                    "description": "Handle returned by mem_snapshot.",
                },
                "op": {
                    "type": "string",
                    "enum": ["changed", "unchanged", "increased",
                             "decreased", "equals"],
                    "description": "How to compare each candidate's value to what was recorded for it.",
                },
                "width": {
                    "type": "integer",
                    "enum": [1, 2, 4],
                    "description": (
                        "Comparison width in bytes (default 1). Only "
                        "meaningful on a handle's first mem_diff call - "
                        "it locks in from then on."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                    "description": f"Max matches to return (1-{MAX_SEARCH_LIMIT}, default {DEFAULT_SEARCH_LIMIT}).",
                },
            },
            "required": ["handle", "op"],
        },
        handler=lambda args: _mem_diff(client, args),
        feature=feature,
    )


def register_allocation(server, client, add_tool, feature=None):
    add_tool(
        name="mem_alloc",
        description=(
            "Allocate a block of guest memory through the DOS/XMS "
            "allocator and return its physical address. 'area' is "
            "'CONV' (conventional, default), 'UMA' (upper memory), or "
            "'XMS' (extended, via the page allocator - only supports "
            "'BEST_FIT'). Refuses (503) rather than guessing when no "
            "free block is large enough, or when this API's own "
            "allocation registry is already full - free some "
            "allocations first (see mem_allocations). Pair with "
            "mem_free: an address this returns must be freed through "
            "mem_free, not assumed reclaimed automatically. Free it "
            "before the DOS program that's active right now exits - "
            "DOS reclaims a program's memory when it exits and can "
            "hand it to something else, and mem_free refuses rather "
            "than freeing a block whose owner has since changed."
        ),
        risk="mutate_guest",
        title="Allocate Memory",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "Bytes to allocate (1-65535).",
                },
                "area": {
                    "type": "string",
                    "enum": ["CONV", "UMA", "XMS"],
                    "description": "Defaults to CONV.",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["BEST_FIT", "FIRST_FIT", "LAST_FIT"],
                    "description": "Defaults to BEST_FIT. XMS only supports BEST_FIT.",
                },
            },
            "required": ["size"],
        },
        handler=lambda args: _mem_alloc(client, args),
        feature=feature,
    )

    add_tool(
        name="mem_free",
        description=(
            "Free a block previously returned by mem_alloc. Refuses "
            "(400) an address this API never allocated, one already "
            "freed, or one it never got back a success for - never a "
            "guess at what might be at that address. Also refuses if "
            "the block's owner has changed since it was allocated: "
            "DOS reclaims a conventional/UMA block when the program it "
            "belonged to exits, and can hand that same memory to a "
            "different, currently-running program - freeing it at that "
            "point would corrupt that program's memory. Free a block "
            "before the program active at mem_alloc time exits; this "
            "is not a heap independent of DOS process lifetime."
        ),
        risk="mutate_guest",
        title="Free Memory",
        idempotent=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "addr": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 0xFFFFFFFF,
                    "description": "Physical address returned by mem_alloc.",
                },
            },
            "required": ["addr"],
        },
        handler=lambda args: _mem_free(client, args),
        feature=feature,
    )

    add_tool(
        name="mem_allocations",
        description=(
            "List every block this API has allocated and not yet "
            "freed, plus free-memory totals: 'conventionalFreeBytes' "
            "(total free conventional memory) and "
            "'conventionalLargestBlockBytes' (the single largest free "
            "block - what actually bounds the next mem_alloc call, "
            "since a request bigger than this fails even when the "
            "total free bytes would suggest otherwise), 'umbFreeBytes', "
            "and 'xmsFreeBytes'. 'conventionalTruncated'/'umbTruncated' "
            "are true if the underlying MCB chain walk was cut short "
            "(corrupt chain, or a 1000-block cap) - the free-byte totals "
            "may then be an undercount."
        ),
        risk="read",
        title="List Allocations",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _mem_allocations(client),
        feature=feature,
    )


def _mem_alloc(client, args):
    import mcp.types as types
    body = {"size": args["size"]}
    if "area" in args:
        body["area"] = args["area"]
    if "strategy" in args:
        body["strategy"] = args["strategy"]
    result = client.post("/api/v1/memory/allocate", json=body)
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _mem_free(client, args):
    import mcp.types as types
    body = {"addr": args["addr"]}
    client.post("/api/v1/memory/free", json=body)
    return [types.TextContent(type="text", text=json.dumps({"status": "ok"}))]


def _mem_allocations(client):
    import mcp.types as types
    result = client.get("/api/v1/memory/allocations")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _mem_snapshot(client, args):
    import mcp.types as types
    body = {"start": args["start"], "end": args["end"]}
    result = client.post("/api/v1/memory/snapshot", json=body)
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _mem_diff(client, args):
    import mcp.types as types
    body = {"handle": args["handle"], "op": args["op"]}
    if "width" in args:
        body["width"] = args["width"]
    if "limit" in args:
        body["limit"] = args["limit"]
    result = client.post("/api/v1/memory/diff", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _mem_search(client, args):
    import mcp.types as types
    body = {
        "start": args["start"],
        "end": args["end"],
        "value": args["value"],
        "width": args.get("width", 1),
    }
    if "limit" in args:
        body["limit"] = args["limit"]
    result = client.post("/api/v1/memory/search", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _mem_scan(client, args):
    import mcp.types as types
    body = {
        "pattern": args["pattern"],
        "start": args["start"],
        "end": args["end"],
    }
    if "limit" in args:
        body["limit"] = args["limit"]
    result = client.post("/api/v1/memory/scan", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _dos_memory_map(client, args, annotate=None):
    import mcp.types as types
    result = client.get("/api/v1/dos/internals")
    mem_map = result.get("memoryMap", [])
    if annotate:
        for block in mem_map:
            # block["segment"] is the MCB header paragraph, not a
            # segment any running program's CS/DS ever holds - the
            # block's own owned/addressable memory starts one
            # paragraph later (same convention dos.cpp's
            # FreeMemoryCommand and this bridge's own ghidra.py already
            # rely on: block_start = (segment + 1) * 16).
            symbol = annotate(block["segment"] + 1, 0)
            if symbol is not None:
                block["symbol"] = symbol

    # MCB_FREE (dos.h) - the same convention dos.cpp's own
    # MemoryAllocationsCommand uses to total up free conventional memory.
    free_blocks = [b["sizeBytes"] for b in mem_map if b.get("pspSegment") == 0]

    out = {
        "block_count": len(mem_map),
        "truncated": result.get("memoryMapTruncated", False),
        "free_bytes": sum(free_blocks),
        "largest_free_bytes": max(free_blocks, default=0),
        "blocks": mem_map,
    }
    if args.get("detail"):
        out["list_of_lists"] = result.get("listOfLists")
        out["dos_swappable_area"] = result.get("dosSwappableArea")
        out["first_shell"] = result.get("firstShell")

    return [types.TextContent(type="text", text=json.dumps(out))]
