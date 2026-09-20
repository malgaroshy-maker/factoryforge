# FactoryForge — Roadmap

*Status: living document · Last updated: 2026-09-21*

Pace assumption: **full-time solo**. Every milestone must be independently
useful — nothing of value should be gated behind a distant v1.

M0 through M6 are complete. What is open is no longer a milestone: it is
`HARDENING_PLAN.md`, which is where the work that makes any of this survive
contact with a second person now lives. The one thing M6 never did is publish a
release — see *Beyond v1* below, and HP-09.

---

## M0 — Tag bus ✅ **complete**

*The integration seam, proven with no 3D at all.*

- [x] Tag bus protocol spec (`docs/tag-bus.md`)
- [x] Tag model: types, coercion, forcing, epochs
- [x] Bus client (sidecar) and server (engine reference)
- [x] Fixed-timestep loop with wall-clock accumulator
- [x] Headless sorting-by-height scene
- [x] Driver ABC + registry
- [x] Modbus TCP driver + hand-written Modbus server
- [x] Mock driver for CI
- [x] 27 tests passing

**Why this first:** it is the riskiest integration and the cheapest to change
now. It is also the contract every future contributor codes against.

---

## M1 — OPC UA ✅ **complete but for two items**

*The primary integration path, and the one that reaches beyond Siemens.*

The two unchecked boxes below are the only ones left anywhere in M0–M6, and both
are still genuinely open rather than quietly abandoned: the OpenPLC cross-check
was deferred at the user's request and is tracked as HP-45, and the two
PLCSIM-Advanced facts that need writing down are HP-46.

Reference target is **S7-PLCSIM Advanced** — no physical hardware is available
to this project. Everyday development runs against OpenPLC over the existing
Modbus driver, which needs no licence.

- [x] ~~Verify S7-1200 OPC UA server support~~ — resolved: **S7-1500 is the
      reference CPU**, forced by PLCSIM Advanced simulating only the S7-1500
      family. S7-1200 should work (OPC UA server from FW V4.4+, licence included
      from TIA V16) but is untestable here, since only plain PLCSIM covers it
      and that has no external network interface.
- [x] **Client mode**: connect to a PLC's server, subscribe, write back —
      `drivers/opcua_client.py`
- [x] Automatic reconnection, surfaced as bus `status` messages; a missing PLC
      never blocks startup
- [x] **Server mode**: expose scene tags at `ns=2;s=<tag_id>` —
      `drivers/opcua_server.py`. Simulator-owned inputs are read-only, so a
      client cannot fake a sensor.
- [x] Integration tests against a local `asyncua` server (no Siemens software in
      CI) — 11 tests, `tests/test_opcua.py`
- [x] **Manual verification: TIA Portal → PLCSIM Advanced → OPC UA → scene.
      Working end to end on a real S7-1500 (CPU 1511-1 PN).** Boxes sort by
      height, **100.0% perfect split (99 tall / 99 short)** with SCL v0.4 (`PUSH_HOLD` `T#1S500MS`).
- [x] Polling read path, now the default — the S7 forces a 1000 ms subscription
      publishing interval that swallowed the 500 ms pusher pulse
- [x] **`PUSH_HOLD` → `T#1S500MS` (SCL v0.4)** — verified live on real S7-1500 (99 tall / 99 short)
- [ ] Cross-check the same scene driven by OpenPLC over Modbus
- [ ] Document the 100-variable unlicensed trial limit, and that **PLCSIM
      Advanced is required** — plain bundled PLCSIM will not work
- [x] Worked example: Node-RED flow connecting to server mode —
      `examples/nodered/factoryforge-flow.json`. Node-RED replaces the PLC
      entirely: runs the belt, pulses the emitter, fires the pusher on the high
      sensor. Verified: **9 tall / 9 short, a perfect split.**

**Ships:** anyone with a PLC or SCADA package can drive the headless scene.
Useful on its own, before any 3D exists.

**Server mode matters more than it looks.** Node-RED, Ignition, and most SCADA
speak OPC UA client-side, so exposing the scene as a server reaches MQTT, HTTP,
dashboards, and cloud services through one Node-RED flow — without us writing a
single integration. Do not let it slip to "if there's time."

---

## M1.5 — MQTT *(~3 days, optional)*

*Small, and it proves the driver abstraction generalises.*

Modbus and OPC UA are both request/response over an address space. MQTT is
pub/sub with no addressing at all. If the `Driver` ABC survives MQTT unchanged,
it is a real abstraction — worth confirming **before** contributors start
writing drivers against it.

