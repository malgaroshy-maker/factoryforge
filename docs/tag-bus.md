# Tag Bus Protocol v0

The tag bus is the seam between the **simulation engine** (3D, physics, parts) and the
**driver sidecar** (PLC communication). It exists so that a contributor can add a driver in
Python without touching the engine, and add a part in the engine without touching Python.

Everything in this document is versioned. Breaking changes bump `protocol` in the `hello`
message.

## Roles

| Role | Who | Responsibility |
|---|---|---|
| **Engine** | Godot 4.6 / C# (Python stub for tests) | Owns the authoritative tag table. Acts as the **server**. |
| **Sidecar** | Python | Owns PLC protocol connections. Acts as the **client**. |

The engine is authoritative. If the two ever disagree about a tag's value, the engine wins.
The sidecar holds a cache purely so its drivers can serve reads without a round-trip.

Only one sidecar may be connected at a time. A second connection is rejected with
`status.error`. This is deliberate — two drivers writing the same output tag is a bug, not a
feature.

### Connection lifecycle

One shape, and both engines answer it identically — `engine/fixtures/server_cases.json`
drives every line of this over the wire against each of them.

- Every connection is greeted with `hello` and then `describe`, in that order, before
  anything else. A reconnect is greeted exactly like a first connection.
- The engine is the authority, so a reconnecting sidecar is handed the current state, not a
  blank table: whatever the last controller wrote is still what the machine is doing.
- Every `describe` advances the `epoch`, including the one a reconnect gets, so no frame
  composed against an earlier connection can pass for a current one.
- A **second** sidecar is refused *after* its handshake completes, with
  `{"t":"status","level":"error","code":"already_connected"}`, and the connection is then
  closed. It is never sent `hello`, the tag set, or an epoch. Refusing it at the socket
  instead would leave it unable to tell "another driver has this engine" from "there is no
  engine here", and the two answers call for opposite responses.
- What a sidecar said about its controller (`controller`, below) belongs to its session.
  The engine forgets it on disconnect, and a reconnecting sidecar says it again.
- A sidecar that vanishes **without** closing — a crash, a killed process — does not hold
  the seat. The engine's idea of who is connected has to be refreshed before it is used to
  turn anybody away, or the first reconnect after a crash is refused as a second sidecar.

## Transport

JSON text frames over a WebSocket on `ws://127.0.0.1:7411/tagbus`.

Port 7411 sits next to Factory I/O's 7410 so the two can run side by side during the mod-spike
phase without a clash.

JSON was chosen over MessagePack for v0 because it is debuggable from a browser console and
tag counts are small (a full sorting scene is under 200 tags at a 10 ms tick — roughly 20 kB/s
worst case, and far less in practice because updates are delta-only). If profiling ever shows
this matters, swap the codec; the message shapes stay identical.

Bind to loopback only. There is no authentication, and there must never be a reason to expose
this to a network.

## Tag model

```json
{
  "id": "conveyor_1.rotate",
  "name": "Belt Conveyor 1 (Rotate)",
  "type": "bit",
  "kind": "output",
  "value": false
}
```

| Field | Notes |
|---|---|
| `id` | Stable, machine-readable, unique within a scene. Format `<part_id>.<tag_name>`. Survives renaming. |
| `name` | Human-readable, shown in UI. May change freely; never key off this. |
| `type` | `bit` \| `int` \| `float` |
| `kind` | `input` \| `output` |
| `value` | `bool` for `bit`, `int` for `int`, `float` for `float` |

### Value ranges are part of the contract

- **`bit`** accepts `true`/`false`, and `0`/`1`. A stray `2` is a bug, not a bit, and is
  rejected.
- **`int`** is a **signed 32-bit integer**: `-2147483648` … `2147483647`. Anything outside
  that is rejected. Python would happily hold a larger integer and the C# engine would not,
  and a bus whose two engines disagree about what `2147483648` means is not a contract. It
  is also what a PLC has — an S7 `DInt` is exactly this.
- **`float`** is a double, and must be **finite**. `NaN` and `±Infinity` are rejected at
  every edge: they cannot be written, cannot be forced, and are not valid JSON in the first
  place, so a value that reached a tag would leave as a payload the other engine's parser
  refuses.

A value a tag cannot hold is refused **per value**. The rest of the batch still lands, and
the engine answers with a `status` of level `warn` and code `bad_value` naming the tags it
refused. A whole frame the engine cannot read at all draws `bad_message`. Neither is ever a
reason to close the connection.

