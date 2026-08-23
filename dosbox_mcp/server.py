# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import asyncio

import mcp.server.stdio
from mcp.server.lowlevel import Server
import mcp.types as types

from .config import MODES, Config
from .connection import Connection, guard
from .lifecycle import InstanceManager
from .tools import bridge, session, screen, input as input_tools, memory, freeze, io, cpu, debug, ghidra, media, script, wait

# Groups whose non-read-only tools register under "interact" mode.
# Everything else non-read-only (memory surgery, port IO, cpu control,
# debugger, shutdown) needs "full".
_INTERACT_GROUPS = {"input", "media", "script", "bridge"}

# Groups whose non-read-only tools register in every mode, including
# observe: their mutation is confined to the bridge process's own local
# bookkeeping (e.g. debug_map_set_base's Ghidra address-mapping ranges)
# and never reaches the connected engine or guest, so the mode's
# read-only guarantee about the emulator holds regardless. This is an
# unconditional bypass checked before read_only/mode at all (see
# _mode_allows below) - a tool that reaches the engine in any way has no
# business in one of these groups, whatever its read_only flag says.
# debug_map_auto is deliberately registered under its own separate group
# ("mapping_auto", server.py's ghidra.register_auto call) rather than
# here, precisely because it does reach the engine.
_LOCAL_ONLY_GROUPS = {"mapping"}


def _mode_allows(mode: str, read_only: bool, group: str) -> bool:
    if group in _LOCAL_ONLY_GROUPS:
        return True
    if mode == "full":
        return True
    if read_only:
        return True
    if mode == "interact":
        return group in _INTERACT_GROUPS
    return False


def _make_attach(conn):
    """The spawn-identity check for bridge_start: authenticate against
    the child with the token from the config dir the manager created.
    A port squatter cannot pass this (self-audit 2026-07-17)."""
    def attach(base_url, token):
        conn.detach()
        conn.config.token = token
        conn.ensure_connected()
        return conn.engine_info
    return attach


def build_server(conn, mode: str = "full", manager=None):
    """Build the MCP server. `mode` is the operator's capability mode
    (observe / interact / full): tools outside the mode are not
    registered, so the client never sees them."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if manager is None:
        manager = InstanceManager(conn.config, attach=_make_attach(conn))

    server = Server("dosbox-mcp")
    registry = {}

    def add_tool(name, description, schema, handler, read_only=False,
                 feature=None, group="session", needs_connection=True):
        if not _mode_allows(mode, read_only, group):
            return
        # Bridge-internal tools skip the connection guard: most of them
        # must work while disconnected - that is their point.
        wrapped = (guard(conn, handler, feature=feature, tool_name=name)
                   if needs_connection else handler)
        annotations = types.ToolAnnotations(readOnlyHint=read_only)
        registry[name] = (
            types.Tool(
                name=name,
                description=description,
                inputSchema=schema,
                annotations=annotations,
            ),
            wrapped,
        )

    def add_tool_for(group):
        def add(name, description, schema, handler, read_only=False,
                feature=None, needs_connection=True):
            add_tool(name, description, schema, handler,
                     read_only=read_only, feature=feature, group=group,
                     needs_connection=needs_connection)
        return add

    def get_tools():
        return [(name, tool.description.split(". ")[0].rstrip(".") + ".")
                for name, (tool, _) in registry.items()]

    for mod, group in ((session, "session"), (screen, "screen"),
                       (media, "media"), (script, "script"),
                       (wait, "wait")):
        mod.register(server, conn, add_tool_for(group))

    input_tools.register(server, conn, add_tool_for("input"), feature="input")
    memory.register(server, conn, add_tool_for("memory"), feature="memory")
    memory.register_allocation(server, conn, add_tool_for("memory"), feature="memory")
    memory.register_search(server, conn, add_tool_for("memory"), feature="memory")
    memory.register_snapshot(server, conn, add_tool_for("memory"), feature="memory")
    freeze.register(server, conn, add_tool_for("freeze"), feature="freeze")
    io.register(server, conn, add_tool_for("port_io"), feature="port_io")
    cpu.register(server, conn, add_tool_for("cpu"), feature="cpu_control")
    cpu.register_state(server, conn, add_tool_for("cpu"), feature="cpu_registers")
    debug.register(server, conn, add_tool_for("debug"), feature="debugger")
    # debug_map_set_base/to_live/to_ghidra/status are pure client-side
    # arithmetic - no engine call, so feature=None (they used to be
    # gated behind feature="debugger", which made all four permanently
    # refuse on a stock non-debugger build despite three of them never
    # touching the engine at all) and group="mapping" is in
    # _LOCAL_ONLY_GROUPS (above), so set_base's own local-state mutation
    # survives observe mode too. debug_map_auto is registered separately,
    # under its own group, deliberately NOT in _LOCAL_ONLY_GROUPS: it
    # reads live engine memory (mem_scan, dos_memory_map) as part of
    # deriving what to persist, so it stays gated to full mode like any
    # other engine-reaching mutation - see ghidra.register_auto's own
    # docstring.
    ghidra_state = ghidra.register(server, conn, add_tool_for("mapping"))
    ghidra.register_auto(server, conn, add_tool_for("mapping_auto"),
                         ghidra_state, feature="memory")
    bridge.register(server, conn, add_tool_for("bridge"),
                    manager=manager, mode=mode, get_tools=get_tools)

    @server.list_tools()
    async def list_tools():
        return [tool for tool, _ in registry.values()]

    @server.call_tool()
    async def call_tool(name, arguments):
        if name not in registry:
            raise ValueError(f"unknown tool: {name}")
        _, handler = registry[name]
        return handler(arguments or {})

    def _registered_tool_names():
        return set(registry.keys())

    server.registered_tool_names = _registered_tool_names
    return server


async def _run():
    config = Config.load()
    conn = Connection(config)
    server = build_server(conn, mode=config.mode)
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    asyncio.run(_run())
