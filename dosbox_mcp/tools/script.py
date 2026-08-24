# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

# Mirrors the engine's own ScriptValidator::ValidateParams constraints
# (src/lua/script_validator.cpp/.h) - duplicated here for the same
# reason as input.py's MAX_TYPED_TEXT_CHARS etc: schemas are built once
# at server startup, before any connection. Keep in sync by hand; drift
# would show up as the bridge accepting/rejecting a name or seed the
# engine disagrees with.
MAX_SCRIPT_NAME_LENGTH = 64
SCRIPT_NAME_PATTERN = "^[A-Za-z0-9_-]+$"
MIN_SEED = -9223372036854775808
MAX_SEED = 9223372036854775807


def register(server, client, add_tool, feature=None):
    add_tool(
        name="script_load",
        description=(
            "Load a Lua script; starts it immediately unless start:false. "
            "Runs sandboxed on the emulation thread: reaches DOS memory "
            "(read/write), text-mode screen reads, keyboard/mouse "
            "injection (relative-only, no wheel), video capture start/"
            "stop, and drive-mount locking. Does NOT reach port I/O, CPU "
            "registers, the debugger, memory freeze/search, drive "
            "swapping, pixel-level screen capture, or absolute mouse "
            "positioning - use the dedicated tools for those. "
            "name (letters/digits/-/_, <=64 chars, default \"unnamed\") "
            "tags the run; seed fixes math.random()'s sequence; "
            "debug:true writes a timestamped trace log to disk, readable "
            "via script_log even after the script finishes or errors. "
            "Rejected while a script is already running - call "
            "script_stop first. Rate-limited to one load per 2 seconds; "
            "a 429's message names the wait."
        ),
        risk="mutate_guest",
        title="Load Script",
        interact_ok=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "script": {
                    "type": "string",
                    "description": "Lua source code to load.",
                },
                "name": {
                    "type": "string",
                    "maxLength": MAX_SCRIPT_NAME_LENGTH,
                    "pattern": SCRIPT_NAME_PATTERN,
                    "description": (
                        "Tag for this run and its debug log's filename "
                        "(default \"unnamed\")."
                    ),
                },
                "seed": {
                    "type": "integer",
                    "minimum": MIN_SEED,
                    "maximum": MAX_SEED,
                    "description": "Fixes math.random()'s sequence.",
                },
                "debug": {
                    "type": "boolean",
                    "description": (
                        "Write a timestamped trace log, readable via "
                        "script_log."
                    ),
                },
                "start": {
                    "type": "boolean",
                    "default": True,
                    "description": "Start immediately after loading (default true).",
                },
            },
            "required": ["script"],
        },
        handler=lambda args: _script_load(client, args),
    )

    add_tool(
        name="script_start",
        description=(
            "Start the currently loaded script. Only needed after "
            "script_load was called with start:false - by default "
            "script_load already starts it."
        ),
        risk="mutate_guest",
        title="Start Script",
        interact_ok=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _script_start(client),
    )

    add_tool(
        name="script_status",
        description=(
            "Check the running script's state and read its output table. "
            "Scripts communicate results through dosbox.output['key'] = "
            "value. log_path is present when the loaded script requested "
            "a debug log (script_load debug:true) - read its content via "
            "script_log."
        ),
        risk="read",
        title="Script Status",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _script_status(client),
    )

    add_tool(
        name="script_log",
        description=(
            "Tail of the current debug log (up to the last 64 KB), for a "
            "script loaded with debug:true. Refused if the currently "
            "loaded script wasn't - including a reload that failed to "
            "compile, which drops access to whatever was loaded before "
            "it. The log survives after the script finishes or errors, "
            "but loading a fresh script without debug:true drops access "
            "to the previous one. Two distinct not-found cases: the "
            "debug log never opened on the engine side (rare - load "
            "still succeeds without one), or the file became unreadable "
            "on disk after the fact. The last line can be torn if read "
            "while the script is still writing to it - truncated marks "
            "whether the head was cut for length, not this."
        ),
        risk="read",
        title="Script Debug Log",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _script_log(client),
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


def _script_load(client, args):
    import mcp.types as types
    # The load endpoint takes the raw Lua as a text/plain body, not JSON
    # (aug-bt7n: the old JSON post 415'd before reaching the loader);
    # name/seed/debug travel as query params, matching the engine's own
    # ScriptValidator::ValidateParams signature.
    params = {}
    if "name" in args:
        params["name"] = args["name"]
    if "seed" in args:
        # int(), not str(), directly: the schema's type:"integer" also
        # accepts a JSON float with a zero fractional part (5.0), and
        # str(5.0) == "5.0" fails the engine's std::from_chars parse
        # with a confusing "seed is not a valid integer" - int() only
        # ever runs on a value jsonschema already confirmed integral.
        params["seed"] = str(int(args["seed"]))
    if "debug" in args:
        params["debug"] = "true" if args["debug"] else "false"
    load_result = client.post_text("/api/v1/script/load", args["script"],
                                   params=params)
    if args.get("start", True):
        # /script/start's own response has no "name" field - fold the
        # load response's name back in so a caller who set one still
        # sees it confirmed, instead of it silently disappearing behind
        # start's {"status": "started"}.
        result = {**client.post("/api/v1/script/start"),
                 "name": load_result.get("name")}
    else:
        result = load_result
    return [types.TextContent(type="text", text=json.dumps(result))]


def _script_start(client):
    import mcp.types as types
    result = client.post("/api/v1/script/start")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _script_status(client):
    import mcp.types as types
    result = client.get("/api/v1/script/status")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _script_log(client):
    import mcp.types as types
    result = client.get("/api/v1/script/log")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _script_stop(client):
    import mcp.types as types
    result = client.post("/api/v1/script/stop")
    return [types.TextContent(type="text", text=json.dumps(result))]
