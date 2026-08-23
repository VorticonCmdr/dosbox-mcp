# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json
from urllib.parse import quote, urlencode

# Mirrors the engine's own constants (src/webserver/input.h), surfaced
# at runtime via capabilities.input.limits - duplicated here because
# schemas are built once at server startup, before any connection (and
# possibly with none ever made - see bridge_start), so there is no live
# capabilities response to read from at registration time. Keep in sync
# by hand; drift would show up as the bridge accepting/rejecting a
# request the engine disagrees with.
MAX_TYPED_TEXT_CHARS = 4096
MIN_TYPING_CPS = 0.1
MAX_TYPING_CPS = 1000
MAX_INPUT_EVENTS = 32000
MAX_RECORDING_NAME_LENGTH = 64
MAX_EVENT_TIME_MS = 24 * 60 * 60 * 1000  # 24 hours
MAX_EVENT_FRAME = 1_000_000_000
# RecordingStore::IsValidName's exact rule (also enforced server-side,
# this is belt-and-suspenders so a bad name never round-trips at all).
_RECORDING_NAME_PATTERN = "^[A-Za-z0-9_-]+$"

# One oneOf branch per event type, matching the engine's own per-type
# field allow-list exactly (src/webserver/input.cpp's allowed_fields) -
# an unknown field, or a field that belongs to a different event type
# (the classic x_rel/x typo), fails schema validation before the
# request goes out, instead of silently dispatching a zero-motion event
# the engine would otherwise accept-and-ignore.
_COMMON_EVENT_PROPS = {
    "t": {
        "type": "number",
        "minimum": 0,
        "maximum": MAX_EVENT_TIME_MS,
        "description": (
            "Absolute position on the sequence timeline in ms "
            "(recording format). Mutually exclusive with 'delay_ms'."
        ),
    },
    "delay_ms": {
        "type": "number",
        "minimum": 0,
        "maximum": MAX_EVENT_TIME_MS,
        "description": (
            "Wait this many ms after the previous event before firing "
            "(relative timing; the natural choice for hand-written "
            "sequences). Mutually exclusive with 't'."
        ),
    },
    "frame": {
        "type": "integer",
        "minimum": 0,
        "maximum": MAX_EVENT_FRAME,
        "description": (
            "Fire on this rendered frame number instead of by elapsed "
            "time. A 'frame' field on any event in the chain makes the "
            "whole chain dispatch through the frame-timed engine "
            "instead of the wall-clock-timed one - see this tool's own "
            "description."
        ),
    },
}

_TYPE_SPECIFIC_PROPS = {
    "key": {
        "key": {
            "type": "string",
            "description": "KBD_* key name, e.g. KBD_enter, KBD_up, KBD_kp8.",
        },
        "pressed": {
            "type": "boolean",
            "description": "Press (true) or release (false). Default true.",
        },
    },
    "mouse_move": {
        "x_rel": {
            "type": "number",
            "description": "Horizontal mouse delta in pixels, positive is right.",
        },
        "y_rel": {
            "type": "number",
            "description": "Vertical mouse delta in pixels, positive is down.",
        },
        # The engine accepts and round-trips these (a record_stop
        # response's mouse_move events always carry them) but ignores
        # them for actual dispatch - only x_rel/y_rel move the cursor.
        # Accepted here so feeding a recorded 'events' array straight
        # back into this tool doesn't fail schema validation; not
        # documented as a way to position the cursor (see this tool's
        # own "no absolute positioning" note).
        "x_abs": {
            "type": "number",
            "description": "Recorded absolute X - accepted, ignored for dispatch.",
        },
        "y_abs": {
            "type": "number",
            "description": "Recorded absolute Y - accepted, ignored for dispatch.",
        },
    },
    "mouse_button": {
        "button": {
            "type": "string",
            "enum": ["left", "right", "middle"],
            "description": "Mouse button.",
        },
        "pressed": {
            "type": "boolean",
            "description": "Press (true) or release (false). Default true.",
        },
    },
    "mouse_wheel": {
        "delta": {
            "type": "number",
            "description": "Wheel movement.",
        },
    },
}


def _event_branch(type_name, *, type_required):
    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": [type_name],
                "description": (
                    "Event kind. Defaults to 'key' if omitted."
                    if type_name == "key" else "Event kind."
                ),
            },
            **_COMMON_EVENT_PROPS,
            **_TYPE_SPECIFIC_PROPS[type_name],
        },
        "additionalProperties": False,
        **({"required": ["type"]} if type_required else {}),
    }


# 'key' is the only type omission defaults to (matching the engine's
# jev.value("type", "key")), so it's the only branch where 'type' isn't
# required - the other three must name themselves explicitly, which is
# also what stops an event from accidentally satisfying more than one
# branch (oneOf requires exactly one match).
_EVENT_ONE_OF = [
    _event_branch("key", type_required=False),
    _event_branch("mouse_move", type_required=True),
    _event_branch("mouse_button", type_required=True),
    _event_branch("mouse_wheel", type_required=True),
]


def register(server, client, add_tool, feature=None):
    add_tool(
        name="input_type",
        description=(
            "Type a string on the DOS keyboard. Characters are injected "
            "with pacing so the 8-slot i8042 buffer never overflows. "
            "Supports printable ASCII and common symbols (US layout)."
        ),
        risk="mutate_guest",
        title="Type Text",
        interact_ok=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {
                    "type": "string",
                    "maxLength": MAX_TYPED_TEXT_CHARS,
                    "description": f"Text to type (max {MAX_TYPED_TEXT_CHARS} chars).",
                },
                "cps": {
                    "type": "number",
                    "minimum": MIN_TYPING_CPS,
                    "maximum": MAX_TYPING_CPS,
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
        risk="mutate_guest",
        title="Press Key",
        interact_ok=True,
        schema={
            "type": "object",
            "additionalProperties": False,
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
        risk="mutate_guest",
        title="Inject Input Sequence",
        interact_ok=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "recording": {
                    "type": "string",
                    "maxLength": MAX_RECORDING_NAME_LENGTH,
                    "pattern": _RECORDING_NAME_PATTERN,
                    "description": (
                        "Name of a stored recording to replay instead "
                        "of 'events' - see recordings_list."
                    ),
                },
                "events": {
                    "type": "array",
                    "maxItems": MAX_INPUT_EVENTS,
                    "description": (
                        "Input events, dispatched in order on one timeline."
                    ),
                    "items": {"oneOf": _EVENT_ONE_OF},
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
        risk="read",
        title="Replay Status",
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
        risk="mutate_guest",
        title="Cancel Replay",
        interact_ok=True,
        idempotent=True,
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
        risk="mutate_guest",
        title="Start Recording Input",
        interact_ok=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _record_start(client),
        feature=feature,
    )

    add_tool(
        name="record_pause",
        description="Toggle pause on the running recording. Refused with 409 if nothing is recording.",
        risk="mutate_guest",
        title="Pause/Resume Recording",
        interact_ok=True,
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
        risk="mutate_guest",
        title="Stop Recording Input",
        interact_ok=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {
                    "type": "string",
                    "maxLength": MAX_RECORDING_NAME_LENGTH,
                    "pattern": _RECORDING_NAME_PATTERN,
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
        risk="read",
        title="Recording Status",
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
        risk="read",
        title="List Recordings",
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
        risk="mutate_guest",
        title="Delete Recording",
        interact_ok=True,
        idempotent=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {
                    "type": "string",
                    "maxLength": MAX_RECORDING_NAME_LENGTH,
                    "pattern": _RECORDING_NAME_PATTERN,
                },
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
