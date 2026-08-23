# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json


def register(server, client, add_tool, feature=None):
    add_tool(
        name="port_read",
        description=(
            "Read an x86 I/O port. Width is 1 (byte) or 2 (word). "
            "Use for VGA registers, sound cards, HGC control ports."
        ),
        risk="read",
        title="Read I/O Port",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
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
            "required": ["port"],
        },
        handler=lambda args: _port_read(client, args),
        feature=feature,
    )

    add_tool(
        name="port_write",
        description=(
            "Write to an x86 I/O port. Width is 1 (byte) or 2 (word). "
            "For Mode X unchaining, Hercules graphics, hardware config."
        ),
        risk="mutate_guest",
        title="Write I/O Port",
        # Deliberately not marked idempotent: unlike a plain memory or
        # register set, many I/O ports are index/data pairs or trigger
        # latches where writing the same value twice has a real,
        # port-specific side effect (that's the point of a port, not a
        # memory cell) - see this tool's own Mode X unchaining example.
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "port": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 0xFFFF,
                    "description": "I/O port address (0x0000..0xFFFF).",
                },
                "value": {
                    "type": "integer",
                    "description": "Value to write.",
                },
                "width": {
                    "type": "integer",
                    "enum": [1, 2],
                    "description": "Width: 1 (byte) or 2 (word). Default 1.",
                    "default": 1,
                },
            },
            "required": ["port", "value"],
        },
        handler=lambda args: _port_write(client, args),
        feature=feature,
    )


def _port_read(client, args):
    import mcp.types as types
    params = {"port": args["port"], "width": args.get("width", 1)}
    result = client.get("/api/v1/io/port", params=params)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _port_write(client, args):
    import mcp.types as types
    body = {
        "port": args["port"],
        "value": args["value"],
        "width": args.get("width", 1),
    }
    result = client.put("/api/v1/io/port", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]
