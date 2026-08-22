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
        description=(
            "Execute one instruction (or count, up to 64) and pause again. "
            "Only breakpoints already armed by a prior debug_continue are "
            "honored mid-burst with count > 1 - one added since is invisible "
            "until the next continue - and IRQs are serviced only after the "
            "whole burst, not between each instruction, so a large count "
            "isn't equivalent to that many separate debug_step calls."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Instructions to execute (1-64, default 1).",
                },
            },
        },
        handler=lambda args: _step(client, args),
        feature=feature,
    )

    add_tool(
        name="debug_step_over",
        description=(
            "Step over the current instruction: if it's a call/int/loop/rep, "
            "run past it in one call instead of single-stepping through "
            "everything it does, by planting a one-shot breakpoint right "
            "after it and resuming. Like debug_continue, the actual stop "
            "happens arbitrarily later (poll debug_wait with the returned "
            "resumed_from_stop_id) - stepped_over:true means that's what "
            "happened. If the current instruction isn't one of those kinds "
            "(or the emulator wasn't paused), it falls back to a plain step "
            "instead: stepped_over:false, stepped:true, and stop already "
            "has the new record synchronously (or stepped:false if nothing "
            "was paused to begin with)."
        ),
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _step_over(client),
        feature=feature,
    )

    add_tool(
        name="debug_run_to",
        description=(
            "Run until execution reaches segment:offset, by planting a "
            "one-shot breakpoint there and resuming - like debug_continue, "
            "the actual stop happens arbitrarily later (poll debug_wait "
            "with the returned resumed_from_stop_id)."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "segment": {
                    "type": "integer",
                    "description": "Target segment (0x0000..0xFFFF).",
                },
                "offset": {
                    "type": "integer",
                    "description": "Target offset.",
                },
            },
            "required": ["segment", "offset"],
        },
        handler=lambda args: _run_to(client, args),
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
            "breakpoint internally, same effect for stepping purposes).\n"
            "ignore_count and condition make a hot breakpoint usable: skip "
            "the first N hits (ignore_count), or only actually stop when a "
            "register or memory value compares a certain way (condition). "
            "A skipped hit still counts in hit_count (debug_breakpoint_list) "
            "- it just doesn't stop the emulator. This costs a full "
            "stop/resume cycle per skipped hit, so a condition on a very "
            "hot address is slow, not free. Neither can combine with "
            "once=true (a once-only breakpoint self-deletes on its first "
            "match, before any condition/ignore_count could apply). Adding "
            "a breakpoint at a location that already has one is refused "
            "(409) whenever either carries an ignore_count or condition - "
            "only the first match at a location is ever evaluated, so a "
            "second breakpoint there would silently never get its own "
            "checked; delete the existing one first if you meant to "
            "replace it."
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
                "once": {
                    "type": "boolean",
                    "description": "Remove this breakpoint automatically the first time it fires (default false).",
                },
                "ignore_count": {
                    "type": "integer",
                    "description": "Skip this many genuine hits before actually stopping (default 0). Cannot combine with once=true.",
                },
                "condition": {
                    "type": "object",
                    "description": (
                        "Only actually stop when this holds (default: always stop on a "
                        "genuine hit). Exactly one of 'register' or "
                        "'segment'/'offset'/'width' - a register comparison or a "
                        "memory-operand comparison. Both forms compare 'value' with "
                        "'op', unsigned. Cannot combine with once=true."
                    ),
                    "properties": {
                        "register": {
                            "type": "string",
                            "description": (
                                "Register to compare: eax/ebx/ecx/edx/esi/edi/esp/ebp, "
                                "ax/bx/cx/dx/si/di/sp/bp, al/bl/cl/dl/ah/bh/ch/dh, or "
                                "cs/ds/es/ss/fs/gs. Mutually exclusive with "
                                "segment/offset/width."
                            ),
                        },
                        "segment": {
                            "type": "integer",
                            "description": "Segment for a memory-operand condition.",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Offset for a memory-operand condition.",
                        },
                        "width": {
                            "type": "integer",
                            "description": "Bytes to read for a memory-operand condition: 1, 2, or 4.",
                        },
                        "op": {
                            "type": "string",
                            "enum": ["eq", "ne", "lt", "le", "gt", "ge"],
                            "description": "Comparison operator.",
                        },
                        "value": {
                            "type": "integer",
                            "description": "Value to compare the register/memory operand against.",
                        },
                    },
                    "required": ["op", "value"],
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
            "List all breakpoints. Each entry's 'id' is a stable identifier "
            "(never reused or renumbered) - prefer it for debug_breakpoint_delete. "
            "'index' is only its current position in this list, which shifts "
            "whenever any breakpoint is added or removed. 'hit_count' counts "
            "every genuine match at this breakpoint's location, including "
            "ones skipped by 'ignore_count' or a false 'condition' - it does "
            "not mean the emulator actually stopped that many times."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _breakpoint_list(client),
        feature=feature,
    )

    add_tool(
        name="debug_breakpoint_delete",
        description=(
            "Remove a breakpoint by id (stable, see debug_breakpoint_list) "
            "or by its current index. Specify exactly one of the two, or "
            "omit both to clear all breakpoints."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Stable id to remove (see debug_breakpoint_list). Mutually exclusive with 'index'.",
                },
                "index": {
                    "type": "integer",
                    "description": "Current list position to remove. Mutually exclusive with 'id'.",
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


def _step(client, args):
    import mcp.types as types
    result = client.post("/api/v1/debug/step", json=args)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _step_over(client):
    import mcp.types as types
    result = client.post("/api/v1/debug/step_over")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _run_to(client, args):
    import mcp.types as types
    result = client.post("/api/v1/debug/run_to", json=args)
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
    # Pass through whichever of 'id'/'index' the caller sent, rather than
    # picking one - if a confused caller sends both, the engine's own
    # validation rejects it with a clear error instead of one being
    # silently ignored.
    body = {k: args[k] for k in ("id", "index") if k in args}
    if body:
        result = client.delete("/api/v1/debug/breakpoints", json=body)
    else:
        result = client.delete("/api/v1/debug/breakpoints")
    return [types.TextContent(type="text", text=json.dumps(result))]