### A tag's type belongs to a `describe`, not to its id

A tag's `type` can change between two `describe`s while its `id` stays the same.
The case that does it today is an analog input switched between engineering
units and raw card counts in the property inspector (IP-16): `tank.level` is a
`float` percentage by default and an `int` count in S7 raw or 4–20 mA mode, and
at 4–20 mA the part also gains a `tank.wirebreak` bit. The engine announces the
change the way it announces any other edit to the tag set — a fresh `describe`
with a new `epoch` — so a driver that rebuilds its map from every `describe`, as
the epoch rules above already require, handles it with nothing extra. A driver
that cached types by id across epochs does not.

A raw analog value is an ordinary `int` tag. It carries a 16-bit card's codes
inside the 32-bit range: 0 … 27648 across the measuring range, up to 32511 of
overrange and down to -4864 of underrange, 32767 (7FFFh) for overflow and for a
broken 4–20 mA wire, and -32768 (8000h) for underflow. Like every `int`, it is a
JSON integer on every channel — `describe`, `update`, `observe` — and never
`27648.0`; `engine/fixtures/server_cases.json` checks that on both engines. The
OPC UA client writes `int` tags as Int32, so the PLC-side variable for a raw
count is a `DInt`, not the `Int` a real AI channel's `%IW` would be; `NORM_X`
accepts either.

### `kind` is from the controller's point of view

This trips people up constantly, so it is stated once, loudly, and never varies:

- **`output`** — the PLC writes it, the simulator reads it. A motor, a valve, a lamp.
- **`input`** — the simulator writes it, the PLC reads it. A sensor, a button, a counter.

This matches Factory I/O's convention. Students already have this model in their heads and
inverting it would be gratuitous.

Consequence for message direction:

- Sidecar sends `write` for `output` tags only.
- Engine sends `update` for `input` tags only.

A `write` naming an `input` tag, or vice versa, is a protocol error and is rejected. The one
exception is **forcing** (see below).

## Messages

Every message is a JSON object with a `t` field naming its kind.

### `hello` — engine → sidecar, on connect

```json
{ "t": "hello", "protocol": 0, "engine": "factoryforge-engine/0.1.0", "tick_ms": 10 }
```

Sent immediately on connection, before any `describe`. The sidecar must check `protocol` and
disconnect on mismatch rather than guessing.

### `describe` — engine → sidecar

```json
{
  "t": "describe",
  "scene": "sorting-by-height",
  "epoch": 3,
  "tags": [ { "id": "...", "name": "...", "type": "bit", "kind": "output", "value": false } ]
}
```

The complete tag list. Sent after `hello`, and again on every scene load or edit that changes
the tag set.

`epoch` increments on each `describe`. It is the mechanism that makes scene reloads safe:

- The sidecar must stamp every `write` with the `epoch` it was based on.
- The engine drops any `write` carrying a stale `epoch`.

Without this, a `write` in flight during a scene change lands on whatever tag inherited that
id in the new scene. Drivers should rebuild their address maps whenever `epoch` changes.

The stamp is an **integer**, and the two failures are not the same failure:

- An epoch that is an integer but not the current one is **stale**. The frame is dropped in
  silence — a driver's map briefly lagging a scene edit is normal, and the driver re-reads
  and republishes on its next `rebuild`.
- An epoch that is missing, or is not an integer at all (a string, `3.0`, `true`), is
  **malformed**. The engine cannot tell current from stale, so it refuses the frame and says
  so with `bad_message`. That is a bug in the sidecar rather than a race, and silence there
  teaches nobody anything. `3.0` is worth naming: Python compares `3.0 == 3` as true, so one
  engine applied it while the other refused it — the same frame with two answers.

### `write` — sidecar → engine

```json
{ "t": "write", "epoch": 3, "values": { "conveyor_1.rotate": true, "pusher_1.extend": false } }
```

Batched. Send at most one per tick; coalesce multiple driver writes within a tick into one
message. Unknown ids are ignored with a `status.warn`, not an error — a driver's address map
briefly lagging a scene edit is normal.

### `update` — engine → sidecar

```json
{ "t": "update", "tick": 14203, "values": { "sensor_low.detect": true } }
```

**Delta-only.** Contains solely the `input` tags whose values changed since the last `update`.
A tick with no changes sends nothing at all — an idle scene should produce zero traffic.

`tick` is a monotonically increasing counter, useful for diagnosing latency and dropped frames.

Float comparison uses an epsilon (default `1e-6`) so that physics jitter in the last bits does
not generate a message every single tick.

