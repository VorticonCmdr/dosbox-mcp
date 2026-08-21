# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json


def register(server, client, add_tool, feature=None):
    add_tool(
        name="wait_for",
        description=(
            "Block until a condition is true, or timeout_ms elapses - one "
            "call instead of a poll loop. Returns {satisfied, reason, for, "
            "...}; reason is 'matched', 'timeout', or 'emulator_stopped' "
            "(the condition can't progress while paused or stopped in the "
            "debugger). Conditions: text (pattern match against the "
            "screen; ignore_case optional), screen_change (current hash "
            "differs from baseline_hash - reuse the text_hash/frame_hash "
            "from a prior screen_text/screen_info/screen_capture call; "
            "source is 'text' or 'frame', default 'text'), frames (count "
            "frames from now), replay_done, memory (addr/width/value/op, "
            "width is 1/2/4, op is eq/ne/lt/gt/le/ge, default eq), "
            "stopped (debugger builds only), script_done, program "
            "(pattern match, or waits for the program name to change if "
            "pattern is omitted)."
        ),
        read_only=True,
        schema={
            "type": "object",
            "properties": {
                "for": {
                    "type": "string",
                    "enum": [
                        "text", "screen_change", "frames", "replay_done",
                        "memory", "stopped", "script_done", "program",
                    ],
                    "description": "The condition to wait for.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Max wait, milliseconds (1-15000, default 5000).",
                },
                "pattern": {
                    "type": "string",
                    "description": (
                        "Substring to match. Required for 'text'; optional "
                        "for 'program' (omit to wait for the program name "
                        "to change instead)."
                    ),
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive match for 'text' (default false).",
                },
                "baseline_hash": {
                    "type": "string",
                    "description": (
                        "16-hex-char hash to wait for a change from. "
                        "Required for 'screen_change'."
                    ),
                },
                "source": {
                    "type": "string",
                    "enum": ["text", "frame"],
                    "description": "Which hash 'screen_change' compares (default 'text').",
                },
                "count": {
                    "type": "integer",
                    "description": "Frames to wait for, relative to now. Required for 'frames'.",
                },
                "addr": {
                    "type": "integer",
                    "description": "Linear address to poll. Required for 'memory'.",
                },
                "width": {
                    "type": "integer",
                    "enum": [1, 2, 4],
                    "description": "Byte width to read for 'memory' (default 1).",
                },
                "value": {
                    "type": "integer",
                    "description": "Value to compare against. Required for 'memory'.",
                },
                "op": {
                    "type": "string",
                    "enum": ["eq", "ne", "lt", "gt", "le", "ge"],
                    "description": "Comparison for 'memory' (default 'eq').",
                },
            },
            "required": ["for"],
        },
        handler=lambda args: _wait_for(client, args),
    )


def _wait_for(client, args):
    import mcp.types as types

    timeout_ms = args.get("timeout_ms", 5000)
    # httpx's timeout needs slack over the server-side deadline so the
    # engine's own timeout fires first, not the transport's.
    result = client.post("/api/v1/wait", json=args,
                         timeout=(timeout_ms / 1000.0) + 5.0)
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
