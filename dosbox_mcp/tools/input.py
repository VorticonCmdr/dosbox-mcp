# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json
from urllib.parse import quote, urlencode


def register(server, client, add_tool, feature=None):
    add_tool(
        name="input_type",
        description=(
            "Type a string on the DOS keyboard. Characters are injected "
            "with pacing so the 8-slot i8042 buffer never overflows. "
            "Supports printable ASCII and common symbols (US layout)."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to type (max 4096 chars).",
                },
                "cps": {
                    "type": "number",
                    "description": "Characters per second (default 30).",
                },
            },
            "required": ["text"],
        },
        handler=lambda args: _input_type(client, args),
        feature=feature,
    )

    add_tool(
        name="input_key",
        description=(
            "Press or release a single key by its KBD_* name. "
            "Use for special keys (F1-F12, arrows, Escape, etc) that "
            "input_type cannot produce."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Key name, e.g. KBD_enter, KBD_f1, KBD_esc.",
                },
                "pressed": {
                    "type": "boolean",
                    "description": "True for press, false for release.",
                    "default": True,
                },
            },
            "required": ["key"],
        },
        handler=lambda args: _input_key(client, args),
        feature=feature,
    )

    add_tool(
        name="input_sequence",
        description=(
            "Inject a timed sequence of key, mouse, and wheel events, "
            "either inline ('events') or by replaying a stored named "
            "recording ('recording' - see record_stop and "
            "recordings_list). Provide exactly one. Returns immediately "
            "once the chain is armed (events_scheduled), before any of "
            "it has actually dispatched - use replay_status to track "
            "progress and replay_cancel to stop it early. Refused with "
            "a 409 if a chain of the same kind (timed via "
            "'t'/'delay_ms', or frame-relative via a 'frame' field on "
            "any event - a stored recording always replays frame-"
            "relative) is already running, or 404 if 'recording' names "
            "nothing stored. "
            "Mouse movement is RELATIVE (x_rel/y_rel deltas from the "
            "current cursor position); there is no absolute positioning. "
            "To reach a known position, sweep past a screen corner first "
            "(e.g. x_rel:-4000, y_rel:-4000 pins the cursor top-left), "
            "then move by the target offset. A click is a mouse_button "
            "press event followed by a release event. Unknown fields are "
            "rejected with an error naming the allowed ones."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "recording": {
                    "type": "string",
                    "description": (
                        "Name of a stored recording to replay instead "
                        "of 'events' - see recordings_list."
                    ),
                },
                "events": {
                    "type": "array",
                    "description": (
                        "Input events, dispatched in order on one timeline."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "key",
                                    "mouse_move",
                                    "mouse_button",
                                    "mouse_wheel",
                                ],
                                "description": "Event kind (default: key).",
                            },
                            "delay_ms": {
                                "type": "number",
                                "description": (
                                    "Wait this many ms after the previous "
                                    "event before firing (relative timing; "
                                    "the natural choice for hand-written "
                                    "sequences). Mutually exclusive with 't'."
                                ),
                            },
                            "t": {
                                "type": "number",
                                "description": (
                                    "Absolute position on the sequence "
                                    "timeline in ms (recording format). "
                                    "Mutually exclusive with 'delay_ms'."
                                ),
                            },
                            "key": {
                                "type": "string",
                                "description": (
                                    "KBD_* key name (key events), "
                                    "e.g. KBD_enter, KBD_up, KBD_kp8."
                                ),
                            },
                            "pressed": {
                                "type": "boolean",
                                "description": (
                                    "Press (true) or release (false), for "
                                    "key and mouse_button events."
                                ),
                            },
                            "x_rel": {
                                "type": "number",
                                "description": (
                                    "Horizontal mouse delta in pixels, "
                                    "positive is right (mouse_move events)."
                                ),
                            },
                            "y_rel": {
                                "type": "number",
                                "description": (
                                    "Vertical mouse delta in pixels, "
                                    "positive is down (mouse_move events)."
                                ),
                            },
                            "button": {
                                "type": "string",
                                "enum": ["left", "right", "middle"],
                                "description": (
                                    "Mouse button (mouse_button events)."
                                ),
                            },
                            "delta": {
                                "type": "number",
                                "description": (
                                    "Wheel movement (mouse_wheel events)."
                                ),
                            },
                        },
                    },
                },
            },
        },
        handler=lambda args: _input_sequence(client, args),
        feature=feature,
    )

    add_tool(
        name="replay_status",
        description=(
            "Progress of the current (or most recently finished) "
            "input_sequence chain: active, engine (pic/frame/mixed/"
            "none), total, dispatched, remaining, elapsed_ms, "
            "drift_ms, current_frame. Keeps reporting the finished "
            "run's numbers after it stops, is cancelled, or "
            "self-aborts (stuck too long waiting for keyboard buffer "
            "space) - checking status right after is the normal "
            "sequence, not an error."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _replay_status(client),
        feature=feature,
    )

    add_tool(
        name="replay_cancel",
        description=(
            "Cancel the running input_sequence chain, if any - drains "
            "it immediately instead of letting it run to completion. "
            "Safe to call when nothing is running (returns "
            "cancelled:false rather than erroring). Use this instead "
            "of waiting out a sequence armed by mistake."
        ),
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _replay_cancel(client),
        feature=feature,
    )

    add_tool(
        name="record_start",
        description=(
            "Start recording keyboard and mouse input as the agent (or "
            "a human) drives the guest. Consecutive mouse_move samples "
            "landing in the same rendered frame are coalesced into one "
            "event, so a long mouse-driven session doesn't balloon into "
            "tens of thousands of events. Input injected via "
            "input_sequence/input_key/input_type while recording is "
            "NOT captured (a replay never re-records itself)."
        ),
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _record_start(client),
        feature=feature,
    )

    add_tool(
        name="record_pause",
        description="Toggle pause on the running recording. Refused with 409 if nothing is recording.",
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _record_pause(client),
        feature=feature,
    )

    add_tool(
        name="record_stop",
        description=(
            "Stop recording. Pass 'name' to also save it under that "
            "name (<=64 chars, letters/digits/-/_) in the process-"
            "lifetime named recording store - see recordings_list and "
            "input_sequence's 'recording' field to replay it later. An "
            "invalid name is refused (400) without stopping the "
            "recording; a full store (max 20 - see "
            "dosbox_status.info.capabilities.input.limits.max_stored_"
            "recordings) is refused (503, error_code registry_full) the "
            "same way - delete one first. Pass include_events:false to "
            "omit the captured events from the response body, useful "
            "once you're also saving by name and don't need the raw "
            "list. The response's 'truncated' is true if the recording "
            "hit the 32000-event cap and lost the tail."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Save the recording under this name.",
                },
                "include_events": {
                    "type": "boolean",
                    "description": "Set false to omit 'events' from the response (default true).",
                },
            },
        },
        handler=lambda args: _record_stop(client, args),
        feature=feature,
    )

    add_tool(
        name="record_status",
        description=(
            "Whether a recording is active/paused, its event count and "
            "duration so far, and whether it has hit the event cap "
            "(truncated)."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _record_status(client),
        feature=feature,
    )

    add_tool(
        name="recordings_list",
        description=(
            "Metadata for every recording currently in the named "
            "store: name, event_count, duration_ms, truncated. "
            "Process-lifetime and in-memory - nothing here survives a "
            "restart. Use input_sequence {\"recording\": \"<name>\"} to "
            "replay one."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _recordings_list(client),
        feature=feature,
    )

    add_tool(
        name="recording_delete",
        description=(
            "Delete a stored recording by name. 404 if no recording "
            "has that name. Frees a slot in the store (max 20)."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=lambda args: _recording_delete(client, args),
        feature=feature,
    )


def _input_type(client, args):
    import mcp.types as types
    body = {"text": args["text"]}
    if "cps" in args:
        body["cps"] = args["cps"]
    result = client.post("/api/v1/input/type", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _input_key(client, args):
    import mcp.types as types
    pressed = args.get("pressed", True)
    events = [{"type": "key", "key": args["key"], "pressed": pressed}]
    if pressed:
        events.append({"type": "key", "key": args["key"], "pressed": False})
    result = client.post("/api/v1/input/sequence", json={"events": events})
    return [types.TextContent(type="text", text=json.dumps(result))]


def _input_sequence(client, args):
    import mcp.types as types
    body = {}
    if "events" in args:
        body["events"] = args["events"]
    if "recording" in args:
        body["recording"] = args["recording"]
    result = client.post("/api/v1/input/sequence", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _replay_status(client):
    import mcp.types as types
    result = client.get("/api/v1/input/replay/status")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _replay_cancel(client):
    import mcp.types as types
    result = client.delete("/api/v1/input/replay")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _record_start(client):
    import mcp.types as types
    result = client.post("/api/v1/input/record/start")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _record_pause(client):
    import mcp.types as types
    result = client.post("/api/v1/input/record/pause")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _record_stop(client, args):
    import mcp.types as types
    params = {}
    if "name" in args:
        params["name"] = args["name"]
    if args.get("include_events") is False:
        params["include_events"] = "false"
    path = "/api/v1/input/record/stop"
    if params:
        path += "?" + urlencode(params)
    result = client.post(path)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _record_status(client):
    import mcp.types as types
    result = client.get("/api/v1/input/record/status")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _recordings_list(client):
    import mcp.types as types
    result = client.get("/api/v1/input/recordings")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _recording_delete(client, args):
    import mcp.types as types
    result = client.delete(f"/api/v1/input/recordings/{quote(args['name'], safe='')}")
    return [types.TextContent(type="text", text=json.dumps(result))]