The epsilon suppresses the **store**, not only the comparison. A value that does not
meaningfully differ is not written into the table at all, so the reference it is next
compared against is the last value actually published. Storing it anyway — which both
engines used to do while reporting "no change" — lets the reference creep by a hair a scan,
so a signal drifting `1e-9` per scan crosses the epsilon on the very next comparison and
publishes on every single scan, which is the traffic the epsilon exists to prevent.

### `observe` — engine → sidecar

```json
{ "t": "observe", "tick": 14203, "forced": { "conveyor_1.rotate": false }, "cleared": ["sensor_high.detect"] }
```

**Delta-only**, like `update`, and sent on the same tick — but *before* it.
`forced` names the tags the engine currently has pinned and the value each is
pinned to; `cleared` names tags whose force has just been released. A scene with
nothing forced never produces one.

This is what makes a force visible from the sidecar. `update` carries `input`
tags only, so without it a `conveyor_1.rotate` forced off while the PLC
commands it on reads as *on* from every driver and every status display — which
defeats the one diagnostic forcing exists for.

It is a separate message rather than a field on `update` for a reason worth
stating plainly: drivers hang their `push()` hook off `update`, and a driver's
`push()` writes what it is handed into the PLC. Routing observed *output* state
through that hook would write a simulator-invented value back into a node the
PLC owns — a worse fault than the one it fixes. Nothing subscribes to `observe`
by default; it updates the sidecar's cache, so `read()` tells the truth.

Ordering matters and is fixed: `observe` precedes `update` within a tick, so a
release reaches the sidecar before the value it reveals.

### `force` — sidecar → engine

```json
{ "t": "force", "epoch": 3, "values": { "sensor_low.detect": true }, "clear": ["sensor_high.detect"] }
```

Overrides a tag's value regardless of `kind`, including simulator-owned `input` tags. This is
the deliberate exception to the direction rule, and it is what makes fault injection and
automated testing possible — you can assert a PLC program's response to a sensor that is
stuck on without physically arranging boxes.

Forced tags keep their forced value until cleared. The engine echoes forced state in
`describe` — as a `"forced": true` field on the tag — and republishes every later change
to it on `observe`. Do not use `force` as a shortcut for `write`.

A forced tag still absorbs writes underneath the pin: the engine stores the written value
so that releasing the force reveals whatever the controller is currently commanding,
rather than the value that was in effect when the force was applied.

### `status` — either direction

```json
{ "t": "status", "level": "info", "code": "driver_connected", "message": "Modbus TCP server listening on 0.0.0.0:502" }
```

`level` is `info` | `warn` | `error`. Surfaced in the engine's UI so a student can see why
their PLC will not connect without reading a log file.

### `controller` — sidecar → engine, optional

```json
{ "t": "controller", "ready": true, "driver": "opcua-client", "message": "connected to opc.tcp://192.168.1.20:4840; 10 of 10 tags bound" }
```

Whether the sidecar's driver has **reached the controller**: whether the PLC behind the
sidecar can now see the tags. Having a sidecar attached is not this. `connect` starts its
driver only after the `describe` arrives, and an OPC UA or S7 driver can take seconds to
reach its PLC after that. The grader opens its window on this message (IP-30).

| Field | Notes |
|---|---|
| `ready` | JSON `true` or `false`. Not `1`/`0`: this is a protocol field, not a tag value. |
| `driver` | The reporting driver's name, e.g. `opcua-client`. With several drivers, their names joined by `, `. |
| `message` | Human-readable detail: where it connected, or why it has not. May be empty, never absent. |

`ready: true` means the following, depending on the driver:

- **A client driver** (OPC UA client, S7 via snap7, PLCSIM Advanced) is connected to the PLC
  **and** has bound the current tag set, with the simulator's inputs written into the PLC.
  Connected alone is not enough, because until the map is in, a sensor edge has nowhere to
  go.
- **A server driver** (Modbus TCP, OPC UA server) has had a request from a controller that
  it answered. For OPC UA, that means a read, a write or a subscription that touches one of
  the scene's tags. Listening is not enough: the student's PLC may start polling seconds
  later. A server cannot tell a controller that has gone away from one that is between two
  polls, so once a server driver is reached it stays reached until it stops.
- **MQTT** has connected to the broker, subscribed, and published the inputs as retained
  messages. That is as far as this driver can see: the broker does not say who
  subscribes.

When it is sent:

