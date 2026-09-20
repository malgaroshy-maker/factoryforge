# AGENTS.md — handoff for the next agent

Read this first, then [`docs/HARDENING_PLAN.md`](docs/HARDENING_PLAN.md) for what
is open and [`docs/ROADMAP.md`](docs/ROADMAP.md) for what is done.

The thirteen completed plan documents live in
[`docs/history/`](docs/history/) — including `PLAN.md`, which this line used to
send you to first. They are worth reading for *why* something was built the way
it was, and they are not current instructions; the figures in them are the
figures of their own day.

**FactoryForge** is a free, open 3D factory simulator for learning PLC
programming — a replacement for Factory I/O, which is stagnant, closed to custom
parts, and €278/year. See [`docs/PRD.md`](docs/PRD.md) for the full rationale.

---

## Absolute paths on this machine

| What | Path |
|---|---|
| **Repo** | `C:\Users\masal\source\factoryforge` |
| **Godot 4.7.2 mono** (console build — use this, it prints to stdout) | `D:\Godot_v4.7.2-stable_mono_win64\Godot_v4.7.2-stable_mono_win64_console.exe` |
| Python 3.12 | `D:\Python312` (on PATH as `python`) |
| .NET SDK | `C:\Program Files\dotnet` (v10; the project targets net8.0) |
| Node-RED user dir | `C:\Users\masal\.node-red` — **contains the user's own flows, never overwrite** |
| Factory I/O install (reference only) | `F:\Program Files (x86)\Real Games\Factory IO` |

## Live hardware

| What | Detail |
|---|---|
| **S7-PLCSIM Advanced** | `192.168.1.20`, OPC UA at `opc.tcp://192.168.1.20:4840` |
| CPU | 1511-1 PN, FW V2.9, TIA Portal V19 |
| PLC program | Global DB `FF_IO` + FB `Sorting` (instance `Sorting_DB`), currently **SCL v0.3** |
| Node ids | `ns=3;s="FF_IO"."ConveyorRotate"` etc. — quotes are part of the identifier |

The user has **no physical PLC**. PLCSIM Advanced is the reference target and
simulates only the S7-1500 family. Plain bundled PLCSIM has no external network
interface and cannot be used.

---

## Architecture

```
┌────────────────────────────┐          ┌──────────────────────────┐
│  SIM ENGINE                │          │  DRIVER SIDECAR (Python) │
│  Godot 4.7 / C#  (engine/) │  tag bus │  asyncua      (OPC UA)   │
│  or Python stub (harness/) │ ◄──────► │  built-in     (Modbus)   │
│                            │  WS/JSON │  mock         (tests)    │
└────────────────────────────┘          └──────────────────────────┘
```

The **tag bus** ([`docs/tag-bus.md`](docs/tag-bus.md)) is the seam and the most
important contract in the project. Two engine implementations already speak it
(`harness/engine_stub.py` and `engine/src/TagBus/TagBusServer.cs`) and the
sidecar cannot tell them apart. Keep it that way.

`kind` is always from the **controller's** point of view: `output` = PLC writes
it, `input` = simulator writes it. This trips everyone up; it is enforced.

### Layout

```
engine/          Godot 4.7 C# project
  src/TagBus/    Tag, TagTable, TagBusServer  (C# port, must match Python)
  src/Scenes/    SortingScene.cs              (port of harness/scene.py)
  src/View/      SceneView.cs, OrbitCamera.cs (reads state only, never writes)
harness/         Python engine reference + headless scenes (CI scene runner)
sidecar/         Python package: bus client, drivers, minimal Modbus server
examples/tia/    Sorting.scl, FF_IO DB spec, setup walkthrough
examples/nodered/ flow that replaces the PLC entirely
tools/           drive_engine.py (parity check), drv_trace.py (driver tracing)
tests/           the pytest suite; no Siemens software and no GPU required
```

---

## Commands

