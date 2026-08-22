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
| capture | `capture/video/start`, `capture/video/stop`, `capture/video/status` | video recording control |
| input | `input/sequence`, `input/type` | named-key sequences; paced string typing (feature: input) |
| memory | `memory/{offset}/{length}` GET, `memory/{segment}/{offset}/{length}` GET, `memory/{offset}` PUT, `memory/{segment}/{offset}` PUT, `memory/search`, `memory/scan`, `memory/snapshot`, `memory/diff` | guest physical memory (feature: memory) |
| freeze | `memory/freeze` POST/GET/DELETE | per-frame value locks (feature: freeze) |
| dos | `dos/internals` | DOS internals incl. the MCB memory map (feature: memory) |
| cpu | `cpu/register` PUT, `cpu/state` GET | writes (feature: cpu_control), reads (feature: cpu_registers) |
| io | `io/port` GET/PUT | port I/O (feature: port_io) |
| script | `script/load`, `script/start`, `script/status`, `script/stop` | sandboxed Lua; `script/load` takes the raw source as a text/plain body |
| media | `drive/swap`, mount routes | disk image swapping; mount policy applies underneath |

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
