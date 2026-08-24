# REFERENCE CARD - dosbox-mcp tools

Keep this by the keyboard. Every tool, one block, with an example call
in the arguments an MCP client passes. Tools marked (read-only) are
safe to auto-approve. A tool's feature flag (if any) must be enabled in
the engine build, and the tool must be within your capability mode.

## Bridge tools (about the bridge itself)

These work whether or not an engine is connected.

- **bridge_version** (read-only) - bridge version and protocol level.
  `{}`
- **bridge_status** (read-only) - connection state, engine version,
  effective protocol, features, mode, managed instance, token presence.
  `{}`
- **bridge_help** (read-only) - one-call orientation: version, state,
  and every available tool with a one-line description. `{}`
- **bridge_connect** - attach to the running engine explicitly; reports
  a precise reason on failure. `{}`
- **bridge_disconnect** - detach; the engine keeps running. `{}`
- **bridge_start** - spawn the engine binary from the config file (with
  an isolated config dir) and attach. `{}`
- **bridge_stop** - stop the instance this bridge spawned (never one it
  only attached to). `{}`
- **bridge_logs** (read-only) - tail the spawned instance's output.
  `{"n": 50}`
- **bridge_setup** - change safe settings (port, headless, protocol).
  Binary path and mode are human-edited only.
  `{"port": 8386, "headless": true}`
- **bridge_swagger** (read-only) - digest of the engine's OpenAPI
  surface: route counts and anything unknown to the protocol. `{}`

## Session and screen

- **dosbox_status** (read-only) - machine and program state in one call.
  `{}`
- **dosbox_shutdown** - shut the emulator down. Irreversible. `{}`
- **session_info** (read-only) - base URL and token location for
  driving the REST API directly (never the token value). `{}`
- **screen_text** (read-only) - read the text-mode screen. `{}`
- **screen_capture** (read-only) - capture a frame as an image. `{}`
- **screen_info** (read-only) - current video mode and geometry. `{}`

## Input (feature: input)

- **input_key** - press one named key.
  `{"key": "KBD_enter"}`
- **input_sequence** - a timed sequence of key and mouse events on one
  timeline (each event has a `type`, its data, and an optional
  `delay_ms`), or replay a stored recording by name instead.
  `{"events": [{"key": "KBD_up"}, {"key": "KBD_enter", "delay_ms": 100}]}`
  or `{"recording": "install-run-1"}`
- **input_type** - type a string, paced so the keyboard buffer keeps
  up. `{"text": "dir *.exe"}`
- **replay_status** (read-only) - progress of the current or most
  recently finished input_sequence chain: active, engine, total,
  dispatched, remaining, elapsed_ms, drift_ms, current_frame. `{}`
- **replay_cancel** - stop the running input_sequence chain early.
  `{}`
- **record_start** - start recording keyboard/mouse input. `{}`
- **record_pause** - toggle pause on the running recording. `{}`
- **record_stop** - stop recording. Pass `name` to also save it in the
  named store (see recordings_list); `include_events:false` omits the
  raw event list from the response.
  `{"name": "install-run-1", "include_events": false}`
- **record_status** (read-only) - recording/paused, event count,
  duration, truncated. `{}`
- **recordings_list** (read-only) - metadata for every stored
  recording: name, event_count, duration_ms, truncated. `{}`
- **recording_delete** - remove a stored recording by name.
  `{"name": "install-run-1"}`
- **mouse_position** (read-only) - the DOS mouse driver's cursor
  position and button state, in guest pixels. `driver_started:false`
  if the guest never started the INT 33h driver. `{}`
