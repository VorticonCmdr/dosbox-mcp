# REFERENCE CARD - dosbox-mcp tools

Keep this by the keyboard. Every tool, one block, with an example call
in the arguments an MCP client passes. Tools marked (read-only) are
safe to auto-approve. A tool's feature flag (if any) must be enabled in
the engine build, and the tool must be within your capability mode.

## Bridge tools (about the bridge itself)

These work whether or not an engine is connected.

- **bridge_status** (read-only) - bridge version, highest protocol it
  implements, connection state, engine version, effective (negotiated)
  protocol, the attached engine's instance_id (changes across a
  restart), features, mode, managed instance, token presence. `{}`
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
- **mem_write** - write bytes to guest memory. Optional `expected`
  performs a compare-and-swap, refusing the write if the current bytes
  don't match. `{"offset": 4660, "data": "AAECAw=="}`
- **mem_search** (read-only) - scan a range for a value (width 1/2/4).
  `{"start": 0, "end": 655360, "value": 100, "width": 2}`
- **mem_scan** (read-only) - scan a range for a masked byte signature,
  Ghidra's copyable-byte-string format: space-separated hex-pair bytes
  and `??` wildcards, 1-256 tokens, at least one fixed byte.
  `{"pattern": "8B 46 ?? 50 E8", "start": 0, "end": 1048576}`
- **dos_memory_map** (read-only) - walk the DOS MCB chain: which PSP
  owns which block; free/largest-free byte summary. `detail:true` for
  raw internals. `{}`
- **dos_ems_status** (read-only) - guest-visible EMS state: driver
  enabled, total/free 16KB pages, and every active handle's name, page
  count, and current page-frame mapping. `{}`
- **dos_xms_status** (read-only) - guest-visible XMS state:
  total/largest-free KB, A20 line state, HMA/UMB ownership, and every
  allocated handle's size and lock count. `{}`
- **mem_snapshot** - capture a memory range (up to 16 MB) for later
  comparison; returns a `handle` to pass to mem_diff.
  `{"start": 0, "end": 65536}`
- **mem_diff** - compare current memory against a snapshot handle,
  narrowing surviving candidates on each call - the classic "find the
  address of my HP counter" workflow. `op` is `changed`, `unchanged`
  (or `equals`), `increased`, or `decreased`, each re-baselined against
  the *previous* mem_diff call, not the original snapshot.
  `{"handle": "snap-1", "op": "decreased", "width": 2}`
- **mem_alloc** - allocate guest memory through the DOS/XMS allocator.
  `area` is `CONV` (default), `UMA`, or `XMS` (best-fit only).
  `{"size": 1024, "area": "CONV"}`
- **mem_free** - free a block previously returned by mem_alloc. Refuses
  (409) if the block's owner has since changed. `{"addr": 4660}`
- **mem_allocations** (read-only) - this API's own live allocations,
  plus free-memory totals per area. `{}`

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

Execution control. Breakpoints and stepping only fire with an
interpreted CPU core (`core = normal` or `full`); disassembly and
backtrace work on any build regardless of the debugger capability. See
PROTOCOL.md.

- **debug_status** (read-only) - whether execution is paused. `{}`
- **debug_pause** - pause at the current instruction. `{}`
- **debug_continue** - resume; arms any breakpoints added while paused.
  Returns immediately, does not wait for a breakpoint. `{}`
- **debug_step** - execute 1-64 instructions, pause again.
  `{"count": 1}`
- **debug_step_over** - step over the current instruction if it's a
  call/int/loop/rep (plants a one-shot breakpoint past it and resumes);
  falls back to a plain step otherwise. The actual stop happens later -
  poll debug_wait with the returned `resumed_from_stop_id`. `{}`
- **debug_run_to** - run until execution reaches segment:offset (a
  one-shot breakpoint). Same "stop happens later" shape as step_over.
  `{"segment": 4660, "offset": 256}`
- **debug_step_out** - run until the current frame returns, via a
  backtrace-derived return address. `no_confident_caller_frame` (not an
  error) if the backtrace can't resolve the caller with high
  confidence. `{}`
- **debug_wait** - block until the debugger stops again (breakpoint,
  pause, or step), or timeout_ms elapses - one call instead of polling
  debug_status. Pass `since_stop_id` from a prior stop so one that
  already happened isn't missed.
  `{"since_stop_id": 3, "timeout_ms": 5000}`
- **debug_breakpoint_add** - add an execute/interrupt/memory
  breakpoint. Optional `once`, `ignore_count`, register/memory
  `condition`; memory breakpoints take `trigger: "write"` (default) or
  `"read"`. `{"type": "interrupt", "int": 33, "ah": 61}`
- **debug_breakpoint_list** (read-only) - list breakpoints; `id` is
  stable, `index` is positional and shifts as breakpoints are added or
  removed. `{}`
