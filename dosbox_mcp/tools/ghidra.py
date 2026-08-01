# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#
# Address translation between a Ghidra static analysis address space and
# live DOSBox segment:offset addresses. This is pure client-side
# arithmetic - it never talks to the engine and has no protocol
# dependency. It exists to support the common real-mode workflow:
# analyze a program in Ghidra, then set breakpoints or interpret a
# paused CPU state against the exact same addresses.
#
# The model is deliberately narrow: one linear delta between a Ghidra
# address and a live offset, anchored at a single known correspondence
# point (usually the entry point) and assumed to hold for one live
# segment. That is exact for .COM programs (CS is constant for the
# whole program) and for small, single-segment .EXE programs. It breaks
# down across multiple code segments or unresolved relocations -
# debug_map_to_ghidra refuses to guess when asked about a different live
# segment than the one the mapping was anchored to.
#
# Don't expect the raw numbers to already match before anchoring: a
# Ghidra real-mode (x86:LE:16:Real Mode) import of a .COM file can't be
# rebased to the conventional segment:0x0100 layout - Ghidra requires a
# segmented image base to have a zero segment offset, so it typically
# lands at 0000:0000 instead, 0x100 below every live address. That
# constant offset is exactly what the anchor step absorbs; the two
# address spaces are not expected to agree on numbering on their own.

import json


def register(server, client, add_tool, feature=None):
    state = {"base_segment": None, "delta": None, "ghidra_anchor": None}

    add_tool(
        name="debug_map_set_base",
        description=(
            "Anchor the Ghidra <-> live address mapping at one known "
            "correspondence point: the Ghidra static address of an "
            "instruction and the live segment:offset of that same "
            "instruction (get the latter from cpu_read_registers - cs "
            "and eip - while paused on it). Exact for .COM programs and "
            "single-segment .EXE programs; see debug_map_to_ghidra for "
            "what happens outside that segment."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "ghidra_address": {
                    "type": "integer",
                    "description": "Static address of the anchor instruction in Ghidra.",
                },
                "live_segment": {
                    "type": "integer",
                    "description": "Segment register (usually cs) at the anchor instruction.",
                },
                "live_offset": {
                    "type": "integer",
                    "description": "Offset (usually eip) at the anchor instruction, in that segment.",
                },
            },
            "required": ["ghidra_address", "live_segment", "live_offset"],
        },
        handler=lambda args: _set_base(state, args),
        feature=feature,
    )

    add_tool(
        name="debug_map_to_live",
        description=(
            "Translate a Ghidra static address to a live segment:offset, "
            "using the mapping from debug_map_set_base. Handy for turning "
            "a function address found in Ghidra into an execute "
            "breakpoint's segment/offset."
        ),
        read_only=True,
        schema={
            "type": "object",
            "properties": {
                "ghidra_address": {"type": "integer"},
            },
            "required": ["ghidra_address"],
        },
        handler=lambda args: _to_live(state, args),
        feature=feature,
    )

    add_tool(
        name="debug_map_to_ghidra",
        description=(
            "Translate a live segment:offset to a Ghidra static address, "
            "using the mapping from debug_map_set_base. Handy for looking "
            "up what function a breakpoint hit inside, or what a paused "
            "cs:eip corresponds to in the decompilation. Refuses (rather "
            "than guessing) when the segment doesn't match the one the "
            "mapping was anchored to."
        ),
        read_only=True,
        schema={
            "type": "object",
            "properties": {
                "live_segment": {"type": "integer"},
                "live_offset": {"type": "integer"},
            },
            "required": ["live_segment", "live_offset"],
        },
        handler=lambda args: _to_ghidra(state, args),
        feature=feature,
    )

    add_tool(
        name="debug_map_status",
        description="Show the current Ghidra <-> live address mapping, if one is set.",
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _status(state),
        feature=feature,
    )


def _set_base(state, args):
    import mcp.types as types
    ghidra_address = args["ghidra_address"]
    live_segment = args["live_segment"]
    live_offset = args["live_offset"]
    state["base_segment"] = live_segment
    state["delta"] = ghidra_address - live_offset
    state["ghidra_anchor"] = ghidra_address
    return [types.TextContent(type="text", text=json.dumps(_status_dict(state)))]


def _to_live(state, args):
    import mcp.types as types
    if state["delta"] is None:
        return [types.TextContent(
            type="text",
            text="No mapping set. Call debug_map_set_base first.",
        )]
    ghidra_address = args["ghidra_address"]
    offset = ghidra_address - state["delta"]
    result = {
        "segment": state["base_segment"],
        "offset": offset,
        "linear": state["base_segment"] * 16 + offset,
    }
    return [types.TextContent(type="text", text=json.dumps(result))]


def _to_ghidra(state, args):
    import mcp.types as types
    if state["delta"] is None:
        return [types.TextContent(
            type="text",
            text="No mapping set. Call debug_map_set_base first.",
        )]
    live_segment = args["live_segment"]
    live_offset = args["live_offset"]
    if live_segment != state["base_segment"]:
        return [types.TextContent(type="text", text=json.dumps({
            "error": (
                f"segment {live_segment:#06x} does not match the "
                f"mapping's base segment {state['base_segment']:#06x} - "
                "this address is outside what the mapping covers; "
                "translating it would silently produce a wrong Ghidra "
                "address."
            ),
        }))]
    result = {"ghidra_address": live_offset + state["delta"]}
    return [types.TextContent(type="text", text=json.dumps(result))]


def _status(state):
    import mcp.types as types
    return [types.TextContent(type="text", text=json.dumps(_status_dict(state)))]


def _status_dict(state):
    if state["delta"] is None:
        return {"set": False}
    return {
        "set": True,
        "base_segment": state["base_segment"],
        "ghidra_anchor": state["ghidra_anchor"],
        "delta": state["delta"],
    }