- [ ] MQTT driver via `aiomqtt`
- [ ] Topic scheme: `factoryforge/<scene>/tag/<tag_id>`, retained for inputs
- [ ] Subscribe on output topics; publish input changes (delta-only, as the bus does)
- [ ] Tests against a local broker
- [ ] If the ABC needs changing to fit, **that is the finding** — fix it now

**Ships:** direct IIoT scenarios, and an Industry 4.0 teaching angle Factory I/O
has no answer to — its driver list contains no MQTT at all.

**Note:** Node-RED speaks OPC UA natively, so M1 server mode *already* reaches
MQTT, HTTP, and dashboards through a Node-RED flow with no code from us. This
milestone is about removing the extra hop, not enabling something impossible.
Skip it without guilt if M2 is more urgent.

---

## M2 — Godot engine skeleton ✅ **complete**

*First pixels.*

- [x] **Godot 4.7.1 mono + C#/.NET 8 project**, Jolt set as the 3D physics
      engine. Builds and runs headless, so CI needs no GPU.
- [x] **Tag bus server ported to C#** — `engine/src/TagBus/`. Same protocol,
      same epoch guard, same delta-only updates, same fixed-timestep accumulator.
- [x] **Cross-check passed:** the *unchanged* Python sidecar and driver stack
      drive the Godot engine — `python tools/drive_engine.py` gives
      `tall=5 short=5`. The bus abstraction holds across engines.
- [x] Headless `SortingScene` ported to C#, faithful to `harness/scene.py`
- [x] **Integer voxel grid and floor grid rendering** — `engine/src/View/VoxelGrid.cs`
- [x] Orbit camera (drag to rotate, wheel to zoom, middle-drag to pan) — `OrbitCamera.cs`
- [x] **3D geometry driven by tags** — belt, boxes coloured by height, sensor
      beams that light on detection, animated pusher, stack light. Screenshot in
      the README. Verified by rendering a frame and looking at it, via
      `-- --screenshot=<path> --screenshot-at=<seconds>`.
- [x] **Free-look / fly camera** — WASD movement & right-click look (`engine/src/View/FreeLookCamera.cs`)
- [x] **Run the C# engine against the Python protocol tests in CI** — `tools/drive_engine.py` verified (PASS tall=5 short=5)

**Ships:** the headless scene, visible. The bus is unchanged, so M1's drivers
keep working untouched.

---

## M3 — Physics and the seven parts ✅ **complete**

*The core parts library & physics engine.*

- [x] **Belt conveyor as a surface-velocity constraint** — `engine/src/Parts/ConveyorBelt.cs`
- [x] **Emitter and remover** — `engine/src/Parts/Emitter.cs`, `Remover.cs`
- [x] **Boxes that do not jitter, stack wrong, or fall through** — `engine/src/Parts/BoxPhysics.cs`
- [x] **Diffuse photoelectric sensors (raycast, adjustable range)** — `engine/src/Parts/PhotoelectricSensor.cs`
- [x] **Pusher with extend/retract limits** — `engine/src/Parts/PusherMechanism.cs`
- [x] **Button panel and indicator light** — `engine/src/Parts/ButtonPanel.cs`
- [x] `test_sorting.py` assertions still pass against the real engine

**Risk:** conveyor physics is the classic trap in this genre. If it overruns,
it overruns here rather than surprising us at v1.

---

## M4 — Scene editor ✅ **complete**

- [x] **Part palette UI** — `engine/src/Editor/PartPaletteUI.cs`
- [x] **Tag list UI with live values, manual force, and dynamic part tag registration** — `TagInspectorUI.cs`, `PartTagManager.cs`
- [x] **Save/load JSON data format & Top Toolbar** — `SceneData.cs`, `SceneToolbarUI.cs`
- [x] **Place, rotate (R), select, delete (Del), cancel with grid snapping** — `engine/src/Editor/SceneEditor.cs`
- [x] **Live Part Property Inspector, 3D Selection Gizmo, and 8-Part Library (Conveyors, Sensors, Pusher, Ramp, StackLight, Emitter, Remover, Panel)** — `Chute.cs`, `StackLight.cs`, `SelectionGizmo.cs`
- [x] **Undo/Redo (Ctrl+Z/Ctrl+Y) & Move Mode (M)** — `EditorCommandHistory.cs`, `SceneEditor.cs`

