# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import asyncio

import anyio.to_thread
import mcp.server.stdio
from mcp.server.lowlevel import Server
import mcp.types as types

from .config import MODES, Config
from .connection import Connection, guard
from .lifecycle import InstanceManager
from .tools import bridge, session, screen, input as input_tools, memory, freeze, io, cpu, debug, ghidra, media, script, symbols, wait, batch

# The risk taxonomy every tool declares itself under (add_tool's `risk`
# param), replacing a single read_only boolean:
#   read          - no side effects anywhere.
#   mutate_guest  - reaches the connected engine or guest in a normal,
#                   non-destructive way (memory/CPU/port writes, input
#                   injection, drive mounts, debugger control, Lua).
#   mutate_host   - confined to the bridge process's own local
#                   bookkeeping (e.g. debug_map_set_base's Ghidra
#                   address-mapping ranges) and never reaches the
#                   engine or guest at all - always allowed, in every
#                   mode, unconditionally (see _mode_allows below).
#   destructive   - an irreversible or high-blast-radius mutation
#                   (dosbox_shutdown, mount_lock).
#   lifecycle     - manages the bridge-to-engine connection or process
#                   itself (bridge_connect/start/stop/...), distinct
#                   from mutating guest state.
# Only "read" and "mutate_host" get an unconditional pass; every other
# tool declares its own interact_ok (default False - full-only) instead
# of being approximated by which named group it happened to be filed
# under. This is a deliberate behavior-preserving refactor (3.1): every
# interact_ok value below reproduces the pre-3.1 group-based table
# exactly (input/media/script/bridge groups were interact-eligible;
# everything else wasn't) - see docs/mcp-plan.md item 3.1 for why this
# item does not also change *which* tools are interact-eligible.
RISK_LEVELS = ("read", "mutate_guest", "mutate_host", "destructive", "lifecycle")


def _mode_allows(mode: str, risk: str, interact_ok: bool) -> bool:
    if risk in ("read", "mutate_host"):
        return True
    if mode == "full":
        return True
    if mode == "interact":
        return interact_ok
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

    def add_tool(name, description, schema, handler, risk, title,
                 interact_ok=False, idempotent=False, feature=None,
                 needs_connection=True):
        assert risk in RISK_LEVELS, f"{name}: unknown risk {risk!r}"
        if not _mode_allows(mode, risk, interact_ok):
            return
        # Bridge-internal tools skip the connection guard: most of them
        # must work while disconnected - that is their point.
        wrapped = (guard(conn, handler, feature=feature, tool_name=name)
                   if needs_connection else handler)
        if risk == "read":
            # destructiveHint/idempotentHint are spec-defined to be
            # meaningful only when readOnlyHint is false - leave them
            # unset here rather than force a value that means nothing.
            annotations = types.ToolAnnotations(readOnlyHint=True)
        else:
            annotations = types.ToolAnnotations(
                readOnlyHint=False,
                # The spec defaults destructiveHint to true whenever
                # readOnlyHint is false, so every non-destructive
                # mutator needs this stated explicitly, not left to
                # default - a silent default would call mem_write and
                # input_type "destructive" right alongside
                # dosbox_shutdown.
                destructiveHint=(risk == "destructive"),
                idempotentHint=idempotent,
            )
        registry[name] = (
            types.Tool(
                name=name,
                title=title,
                description=description,
                inputSchema=schema,
                annotations=annotations,
            ),
            wrapped,
        )

    for mod in (session, screen, media, script, wait):
        mod.register(server, conn, add_tool)
    media.register_drive(server, conn, add_tool, feature="drive")

    input_tools.register(server, conn, add_tool, feature="input")
    # debug_map_set_base/to_live/to_ghidra/status are pure client-side
    # arithmetic - no engine call, so feature=None (they used to be
    # gated behind feature="debugger", which made all four permanently
    # refuse on a stock non-debugger build despite three of them never
    # touching the engine at all) and risk="mutate_host"/"read" mean
    # set_base's own local-state mutation survives observe mode too
    # (see RISK_LEVELS above). debug_map_auto is registered separately
    # (register_auto) and classified risk="mutate_guest" instead, even
    # though its own write is purely local too: it reads live engine
    # memory (mem_scan, dos_memory_map) as part of deriving what to
    # persist, so it stays gated to full mode like any other
    # engine-reaching operation - see ghidra.register_auto's own
    # docstring.
    #
    # Registered before memory/debug so annotate_fn (2.17) can close
    # over both states in time to be handed to their register() calls.
    ghidra_state = ghidra.register(server, conn, add_tool)
    ghidra.register_auto(server, conn, add_tool, ghidra_state, feature="memory")
    symbol_state = symbols.register(server, conn, add_tool)
    annotate_fn = symbols.make_annotator(ghidra_state, symbol_state)

    memory.register(server, conn, add_tool, feature="memory")
    memory.register_allocation(server, conn, add_tool, feature="memory")
    memory.register_search(server, conn, add_tool, feature="memory",
                           annotate=annotate_fn)
    memory.register_snapshot(server, conn, add_tool, feature="memory")
    freeze.register(server, conn, add_tool, feature="freeze")
    io.register(server, conn, add_tool, feature="port_io")
    cpu.register(server, conn, add_tool, feature="cpu_control")
    cpu.register_state(server, conn, add_tool, feature="cpu_registers")
    debug.register(server, conn, add_tool, feature="debugger",
                   annotate=annotate_fn)
    batch.register(server, conn, add_tool, feature="batch")
    bridge.register(server, conn, add_tool, manager=manager, mode=mode)

    @server.list_tools()
    async def list_tools():
        return [tool for tool, _ in registry.values()]

    @server.call_tool()
    async def call_tool(name, arguments):
        if name not in registry:
            raise ValueError(f"unknown tool: {name}")
        _, handler = registry[name]
        # Every handler is sync and makes a blocking httpx call (client.py's
        # 30s flat timeout). The SDK already dispatches each request as its
        # own anyio task (see Server.run's task group), but calling a sync
        # handler inline blocks the single event loop thread underneath all
        # of them - one slow call (video/frame's mode=rendered waits up to
        # 2s in the engine) stalls every other in-flight tool call. Running
        # it in a worker thread instead gives genuine concurrency.
        # abandon_on_cancel stays False (the default): Connection has no
        # locking around its own reconnect state, so abandoning a thread
        # mid-request while a cancelled caller moves on risks a detach()/
        # _try_connect() still running unobserved against shared state.
        return await anyio.to_thread.run_sync(handler, arguments or {})

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