```bash
cd C:/Users/masal/source/factoryforge

# The whole test plan: build, pytest, engine self-tests, determinism, the
# engine/sidecar seam, robustness. Needs no PLC. See docs/TEST_PLAN.md.
python tools/test_plan.py            # add --gui for the display-dependent check

# The Python suite. 73 of them on 2026-09-21; the command is the authority and
# this comment is not, which is how it came to say 41 long after it was 73.
python -m pytest -q

# Engine build
cd engine && dotnet build

# Drive a RUNNING engine from a real driver. This is the one to use with the
# 3D engine; `demo` starts its own Python scene on the bus port, so against a
# live engine it either fails to bind or drives a scene you cannot see.
cd sidecar && python -m factoryforge_sidecar connect --driver opcua-client \
    --mapping <exported io_mapping.json> -o url opc.tcp://192.168.1.20:4840

# Engine self-tests. All exit non-zero on failure.
# Headless: panel tags — momentary pulse width, maintained E-stop, cap picking.
"<GODOT>" --headless --path engine/ -- --self-test=buttons
# Needs a display: the whole click path, from a synthesized mouse event to the
# tag. Keep both — the headless half passed in full while Run-mode clicking was
# completely dead, because the failure was in the input handler, not the logic.
"<GODOT>" --path engine/ -- --self-test=click --duration=30
# Headless: renaming parts and the I/O export — the path from a scene you built
# to a PLC you can wire.
"<GODOT>" --headless --path engine/ -- --self-test=io --duration=25
# Headless: scene save/load round-trip across every part type in the palette.
"<GODOT>" --headless --path engine/ -- --self-test=scene --duration=25
# Headless: every start-screen template loads and registers its I/O.
"<GODOT>" --headless --path engine/ -- --self-test=templates --duration=30
# Headless: the nine parts added in CP-01..CP-09 do what their tags claim --
# the VFD's actual speed lags its reference, a seized diverter freezes
# mid-sweep, the gantry refuses to claim a hold it does not have, the scanner's
# read pulse does not repeat for the same carton, the heater has a real time
# constant, a locked guard refuses the handle, and a selector still reads the
# same many ticks after nobody touched it. Asserts effects, not existence.
"<GODOT>" --headless --path engine/ -- --self-test=newparts --duration=60
# Headless: the five parts added in LP-01..LP-05, two of them against real
# physics rather than a hand-turned clock -- a raised blade genuinely stops a
# carton on a belt that is still running, and a turntable deck carries its load
# round by friction. Plus a measuring wheel that counts at the belt's true rate
# and counts nothing over nothing, a fan that moves a heating station's balance
# point, and a two-hand station that refuses two presses a second apart.
"<GODOT>" --headless --path engine/ -- --self-test=lineparts --duration=60

# Open a specific scene instead of the start screen (scripting, screenshots)
"<GODOT>" --path engine/ -- --scene=res://templates/tank_level_control.json

# Engine headless, deterministic — the CI / regression path (no GPU needed)
"<GODOT>" --headless --path engine/ -- --deterministic --duration=20

# Engine with 3D + screenshot (renders a frame you can then look at)
"<GODOT>" --path engine/ --resolution 1600x900 -- \
    --duration=26 --screenshot=C:/tmp/shot.png --screenshot-at=18

# Default launch is the rigid-body scene; nothing to pass
"<GODOT>" --path engine/

# Fast-forward or slow down a headless run (Space / the toolbar do it live)
"<GODOT>" --headless --path engine/ -- --deterministic --time-scale=4 --duration=30

# Parity check: unchanged Python sidecar drives the C# engine.
# The engine MUST be started with --deterministic or the counts are not
# reproducible and the assertion is meaningless.
python tools/drive_engine.py

# Drive the Python stub from the real PLC
cd sidecar && python -m factoryforge_sidecar demo --driver opcua-client \
    --mapping ../examples/opcua_mapping.json \
    -o url opc.tcp://192.168.1.20:4840 --duration 60

# Expose the scene as an OPC UA server (for Node-RED / SCADA / Ignition)
cd sidecar && python -m factoryforge_sidecar demo --driver opcua-server

# Discover NodeIds on any OPC UA server
cd sidecar && python -m factoryforge_sidecar browse opc.tcp://192.168.1.20:4840
```

---

## Two conventions the parts depend on

**Every part's origin sits on the work plane** (`PartLayout.WorkPlaneY`, y = 0.5),
and each part offsets its own geometry from there — sensor posts and the pusher
pedestal reach down to the floor, the chute's deck hangs below and forward of its
anchor. That is why the scene editor can snap X/Z to the 0.5 m grid, pin Y, and
have any part land correctly. Never bake a mounting height into a scene position.

**A part's instance id is a tag *prefix*, never a whole tag name.** The dispatch
in `SceneEditor._PhysicsProcess` appends the suffix, so registering a part as
`"conveyor.rotate"` looks up `conveyor.rotate.rotate` and silently disables it.