**Ships:** users can build their own scenes. This is the point the project
becomes a *tool* rather than a demo.

---

## M5 — Siemens breadth ✅ **complete**

- [x] **PLCSIM Advanced Simulation Runtime API** driver via `pythonnet` (`plcsim_advanced.py`) — direct shared-memory I/O access, no OPC UA licence, no TCP latency.
- [x] **S7 driver via `python-snap7`** (`s7_snap7.py`) — ISO-on-TCP direct S7 communication for physical S7-300, S7-400, S7-1200, S7-1500 PLCs.
- [x] Driver unit tests — `tests/test_siemens.py`. (This line used to record the
      whole suite's size at the time, 41, which then aged badly in four
      documents at once. The suite is 73 today; `python -m pytest -q` is the
      only place worth reading it from.)

**Ships:** a Siemens user with PLCSIM Advanced needs no OPC UA licence at all.
Deliberately after M1, because this path helps only PLCSIM Advanced owners
whereas OPC UA reaches every vendor.

---

## M6 — v1 release ✅ **complete**

- [x] Windows and Linux setup; sidecar pip-installable (`factoryforge-sidecar`)
- [x] Student getting-started guide — `docs/GETTING_STARTED.md`
- [x] Part authoring guide + template — `docs/PART_AUTHORING.md`
- [x] Driver authoring guide — `docs/DRIVER_AUTHORING.md`
- [x] Example scenes, TIA Portal SCL project (`Sorting.scl`), and Node-RED flow (`factoryforge-flow.json`)

**Definition of done:** a student who has never seen the project goes from
download to a PLCSIM-driven sorting scene in under 30 minutes, using only the
written guide.

**And that word "download" is still unearned.** The build and freeze machinery
landed afterwards and is verified on both platforms (see *Beyond v1*), but no
version has been tagged and no archive has been published, so the 30 minutes
currently start with installing Godot and the .NET SDK. Closing this properly is
HP-09.

---

## Total: roughly 5 months to v1

Front-loaded with the risky integration work, which is now behind us. M3 is the
most likely to slip.

---

## Beyond v1

**Done since v1:**

- [x] **Rigid-body scene by default**, with the fixed-timestep scene behind
      `--deterministic` as the regression contract. One tag interface, two
      solvers — the same SCL drives either.
- [x] **Run / pause / reset and time scale** (0.25x–4x, `--time-scale=N`
      headless). The tag bus stays connected while paused, so a frozen scene is
      still readable from the PLC.
- [x] **Analog parts** — Level Tank with modulating fill and drain valves and a
      level transmitter, all Float. Outflow follows Torricelli, so process gain
      varies with level and a PID tuned full overshoots when empty. Verified by
      closing a PI loop over the bus: holds 60.0% within 0.15%.
- [x] **Sensor family** — diffuse, retroreflective and inductive modes, plus a
      Light Array reporting measured height as a Float. Items carry a material,
      so the inductive sensor sorts metal from cardboard.
- [x] **Roller conveyor**, and a display that can show an analog reading.
- [x] **Scene files persist part settings**, not just transforms.
- [x] **Edit / Run mode separation** (`F1`), because a click cannot both pick a
      part up and press it.
- [x] **Clickable operator buttons** — Start, Stop and Reset as true momentary
      contacts (one scan per click, however long the mouse is held), plus a
      latching E-stop wired normally closed. The first inputs in the library a
      human drives rather than the simulation computing them for you.

- [x] **All three Siemens paths verified driving the 3D scene** from a virtual
      S7-1500 — PLCSIM Advanced's native API, OPC UA client, and snap7. Two of
      the three had never been run at all: they called `TagTable.outputs()` and
      `tag.kind.value`, neither of which exists, had no tag→symbol mapping, and
      swallowed the resulting exceptions. Shipped, documented, and dead.
- [x] **A test plan that covers the seam** — `docs/TEST_PLAN.md` and
      `tools/test_plan.py`, one command, non-zero on failure. Written because
      the two worst defects this project has had were both invisible to the
      layer tests: Run-mode clicking was dead while every headless assertion
      passed, and no real driver could reach the 3D engine at all while the
      drivers themselves were fine.
- [x] **A full-surface review of the app against its own claims**
      (`UX_PLAN.md`, then `LOOSE_ENDS_PLAN.md` for what the first sweep left) —
      a start screen with templates instead of cold-opening one demo, a
      launcher that finds Godot on a machine that is not the author's
      (`run.py`), a driver dialog that stays on screen when it has something to
      say, and the removal of several controls that moved without changing
      anything.
