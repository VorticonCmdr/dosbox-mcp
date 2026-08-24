# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from .cpu import REGISTERS

# Mirrors the engine's own batch.h constants (src/webserver/private/
# batch.h) - duplicated here for the same reason as every other
# engine-constant mirror in this bridge: schemas are built once at
# startup, before there's a live connection to read the real numbers
# from. Deliberately tighter than the single-op routes' own caps
# (mem_read's own 65536-byte bridge-chosen cap, or the engine's real
# 128 MiB per-op maximum) - a batch runs on a timeout budget scaled by
# op count, not a single large transfer's generous deadline.
MAX_BATCH_OPS = 64
MAX_BATCH_READ_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_BATCH_WRITE_BYTES = 256 * 1024  # 256 KiB

_SEGMENT_PROP = {
    "type": ["string", "integer"],
    "description": (
        "Register name (cs/ds/es/fs/gs/ss, case-insensitive, resolved "
        "live at execution time) or a fixed paragraph value "
        "0x0000..0xFFFF. Omit for a plain linear offset."
    ),
}
_OFFSET_PROP = {
    "type": "integer",
    "minimum": 0,
    "maximum": 0xFFFFFFFF,
    "description": (
        "Segment-relative if 'segment' is given, otherwise a linear "
        "physical offset."
    ),
}


def _op_branch(op_name, extra_props, required_extra):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["op", *required_extra],
        "properties": {
            "op": {"type": "string", "enum": [op_name]},
            **extra_props,
        },
    }


_OP_ONE_OF = [
    _op_branch(
        "mem_read",
        {
            "segment": _SEGMENT_PROP,
            "offset": _OFFSET_PROP,
            "len": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_BATCH_READ_BYTES,
                "description": "Bytes to read.",
            },
        },
        ["offset", "len"],
    ),
    _op_branch(
        "mem_write",
        {
            "segment": _SEGMENT_PROP,
            "offset": _OFFSET_PROP,
            "data": {
                "type": "string",
                "description": "Base64-encoded bytes to write.",
            },
        },
        ["offset", "data"],
    ),
    _op_branch(
        "mem_cas",
        {
            "segment": _SEGMENT_PROP,
            "offset": _OFFSET_PROP,
            "data": {
                "type": "string",
                "description": (
                    "Base64-encoded bytes to write if 'expected' matches."
                ),
            },
            "expected": {
                "type": "string",
                "description": (
                    "Base64-encoded bytes the address must currently "
                    "hold for the write to happen."
                ),
            },
        },
        ["offset", "data", "expected"],
    ),
    _op_branch("cpu_read", {}, []),
    _op_branch(
        "cpu_write",
        {
            "register": {
                "type": "string",
                "enum": list(REGISTERS),
                "description": (
                    "Register name: eax, ebx, ecx, edx, esi, edi, esp, "
                    "ebp, cs, ds, es, ss, fs, gs."
                ),
            },
            "value": {
                "type": "integer",
                "minimum": 0,
                "maximum": 0xFFFFFFFF,
                "description": (
                    "0..0xFFFFFFFF for general, 0..0xFFFF for segment."
                ),
            },
        },
        ["register", "value"],
    ),
    _op_branch(
        "port_read",
        {
            "port": {
                "type": "integer",
                "minimum": 0,
                "maximum": 0xFFFF,
                "description": "I/O port address (0x0000..0xFFFF).",
            },
            "width": {
                "type": "integer",
                "enum": [1, 2],
                "description": "Width: 1 (byte) or 2 (word). Default 1.",
                "default": 1,
            },
        },
        ["port"],
    ),
    _op_branch(
        "port_write",
        {
            "port": {
                "type": "integer",
                "minimum": 0,
                "maximum": 0xFFFF,
                "description": "I/O port address (0x0000..0xFFFF).",
            },
            "value": {"type": "integer", "description": "Value to write."},
            "width": {
                "type": "integer",
                "enum": [1, 2],
                "description": "Width: 1 (byte) or 2 (word). Default 1.",
                "default": 1,
            },
        },
        ["port", "value"],
    ),
    _op_branch(
        "freeze_set",
        {
            "address": {
                "type": "integer",
                "minimum": 0,
                "maximum": 0xFFFFFFFF,
                "description": "Physical memory address to freeze.",
            },
            "value": {
                "type": "integer",
                "description": "Value to hold at the address.",
            },
            "width": {
                "type": "integer",
                "enum": [1, 2, 4],
                "description": "Width in bytes: 1, 2, or 4 (default 1).",
                "default": 1,
            },
        },
        ["address", "value"],
    ),
    _op_branch(
        "freeze_clear",
        {
            "address": {
                "type": "integer",
                "minimum": 0,
                "maximum": 0xFFFFFFFF,
                "description": "Address to unfreeze.",
            },
        },
        ["address"],
    ),
]


def register(server, client, add_tool, feature=None):
    add_tool(
        name="batch_execute",
        description=(
            "Apply 1-64 memory, CPU register, I/O port, and freeze "
            "operations in order, in a single atomic pass on the "
            "emulation thread - no other request can interleave "
            "mid-batch. Use this when a caller needs more than one "
            "operation to land as a unit (a mem_cas lock byte followed "
            "by several dependent writes, a multi-register VGA sequence "
            "that would otherwise cost several separate requests during "
            "which the hardware state has moved on). Each op is one of: "
            "mem_read (segment?, offset, len), mem_write (segment?, "
            "offset, data), mem_cas (segment?, offset, data, expected - "
            "a separate op from mem_write, not an optional field on it, "
            "since there's no per-op HTTP header inside a JSON body to "
            "carry the If-Match concept mem_write's own compare-and-swap "
            "uses), cpu_read (), cpu_write (register, value), port_read "
            "(port, width?), port_write (port, width?, value), "
            "freeze_set (address, value, width?), or freeze_clear "
            "(address). Total mem_read length across the batch is capped "
            f"at {MAX_BATCH_READ_BYTES} bytes, total mem_write/mem_cas "
            f"data+expected at {MAX_BATCH_WRITE_BYTES} bytes - tighter "
            "than the single-op routes' own limits, since a batch runs "
            "on a timeout scaled by op count rather than one large "
            "transfer's generous deadline. This is not a transaction: "
            "there is no rollback. 'on_error' (default 'abort') stops "
            "applying further ops once one fails - a mem_cas conflict, "
            "a full freeze registry, a freeze_clear with nothing to "
            "clear, or (only for a register-relative segment, whose "
            "real address isn't known until execution) an out-of-range "
            "address - the only failure modes reachable once every op "
            "is otherwise fully validated before any op runs; set it to "
            "'continue' to apply every op regardless of earlier "
            "outcomes. The response's 'results' is always exactly as "
            "long as the request's 'ops', index-correlated with it - an "
            "op an abort never reached gets status 'skipped' rather "
            "than being omitted."
        ),
        risk="mutate_guest",
        title="Execute Operation Batch",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "ops": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_BATCH_OPS,
                    "items": {"oneOf": _OP_ONE_OF},
                },
                "on_error": {
                    "type": "string",
                    "enum": ["abort", "continue"],
                    "description": (
                        "'abort' (default) stops at the first failing "
                        "op; 'continue' applies every op regardless."
                    ),
                },
            },
            "required": ["ops"],
        },
        handler=lambda args: _batch_execute(client, args),
        feature=feature,
    )


def _batch_execute(client, args):
    import mcp.types as types

    body = {"ops": args["ops"]}
    if "on_error" in args:
        body["on_error"] = args["on_error"]

    result = client.post("/api/v1/batch", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]