**A part's settings must be in `PartProperties`, or they are lost.** Scene files
store a properties map next to the transform. Anything a part reads in `_Ready`
and is not captured there silently reverts on load — which once cost the
removers their count tags and the sensors their `VisualOnly` flag. The converse
also bites: a value the part *computes* every tick is not a setting, and saving
it stores a sample and restores it as configuration. `VariableConveyor.Speed` is
the case — the drive recomputes it from the speed reference, so `PartProperties`
skips it for that subclass and `--self-test=scene` asserts its absence.

**The palette is generated from `PartCatalog`, not written by hand.** One entry
gives a part its button, its group, its tooltip, its summary in the property
inspector, and its place in `--self-test=scene`'s round-trip. That list used to
be written out three times and a part missing from one of the copies was a part
nobody checked could be saved. Adding a part means adding a catalog entry; see
the "everything a part must touch" table in `docs/PART_AUTHORING.md`.

**`inposition` on a positioning axis is stale on the scan that commands a
move.** `PickPlaceArm.InPosition` compares the axis to the target *the machine
currently holds*, and a target written this scan does not reach the machine
until the next physics tick — so the bit still reports "arrived", at the place
you are trying to leave. Both the demo profile and `tools/try_scene.py` released
every carton straight back onto the pick station because of it. Check the
position feedback against the destination the step wants, not the bit alone.
This is exactly the mistake a student will make, so both files carry the
explanation rather than only the fix.

**A click means one thing at a time.** `SceneEditor.Mode` is `Edit` or `Run`
(`F1`). Edit mode selects, moves and deletes; Run mode routes a left click to
`PressControlAt`, which asks each `ButtonPanel` whether the ray hit one of its
*caps* — not the part's bounding box, which also covers the housing, the
pedestal and both lamps, and would turn the whole station into one big Start
button. Everything that reflects the mode (toolbar label, palette visibility)
listens to `ModeChanged` rather than tracking it separately.

**Momentary means one scan, not one mouse-down.** A click lands on the frame
clock and tags are written on the physics clock, so `ButtonPanel` *queues*
presses and `SceneEditor.StepPanelButtons` drains the queue, clearing the
previous tick's pulse before raising this one's. Holding the mouse down, or
clicking three times between two ticks, still yields exactly one clean edge.
Note `panel.estop` is inverted on purpose: **normally closed**, true = healthy.

**The engine speaks the tag bus and nothing else.** Every PLC protocol lives in
the Python sidecar, so "connect to a PLC" always means "start the sidecar
against the running engine" — `factoryforge_sidecar connect`, never `demo`. The
F5 dialog does exactly that (`DriverConnectionUI.LaunchSidecar`) and copies the
command, because the launch can fail for reasons the engine cannot fix, such as
python not being on PATH.

**An instance id is a name, so let people choose it.** `SceneEditor.TryRenamePart`
moves every tag under the old prefix, keeping types, kinds and values, and
refuses rather than half-applying. Parts that only *view* simulation-owned tags
cannot be renamed at all — `SortingScene` writes `conveyor.rotate` by that exact
name every tick.

**Changing the scene has to be announced.** Placing, deleting or renaming a part
changes the I/O list, and a connected driver is working from the tag list it was
handed at connect time. `SceneEditor` raises `TagsChanged`; `Main` answers it
with `_bus.SendDescribe()`. The bus always knew how to republish — for a long
time nothing asked it to.

**Simulation controls are engine-global.** Run/pause and time scale go through
`Engine.TimeScale`, so one switch covers the fixed-timestep accumulator, Jolt,
and every part animation. The tag bus is deliberately exempt — it polls from
`_Process`, which Godot still calls at time scale 0, so a paused scene keeps its
PLC session instead of dropping it. `--duration` and `--screenshot-at` run on
wall-clock for the same reason: a paused run whose clock also stopped would
never terminate.

**Two scenes, one tag interface.** The engine launches the *rigid-body* scene:
real colliders, real gravity, a held-out pusher genuinely blocks the line.
`--deterministic` swaps in the fixed-timestep `SortingScene` instead — that one
advances by exactly `TickMs`, mirrors `harness/scene.py`, and is the regression
contract (`tools/drive_engine.py` → `tall=5 short=5`). Jolt cannot promise
reproducible counts, so **anything asserting exact numbers must pass
`--deterministic`.**

Both declare the same ten tags via `SortingTags.Declare`, and both report the
same scene name on the bus, so the same `Sorting.scl` or Node-RED flow drives
either without noticing. Keep it that way: if you add a tag to one, add it to
`SortingTags`.

