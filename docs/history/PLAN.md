# FactoryForge — Technical Plan

*Status: living document · Last updated: 2026-08-07*

See [PRD.md](../PRD.md) for why, [ROADMAP.md](../ROADMAP.md) for when.

## Architecture

```
┌────────────────────────────┐          ┌──────────────────────────┐
│  SIM ENGINE (Godot 4.7/C#) │          │  DRIVER SIDECAR (Python) │
│                            │          │                          │
│  3D render + Jolt physics  │  tag bus │  asyncua      (OPC UA)   │
│  scene editor / voxel grid │ ◄──────► │  pythonnet    (PLCSIM)   │
│  part behaviours           │    WS    │  python-snap7 (S7)       │
│  tag registry (authority)  │   JSON   │  built-in     (Modbus)   │
└────────────────────────────┘          └──────────────────────────┘
```

**The tag bus is the seam.** It is deliberately the first thing built and the
most carefully specified, because it is what lets a contributor add a driver in
Python without touching Godot, or a part in Godot without touching Python.

Protocol: [tag-bus.md](../tag-bus.md). **Two engine implementations already speak
it** — `harness/engine_stub.py` (Python) and `engine/src/TagBus/TagBusServer.cs`
(C#) — and the sidecar cannot tell them apart. Keep it that way.

### Repository layout

```
factoryforge/
  docs/          PRD, this plan, roadmap, tag bus spec
  sidecar/       Python package: bus client, drivers, minimal Modbus server
  harness/       Engine-side reference impl + headless scenes (Python)
  tests/         Protocol, driver, and end-to-end regression tests
  engine/        Godot 4.7 C# project: src/TagBus, src/Scenes, src/View
  examples/      Sorting.scl, FF_IO spec, Node-RED flow, mapping file
  tools/         drive_engine.py (parity check), drv_trace.py (driver tracing)
```

The harness is not throwaway. When the Godot engine exists, it stays as the
protocol reference and as CI's scene runner — CI must not need a GPU.

## Current state

**Done — the integration seam is proven with no 3D involved.**

| Component | Status |
|---|---|
| Tag bus protocol spec | Done — `docs/tag-bus.md` |
| Tag model, forcing, epoch handling | Done — `sidecar/factoryforge_sidecar/tags.py` |
| Bus client (sidecar side) | Done — `tagbus.py` |
| Bus server + fixed-timestep loop (engine side) | Done — `harness/engine_stub.py` |
| Headless sorting-by-height scene | Done — `harness/scene.py` |
| Driver ABC + registry | Done — `drivers/__init__.py` |
| Modbus TCP driver + minimal server | Done — `drivers/modbus_tcp.py`, `modbus/server.py` |
| Mock driver (CI) | Done — `drivers/mock.py` |
| **OPC UA client driver** | Done — `drivers/opcua_client.py` |
| **OPC UA server driver** | Done — `drivers/opcua_server.py` |
| Test suite | **38 passing** in ~32s |

### Godot engine (M2, complete)

| Component | Status |
|---|---|
| Godot 4.7.1 mono + C#/.NET 8 project, Jolt physics | Done — `engine/` |
| Tag bus server in C# | Done — `engine/src/TagBus/` |
| Headless `SortingScene` port | Done — `engine/src/Scenes/SortingScene.cs` |
| 3D view + orbit camera | Done — `engine/src/View/` |
| Parity: unchanged sidecar drives the C# engine | Done — `tools/drive_engine.py` |
| Voxel grid, free-look camera, CI parity run | Done — `engine/src/View/VoxelGrid.cs`, `FreeLookCamera.cs` |

### 3D Physics components (M3, complete)

| Component | Status |
|---|---|
| Surface-velocity conveyor belt | Done — `engine/src/Parts/ConveyorBelt.cs` |
| Non-jittering rigid box physics | Done — `engine/src/Parts/BoxPhysics.cs` |
| RayCast3D photoelectric sensors | Done — `engine/src/Parts/PhotoelectricSensor.cs` |
| AnimatableBody3D pneumatic pusher | Done — `engine/src/Parts/PusherMechanism.cs` |
| Emitter and Area3D remover | Done — `engine/src/Parts/Emitter.cs`, `Remover.cs` |
| Control panel & indicator lamps | Done — `engine/src/Parts/ButtonPanel.cs` |
| 100% 3D Jolt Physics scene manager | Done — `engine/src/Scenes/PhysicsScene.cs` |

### Scene editor suite (M4, complete)

| Component | Status |
|---|---|
| 3D placement controller & voxel snapping | Done — `engine/src/Editor/SceneEditor.cs` |
| 8-part palette UI panel | Done — `engine/src/Editor/PartPaletteUI.cs` |
| Tag inspector UI & live forcing | Done — `engine/src/Editor/TagInspectorUI.cs` |
| Dynamic part tag bus registration | Done — `engine/src/Editor/PartTagManager.cs` |
| Live part property inspector | Done — `engine/src/Editor/PartPropertyInspectorUI.cs` |
| Save/load JSON scene format & top toolbar | Done — `engine/src/Editor/SceneData.cs`, `SceneToolbarUI.cs` |
| 3D visual selection wireframe gizmo | Done — `engine/src/Editor/SelectionGizmo.cs` |
| Inclined ramp (Chute) & Stack light parts | Done — `engine/src/Parts/Chute.cs`, `StackLight.cs` |

### Siemens breadth (M5, complete)

| Component | Status |
|---|---|
| Siemens PLCSIM Advanced Simulation Runtime API driver | Done — `sidecar/factoryforge_sidecar/drivers/plcsim_advanced.py` |
| Siemens S7 protocol driver via Snap7 | Done — `sidecar/factoryforge_sidecar/drivers/s7_snap7.py` |
| Siemens drivers unit test suite (41 tests passing) | Done — `tests/test_siemens.py` |

The renderer is optional: `Main` skips building the view entirely when
`DisplayServer.GetName() == "headless"`, so CI runs the same scene with no GPU.
`SceneView` only ever *reads* simulation state, which is what makes that safe.

Modbus arrived first because it fell out of building the bus and needs no
vendor software. It stays as the zero-dependency CI driver. **OPC UA is the
primary integration path and is verified against real hardware.**

## Design decisions worth knowing

### Tag direction is from the controller's point of view

`output` = PLC writes it, simulator reads it (motor, valve, lamp).
`input` = simulator writes it, PLC reads it (sensor, button, counter).

This matches Factory I/O. Students already hold this model; inverting it would
be gratuitous. It is enforced: a `write` naming an `input` is rejected, and
`force` is the single deliberate exception, which is what makes fault injection
and automated testing possible.

### Epoch guards against scene reloads

Every `describe` bumps an epoch; every `write` carries the epoch it was based
on; the engine drops stale writes. Without this, a write in flight during a
scene change lands on whatever tag inherited that id.

### Fixed timestep with a wall-clock accumulator

The scene always advances by exactly `tick_ms`, never by measured elapsed time
— variable dt would make runs non-reproducible and defeat the regression scene.
But sleeping for `tick_ms` and stepping once per wake runs the simulation
*slow*, because timer granularity exceeds the interval. So real elapsed time is
accumulated and however many whole fixed steps fit are run, capped to avoid a
death spiral.

This was found empirically: the first implementation ran at ~60% of real time.

### Windows asyncio timer resolution

On Windows the asyncio clock resolution is **15.6 ms**, and `_run_once` treats
any timer inside that window as already expired. `await asyncio.sleep(0.01)`
therefore returns *immediately*. A 500-iteration poll loop completes in 10 ms of
real time.

Consequences, both already applied:
- The engine loop must measure real elapsed time, never count iterations.
- Tests must wait on events (`wait_for` on a tag), not poll with short sleeps.

### The S7-1500 forces a 1000 ms publishing interval

**This is the single most important constraint discovered so far, and it shapes
the whole driver design.**

Requesting a 50 ms publishing interval from a real S7-1500 (CPU 1511-1 PN,
FW V2.9, via PLCSIM Advanced) gets silently revised:

```
CreateSubscriptionResult(..., RevisedPublishingInterval=1000.0, ...)
```

With the default monitored-item queue size, only the latest value per publish
cycle survives. So **any signal that rises and falls inside one second can be
dropped entirely.** A 500 ms pulse survives or vanishes essentially at random
depending on where it lands in the cycle — it was observed passing in an
isolated test and failing consistently in a live run.

Consequences:

- Any PLC signal the simulator must observe should be **held longer than
  1000 ms**. This is why the emitter square wave uses a 1.5 s half-period.
- Sub-second control loops are **not achievable** through OPC UA subscriptions
  on this server.

**Polling is dramatically faster than subscribing here.** Direct `read_value`
calls sustained ~1100 reads/second against the same CPU. A 50 ms poll gives 20x
better latency than the forced 1000 ms subscription. The driver should offer
polling as an alternative — see "Next steps".

Trying to fix this with a larger `queuesize` and a faster `sampling_interval`
made things *worse*: the S7 appears to reject those parameters, and the
monitored item then reported nothing at all. Do not assume standard OPC UA
tuning knobs are honoured.

### Short PLC pulses are invisible to a sampled subscription

OPC UA subscriptions **sample**; they do not capture every transition. At the
default 50 ms publishing interval, a signal the PLC holds for one scan (~10 ms)
is usually never observed. Found empirically: the fake PLC emitted a box every
3 s and the simulator saw roughly one in three.

Anything the simulator must observe has to be held longer than one sampling
period. An early `Sorting.scl` widened its emit trigger to 200 ms with a `TP`
timer — **that was not enough on a real S7**, whose 1000 ms publishing interval
swallowed it. v0.3 uses a square wave with a 1.5 s half-period instead. Do not
"fix" this by lowering the publish interval: servers are not obliged to honour
small values, and a real PLC on a real network will be worse.

This belongs in the part-authoring guide too: any part triggered by an edge
should tolerate a pulse arriving late or, in pathological cases, not at all.

### A hand-written Modbus server

pymodbus 3.14 deprecated `ModbusDeviceContext`/`ModbusSequentialDataBlock`
(removal in v4), and its replacement stores coils as packed 16-bit registers,
which makes per-bit mapping awkward and couples us to internals in flux.
Implementing the eight function codes a simulator needs was less code than that
adapter, and removes a moving dependency. pymodbus remains the *test master*,
which is its stable half.

## Next: the OPC UA driver

**Why first:** widely compatible — Siemens S7-1500, Codesys, Beckhoff, WAGO,
Ignition, and any SCADA speak it. Modbus reaches OpenPLC and little else.

**Reference target: S7-PLCSIM Advanced.** It runs genuine S7-1500 firmware in a
Windows process behind a virtual Ethernet adapter, which is what lets external
clients reach the simulated CPU at all. Plain S7-PLCSIM (the one bundled with
TIA Portal) does not expose a usable network interface to third-party clients —
this is why Factory I/O ships two separate Siemens simulator drivers,
`S7PLCSIMDriver` (COM, via `Interop.S7PROSIMLib`) and `S7PLCSIMAdvancedClientV3`.
Anyone documenting setup must be explicit about which product is required.

**Licensing, stated plainly:** an S7-1500's OPC UA *server* needs a paid SIMATIC
runtime licence; unlicensed it runs a 100-variable trial. The v1 scene has ten
tags, so this does not bite at our scope. The *client* side is always free.
Document the limit so nobody is surprised when their scene grows.

**A licence-free alternative exists, deliberately deferred.** PLCSIM Advanced
exposes a .NET Simulation Runtime API
(`Siemens.Simatic.Simulation.Runtime.Api.x64`) that reads and writes I/O
directly — no OPC UA licence, no TCP, lower latency. It is what Factory I/O
uses. It is *not* M1 because it only helps users who own PLCSIM Advanced,
whereas OPC UA reaches Codesys, Beckhoff, WAGO, Ignition and any SCADA. It is
scheduled for M5 as the frictionless Siemens path.

**Reference CPU: S7-1500.** This is forced, not chosen — PLCSIM Advanced
simulates the S7-1500 family (plus ET 200SP and the Software Controller), and
not the S7-1200.

S7-1200 is *expected* to work with our client-mode driver: it supports an OPC UA
server from firmware V4.4+, and from TIA V16 the server licence is included in
the software. It is server-only — it cannot act as an OPC UA client — which
suits us fine, because in client mode the sidecar is the client. But the only
simulator that covers S7-1200 is plain PLCSIM, which has no external network
interface, so S7-1200 is reachable only on real hardware. **We cannot test it.**
Document it as expected-to-work, untested, and flag it for anyone with hardware.

### Two roles, in order

1. **Client mode first.** The sidecar connects to the PLC's OPC UA server,
   browses the address space, subscribes to changes, and writes back. This is
   the setup already proven working against S7-1200/1500, so it validates
   fastest.
2. **Server mode second.** The sidecar *exposes* the scene's tags as an OPC UA
   server, so anything — SCADA, Ignition, another PLC, a student's Python
   script — can connect without the sim needing to know about it. This is what
   makes "widely compatible" real.

Both use `asyncua`. Server mode is what most reduces support burden long term,
because it inverts who must be configured.

### Design notes

- Map tags to nodes as `ns=2;s=<tag_id>`, so node ids are stable and readable.
- Subscribe rather than poll; use a monitored-item queue matched to the tick.
- Tag-to-node mapping must be rebuilt on every `describe`, like the Modbus
  driver already does.
- Anonymous, unencrypted access by default for teaching, with certificate and
  username auth available. Bind loopback unless explicitly told otherwise.
- Reconnection must be automatic and visible in the UI — "why won't it connect"
  is the single most common student question.

## Verification

- **Protocol/unit** — tag coercion, forcing, epoch handling, Modbus PDU encoding.
  No sockets; fast.
- **Driver integration** — a real client/master drives the sim through the bus.
  Already done for Modbus via pymodbus.
- **Deterministic regression** — `test_sorting.py` runs the scene synchronously
  with a scripted controller and asserts box outcomes. This is what stops
  physics tuning from silently breaking scenes, and it must keep passing when
  the Godot engine replaces the stub.
- **End to end (manual, per milestone)** — one TIA Portal program drives
  S7-PLCSIM Advanced through the OPC UA driver, and the same scene is driven by
  OpenPLC over Modbus. Two controllers, two protocols, one scene.

**No physical PLC is available to this project.** PLCSIM Advanced runs genuine
S7-1500 firmware over a virtual Ethernet adapter, so protocol behaviour is
faithful; real-hardware timing and network latency are not covered. The tag bus
is explicitly not hard real-time and tolerates jitter by design, which limits
the blast radius, but hardware validation stays an open help-wanted item.

Run: `python -m pytest` from the repo root.

## Open questions

- Project name. `factoryforge` is a working placeholder.
- OPC UA server mode: expose a flat tag list, or a folder hierarchy mirroring parts?
- Whether the sidecar ships bundled (PyInstaller) or as a `pip install`.
- Whether to reuse Factory I/O's localisation-key convention — good design, but
  consider whether the resemblance is too close.