- Never before the sidecar's drivers have finished their first `rebuild` on the
  connection. After that, `ready: true` is held back while a rebuild is in progress.
- Once per change of the answer. A driver that loses its PLC sends `ready: false`, and
  getting the PLC back sends `ready: true` again. A driver that starts connecting sends
  `ready: false` first, which tells the engine that this sidecar reports at all.
- On every reconnect to the engine, whatever the answer is. The engine forgets a
  sidecar's report when that sidecar disconnects, exactly as it forgets its seat.
- A new `describe` does not reset it. The drivers rebuild their maps, and the link to
  the PLC is unchanged.

With several drivers, the sidecar is ready only when every driver that reports is ready. A
driver that never reports is not counted either way.

The engine never replies to a well-formed `controller` message. A malformed one draws
`status` `warn` `bad_message`, the connection stays open, and the last good report stands.
A malformed message is one where `ready` is missing or not a boolean, or where `driver` or
`message` is missing or not a string. The engine ignores fields it does not know, so a
later revision can add one without an older engine refusing the frame. Both engines record
the report: `EngineStub.controller`, and `TagBusServer.ControllerReported`, `ControllerReady`,
`ControllerDriver` and `ControllerMessage`.

It is **optional in both directions**, and adding it did not bump `protocol`:

- A sidecar that never sends it still works. Its engine simply never learns when its PLC
  arrived, and the grader falls back to opening on `describe` after a bounded wait (see
  `docs/GRADING.md`). Hand-written clients and sidecars from before IP-30 are like this.
- An engine that does not know it ignores it (see *Unknown messages* below). That is
  true of every engine from before IP-30, and `server_cases.json` now pins it.

It is a message of its own and not a `status` code, because something acts on it. `status`
is free text for a person to read, and drivers have always chosen its codes for
themselves. This document's own example code, `driver_connected`, means "a Modbus server is
listening", which is exactly the thing that is *not* readiness.

### Unknown messages

A message whose `t` the receiver does not know is ignored, and nothing is sent back. This
holds for either end and for both engines. It is what lets an optional message like
`controller` be added without a protocol bump. An engine that one day answered unknown
kinds with `bad_message` would break every newer sidecar, so
`engine/fixtures/server_cases.json` checks that both engines stay silent.

## Timing

The engine ticks at `tick_ms` (default 10 ms) and sends at most one `update` per tick. The
sidecar sends at most one `write` per tick.

This bus is **not** hard real-time and does not pretend to be. A tag written by the PLC lands
in the simulation within one to two ticks. That is well inside the tolerance of every teaching
scenario, and matches what Factory I/O's own drivers achieve over TCP.

Drivers run on their own asyncio tasks and must never block the bus. A driver that stalls gets
its writes dropped, not the whole simulation.

The sidecar enforces that rather than trusting it. Incoming frames are applied to the
sidecar's cache on the receive loop and the driver hooks are run on a separate task, so a
driver that takes half a second to write a PLC does not hold up the next `update`, the next
`observe`, or the next `describe` — for itself or for anybody else. Updates that arrive while
a hook is busy are coalesced: they are deltas, so the merge of two is the message the engine
would have sent had it batched them, and a driver that has fallen behind wants the current
state rather than a queue of history.

Two rules follow from coalescing being a merge and not a queue. A `describe` ends the deltas
behind it — including one already being handed to the hooks when it arrived, which stops
there — because they are deltas against a tag set nobody has any more, and `rebuild` re-reads
the plant before returning anyway. And the merge respects arrival order *across* an
`observe`'s two collections: a tag forced, released and forced again while a hook is busy
arrives forced, not forced *and* released, which applied in the order they are sent would
leave the pin gone.

### Epoch ownership

The **driver** owns cancellation of its own data flow. Anything still reading through the
previous epoch's address map must be stopped inside `rebuild`, before the new map goes in.

The **sidecar** owns the epoch, and holds writes back until every driver has returned from
`rebuild`. Writes queued in that window are **discarded**, not delivered late: they came out
of a map older than the epoch they would be stamped with, and the epoch stamp is the engine's
only defence against a write in flight across a scene change. `rebuild` re-reads the PLC
before returning, which is what puts the current value back on the bus.

## Reference

- Engine-side server: `sidecar/factoryforge_sidecar/engine_stub.py` (Python reference implementation; `harness/engine_stub.py` is an alias of it)
- Sidecar client: `sidecar/factoryforge_sidecar/tagbus.py`
- Tag model: `sidecar/factoryforge_sidecar/tags.py`
