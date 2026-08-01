# dosbox-mcp

MCP server for [dosbox-automation](https://dosbox-automation.org):
drive a DOS machine from an AI agent.

dosbox-automation is a DOSBox fork with a built-in automation API.
This bridge connects any MCP-capable agent runtime (Claude Code and
friends) to a running emulator. The agent can type into DOS programs,
read the screen, capture frames and video, run sandboxed Lua scripts,
and inspect memory: everything the automation API offers, as MCP tools.

## Quick start

1. Install dosbox-automation and start it with the API enabled. The
   [manual](https://dosbox-automation.org) covers setup for all
   platforms.

2. Add the bridge to your MCP client. For Claude Code:

   ```
   claude mcp add dosbox -- uvx dosbox-mcp
   ```

   Or in a JSON MCP configuration:

   ```json
   {
     "mcpServers": {
       "dosbox": { "command": "uvx", "args": ["dosbox-mcp"] }
     }
   }
   ```

   `pipx install dosbox-mcp` or `pip install dosbox-mcp` work just as
   well; the command is `dosbox-mcp` either way.

That's it. The bridge finds the running emulator, checks what it can
do, and registers the matching tools. Ask your agent for `bridge_status`
to confirm the connection.

## Configuration

Optional. Write a commented config file to tune the port, spawn an
emulator on demand, or limit what an agent may do:

```
dosbox-mcp setup --init
```

The comments in the generated file explain each setting. Environment
variables `DOSBOX_API_URL` and `DOSBOX_API_TOKEN` override it. The
bridge talks to loopback addresses only; it will not reach out to a
remote machine.

## What the agent gets

Tools for session control, screen text and captures, video recording,
Lua scripting, keyboard and mouse input, and memory access. You choose
how much reach an agent has, from read-only observation to full
control.

## Pairs with Ghidra for reverse engineering

The `debugger` feature (execution control: pause/step/continue and
breakpoints) plus a handful of address-mapping tools let an agent
combine this bridge with a separate Ghidra MCP server: analyze a
program statically in Ghidra, then set breakpoints and interpret a
paused CPU state against the exact same addresses live in DOSBox. The
mapping tools (`debug_map_set_base` and friends) are pure client-side
arithmetic - they don't talk to Ghidra or the engine, just translate
addresses once anchored at one known correspondence point (usually the
entry point). See the "Debugger" and "Ghidra address mapping" sections
of `feelies/REFCARD.md` for the full tool list.

## An open protocol

The contract between this bridge and the emulator is written down in
[PROTOCOL.md](PROTOCOL.md) and versioned independently. dosbox-automation
is the reference implementation; any DOSBox variant that speaks the
protocol works with this bridge.

## In the box

Like the games it drives, this one ships with its paper. The `feelies/`
folder holds the manual, a reference card for every tool, an install
sheet, and a tech-support page.

## Requirements

- Python 3.11 or newer
- A running dosbox-automation (0.84-da2 or newer) on the same machine

## License

GPL-2.0-or-later. See LICENSE. The protocol specification
(PROTOCOL.md) is licensed CC-BY-SA-4.0.

---
This project is developed with tooled assistance, but tested, reviewed and
signed off by a human developer.