## Hard-won gotchas — these cost hours, do not rediscover them

1. **The S7-1500 forces a 1000 ms subscription publishing interval**, silently
   revising whatever you request. Any signal that rises and falls inside one
   second can vanish. This is why `opcua_client.py` **polls by default**.
   Raising `queuesize` / lowering `sampling_interval` makes it strictly *worse*
   — the S7 rejects them and then reports nothing at all.

2. **On Windows, `asyncio.sleep()` under ~15.6 ms returns immediately.** The
   clock resolution is 15.6 ms and asyncio treats anything inside that window as
   already expired. A 500-iteration poll loop finished in 10 ms of real time.
   Never count iterations to measure time; measure real elapsed time. Tests must
   wait on events, not poll with short sleeps.

3. **Fixed timestep needs a wall-clock accumulator.** Stepping once per
   `sleep(tick_ms)` runs the sim slow (first version ran at ~60% of real time).
   Both engines accumulate real elapsed time and run whole fixed steps. Never
   switch to variable dt — it destroys reproducibility.

4. **`fake_plc.py` is a logic check only, not an authority.** It passed two SCL
   versions that failed on the real CPU. A Python scan loop reproduces IEC timer
   semantics but not a real CPU's scan behaviour. **Trust the PLC over the
   model.** (I got this wrong and told the user their download hadn't landed
   when their code was in fact running.)

5. **Never write `#tEmit(IN := NOT #tEmit.Q, ...)` in SCL.** It makes `Q` high
   for one scan; on a real S7-1500 the follow-on statement did not execute. Hold
   `IN := TRUE` so `Q` latches, then reset explicitly. There is a warning
   comment in `Sorting.scl`.

6. **Never kill test processes with `os._exit()`.** Each leaks an OPC UA session;
   the S7 allows only a few and then refuses connections for ~35 s. Use
   `demo --duration N`, which shuts down cleanly.

7. **One OPC UA session at a time against the S7.** Running the driver plus a
   separate monitoring client destabilised the server. Instrument the driver
   instead — `tools/drv_trace.py` prints every write and receive.

8. **asyncua's default 4 s connect timeout is too short for a real S7.** Use
   `timeout=10`.

9. **Never run Node-RED against `C:\Users\masal\.node-red`** — it holds the
   user's own flows. Use a temp userDir with a **junction** to their
   `node_modules`, and remove it with `cmd //c rmdir` (not `rm -rf`, which
   follows the junction and would delete their packages).

10. **A click handler must use the event's position, not the cursor's.**
    `GetViewport().GetMousePosition()` returns where the pointer *is now*, which
    for a real click is the same place — and for a synthesized one is not. Run
    mode's first version used it and worked for nobody except by accident: the
    headless self-test passed every assertion it could reach while clicking a
    button did nothing at all. Take the position off the `InputEventMouseButton`.
    (`SelectPartAtMouse` still reads the cursor; it predates this and is only
    ever driven by hand.)

11. **A UI that configures nothing is worse than no UI.** The F4 wiring panel
    kept its mappings in a dictionary nothing outside that file ever read, and
    F5's "Apply & Connect" assigned four fields and hid the dialog. Both looked
    like they worked. If a panel implies an effect, follow the value to whoever
    consumes it before believing it — `grep` for the field, not for the button.

12. **A crashing self-test can exit 0.** An exception inside `_PhysicsProcess`
    is logged by Godot and execution continues, so a test that throws before its
    report never reports, the run ends via `--duration`, and the exit code is
    success. Wrap the body in try/catch and count a throw as a failure.

13. **`Quit()` takes effect at the end of the frame.** A self-test that does its
    work in `_PhysicsProcess` runs again on the next tick — against state the
    first pass already mutated — burying the real failure under repeats. Latch a
    `_done` flag.

14. **A failed `dotnet build` leaves the previous binary in place**, and Godot
    runs it without complaint. A run that produces no output where you expected
    some is more likely a stale binary than a logic error; check the build
    result before debugging the code.

15. **Godot hangs if its stdout is a pipe nobody drains**, and it fills that
    buffer *before* it binds the tag bus — so a piped engine prints its banner
    and then waits forever, listening on nothing. Measured: inherited stdout or
    a file, port open in 0.25s; bare `Popen(stdout=PIPE)`, never. `subprocess.run`
    is safe because `communicate()` drains concurrently. Redirect to a file.

