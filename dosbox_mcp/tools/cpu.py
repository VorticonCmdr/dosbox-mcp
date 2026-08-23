# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json


def register_state(server, client, add_tool, feature=None):
    add_tool(
        name="cpu_read_registers",
        description=(
            "Read all CPU registers: general (eax..ebp), segment "
            "(cs,ds,es,ss,fs,gs), eip, and flags. Use this to get the "
            "live cs:eip for a paused program, e.g. to anchor a "
            "debug_map_set_base call against a Ghidra static address."
        ),
        risk="read",
        title="Read CPU Registers",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _cpu_state(client),
        feature=feature,
    )


_REGISTERS = ("eax", "ebx", "ecx", "edx", "esi", "edi", "esp", "ebp",
             "cs", "ds", "es", "ss", "fs", "gs")


def register(server, client, add_tool, feature=None):
    add_tool(
        name="cpu_write_register",
        description=(
            "Write a CPU register. General registers (eax..ebp) accept "
            "32-bit values. Segment registers (cs,ds,es,ss,fs,gs) accept "
            "16-bit values and update the cached physical base."
        ),
        risk="mutate_guest",
        title="Write CPU Register",
        idempotent=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "register": {
                    "type": "string",
                    "enum": list(_REGISTERS),
                    "description": "Register name: eax, ebx, ecx, edx, esi, edi, esp, ebp, cs, ds, es, ss, fs, gs.",
                },
                "value": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 0xFFFFFFFF,
                    "description": "Value to write (0..0xFFFFFFFF for general, 0..0xFFFF for segment).",
                },
            },
            "required": ["register", "value"],
        },
        handler=lambda args: _cpu_write(client, args),
        feature=feature,
    )


def _cpu_write(client, args):
    import mcp.types as types
    body = {"register": args["register"], "value": args["value"]}
    result = client.put("/api/v1/cpu/register", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _cpu_state(client):
    import mcp.types as types
    result = client.get("/api/v1/cpu/state")
    return [types.TextContent(type="text", text=json.dumps(result))]
