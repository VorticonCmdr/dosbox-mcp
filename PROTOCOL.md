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
| memory | `memory/{offset}/{length}` GET, `memory/{offset}` PUT, `memory/search` | guest physical memory (feature: memory) |
| freeze | `memory/freeze` POST/GET/DELETE | per-frame value locks (feature: freeze) |
| dos | `dos/internals` | DOS internals incl. the MCB memory map (feature: memory) |
| cpu | `cpu/register` PUT | register writes (feature: cpu_control) |
| io | `io/port` GET/PUT | port I/O (feature: port_io) |
| script | `script/load`, `script/start`, `script/status`, `script/stop` | sandboxed Lua; `script/load` takes the raw source as a text/plain body |
| media | `drive/swap`, mount routes | disk image swapping; mount policy applies underneath |

Semantics that are part of the contract, not just the schemas:

- Memory routes address guest physical memory with plain integer
  offsets; JSON output (base64 payload) is selected with an
  `Accept: application/json` header, binary is the default.
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
- **1.1.0 (draft)** - fills in the `debugger` feature flag: adds the
  `debug/status`, `debug/pause`, `debug/continue`, `debug/step`, and
  `debug/breakpoints` routes (execute/interrupt/memory breakpoints).
  Non-blocking pause/continue/step and the engine-side `core =
  normal`/`full` requirement for breakpoints to fire are part of the
  contract, not just implementation notes.