16. **A test that passes while the simulation does nothing is not a test.** A
    determinism check compared two *undriven* runs and asserted `(0,0) == (0,0)`
    for weeks' worth of confidence it never earned. Assert that real work
    happened, not only that two runs agree.

17. **`--driver mock` runs no control logic.** It is a passthrough that lets a
    Python script drive tags; the logic lives in the script (`drive_engine.py`).
    An engine "driven by mock" with nothing else correctly does nothing, which
    is easy to misread as a broken scene.

18. **Never `PowerOff()` a PLCSIM instance the sidecar did not start.** The
    driver's `stop()` used to do it, so a 40-second test run switched off the
    user's CPU. Attaching to a controller is not owning it — no PowerOn, no Run,
    no PowerOff. (Recovery: `PowerOn()` then `Run()`; the downloaded program
    does survive the power cycle, but do not rely on that.)

19. **PLCSIM Advanced in Softbus mode has no IP.** The default communication
    interface is local softbus, so the virtual CPU is unreachable over the
    network and its OPC UA endpoint is not listening — whatever address the TIA
    project shows. Switch the instance to **PLCSIM Virtual Ethernet Adapter** if
    you need OPC UA or snap7; the native API driver works either way.

19b. **Driver options from `-o` arrive as strings.** `-o db 1` gives `"1"`, and a
    type hint of `db: int` does not make it one. snap7 failed with "required
    argument is not an integer", which points at ctypes and not at argparse.
    Coerce numeric options in the driver's `__init__`.

19c. **snap7's "Invalid address (0x05)" usually means the read overran the DB**,
    not that the block is optimized. FF_IO is 10 bytes; asking for 12 fails
    exactly that way. I misdiagnosed this as optimized block access and was
    wrong — check the DB's length before believing it.

19d. **A read-modify-write over S7 can stomp bits the PLC owns.** Bits are not
    individually addressable on the wire, so writing a simulator-owned bit means
    rewriting the whole byte, including any PLC-owned bits in it. FF_IO packs
    both directions into byte 0. The driver now writes the narrowest byte range
    it can; a DB you design yourself should put the two directions in separate
    bytes.

20. **The PLCSIM API assembly is not on .NET's probing path.** `clr.AddReference`
    by bare name fails on a normal install; load it by path from
    `C:\Program Files (x86)\Common Files\Siemens\PLCSIMADV\API\<ver>\`.

21. **An exception before `GetTree().Quit()` makes the quit unreachable, and the
    condition stays true.** `Main._Process` read the sorting demo's counters
    unconditionally at the end of a `--duration` run; a scene without them threw
    there, so the run never ended and the same stack trace was logged every
    frame. An idle tank template produced a **23 MB** log and looked exactly
    like a hang — CPU busy, window responding, no output. Do the side effect
    that ends the loop *first*.

22. **Not every scene has the sorting demo's tags.** They are declared by the
    engine at startup, not owned by any part, so switching scenes calls
    `SortingTags.Undeclare`. Anything reading `conveyor.rotate` or
    `counter.tall` must check `Contains` first.

23. **Two conveyors placed end to end wedge, and it is contact slop, not your
    scene.** A rigid body resting on a static deck settles a centimetre or two
    *inside* it — that is the solver's allowed penetration and it cannot be
    tuned away. A carton riding 2 cm low then meets the vertical end face of the
    next conveyor's collider head-on and stops dead, taking the whole queue with
    it, on a belt that is visibly running. It reproduced on roughly half of the
    pick-and-place cell's runs and looked exactly like a template mistake: the
    cartons stopped at x=1.90 on a deck ending at x=2.00, with velocity 0.00 and
    the drive reporting 70 %. `ConveyorBelt.AddTransferRamps` now gives every
    deck a shallow wedge at each end, sloping from below deck level up to it, so
    a low carton rides up rather than into the face. Do not "fix" a jam like
    this by leaving a gap between belts, overlapping their decks, or holding the
    upstream drive — all three were tried, and the last one makes it *worse*
    (a carton stopped **on** the joint can never restart across it).

24. **A test that cannot see the failure mode is not coverage.** The panel had a
    correct hit test and a correct pulse dispatch and was still completely dead
    from a user's seat. Two self-tests exist for this reason — `--self-test=buttons`
    headless for the logic, `--self-test=click` with a display for the input
    path — and both were checked by deliberately reintroducing the bug and
    confirming they fail. Do that before trusting a new self-test.

---

## Current state

**Working end to end**, verified against real hardware:

- Tag bus, drivers (OPC UA client + server, Modbus, mock)
- OPC UA client → real S7-1500 → boxes sort by height (**100.0% perfect split: 99 tall / 99 short** with SCL v0.4)
- Node-RED replacing the PLC entirely — **9 tall / 9 short, perfect split**
- Godot C# engine with 3D geometry driven by tags, screenshot in README
- The unchanged Python sidecar drives the C# engine (`tools/drive_engine.py`)
- 73 of 73 Python tests passing (`python -m pytest -q`); CI needs no Siemens
  software and no GPU

**Resolved imperfection:** In SCL v0.3, ~12% of tall boxes slipped past the pusher due to timing margin (0.6s catch window vs ~100ms OPC UA round-trip jitter). Fixed in SCL v0.4 by setting `PUSH_HOLD` `T#500MS` → `T#1S500MS`, verified live on real S7-1500 (99 tall / 99 short).

