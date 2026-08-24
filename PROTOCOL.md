# The dosbox-mcp Protocol

Version 1.0.0 (2026-07-17)

This document specifies the contract between an MCP bridge and a
DOSBox-family emulator that exposes the automation REST API. The bridge
declares what the protocol can do; the engine delivers it. The reference
implementation of the engine side is dosbox-automation
(https://dosbox-automation.org); any other DOSBox variant that
implements this contract is a valid peer.

License: this specification is licensed under Creative Commons
Attribution-ShareAlike 4.0 International (CC-BY-SA-4.0). You may
implement it under any license you like - the license covers this
document, not your code. If you publish a modified version of the
specification itself, the modification must carry the same license and
credit the dosbox-mcp project. Full text: licenses/CC-BY-SA-4.0.txt in
this repository, or https://creativecommons.org/licenses/by-sa/4.0/


## Versioning

The specification is versioned MAJOR.MINOR.PATCH:

- MAJOR changes break existing shapes. Peers with different majors are
  incompatible.
- MINOR changes are additive: a new route group, route, or field.
  Nothing existing changes meaning.
- PATCH changes fix or clarify the text without changing behavior.
  They never appear on the wire.

On the wire, only "major.minor" travels. An engine advertises its
version in the info payload (below) as the string field `mcp_protocol`,
for example `"1.0"`. Two peers are compatible when their majors match;
the effective contract between them is the smaller of the two minors.

Backward compatibility rule: an engine whose info payload has no
`mcp_protocol` field but does have a `features` object is treated as
implicit version 1.0, because 1.0 is defined as exactly the surface
those engines ship. An info payload with neither field is not a
protocol peer.


## Transport and security

- HTTP (optionally HTTPS) on a loopback address only. Engines must bind
  to 127.0.0.1 (or ::1); bridges must refuse any non-loopback URL.
  Operation across a network is out of scope for protocol 1.x.
- Every request except the hello route (below) carries a bearer token:
  `Authorization: Bearer <token>`. The engine generates the token at
  startup; with the engine setting `webserver_token_file = true` it is
  written to a token file that local clients read. The token must never
  be passed on a command line or echoed in error responses.
- Clients should identify themselves with an `X-Client` header (the
  bridge sends `X-Client: mcp`). Engines may use it to drive on-screen
  activity indicators; they must not grant anything based on it.


## Discovery

### GET /api/v1/hello (unauthenticated)

Added in 1.0 as an optional route; engines that predate it simply
answer 404 and are treated as implicit 1.0. When implemented, it
returns exactly three fields and nothing else:

```json
{"name": "dosbox-automation", "version": "0.84-da3", "mcp_protocol": "1.0"}
```

Its purpose is diagnostics before authentication: a client can
distinguish "no instance on this port" from "instance present, token
missing" and report precisely. Because it is unauthenticated, it must
never expose configuration, state, or anything beyond these three
fields.

### GET /api/v1/dosbox/info (authenticated)

The identification payload. Required fields for a protocol peer:

- `version` (string): the engine's own version.
- `features` (object): capability flags, see below.
- `mcp_protocol` (string, "major.minor"): the protocol version.
  Absent on pre-1.0-advertisement engines (implicit 1.0 rule).

### Feature flags

`features` reports which optional capability groups the running build
supports. Version 1.0 defines these flags, all boolean:

| Flag | Grants |
|---|---|
| memory | memory read/write/search, freeze, DOS memory map |
| input | keyboard and input injection |
| cpu_registers | CPU register reads |
| cpu_control | CPU register writes |
| port_io | x86 I/O port read/write |
| freeze | per-frame value locks |
| debugger | execution control: pause/continue/step and breakpoints (1.1) |

A flag that is absent counts as false. Flags gate at call time: a
client may know a route exists in the contract while the running build
has it disabled.


## The 1.0 contract surface

The route groups below, under the `/api/v1/` prefix, are the 1.0
contract. The machine-readable OpenAPI document served at
`/openapi.json` is part of the contract surface: it is normative for
the exact request and response schemas of these routes, and clients
verify the advertised contract against it.

| Group | Routes | Notes |
|---|---|---|
| session | `status`, `program/state`, `dosbox/info`, `dosbox/shutdown` | machine and program state; shutdown is irreversible |
| screen | `video/frame`, `video/frame/info`, `video/text` | frame capture (clean emulator output) and text-mode screen reading |
| capture | `capture/video/start`, `capture/video/stop`, `capture/video/status` | video recording control; status reports path, frames, elapsed time and bytes written |
| input | `input/sequence`, `input/type`, `input/replay/status` GET, `input/replay` DELETE, `input/record/start` POST, `input/record/pause` POST, `input/record/stop` POST, `input/record/status` GET, `input/recordings` GET, `input/recordings/{name}` DELETE | named-key sequences; paced string typing; replay progress and cancellation; recording and a named recording store (feature: input) |
| memory | `memory/{offset}/{length}` GET, `memory/{segment}/{offset}/{length}` GET, `memory/{offset}` PUT, `memory/{segment}/{offset}` PUT, `memory/search`, `memory/scan`, `memory/snapshot`, `memory/diff`, `memory/allocate` POST, `memory/free` POST, `memory/allocations` GET | guest physical memory (feature: memory) |
| freeze | `memory/freeze` POST/GET/DELETE | per-frame value locks (feature: freeze) |
| dos | `dos/internals` | DOS internals incl. the MCB memory map (feature: memory) |
| cpu | `cpu/register` PUT, `cpu/state` GET | writes (feature: cpu_control), reads (feature: cpu_registers) |
| io | `io/port` GET/PUT | port I/O (feature: port_io) |
| script | `script/load`, `script/start`, `script/status`, `script/stop` | sandboxed Lua; `script/load` takes the raw source as a text/plain body |
| media | `drive` GET, `drive/swap` POST, `mount/lock` GET/POST, `mount/policy` GET, `mount/images` GET | disk drive listing and image swapping; mount policy applies underneath (feature: drive) |

Semantics that are part of the contract, not just the schemas:

- Memory routes address guest physical memory with plain integer
  offsets; JSON output (base64 payload) is selected with an
  `Accept: application/json` header, binary is the default.
- `{segment}` in the memory routes is either a register name
  (`cs`/`ds`/`es`/`fs`/`gs`/`ss`, case-insensitive) or a numeric
  paragraph value 0x0000-0xFFFF; `{offset}` is then relative to it. The
  two resolve at different times: a register name is read live when the
  request executes, a numeric value is fixed the moment the request is
  built. `GET /memory/cs/0/16` and `GET /memory/0x1234/0/16` behave
  differently in exactly that respect even when CS happens to equal
  0x1234 at request time.
- `PUT /memory/{...}` accepts an `If-Match` header (base64, unquoted or
  quoted) as a compare-and-swap precondition: the write only happens if
  the bytes currently at the address exactly equal it. A match writes
  and returns 200 as normal; a mismatch returns 412 with
  `{"memory": {"addr", "data"}}` - `data` is the real current bytes,
  not an error body, so a client can re-read and retry instead of
  guessing. Omitting `If-Match` writes unconditionally, as before.
- `POST /memory/search` caps how many matches it returns: `matches` has
  at most `limit` entries (request field, default 256, max 4096, not
  the scan span - the span cap is separately 16 MB). `total` reports
  the real match count even when it exceeds `limit`, and `truncated` is
  `total > matches.length`. A caller that only reads `matches` still
  gets a valid, if possibly incomplete, result - check `truncated`
  rather than assume completeness.
- `POST /memory/scan` finds a masked byte signature (e.g. Ghidra's
  copyable byte string `8B 46 ?? 50 E8`): space-separated hex-pair
  bytes and `??` wildcards in the `pattern` field, 1-256 tokens, at
  least one fixed byte. Same span cap (16 MB) and `matches`/`total`/
  `truncated`/`limit` contract as `memory/search`. The engine rejects
  (400) a pattern that isn't selective enough for the requested span -
  too many wildcards relative to the span would make it match almost
  everywhere - and separately rejects one whose fixed-byte count is too
  high for the span, since verifying that many candidate matches could
  risk the request's time budget. If an execute breakpoint is active
  inside the scanned range, the scan reads through its patched trap
  byte to the real instruction underneath; a plain memory read over the
  same address sees the trap, not the original byte.
- `POST /memory/snapshot {start, end}` captures a range (max 16 MB) and
  returns a `handle`. `POST /memory/diff {handle, op}` compares current
  memory against it: `op` is `changed`, `unchanged` (or its synonym
  `equals`), `increased`, or `decreased`. The first diff call on a
  handle compares the whole captured range; every call after that
  re-checks only the addresses that survived the previous call and
  *re-baselines* - `increased` means increased since the previous diff
  call on that handle, not since the original snapshot. This is what
  makes repeated diff calls on the same handle a refine loop, not a
  fixed comparison against one fixed baseline. `width` (1, 2, or 4
  bytes, default 1) is settable only on a handle's first diff call and
  locked in from then on; a later call naming a different width is
  rejected. The response is `{matches, total, truncated, candidates}`:
  `matches`/`total`/`truncated` follow the same contract as
  `memory/search`'s, and `candidates` is how many addresses are being
  tracked for the *next* diff call - it can be less than `total` if
  more than 65536 addresses genuinely survived this round, since only
  that many are kept as trackable candidates (an arbitrary subset with
  no relation to which address a caller is actually looking for - a
  round this unselective should prompt a narrower op/range and a fresh
  snapshot, not further refinement of what was kept). A handle that
  narrows to zero candidates is removed; a diff on a handle that
  doesn't currently exist (never created, evicted, or removed this way)
  returns 404, not 400 - the one memory route that does, matching how
  this codebase treats other client-supplied ids/handles that no longer
  reference a live resource.
  Snapshots are capped by total bytes across every live snapshot (32
  MB, LRU-evicted, not capped by count) plus a backstop cap on the
  number of live snapshots - a client should not assume a snapshot
  survives indefinitely if it stops calling diff on it.
- The frame returned by `video/frame` is the clean emulator output:
  on-screen overlays the engine draws for the human watching are never
  in it.
- `POST /capture/video/start` folds `mode` and `compression` into one
  call: the level is set for `mode` and the recording started
  atomically, so a client never races a separate PUT against this POST.
  Refused with 409 if `compression` is given while a capture is already
  running - the zlib level is latched at start, so a mid-recording
  change would silently not apply.
- `GET /capture/video/status`'s `path`/`frames`/`elapsed_ms`/
  `bytes_written` describe the current (or, after a stop, the most
  recently finished) recording, and keep reporting its final values
  after it stops - checking status right after stopping is the normal
  sequence. All are absent/zero if no video capture has run yet this
  session. `elapsed_ms` is measured from when the file was actually
  created, not from the `start` call (the state is `Pending` and no
  file exists until the first frame arrives), and is frozen at the
  recording's real duration once it stops rather than climbing with
  wall-clock time. `frames` staying at 0 while `capturing` is `true` is
  the clearest sign a capture never actually wrote anything - e.g. the
  emulator sat paused or minimized the whole time.
- `POST /memory/allocate {size, area, strategy}` allocates through the
  DOS/XMS allocator and returns `{addr}`. `size` is 1-65535 bytes;
  `area` is `CONV` (conventional, default), `UMA` (upper memory), or
  `XMS` (page allocator, `strategy` must be `BEST_FIT`); `strategy` is
  `BEST_FIT` (default), `FIRST_FIT`, or `LAST_FIT`. Every address this
  route returns must be freed through `POST /memory/free {addr}`, not
  assumed reclaimed automatically. Failure to allocate is a 503 with
  `error_code` `registry_full` (the engine's own allocation-tracking
  registry is already at its cap - free something first) or
  `insufficient_memory` (no block large enough) - not the client's
  request being malformed, so distinct from 400.
  `POST /memory/free {addr}` is a 400 with `error_code` `not_allocated`
  for an address this route never returned or one already freed, or
  `owner_changed` for a CONV/UMA address whose DOS-side owner no longer
  matches who it was allocated under: a program's memory is reclaimed
  by the engine when *that program* exits, invisibly to this API, and
  DOS can then hand the same segment to a different, currently-running
  program - freeing it at that point would silently corrupt that
  program's memory rather than fail cleanly, so the engine detects the
  ownership change and refuses instead. A block should be freed before
  the program active when it was allocated exits; this route is not a
  bridge-managed heap independent of DOS process lifetime. All four
  `error_code` values are `retryable: false` - none of these clear on
  their own, the caller has to act (free something, or accept the
  block is gone) before a retry can succeed.
- `GET /memory/allocations` lists every block `memory/allocate` has
  handed out and not yet freed (`{addr, size, area}`, `size` being the
  actually-reserved paragraph/page-rounded byte count, not necessarily
  the exact size requested), plus free-memory totals:
  `conventionalFreeBytes`, `conventionalLargestBlockBytes` (the figure
  that actually bounds the next allocation - a request can fail even
  when `conventionalFreeBytes` looks big enough, if free space is
  fragmented across several smaller blocks), `umbFreeBytes`, and
  `xmsFreeBytes`. `conventionalTruncated`/`umbTruncated` are true if the
  underlying MCB chain walk was cut short (a corrupt chain, or a
  1000-block hard cap) - the free-byte totals may then be an undercount,
  not the true picture. Not part of `dos/internals`, which exists only
  to hand out pointers.
- `dos/internals`' `memoryMap` walks the same MCB chain and is subject
  to the identical truncation risk, surfaced as `memoryMapTruncated`
  (added alongside the allocation routes above, since both read the
  same underlying chain-walk primitive).
- `GET /drive` always returns exactly 26 entries (A through Z, in
  order); an unmounted letter is just `{letter, mounted:false}`, the
  type/info/read_only/removable fields exist only when mounted.
  `info` on a local/FAT/ISO/CD-ROM drive is the mounted host
  filesystem path - a deliberate disclosure (this whole API sits
  behind the bearer token on loopback), not an oversight.
- `GET /mount/policy`'s `allowed_bases`/`allowed_image_roots` are
  already-canonicalized host paths, read once at engine startup from
  the primary config's `[webserver]` section and never mutated after
  - a client does not need to poll this for changes mid-session. An
  empty `allowed_image_roots` means every API-origin `drive/swap`
  fails by policy regardless of path; this is the out-of-the-box
  state until an operator configures `mount_allowed_image_roots`.
- `GET /mount/images` walks each configured image root
  non-recursively (subdirectories and their contents are never
  listed) and independently re-validates every entry against the same
  primitives `drive/swap` uses at mount time - a path this route
  returns will not then be refused by mount policy, though it can
  still fail `drive/swap`'s structural disk-image check
  (`not_a_disk_image`). Each root has its own file-count cap
  (`dosbox/info`'s `capabilities.drive.limits.max_images_per_root`);
  `truncated` on a root is true only when that root genuinely held
  more matching files than the cap, never as a false positive when a
  root holds exactly the cap's worth.
- `POST /mount/lock` is a one-way latch for the life of the process:
  once locked, every further mount attempt is refused, `drive/swap`
  and the guest's own MOUNT/IMGMOUNT/BOOT alike. There is no unlock.
- `POST /input/sequence` returns as soon as the chain is armed
  (`events_scheduled`), before any of it has dispatched. There are two
  independent dispatch engines - PIC-timed (`t`/`delay_ms`) and
  frame-timed (any event carrying a `frame` field) - each with its own
  409 "already in progress": one engine being busy never blocks
  starting a chain on the other, so two independent POSTs (one of each
  kind) can in principle run concurrently.
  `GET /input/replay/status` returns
  `{active, engine, total, dispatched, remaining, elapsed_ms, drift_ms,
  current_frame}`. `engine` is `"pic"`, `"frame"`, `"mixed"` (both
  chains happened to be active at once - the rare case above), or
  `"none"`. A finished, cancelled, or self-aborted chain keeps
  reporting its final total/dispatched/elapsed_ms/drift_ms rather than
  zeroing them out - checking status right after a replay ends is the
  normal sequence, not a race to catch it. `elapsed_ms` is wall-clock
  time since the chain was armed by the POST call - it is not zero at
  first dispatch, and includes any lead-in wait before the first
  event's own `t`/`delay_ms`/`frame` position is reached (a first event
  at `"t": 5000` shows `elapsed_ms` already near 5000 the moment it
  actually fires) - and is frozen at its final value once the chain
  stops rather than climbing with wall-clock time afterward. A chain that gets stuck
  waiting for keyboard buffer space (the guest never reads its input)
  self-aborts after `capabilities.input.limits.replay_stall_threshold_ms`
  of no progress, rather than wedging forever and leaving every later
  `input/sequence` call refused with 409 - `dispatched` staying below
  `total` after a run is the tell.
  `DELETE /input/replay` cancels whichever chain(s) are active; safe to
  call when nothing is running (`{cancelled: false}`, not an error).
- `InputRecording` coalesces consecutive `mouse_move` samples landing
  in the same rendered frame into one event (summed `x_rel`/`y_rel`,
  latest `x_abs`/`y_abs`) - host mice sample far faster than the
  render clock, and the frame-timed replay engine only ever dispatches
  on frame boundaries anyway, so nothing is lost. A recording is
  capped at `capabilities.input.limits.max_events` (32000) events;
  past that, further events are dropped and `truncated` (in both `GET
  /input/record/status` and the `POST /input/record/stop` response)
  goes true. Input injected via `POST /input/sequence` while recording
  is never captured - the `in_replay_dispatch` guard exists
  specifically so a replay can't re-record itself.
- `POST /input/record/stop?name=<name>` saves the recording into a
  process-lifetime, in-memory named store under `<name>` (`<=`
  `capabilities.input.limits.max_recording_name_length` chars,
  `[A-Za-z0-9_-]`) in the same call - not a separate request, so there
  is no window where the recording is stopped but not yet saved. An
  invalid name, or a new name when the store is already at
  `capabilities.input.limits.max_stored_recordings` capacity, is
  refused (400 / 503 `registry_full`) *before* the recording is
  actually stopped, so a refusal never loses data - the recording is
  left running and the caller can retry (e.g. after `DELETE
  /input/recordings/{name}` frees a slot) without having to redo it.
  Saving under a name that already exists overwrites it and never
  counts against the capacity limit. `?include_events=false` omits the
  `events` array from the stop response - the recording is still saved
  by name either way; this only controls whether the caller also
  receives the raw list. `GET /input/recordings` lists every stored
  recording's metadata (name, event count, duration, truncated) with
  no way to fetch the raw events except by replaying them (`POST
  /input/sequence {"recording": "<name>"}`). A stored recording always
  replays through the frame-timed dispatch engine (every recorded
  event carries a `frame`), and is copied, not consumed, on replay -
  the same name can be replayed any number of times.
- Validation lives engine-side. The engine is the trust boundary;
  a client talking HTTP directly must be subject to exactly the same
  limits as one going through a bridge.


## The 1.1 additions

Additive to 1.0 under the existing `debugger` feature flag (see above).
An engine that does not have the debugger built in still serves these
routes, so a client can distinguish "route exists but this build lacks
the capability" from "route doesn't exist at all": every debug route
returns HTTP 501 with `{"error": "<name>: debugger capability not
built in this binary"}` when the flag is false.

| Group | Routes | Notes |
|---|---|---|
| debugger | `debug/status` GET, `debug/pause` POST, `debug/continue` POST, `debug/step` POST | pause/resume/single-step (feature: debugger) |
| debugger | `debug/breakpoints` GET/POST/DELETE | execute, interrupt, and memory breakpoints (feature: debugger) |

Semantics specific to this group:

- `debug/pause` and `debug/continue` return immediately; they never
  block on the emulator reaching a particular state. A client polls
  `debug/status` to observe whether execution is paused. This is a
  hard requirement, not a style preference: an engine that blocks
  `debug/continue` until the next breakpoint hits (which may be never)
  makes the whole automation API unusable for the duration.
- A breakpoint added via `debug/breakpoints` POST does not take effect
  until the next `debug/continue`; adding one does not retroactively
  arm it while already running.
- Breakpoint `index` (returned by GET and by a successful POST) is the
  breakpoint's position in the engine's list, not a stable identifier.
  It shifts whenever any breakpoint is added or removed. Clients must
  re-list before deleting by index if other mutations may have
  happened in between.
- POST body shape for `debug/breakpoints`: `{"type": "execute" |
  "interrupt" | "memory", ...}`. `execute` and `memory` take `segment`
  and `offset`; `interrupt` takes `int` and optionally `ah`/`al` (omit
  either to match any value at that position -- e.g. `{"type":
  "interrupt", "int": 33, "ah": 61}` breaks on every DOS AH=0x3D file
  open, regardless of AL).
- Breakpoints of any kind require the engine to be running an
  interpreted CPU core (`core = normal` or `core = full`). Under the
  default `core = auto`, real-mode programs typically run on the
  dynamic recompiler, which has no breakpoint hooks: adding a
  breakpoint and continuing both report success, but it silently never
  fires. This is an engine-side limitation, not a protocol one, but
  clients should surface it -- e.g. warn when `debug/breakpoints` is
  used while the engine's configured core is not `normal`/`full`.
- `debug/breakpoints` POST rejects (400) an execute or memory
  breakpoint's `segment`/`offset` -- and a memory-kind `condition`'s own
  `segment`/`offset` -- when `segment*16+offset` falls outside the
  engine's emulated memory. This is checked with the CPU's current
  addressing mode in mind: while the guest is in protected mode, a
  request the flat real-mode formula would reject may in fact resolve
  through the GDT to a valid address, so the check does not apply there
  and the request goes through unchecked, same as before this existed.
  A client computing `segment`/`offset` itself (not just relaying an
  agent's numbers) is not exempt from this -- see "Validation lives
  engine-side" below.


## Conformance

An engine conforms to protocol 1.0 when it:

1. serves the session, screen, capture, and script groups,
2. serves `dosbox/info` with a `features` object and (from now on)
   the `mcp_protocol` field,
3. gates optional groups behind their feature flags,
4. authenticates every route except `hello` with the bearer token,
5. binds to loopback only,
6. serves its OpenAPI document at `/openapi.json`.

A client (bridge) conforms when it negotiates the version as specified,
refuses non-loopback targets, treats feature flags as call-time gates,
and keeps the token out of command lines, logs, and outputs.


## Changelog

Every revision of this specification gets an entry here stating what
changed and the version it produces.

- **1.0.0 (2026-07-17)** - initial specification: versioning and
  negotiation rules, transport and token requirements, hello route,
  feature flags, and the 1.0 route groups as shipped by
  dosbox-automation 0.84.
- **1.0.1 (draft)** - clarification, no behavior change: documents
  `cpu/state` GET (feature: cpu_registers) in the cpu route group. This
  route shipped alongside `cpu/register` PUT in 0.84 but was missing
  from the 1.0 route table.
- **1.0.2 (draft)** - clarification, no behavior change: documents the
  `memory/{segment}/{offset}/{length}` GET and `memory/{segment}/{offset}`
  PUT route forms, and the `If-Match`/412 compare-and-swap precondition
  on memory writes. Both shipped in 0.84 alongside the plain-offset
  routes but were missing from the 1.0 route table and semantics list.
- **1.1.0 (draft)** - fills in the `debugger` feature flag: adds the
  `debug/status`, `debug/pause`, `debug/continue`, `debug/step`, and
  `debug/breakpoints` routes (execute/interrupt/memory breakpoints).
  Non-blocking pause/continue/step and the engine-side `core =
  normal`/`full` requirement for breakpoints to fire are part of the
  contract, not just implementation notes.
- **1.2.0 (draft)** - `POST /memory/search` gains `limit` (request,
  default 256, max 4096) and `total`/`truncated` (response, additive).
  A genuine behavior change, not just documentation: previously
  `matches` held every match in the scanned span with no cap, which
  could be megabytes of response for a common byte value over a large
  range; a caller that only reads `matches` and doesn't pass `limit`
  now gets at most 256 by default. `total`/`truncated` exist precisely
  so that change is detectable rather than a silent truncation.
- **1.3.0 (draft)** - adds `POST /memory/scan` under the existing
  `memory` feature flag: masked byte-signature search (hex-pair bytes
  and `??` wildcards), the mechanism a client uses to locate a Ghidra
  function's live address from its byte pattern. Same `matches`/
  `total`/`truncated`/`limit` contract as `memory/search`.
- **1.4.0 (draft)** - adds `POST /memory/snapshot` and
  `POST /memory/diff` under the existing `memory` feature flag: a
  stateful snapshot-and-refine workflow (capture a range, then narrow
  it across repeated diff calls) for locating an address whose byte
  pattern isn't known ahead of time - a game's health or gold counter,
  not a Ghidra-analyzed function. Snapshots are process-lifetime state
  with their own byte and entry caps (client-visible via
  `dosbox/info`'s `capabilities.memory.limits`), not part of the
  request/response contract of any other route.
- **1.5.0 (draft)** - `debug/breakpoints` POST now rejects (400) an
  execute/memory breakpoint, or a memory-kind condition, whose
  `segment`/`offset` resolves outside emulated memory (real-mode
  addressing only - see the group's own semantics above for the
  protected-mode carve-out). A genuine behavior change, not just
  documentation: previously any `segment` (0..0xFFFF) and `offset`
  (0..0xFFFFFFFF) combination was accepted and handed straight to the
  engine, where the address computation can silently wrap into a small,
  in-range-looking location instead of the one actually requested.
- **1.6.0 (draft)** - documents `POST /memory/allocate` and
  `POST /memory/free` for the first time (shipped in an earlier engine
  version but never added to this spec) and adds
  `GET /memory/allocations` under the existing `memory` feature flag: a
  listing of live allocations plus free-memory totals (conventional,
  UMB, XMS). Not purely a documentation catch-up: `memory/allocate`'s
  503 and `memory/free`'s 400 previously carried no response body at
  all; both now send the full `{"error", "error_code", "retryable"}`
  shape every other error response in this spec already carries, with
  four distinct `error_code` values across the two routes
  (`registry_full`/`insufficient_memory`, `not_allocated`/
  `owner_changed`). `owner_changed` is itself a new refusal, not just a
  new code for an old one: `memory/free` now detects when a CONV/UMA
  address's DOS-side owner has changed since it was allocated (the
  program that owned it exited, and DOS handed the same memory to a
  different, currently-running program) and refuses rather than
  silently corrupting that program's memory. `dos/internals` gains an
  additive `memoryMapTruncated` field, for the same MCB-chain-walk
  truncation risk `memory/allocations` is also subject to.
- **1.7.0 (draft)** - adds `GET /drive` (full drive listing: mounted
  state, type, host info, read-only, removable), `GET /mount/policy`
  (locked state plus the configured `allowed_bases`/
  `allowed_image_roots`), and `GET /mount/images` (a per-root, non-
  recursive listing of files under the image roots, each independently
  re-validated against mount policy) under a new `drive` feature flag,
  which also now gates the previously-ungated `drive/swap` and
  `mount/lock`. Also a genuine behavior change to `drive/swap`, not
  just new routes: its error responses previously collapsed every
  failure into a bare 400 `{"error"}` with no `error_code`; they now
  carry the same `{"error", "error_code", "retryable"}` shape every
  other structured error response in this spec uses (see `memory/free`
  above), with distinct codes -
  `missing_field`, `invalid_drive_letter`, `mount_locked`,
  `file_not_found`, `mount_failed`, or a mount-policy reason
  (`does_not_resolve`, `not_regular_file`, `symlink_component`,
  `system_path`, `outside_whitelist`, `not_a_disk_image`) - and split
  across the status codes that actually distinguish them: `mount_locked`
  moved from a 400-shaped collapse into a clean 403 (previously 403 was
  documented as covering both "locked" and "policy violation"; policy
  violations are 400 now), and `file_not_found`/`mount_failed` are new
  404/500 cases that previously also fell into the generic 400. A
  client that only checked `response.ok` is unaffected; one that
  branched on the exact status code for `drive/swap` needs to widen
  its 400 handling to include 403/404/500.
- **1.8.0 (draft)** - `POST /capture/video/start` gains an optional
  `compression` field (request), set for `mode` and the recording
  started atomically in the same call; refused with a new 409 if
  `compression` is given while a capture is already running. `GET
  /capture/video/status` gains `path`, `frames`, `elapsed_ms`,
  `bytes_written`, and `compression_level` (response) - `path`/`frames`
  in particular close a real gap: an agent could tell a recording was
  active but not where it landed on disk or whether it actually wrote
  any frames. All under the existing `capture` feature flag.
- **1.9.0 (draft)** - adds `GET /input/replay/status` and
  `DELETE /input/replay` under the existing `input` feature flag.
  `POST /input/sequence`'s 400 `error_code`-less error responses are
  unchanged, but its own description now documents the 409 it could
  already return (a chain of the same kind already running) and the
  new status/cancel routes. A genuine engine-side behavior change, not
  just new routes: a replay chain that gets stuck waiting for keyboard
  buffer space (the guest never reads its input) previously wedged
  forever, refusing every later `input/sequence` call with 409
  indefinitely; it now self-aborts after
  `capabilities.input.limits.replay_stall_threshold_ms` (5000 by
  default) of no dispatch progress, logging a warning and leaving the
  engine free to accept a new chain.
- **1.10.0 (draft)** - documents `POST /input/record/start`,
  `POST /input/record/pause`, `POST /input/record/stop`, and
  `GET /input/record/status` for the first time (shipped earlier, but
  never added to this spec) under the existing `input` feature flag,
  and adds `GET /input/recordings` and
  `DELETE /input/recordings/{name}`, a process-lifetime in-memory named
  recording store. Two genuine behavior changes bundled with the
  documentation catch-up: `POST /input/record/stop` gains optional
  `name`/`include_events` query parameters (`name` saves the recording
  into the new store in the same call; `include_events=false` omits
  the `events` array from the response), and consecutive `mouse_move`
  samples landing in the same rendered frame are now coalesced into
  one event at record time rather than one event per host sample - a
  long mouse-driven install session no longer balloons into tens of
  thousands of near-duplicate events. Also adds `truncated` to both
  `POST /input/record/stop`'s response and
  `GET /input/record/status` (true once the new
  `capabilities.input.limits.max_events` recording cap is hit), and a
  `recording` field to `POST /input/sequence`'s request body as an
  alternative to `events`, to replay a stored recording by name (404 if
  the name doesn't exist).
- **1.11.0 (draft)** - adds `POST /batch`, a new `batch` feature flag,
  and `capabilities.batch` (`max_ops` 64, `max_read_bytes` 1 MiB,
  `max_write_bytes` 256 KiB, `base_timeout_ms`/`per_op_timeout_ms`/
  `max_timeout_ms` describing the `250 + 4*ops`, capped at 2000ms,
  timeout formula). Applies 1-64 memory (`mem_read`/`mem_write`/
  `mem_cas`), CPU register (`cpu_read`/`cpu_write`), I/O port
  (`port_read`/`port_write`), and freeze (`freeze_set`/`freeze_clear`)
  operations in order, in one pass on the emulation thread - no other
  request can interleave mid-batch, closing the tearing/race gap every
  other route has when a caller needs more than one operation applied
  as a unit (e.g. a `mem_cas` lock byte followed by several dependent
  writes). Not a transaction: there is no rollback, only in-order
  application and, with the default `on_error: "abort"`, an early stop
  once one operation fails (`mem_cas` conflict, `freeze_set` registry
  full, `freeze_clear` not found, or an out-of-range address for
  `mem_read`/`mem_write`/`mem_cas`/`freeze_set` - re-checked at
  execution time rather than trusted from validation, since a
  register-relative address isn't known until then and even a
  numeric one could in principle go stale between validation and
  execution - the only failure modes reachable once every operation is
  otherwise fully validated up front); `on_error: "continue"` applies
  every operation regardless. Every result is index-correlated with the
  request's `ops`, including `status: "skipped"` for an operation an
  abort never reached. `mem_write`/`mem_cas` are kept as separate
  operations rather than mem_write with an optional CAS field (unlike
  the single-op `PUT /memory/{offset}` route's `If-Match` header) -
  there is no per-operation header inside a JSON body, so `mem_cas`
  spells the same concept out as an explicit `expected` field instead.