- [x] **Breaking it on purpose.** Drives can fail and valves can seize, *while
      the command is still on*. Until that existed, every actuator did exactly
      what it was told, which makes half of real PLC work unteachable — an
      interlock exists precisely because the plant does not always obey.
- [x] **An honest exercise per scene** — `tools/try_scene.py` and the toolbar's
      **🧪 Try**, which drive a scene the way a PLC would and report pass/fail.
      The same sequence is the regression test, so the lesson and the check
      cannot drift apart.
- [x] **A release you can build, and a gate it has to pass.**
      `tools/build_release.py` exports the engine and freezes the sidecar with
      PyInstaller; `build_windows.bat` does it double-clickably and then runs 25
      headless self-tests **against the exported binary**, because a build that
      produced a binary is not the same as one that produced a working binary.
      Verified on both platforms, three times, last on 2026-09-02 — see
      [PACKAGING.md](PACKAGING.md). **Not published**: no tag, no GitHub
      release, no archive anywhere but one machine. That is HP-09, and it is the
      single largest gap between what this project is and what anyone can use.

- [x] **Nine more parts, from fifteen to twenty-four** (CP-01…CP-09, see
      [COMPONENTS_AND_POLISH_PLAN.md](history/COMPONENTS_AND_POLISH_PLAN.md)) — each one
      chosen for something the library could not previously teach: a
      **VFD conveyor** whose actual speed lags the reference you gave it, a
      **pivot diverter** that deflects a carton without stopping the line, a
      **pick-and-place gantry** with an analog axis, a vertical stroke and a
      vacuum cup that really picks a box up, a **barcode scanner** reporting an
      integer code on a one-scan pulse, a **heating station** whose first-order
      lag makes the case for integral action measurable, an **analog gauge**
      (the library could display an int and not a float), and an **alarm
      beacon** that throws real light around — plus a **maintained selector
      switch** (the first operator input that holds a position instead of
      pulsing or latching) and an **interlocked guard door** whose solenoid lets
      the controller decide whether it may be opened at all.
- [x] **Two more templates**, each with a demo profile and a `try_scene.py`
      exercise: `pick-and-place-cell` and `heat-treat-station`. Seven scenes
      now, all seven passing their own exercise.
- [x] **The palette is generated from one catalog**, searchable, with a tooltip
      per part naming the tags it will register — and the same catalog is what
      `--self-test=scene` walks, so a part cannot ship without its save/load
      being checked.
- [x] **A key list you can reach from inside a scene** (`F12`), built from the
      same table the start screen's footer reads.
- [x] **Presentation pass** — conveyor drums that turn at true surface speed, a
      cushioned pusher stroke, stack-light lamps that cast real light, a status
      LED on every sensor head, shop-floor markings, 4× MSAA, and a damped
      camera that frames the scene you just opened instead of leaving you
      looking at a control panel.

- [x] **Five more parts, from twenty-four to twenty-nine** (LP-01…LP-05, see
      [LINE_PRIMITIVES_PLAN.md](history/LINE_PRIMITIVES_PLAN.md)) — chosen by asking
      what a student *could not build* rather than by counting what was already
      there: a **blade stop**, so product can accumulate on a belt that keeps
      running; a **turntable**, so a line can turn a corner; a **measuring
      encoder**, so product can be tracked by distance instead of by a timer; a
      **cooling fan**, so the thermal plant has a second actuator and
      split-range control becomes teachable; and a **two-hand control**, whose
      permissive a tie-down cannot defeat. Plus an eighth template,
      `accumulation-buffer`, and `--self-test=lineparts`.
- [x] **A carton that stops on a running belt can start again.** A belt drives
      its load through a *surface* velocity, which acts through contact
      friction — and contact friction does nothing to a body the solver has put
      to sleep. Anything held stationary on a belt therefore fell asleep after a
      second or two and could never be restarted by the belt underneath it.
      Found by the blade stop, but it was every accumulation the library can
      express.
- [x] **The build loop** (BF-01…BF-06, see [BUILD_FLOW_PLAN.md](history/BUILD_FLOW_PLAN.md))
      — twenty-nine parts, and putting six of them in a row was still six trips
      to the palette. The part now stays in your hand after you place it, with
      the palette button lit to say which one; `Ctrl+D` lands its copy clear of
      the original and selects it, so pressing it again walks a line instead of
      stacking parts in one cell; and the arrow keys nudge the selection a cell
      at a time, one undo step per press.