---

## Next steps, in order

1. **`PUSH_HOLD` → `T#1S500MS`** (SCL v0.4) — **Verified on hardware (99 tall / 99 short, 100% split)**.
2. **Finish M2**: integer voxel grid, free-look camera, C# protocol tests in CI — **Done**.
3. **M3 — physics**: surface-velocity belt constraint, non-jittering rigid boxes, raycast sensors, pusher mechanism, emitter/remover, control panel — **Done**.
4. **M4 — Scene Editor**: part palette, place/rotate/delete with grid snapping, save/load JSON format, top toolbar, live tag forcing UI, property inspector — **Done**.
5. **M5 — Siemens Breadth**: PLCSIM Advanced Simulation Runtime API driver —
   **Done and now actually verified**: it drives the 3D scene from a real
   virtual S7-1500. It had never run before 2026-08-12; it called
   `TagTable.outputs()` and `tag.kind.value`, neither of which exists, had no
   tag→symbol mapping at all, and swallowed every resulting exception. **The
   snap7 driver had the same three API mistakes** plus an addressing scheme that
   packed every bit into byte 0 in tag order, wrapped at 8 with `% 8`, and
   ignored the DInt counters — it now takes a `DBX0.0` / `DBD2` mapping file.
   All three Siemens drivers are verified against a virtual S7-1500.
6. **M6 — v1 Release**: student getting-started guide, part & driver authoring
   guides — **Done**. **Packaging: the machinery is built and verified; nothing
   has ever been published.** Those are two different things and this entry has
   now been wrong about both of them, so read them separately.

   *Built and verified.* `python tools/build_release.py` exports the engine,
   freezes the sidecar with PyInstaller and writes
   `dist/FactoryForge-<platform>.zip`; on Windows `build_windows.bat` does the
   same double-clickably and then runs the release gate — every self-test in
   `check_release.py`'s `SELF_TESTS` list, headless, against the **exported
   binary** rather than the checkout. Verified end to end locally on 2026-08-23,
   in CI on 2026-08-24, and again on 2026-09-02 after the nine new parts, on
   Windows and Linux both — 25 of them on that last run. The list is 27 now and
   has not been run since, so `lineparts` and `buildflow` have never met an
   exported binary. The question this entry once
   called unanswered — how to ship the Python sidecar — was answered by freezing
   it; see `docs/PACKAGING.md`.

   *Never published.* Zero GitHub releases, zero git tags. `release.yml`
   triggers on `v*` and has only ever been fired by hand as a
   `workflow_dispatch` dry run. The archives exist on one machine in the world,
   so running FactoryForge still means installing Godot-mono and the .NET SDK
   and building from source — for the reason a release would fix, and for none
   of the reasons this entry used to give. Publishing one is HP-09 in
   `docs/HARDENING_PLAN.md`.

   The history is worth keeping: this entry first claimed packaging was done
   when no binary had ever been exported, then claimed it was undone after the
   machinery had been built and gated on two platforms. Both times the
   correction was a whole sentence away from a claim nobody had rechecked.

Deliberately skipped at the user's request: OpenPLC/Modbus cross-check.

## What is open

`docs/HARDENING_PLAN.md` (HP-01 … HP-52) is the live work list. Every *feature*
plan in `docs/` is closed. Read the release gate in that file's Sequencing
section before starting anything: it is the list of items that must land before a
release, drawn from every phase rather than from the phase order.

## Working style the user expects

- Verify against real hardware, not models. Render a frame and look at it.
- State findings with the evidence that produced them.
- Flag your own mistakes plainly and correct them.
- Ask before writing to their PLC or touching their Node-RED install.