- **debug_breakpoint_delete** - remove one by id or index, or all if
  both are omitted. `{"id": 1}`
- **debug_watch_add** - add a named 16-bit watched variable at
  segment:offset - the IV/watch-panel concept, exposed for automation.
  `{"name": "hp", "segment": 4660, "offset": 256}`
- **debug_watch_list** (read-only) - list watches, each with a live
  value read fresh from guest memory (not cached) at call time. `{}`
- **debug_watch_delete** - remove a watch by its resolved flat
  `address` (see debug_watch_list), or all if omitted.
  `{"address": 987654}`
- **debug_disassemble** (read-only) - decode x86 instructions to
  assembly text from segment:offset. Doesn't need the debugger
  capability - works on any build, paused or running.
  `{"segment": 4660, "offset": 256, "count": 10}`
- **debug_backtrace** (read-only) - best-effort SS:BP call-stack walk
  from the current CS:EIP, with per-frame confidence. Also works on any
  build. `{"max_frames": 8}`

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
- **debug_map_status** (read-only) - the current mapping, if any; flags
  a stale (unanchored) range reloaded from disk. `{}`
- **debug_map_auto** - anchor automatically: give the same Ghidra-style
  byte pattern mem_scan takes plus the Ghidra address it corresponds
  to, and this locates it live (optionally narrowing the scan range)
  and derives the anchor from the DOS MCB chain. Only correct for
  `.COM`-style single-segment programs; needs full capability mode
  since it reads live engine memory.
  `{"pattern": "8B 46 ?? 50 E8", "ghidra_address": 4211, "ghidra_start": 256, "ghidra_end": 8192, "label": "main"}`

## Symbols (feature: debugger)

- **debug_symbols_load** - load function/global names by pasting raw
  output from a Ghidra MCP server's `list_functions`/`list_globals`/
  `list_functions_enhanced`. Adds a `symbol` field to disassembly,
  debugger stop records, backtrace frames, and the DOS memory map.
  Requires an anchored mapping range first (see debug_map_set_base).
  `{"text": "<pasted Ghidra tool output>"}`
- **debug_symbols_status** (read-only) - how many symbols are
  currently loaded. `{}`

## Scripts (the escape hatch)

- **script_load** - load sandboxed Lua and start it immediately unless
  `start:false`. Anything without a dedicated tool can usually be done
  here. `name`/`seed`/`debug` tag the run, fix `math.random()`, and
  turn on a trace log (see script_log). Rejected while a script is
  already running; rate-limited to one load per 2 seconds.
  `{"script": "dosbox.type('hello')"}`
- **script_start** - start a script loaded with `start:false`. `{}`
- **script_status** (read-only) - script state, its output table, and
  `log_path` if a debug log is active. `{}`
- **script_log** (read-only) - tail (last 64 KB) of the current debug
  log; refused unless the loaded script was loaded with `debug:true`.
  `{}`
- **script_stop** - stop the running script. `{}`

For watching a memory address frame-by-frame and logging every change
via `dosbox.mem_read_byte`/`dosbox.wait_frames`/`dosbox.output`, see
"Logging a memory-triggered event, with a screenshot" in MANUAL.md.

## Media and drives

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
- **drive_mount** - mount a host directory as a drive letter (the
  directory-mount form of guest `MOUNT`). The path must resolve under
  a base directory the operator whitelisted (`mount_allowed_bases` in
  the bridge config) and be free of symlink components.
  `{"drive": "D", "path": "/home/user/dosgames/doom", "readonly": false}`
- **mount_lock** - freeze the mount configuration; one-way for the
  life of the process. `{}`

## Batch and wait (cross-cutting)

- **batch_execute** - apply 1-64 memory/register/port/freeze operations
  atomically in one emulation-thread pass (no interleaving with other
  requests). Ops: `mem_read`, `mem_write`, `mem_cas`, `cpu_read`,
  `cpu_write`, `port_read`, `port_write`, `freeze_set`, `freeze_clear`.
  Not transactional - there's no rollback; `on_error` (`abort` or
  `continue`) controls whether a failing op stops the rest.
  `{"ops": [{"op": "mem_read", "offset": 4660, "len": 2}, {"op": "cpu_read"}]}`
- **wait_for** (read-only) - block until a condition is true or
  timeout_ms elapses - one call instead of a poll loop. Conditions:
  `text` (screen substring match), `screen_change` (hash differs from
  a prior screen_text/screen_info/screen_capture hash), `frames`
  (count from now), `replay_done`, `memory` (addr/width/value/op),
  `stopped` (debugger builds only), `script_done`, `program` (name
  match, or waits for it to change if pattern is omitted).
  `{"for": "text", "pattern": "Installation complete", "timeout_ms": 10000}`
