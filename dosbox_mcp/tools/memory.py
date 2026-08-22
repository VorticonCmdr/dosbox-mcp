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
        read_only=True,
        schema={
            "type": "object",
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
        read_only=False,
        schema={
            "type": "object",
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


def register_search(server, client, add_tool, feature=None):
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
        read_only=True,
        schema={
            "type": "object",
            "properties": {
                "start": {
                    "type": "integer",
                    "description": "Start of search range (physical address).",
                },
                "end": {
                    "type": "integer",
                    "description": "End of search range (exclusive).",
                },
                "value": {
                    "type": "integer",
                    "description": "Value to search for.",
                },
                "width": {
                    "type": "integer",
                    "description": "Width in bytes: 1, 2, or 4 (default 1).",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max matches to return (1-4096, default 256).",
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
        read_only=True,
        schema={
            "type": "object",
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
                    "description": "Start of scan range (physical address).",
                },
                "end": {
                    "type": "integer",
                    "description": "End of scan range (exclusive).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max matches to return (1-4096, default 256).",
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
            "block. Shows the full conventional memory layout."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _dos_memory_map(client),
        feature=feature,
    )


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
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


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
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _dos_memory_map(client):
    import mcp.types as types
    result = client.get("/api/v1/dos/internals")
    mem_map = result.get("memoryMap", [])
    return [types.TextContent(type="text", text=json.dumps(mem_map, indent=2))]
