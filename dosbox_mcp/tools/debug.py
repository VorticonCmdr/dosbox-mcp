# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json


def register(server, client, add_tool, feature=None):
    add_tool(
        name="debug_status",
        description="Debugger state: whether execution is currently paused.",
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _status(client),
        feature=feature,
    )

    add_tool(
        name="debug_pause",
        description="Pause emulation at the current instruction.",
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _pause(client),
        feature=feature,
    )

    add_tool(
        name="debug_continue",
        description=(
            "Resume emulation from the current pause point. If breakpoints "
            "were added while paused, they arm now and execution runs until "
            "one is hit or it is paused again."
        ),
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _continue(client),
        feature=feature,
    )

    add_tool(
        name="debug_step",
        description="Execute one instruction and pause again.",
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _step(client),
        feature=feature,
    )

    add_tool(
        name="debug_wait",
        description=(
            "Block until the debugger stops again (a breakpoint hit, "
            "another pause, or a step), or timeout_ms elapses - one call "
            "instead of polling debug_status. Pass since_stop_id from a "
            "prior debug_status/debug_pause/debug_step/debug_continue "
            "response (continue's is 'resumed_from_stop_id') so a stop "
            "that already happened isn't missed. Returns {satisfied, "
            "debugging, stop_id, reason, registers, linear_eip, "
            "protected_mode, core, breakpoint, code_bytes}: reason is "
            "'paused', 'breakpoint', 'step', or 'never_stopped' (the "
            "debugger hasn't paused even once since the engine started); "
            "code_bytes is 16 base64 bytes at CS:EIP. satisfied is false "
            "on a genuine timeout - stop_id and the rest still describe "
            "the latest known stop."
        ),
        read_only=True,
        schema={
            "type": "object",
            "properties": {
                "since_stop_id": {
                    "type": "integer",
                    "description": "Wait for a stop newer than this. Omit to wait for any stop since server start.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Max wait, milliseconds (1-15000, default 5000).",
                },
            },
        },
        handler=lambda args: _wait(client, args),
        feature=feature,
    )

    add_tool(
        name="debug_breakpoint_add",
        description=(
            "Add a breakpoint. Takes effect on the next debug_continue "
            "(breakpoints are activated when execution resumes, not when "
            "added). Three kinds:\n"
            "- execute: stop before the instruction at segment:offset runs.\n"
            "- interrupt: stop when the given INT is raised, optionally "
            "matching AH (and AL). E.g. int=0x21, ah=0x3d catches every "
            "DOS file-open call. Omit ah/al to match any value.\n"
            "- memory: stop on execution reaching segment:offset (as "
            "classified by the engine; distinct from a plain execute "
            "breakpoint internally, same effect for stepping purposes)."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["execute", "interrupt", "memory"],
                    "description": "Breakpoint kind.",
                },
                "segment": {
                    "type": "integer",
                    "description": "Segment for 'execute'/'memory' breakpoints (0x0000..0xFFFF).",
                },
                "offset": {
                    "type": "integer",
                    "description": "Offset for 'execute'/'memory' breakpoints.",
                },
                "int": {
                    "type": "integer",
                    "description": "Interrupt number for 'interrupt' breakpoints (0x00..0xFF), e.g. 0x21 for DOS API calls.",
                },
                "ah": {
                    "type": "integer",
                    "description": "AH value to match for 'interrupt' breakpoints (0x00..0xFF). Omit to match any AH.",
                },
                "al": {
                    "type": "integer",
                    "description": "AL value to match for 'interrupt' breakpoints (0x00..0xFF). Omit to match any AL. Only meaningful with 'ah' also set.",
                },
            },
            "required": ["type"],
        },
        handler=lambda args: _breakpoint_add(client, args),
        feature=feature,
    )

    add_tool(
        name="debug_breakpoint_list",
        description=(
            "List all breakpoints. Each entry's 'index' is its position in "
            "this list, not a stable id -- it shifts whenever a breakpoint "
            "is added or removed. Re-list before deleting by index."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _breakpoint_list(client),
        feature=feature,
    )

    add_tool(
        name="debug_breakpoint_delete",
        description=(
            "Remove a breakpoint by its current index (see "
            "debug_breakpoint_list), or omit index to clear all."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "Index to remove. Omit to clear all breakpoints.",
                },
            },
        },
        handler=lambda args: _breakpoint_delete(client, args),
        feature=feature,
    )


def _status(client):
    import mcp.types as types
    result = client.get("/api/v1/debug/status")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _pause(client):
    import mcp.types as types
    result = client.post("/api/v1/debug/pause")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _continue(client):
    import mcp.types as types
    result = client.post("/api/v1/debug/continue")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _step(client):
    import mcp.types as types
    result = client.post("/api/v1/debug/step")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _wait(client, args):
    import mcp.types as types
    timeout_ms = args.get("timeout_ms", 5000)
    # httpx's timeout needs slack over the server-side deadline so the
    # engine's own timeout fires first, not the transport's.
    result = client.get("/api/v1/debug/wait", params=args,
                        timeout=(timeout_ms / 1000.0) + 5.0)
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _breakpoint_add(client, args):
    import mcp.types as types
    result = client.post("/api/v1/debug/breakpoints", json=args)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _breakpoint_list(client):
    import mcp.types as types
    result = client.get("/api/v1/debug/breakpoints")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _breakpoint_delete(client, args):
    import mcp.types as types
    if "index" in args:
        result = client.delete("/api/v1/debug/breakpoints",
                               json={"index": args["index"]})
    else:
        result = client.delete("/api/v1/debug/breakpoints")
    return [types.TextContent(type="text", text=json.dumps(result))]
