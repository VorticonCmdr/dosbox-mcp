# dosbox-mcp

This is [VorticonCmdr](https://github.com/VorticonCmdr)'s fork of upstream
[dosbox-automation/dosbox-mcp](https://github.com/dosbox-automation/dosbox-mcp),
extended with the debugger, Ghidra address-mapping, symbols, batch, and
wait_for tool groups; it drops upstream's mixer tools. It's published
to PyPI under a different name (`vorticoncmdr-dosbox-mcp`) so it can't
collide with upstream's own published `dosbox-mcp` package.

MCP server for [dosbox-automation](https://dosbox-automation.org):
drive a DOS machine from an AI agent. This particular bridge is built
against [VorticonCmdr's dosbox-automation fork](https://github.com/VorticonCmdr/dosbox-automation) —
most of what's below (the debugger, batch, wait_for, and Ghidra tools)
needs that fork's expanded REST API and isn't present on upstream's own
dosbox-automation builds.

dosbox-automation is a DOSBox fork with a built-in automation API.
This bridge connects any MCP-capable agent runtime (Claude Code and
friends) to a running emulator. The agent can type into DOS programs,
read the screen, capture frames and video, run sandboxed Lua scripts,
inspect memory, and drive a full headless debugger: everything the
automation API offers, as MCP tools.

## Quick start

1. Build [VorticonCmdr's dosbox-automation
   fork](https://github.com/VorticonCmdr/dosbox-automation) and start
   it with the API enabled: `webserver_enabled = true` in its config,
   or `--set webserver_enabled=true` on the command line. (Upstream's
   own dosbox-automation builds don't expose the debugger/batch/wait_for
   routes most of this bridge's tools rely on.)

2. Add the bridge to your MCP client. For Claude Code:

   ```
   claude mcp add dosbox -- uvx --from vorticoncmdr-dosbox-mcp dosbox-mcp
   ```

   Or in a JSON MCP configuration:

   ```json
   {
     "mcpServers": {
       "dosbox": { "command": "uvx", "args": ["--from", "vorticoncmdr-dosbox-mcp", "dosbox-mcp"] }
     }
   }
   ```

   `pipx install vorticoncmdr-dosbox-mcp` or `pip install
   vorticoncmdr-dosbox-mcp` work just as well; the installed command is
   still `dosbox-mcp` either way.

That's it. The bridge finds the running emulator, checks what it can
do, and registers the matching tools. Ask your agent for `bridge_status`
to confirm the connection, or `bridge_start` to have it spawn a
configured emulator itself.

## Configuration

Optional. Write a commented config file to tune the port, spawn an
emulator on demand, or limit what an agent may do:

```
dosbox-mcp setup --init
```

The comments in the generated file explain each setting; `dosbox-mcp
setup` (no flags) prints the current effective config as JSON, and
`dosbox-mcp probe` validates it end-to-end (spawns or attaches, then
reports the negotiated protocol and features).

| Setting | Default | Purpose |
|---|---|---|
| `binary` | *(unset)* | Path to the dosbox binary `bridge_start` spawns. Human-edited only — no tool can change it. |
| `port` | `8386` | Webserver port on `127.0.0.1`, used both to connect and to configure a spawned instance. |
| `headless` | `false` | Spawn without a window (SDL dummy video driver). |
| `protocol` | *(negotiates highest)* | Pin the bridge to a lower protocol version, e.g. `"1.0"`. |
| `mode` | `full` | Capability mode: `observe` (read-only), `interact` (+ input, video capture, scripts, lifecycle), `full` (+ memory writes, port I/O, register writes, debugger). Human-edited only. |
| `token_file` | *(engine default)* | Token file of an already-running instance to attach to. |
| `mount_allowed_bases` | *(empty)* | Up to 5 absolute host directories a spawned instance may let `drive_mount` mount. Human-edited only; unset means every `drive_mount` call is refused by policy. |
| `mount_allowed_image_roots` | *(empty)* | Same mechanism for `drive_swap`'s disk-image source roots. Human-edited only. |

Every path in `mount_allowed_bases`/`mount_allowed_image_roots` must be absolute, exist, and be
free of symlink components — same rule the engine itself enforces.

Environment variables override the config file:

| Variable | Purpose |
|---|---|
| `DOSBOX_API_URL` | Target base URL (loopback only: `127.0.0.1`, `::1`, or `localhost`). |
| `DOSBOX_API_TOKEN` | Bearer token by value (a token file is preferred). |
| `DOSBOX_TOKEN_FILE` | Override where the bridge reads the token from. |
| `DOSBOX_MCP_CONFIG` | Override the config file path. |
| `DOSBOX_MCP_GHIDRA_MAP` | Override where Ghidra address-mapping state persists. |

The bridge talks to loopback addresses only; it will not reach out to a remote machine.

## What the agent gets

Roughly seventy tools across these areas — how many an agent actually sees depends on `mode` and
what the attached engine build supports:

- **Bridge** — `bridge_status`, `bridge_connect`, `bridge_disconnect`, `bridge_start`,
  `bridge_stop`, `bridge_logs`, `bridge_setup`, `bridge_swagger`: connect to, spawn, or inspect the
  emulator and the bridge itself.
- **Session & screen** — `dosbox_status`, `dosbox_shutdown`, `session_info`, `screen_text`,
  `screen_capture`, `screen_info`: what's running, read the screen as text or an image.
- **Input** — `input_key`, `input_sequence`, `input_type`, `replay_status`, `replay_cancel`,
  `record_start`/`pause`/`stop`, `record_status`, `recordings_list`, `recording_delete`,
  `mouse_position`, `mouse_set_position`: drive the keyboard and mouse, with deterministic
  recording and replay.
- **Memory** — `mem_read`, `mem_write`, `mem_search`, `mem_scan`, `dos_memory_map`,
  `dos_ems_status`, `dos_xms_status`, `mem_snapshot`, `mem_diff`, `mem_alloc`, `mem_free`,
  `mem_allocations`: read/write guest memory, walk DOS/EMS/XMS internals, and hunt down a moving
  value with snapshot-and-diff.
- **Freeze** — `freeze_set`, `freeze_list`, `freeze_clear`: lock an address to a fixed value
  every frame (trainer-style).
- **CPU & ports** — `cpu_read_registers`, `cpu_write_register`, `port_read`, `port_write`.
- **Debugger** — `debug_status`, `debug_pause`, `debug_continue`, `debug_step`,
  `debug_step_over`, `debug_run_to`, `debug_step_out`, `debug_wait`, `debug_breakpoint_add`,
  `debug_breakpoint_list`, `debug_breakpoint_delete`, `debug_watch_add`, `debug_watch_list`,
  `debug_watch_delete`, `debug_disassemble`, `debug_backtrace`: full pause/step/breakpoint/watch
  control, with conditions and ignore counts on breakpoints. Execution control needs an
  interpreted CPU core (`normal`/`full`); disassembly and backtrace work on any build.
- **Ghidra address mapping** — `debug_map_set_base`, `debug_map_to_live`, `debug_map_to_ghidra`,
  `debug_map_status`, `debug_map_auto`: pure client-side arithmetic translating between a Ghidra
  project's addresses and live segment:offset, once anchored at one known point.
- **Symbols** — `debug_symbols_load`, `debug_symbols_status`: paste in names from a Ghidra MCP
  server so disassembly, breakpoint hits, backtraces, and the memory map show symbol names.
- **Scripts** — `script_load`, `script_start`, `script_status`, `script_log`, `script_stop`:
  run sandboxed Lua inside the emulator for logic that shouldn't need a round-trip per step.
- **Media & drives** — `video_capture_start`/`stop`, `video_capture_status`, `drive_list`,
  `mount_status`, `mount_images`, `drive_swap`, `drive_mount`, `mount_lock`: video recording,
  multi-disk swaps, and mounting a host directory as a drive letter.
- **Batch & wait** — `batch_execute` (up to 64 memory/register/port/freeze ops in one atomic
  pass), `wait_for` (block on text appearing, a screen change, elapsed frames, a finished replay,
  a memory condition, a debugger stop, a finished script, or a program change — one call instead
  of a poll loop).

You choose how much reach an agent has, from read-only observation (`mode = observe`) to full
control (`mode = full`, the default) — see the Configuration table above.

## Pairs with Ghidra for reverse engineering

The `debugger` feature (execution control: pause/step/continue and
breakpoints) plus the address-mapping and symbol tools let an agent
combine this bridge with a separate Ghidra MCP server: analyze a
program statically in Ghidra, then set breakpoints and interpret a
paused CPU state against the exact same addresses live in DOSBox. The
mapping tools (`debug_map_set_base` and friends) are pure client-side
arithmetic - they don't talk to Ghidra or the engine, just translate
addresses once anchored at one known correspondence point (usually the
entry point). See the "Debugger" and "Ghidra address mapping" sections
of `feelies/REFCARD.md` for the tool list.

## An open protocol

The contract between this bridge and the emulator is written down in
[PROTOCOL.md](PROTOCOL.md) and versioned independently. Everything
through protocol 1.0.0 traces back to upstream dosbox-automation; every
version past that was added for this bridge, and its reference engine
is [VorticonCmdr's dosbox-automation fork](https://github.com/VorticonCmdr/dosbox-automation).
Any DOSBox variant that implements a given protocol version is a valid
peer at that version.

## In the box

Like the games it drives, this one ships with its paper. The `feelies/`
folder holds the manual, a reference card for every tool, an install
sheet, and a tech-support page.

## Requirements

- Python 3.11 or newer
- A running [VorticonCmdr's dosbox-automation
  fork](https://github.com/VorticonCmdr/dosbox-automation) (0.84-vc1 or
  newer) on the same machine — upstream dosbox-automation builds don't
  implement this bridge's debugger/batch/wait_for protocol additions

## License

GPL-2.0-or-later. See LICENSE. The protocol specification
(PROTOCOL.md) is licensed CC-BY-SA-4.0.

---
This project is developed with tooled assistance, but tested, reviewed and
signed off by a human developer.