- [x] **A shop, and a selection that is a group** (EN-01…EN-02, ES-01…ES-05,
      see [SHOP_AND_SELECTION_PLAN.md](history/SHOP_AND_SELECTION_PLAN.md)) — the line
      stands on poured concrete with a joint around every two-metre bay, inside
      clad walls five metres to the eaves, both generated in code; and Shift+click
      or a Ctrl+drag box selects several parts, which then move, nudge, rotate,
      duplicate and delete together as one undo step.
- [x] **Dragging a part no longer spins the camera.** The orbit camera and the
      editor both listened on `_UnhandledInput` and neither claimed anything, so a
      part drag reached both — the part followed the cursor while the world turned
      underneath it. True for as long as dragging has existed, and the same
      collision was on the setpoint pot in Run mode.
- [x] **Reading what you built, and getting around it** (NV-01…NV-04, see
      [NAMES_AND_VIEWS_PLAN.md](history/NAMES_AND_VIEWS_PLAN.md)) — `N` floats every
      part's instance id over it, which is the tag prefix a PLC program is
      written against and previously took one click per part to read;
      `Ctrl+A`, `Ctrl+C` and `Ctrl+V` select everything and carry a section
      into another scene; and `1`-`4` snap the camera to iso, top, front and
      side without losing what was framed.
- [x] **The tag inspector, for scenes with twenty-seven tags in them**
      (TI-01…TI-04, see [TAG_INSPECTOR_PLAN.md](history/TAG_INSPECTOR_PLAN.md)) — a
      search box, groups per machine that collapse, a filter for one half of
      the I/O at a time, and a forced tag that marks its own name with one
      button to release every one of them. That last is the one that matters:
      a forgotten force is a value that disagrees with the simulation on
      purpose, and it explains more "why is my program not working" than
      anything else here.
- [x] **Every template says what it is asking you to build** (BR-01…BR-04, see
      [TASK_BRIEFS_PLAN.md](history/TASK_BRIEFS_PLAN.md)) — eight scenes, each chosen
      to teach something specific, and the app never said what: the lesson
      lived in `tools/try_scene.py`, a Python test harness. `T` now opens the
      task, the tags to use and how you know it works, and every tag a brief
      names is checked against the scene it belongs to.

**Next, and ahead of everything below it:**
[HARDENING_PLAN.md](HARDENING_PLAN.md) — fifty-two items from three reviews run
on 2026-09-20. None of them are features. The subset that matters is the
*release gate*: a save that reports success after failing, opening a bad file
destroying the good scene, an undo that hands back a part at factory defaults, a
rename that quietly reassigns a PLC's tags. All four are on a student's first
hour, and all four are invisible at the moment they happen. Nothing should ship
over them.

**Near:** part-to-part linking in the editor — the measuring encoder finds the
belt under it by geometry, which is right for a wheel resting on a deck and
would not be right for, say, a drive and a remote readout · more parts driven by
what contributors ask for · headless grading mode for coursework ·
MQTT **Sparkplug B** (the industrial MQTT standard, if plain MQTT proves useful) ·
an example Node-RED flow and dashboard shipped with the docs

**Later:** `.factoryio` scene importer, once the part library overlaps enough ·
EtherNet/IP//Allen-Bradley · web build if Godot's C# web export lands ·
multi-scene projects

**Explicitly not planned:** virtual commissioning claims · cloud/hosted version ·
per-seat licensing of any kind

---

## Parked: the Factory I/O mod spike

The approved plan opened with a 3-week mod spike — a Harmony patch on
`App.LoadMod` redirecting `Resources.Load` to `AssetBundle.LoadFromFile`, which
would give Factory I/O a working disk-based mod loader.

**Parked, not cancelled.** Reasons:

- It is blocked on obtaining a legitimate Factory I/O licence (the analysed
  install is a patched copy, and publishing a loader developed against a cracked
  binary would poison an open-source project's credibility).
- Its purpose was to de-risk the part model by learning from a working
  implementation. That knowledge was obtained directly from the decompile —
  `OperatingMode`, `ComponentIO`, `GroupIO`, the voxel grid, and the
  `PrefabName` scene serialisation are all documented and understood.
- M0 shipped without needing it.

Worth revisiting as a standalone community contribution once v1 is out. The
technical finding stands: the palette auto-populates from
`EditContext.prefabsList` and scenes round-trip by `PrefabName`, so the loader
is genuinely a small patch.
