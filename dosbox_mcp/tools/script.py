# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json


def register(server, client, add_tool, feature=None):
    add_tool(
        name="script_run",
        description=(
            "Load and start a Lua script. The script runs sandboxed on the "
            "emulation thread. Reaches DOS memory (read/write), text-mode "
            "screen reads, keyboard/mouse injection (relative-only, no "
            "wheel), video capture start/stop, and drive-mount locking. "
            "It does NOT reach port I/O, CPU registers, the debugger, "
            "memory freeze/search, drive swapping, pixel-level screen "
            "capture, or absolute mouse positioning - use the dedicated "
            "tools for those."
        ),
        risk="mutate_guest",
        title="Run Lua Script",
        interact_ok=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "script": {
                    "type": "string",
                    "description": "Lua source code to execute.",
                },
            },
            "required": ["script"],
        },
        handler=lambda args: _script_run(client, args),
    )

    add_tool(
        name="script_status",
        description=(
            "Check the running script's state and read its output table. "
            "Scripts communicate results through dosbox.output['key'] = value."
        ),
        risk="read",
        title="Script Status",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _script_status(client),
    )

    add_tool(
        name="script_stop",
        description="Stop a running Lua script.",
        risk="mutate_guest",
        title="Stop Script",
        interact_ok=True,
        idempotent=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _script_stop(client),
    )


def _script_run(client, args):
    import mcp.types as types
    # The load endpoint takes the raw Lua as a text/plain body, not JSON
    # (aug-bt7n: the old JSON post 415'd before reaching the loader).
    client.post_text("/api/v1/script/load", args["script"])
    result = client.post("/api/v1/script/start")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _script_status(client):
    import mcp.types as types
    result = client.get("/api/v1/script/status")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _script_stop(client):
    import mcp.types as types
    result = client.post("/api/v1/script/stop")
    return [types.TextContent(type="text", text=json.dumps(result))]