- **mouse_set_position** - warp the cursor to an exact guest pixel
  position (clamped to the driver's screen range), instead of moving
  it relatively. `{"x": 160, "y": 100}`

## Memory (feature: memory)

- **mem_read** (read-only) - read guest physical memory; returns base64
  plus register state. `{"offset": 4660, "length": 64}`
- **mem_write** - write bytes to guest memory.
  `{"offset": 4660, "data": "AAECAw=="}`
- **mem_search** (read-only) - scan a range for a value (width 1/2/4).
  `{"start": 0, "end": 655360, "value": 100, "width": 2}`
- **dos_memory_map** (read-only) - walk the DOS MCB chain: which PSP
  owns which block. `{}`

## Freeze (feature: freeze)

- **freeze_set** - lock an address to a value each frame (the trainer
  primitive). `{"address": 4660, "value": 999, "width": 2}`
- **freeze_list** (read-only) - list active freezes. `{}`
- **freeze_clear** - clear one freeze or all.
  `{"address": 4660}`

## CPU and ports

- **cpu_read_registers** (read-only) - read all CPU registers: general,
  segment, eip, flags (feature: cpu_registers). `{}`
- **cpu_write_register** - write a CPU register (feature: cpu_control).
  `{"register": "eax", "value": 0}`
- **port_read** (read-only) - read an x86 I/O port (feature: port_io).
  `{"port": 968, "width": 1}`
- **port_write** - write an x86 I/O port (feature: port_io).
  `{"port": 968, "value": 1, "width": 1}`

## Debugger (feature: debugger)

Execution control. Breakpoints only fire with an interpreted CPU core
(`core = normal` or `full`); see PROTOCOL.md.

- **debug_status** (read-only) - whether execution is paused. `{}`
- **debug_pause** - pause at the current instruction. `{}`
- **debug_continue** - resume; arms any breakpoints added while paused.
  Returns immediately, does not wait for a breakpoint. `{}`
- **debug_step** - execute one instruction, pause again. `{}`
- **debug_breakpoint_add** - add an execute/interrupt/memory breakpoint.
  `{"type": "interrupt", "int": 33, "ah": 61}`
- **debug_breakpoint_list** (read-only) - list breakpoints; `index` is
  positional, not stable. `{}`
- **debug_breakpoint_delete** - remove one by index, or all if omitted.
  `{"index": 0}`

## Ghidra address mapping (feature: debugger)

Bridge-side arithmetic only - no engine call, no dependency on Ghidra
itself. Anchors a Ghidra static address against a live segment:offset
(get the latter from `cpu_read_registers`) so the two tools' addresses
translate directly. Exact for .COM programs and single-segment .EXE
programs. Don't expect the raw numbers to already match before
anchoring: Ghidra's real-mode loader can't rebase a .COM import to a
segment:0x0100 layout (it requires a zero segment offset), so it
typically lands at 0000:0000, a constant 0x100 below every live
address - anchoring absorbs that automatically.

- **debug_map_set_base** - anchor the mapping at one known
  correspondence point, e.g. the entry point.
  `{"ghidra_address": 256, "live_segment": 4660, "live_offset": 256}`
- **debug_map_to_live** (read-only) - Ghidra address -> segment:offset.
  `{"ghidra_address": 336}`
- **debug_map_to_ghidra** (read-only) - segment:offset -> Ghidra
  address; refuses rather than guessing if the segment doesn't match
  the anchor. `{"live_segment": 4660, "live_offset": 336}`
- **debug_map_status** (read-only) - the current mapping, if any. `{}`

## Scripts (the escape hatch)

- **script_run** - load and run sandboxed Lua in the engine. Anything
  without a dedicated tool can usually be done here.
  `{"script": "dosbox.type('hello')"}`
- **script_status** (read-only) - script state and its output table.
  `{}`
- **script_stop** - stop the running script. `{}`

For watching a memory address frame-by-frame and logging every change
via `dosbox.mem_read_byte`/`dosbox.wait_frames`/`dosbox.output`, see
"Logging a memory-triggered event, with a screenshot" in MANUAL.md.

## Media and recording

- **video_capture_start** - start recording. `{mode?: raw|rendered,
  compression?: 0-9}` - both optional; compression is set for `mode`
  and recording started atomically, refused with 409 if a capture is
  already running.
- **video_capture_stop** - stop recording. `{}`
- **video_capture_status** (read-only) - capturing, mode, path, frames,
  elapsed_ms, bytes_written, compression_level, last_stop_reason.
  `{}`
- **drive_list** (read-only) - every drive letter A-Z and what's
  mounted on each. `{}`
- **mount_status** (read-only) - whether mounting is locked, and the
  configured directory/image roots. `{}`
- **mount_images** (read-only) - image files under the configured
  image roots, grouped by root. `{}`
- **drive_swap** - mount or swap a disk image onto a drive letter, for
  multi-disk installs. `{"drive": "A", "image": "/path/to/disk2.img"}`
- **mount_lock** - freeze the mount configuration; one-way for the
  life of the process. `{}`
