# FactoryForge Test Plan

*Last run: 2026-08-30. Results at the bottom.*

A single command runs all of it:

```bash
python tools/test_plan.py
```

It exits non-zero if anything fails, prints a table, and needs no PLC and no
Siemens software. Add `--gui` to include the checks that need a display.

---

## Why this shape

The project has three moving parts that fail in different ways, and a test that
only covers one of them has repeatedly passed while the product was broken:

| Layer | Fails as | Caught by |
|---|---|---|
| Python sidecar + drivers | wrong values on the wire | pytest |
| C# engine logic | right values, wrong physics or dispatch | engine self-tests |
| The seam between them | both sides correct, nothing connected | end-to-end checks |

The third row is the one that has burned this project twice — Run-mode clicking
was dead while every assertion the headless test could reach passed, and the 3D
engine could not be driven by any real protocol driver at all while the drivers
themselves worked fine. So end-to-end coverage is not optional here.

---

## What is covered

### A. Build and static

| # | Check | Why |
|---|---|---|
| A1 | `dotnet build` succeeds with **zero warnings** | a failed build silently leaves the old binary in place and Godot runs it |
| A2 | Sidecar package imports | catches a syntax error before it wastes a 40-second engine run |
| A3 | No `TODO`/`FIXME`/temp probe left in `engine/src` | temporary verification hooks have escaped into commits before |
| A4 | The C# tag model matches the shared parity fixture (`--self-test=parity`) | the C# and Python models are two implementations of one contract and drift silently |
| A5 | `hello`/`describe`/`update` carry exactly the fields `docs/tag-bus.md` names (`check_protocol.py`) | the wire format is what every driver is written against |
| A6 | No type in `engine/src` is referenced nowhere outside its own file | a whole feature once shipped this way — `FloatingTagBadge3D` was a complete billboard label the README advertised and nothing ever constructed. Dead code builds cleanly, so A1–A5 could not see it |

### B. Python unit and integration

| # | Check |
|---|---|
| B1 | Full pytest suite (73 tests): tag model, bus protocol, Modbus, OPC UA client/server, Siemens, sorting scene |

### C. Engine self-tests (headless)

| # | Check | Command |
|---|---|---|
| C1 | Panel buttons: one-scan pulse, maintained E-stop, cap picking incl. rotated | `--self-test=buttons` |
| C2 | Rename and I/O export | `--self-test=io` |
| C3 | Scene save/load round-trip, every part type, undo/redo | `--self-test=scene` |
| C4 | Every shipped start-screen template loads and registers its I/O  Each one also carries a **brief** — what to build, which tags to use, how you know it works — and every tag a brief names must exist in the scene the editor has just loaded. A shipped template with no brief is a failure, not a skip. | `--self-test=templates` |
| C5 | F5 driver modal's minimum size still fits the screen once auto-detect writes a long status message | `--self-test=layout` |
| C6 | Tag Inspector's Force button works on int and float tags, not just bits, and refuses a bad value. And the panel around it: tags group under the machine that owns them and a group collapses, the search box keeps what matches and takes an empty group's header away with it, the kind filter shows one half of the I/O at a time, and a forced tag marks its **name** rather than only its button — with one button that hands every forced tag back. Driven through the panel's own controls, found by what is on screen. | `--self-test=force` |
| C7 | `--scene=` loads a template headless (not just windowed) and `--print-tags` dumps its real I/O | `--scene=... --print-tags` |
| C8 | `--deterministic --scene=` is rejected outright, not silently hybridized | `--deterministic --scene=...` |
| C9 | `DemoDriver` picks the right profile for each of the five manifest scene ids, and refuses honestly for an unknown one | `--self-test=demo` |
| C10 | `start-stop-station` profile: Start runs the belt, Stop and E-stop cut it, Start while tripped is refused, Reset clears the fault | `--self-test=startstop` |
| C11 | `tank-level-control` profile: the controller settles within ±5% of setpoint | `--self-test=tank` |
| C12 | `light-curtain-sorting` profile: both tall and short cartons are routed correctly, nothing lost | `--self-test=lightcurtain` |
| C13 | `roller-line-weighing` profile: outfeed counts, the inductive sensor actually fires for a metal carton, a running roller turns about its own axis rather than tumbling end over end, the checkweigher never carries two cartons at once, and the weight readout fits inside its own bezel | `--self-test=roller` |
| C14 | Demo's refusal on a scene with no profile reaches the UI (IdleHintUI), not just the console | `--self-test=refusal` |
| C15 | The part property panel drives live I/O for every tag-type combination (bit/int/float × output/input) | `--self-test=proppanel` |
| C16 | Run mode's click operates the part it lands on -- conveyor, pusher, emitter, stack light lamps and tank valves independently, not just the Control Panel | `--self-test=operate` |
| C17 | Entering Run mode names what's clickable (count and kinds), or says plainly nothing is, in the actual on-screen hint label | `--self-test=modehint` |
| C18 | "Try this scene" resolves the loaded scene to the right manifest entry, and refuses honestly (naming the scene) rather than pretending on a custom scene with no built-in exercise | `--self-test=tryscene` |
| C19 | Every shipped scene's tag set (id/type/kind) matches `engine/fixtures/scene_tag_sets.json` -- catches a template edit that renames or retypes a tag out from under a mapping file | `--self-test=scenes` |
| C20 | The Edit/Run contract as a pair: a click selects in Edit and does not in Run, a control operates in Run and does not in Edit, and entering Run clears both the placement preview and the selection | `--self-test=modes` |
| C21 | Every **settings** control in the part property panel reaches the simulation — a named observable per control (top beam, raycast reach, physics-material friction, belt surface velocity, ramp deck transform); no row is wider than the panel's own scroll bound; and a row the test does not know how to drive is a failure | `--self-test=partsettings` |
| C22 | The engine can find a sidecar to launch and knows how to start it — a frozen build runs itself, a checkout goes through an interpreter | `--self-test=sidecar` |
| C23 | The panel's setpoint pot turns, publishes, clamps to its scale plate, and follows a forced tag — and taking hold of the knob clears that force. Hit-tested at two panel headings, and refused outright in Build mode | `--self-test=setpoint` |
| C24 | A placed part can be dragged to a new cell: it lands on the same grid placement snaps to and stays on the work plane, one whole drag is one `Ctrl+Z`, a press that never travels pushes nothing onto the history, and Run mode refuses to drag at all | `--self-test=drag` |
| C25 | A drive can **fail**: a faulted conveyor stops *while its command is still on*, refuses to restart until the fault clears, a jammed cylinder freezes mid-stroke rather than returning home, and a seized tank valve keeps filling while its command reads zero. Also that the fault tool aims at drives and nothing else, and refuses to arm in Build mode | `--self-test=fault` |
| C26 | The nine parts added in CP-01…CP-09 do what their tags claim, asserted by **effect**: the VFD's actual speed lags its reference on the first tick and reaches it later; the diverter reports *neither* limit mid-sweep and freezes at that angle when seized; the gantry travels to a commanded position and refuses to claim a hold it does not have; the scanner's read pulse does not repeat for the same carton; the gauge's needle really moves; the heater has a measurable time constant and cools while its command still reads 100 %; a locked guard refuses the handle; and a selector still reads the same many ticks after nobody touched it | `--self-test=newparts` |
| C27 | The five parts added in LP-01…LP-05 do what their tags claim, asserted by **effect**, and two of them against real physics rather than a hand-turned clock: a raised blade genuinely stops a carton on a belt that is still running and releases it when it drops; a turntable deck carries its load round by friction without throwing it; the measuring wheel counts at the belt's true rate, counts nothing when it is over nothing, and holds at zero while its reset leg is high; forced cooling moves a heating station's balance point by a hundred degrees and a failed fan delivers no air while its reference still reads 100 %; and a two-hand station refuses the permissive for two presses a second apart while *both* of its bits read true | `--self-test=lineparts` |
| C28 | The loop a person is in while building a line, asserted as a loop rather than as a set of calls: a second click after a placement makes a *second part* with its own instance id, a rotation set while placing survives the placement, Escape puts the part down and a click then places nothing, two `Ctrl+D`s land in two cells instead of stacking in one, an arrow key moves the selection exactly one grid cell and one `Ctrl+Z` puts it back, and committing a **move** leaves the tool empty rather than holding a copy of what was just moved. A selection is also a **group**: Shift+click adds and removes, an arrow key moves every selected part by the same vector, `R` turns each about its own centre, `Ctrl+D` copies the group keeping its shape, `Del` removes all of it — and each of those is one `Ctrl+Z`, not one per part. The projection half of box-select is **not** covered here: it asks a camera where each part's box lands on screen, and a headless run has none.  And the minute after a line exists: `Ctrl+A` takes every part, `Ctrl+C`/`Ctrl+V` copies a group and puts it down clear of itself keeping its shape, every pasted part mints its own instance id rather than adopting the one it was copied from, a second paste lands somewhere new, and one `Ctrl+Z` takes a whole paste back. Part names carry the instance id, follow a rename, and — asserted directly, because it is the trap — do **not** grow the part they name. | `--self-test=buildflow` |

### C-release. The same self-tests, against a built binary

`tools/packaging/check_release.py --target windows|linux` runs 21 of the C
checks against `dist/<target>/FactoryForge*` instead of a source checkout, and
is the gate on `.github/workflows/release.yml`. Green on both platforms on
2026-08-24: Windows 109 MB, Linux 74 MB, 24 self-tests each.

The distinction is the point: a checkout can pass every C check while the
exported binary fails. Two of them read checked-in fixtures, which used to sit
outside `res://` at a path that does not exist beside a binary — and even once
moved in, cannot be read with `System.IO` at all, because an export packs them
inside the `.pck`. It also checks two structural things no self-test can see
from the inside: that the .NET assemblies were exported (without them the binary
still builds, exits 0, and fails every script at runtime) and that the frozen
sidecar is where `SidecarLocator` will look.

It also asks the frozen sidecar which drivers it can genuinely run
(`factoryforge-sidecar drivers`). CI's first release shipped without S7 or
PLCSIM and nothing noticed: a driver whose third-party import failed still
registers, so it stays listed in `--help` and fails only when someone connects.
Neither the help text nor the registry can answer this — only the `HAS_*` flag
each driver module sets.

### D. Engine self-tests (need a display)

| # | Check | Command |
|---|---|---|
| D1 | Whole click path from a synthesized mouse event to a tag | `--self-test=click` |
| D2 | The whole **drag** path: a synthesized press, motion and release move a placed part and one `Ctrl+Z` puts it back. Guards the segment C24 cannot reach — the pixel threshold, and selecting the part under the press rather than under the live cursor | `--self-test=dragpath` |

### E. Determinism and the regression contract

| # | Check | Why |
|---|---|---|
| E1 | `drive_engine.py` → `tall=5 short=5` | the contract the whole project is pinned to |
| E2 | Two `--deterministic` runs produce **identical** counts | reproducibility is the point of that mode; a drift here invalidates E1 |
| E3 | `--time-scale=4` reaches a higher tick count than `1.0` in the same wall-clock | the rate control actually drives the accumulator |

### F. Engine ↔ sidecar seam

| # | Check | Why |
|---|---|---|
| F1 | `connect --driver mock` attaches to a running engine and reports the real scene and tag count | the path that did not exist until recently |
| F2 | `connect --driver modbus-tcp` starts and prints an address map | server drivers need no PLC |
| F3 | `connect --driver opcua-server` starts and reports an endpoint | ditto |
| F4 | `connect` against **no** engine fails fast with a useful message, not a hang | the most likely first-run mistake |
| F5 | Physics scene driven over the bus actually sorts (`counter.tall` > 0) | proves parts, sensors, pusher and removers work together under Jolt |

### G. Robustness

| # | Check | Why |
|---|---|---|
| G1 | Corrupt scene JSON is refused without crashing | a hand-edited scene file is expected |
| G2 | Unknown CLI arg does not prevent startup | |
| G3 | Loading a scene saved by a *newer* build (unknown property keys) still opens | forward compatibility is claimed in `SceneData`'s docs |
| G4 | A second engine instance reports its tag bus is dead rather than claiming ready | `--duration=6` against a held port |
| G5 | The sidecar notices when the engine dies mid-run and starts retrying, rather than serving stale values forever | FF-03 |
| G6 | Forcing an input tag while paused still reaches the bus | `check_force_while_paused.py`, FF-14 |
| G7 | Forcing an int tag and a float tag both reach the bus, not just bits | `check_force_types.py`, UX-45 -- §2.8's Force-button bug at the wire-protocol level, independent of any UI |

### H. Scene exercises (`tools/try_scene.py`)

| # | Check | Why |
|---|---|---|
| H1 | `sorting-by-height`: `try_scene.py` drives it to `tall=5 short=5` | the exact `drive_engine.py` contract, run through the newer self-contained script |
| H2 | `start-stop-station`: the full Start → E-stop → Start-while-tripped (refused) → Reset → Start sequence, **then a production run** — `counter.count` must advance, and the E-stop is **timed** against §4.2's 200 ms | mirrors `StartStopStationProfile` and C10, from the outside. The sequence alone used to end with `produced=0`: it fitted inside one emitter half-period, so the line never made anything |
| H3 | `tank-level-control`: the same controller run at **two** setpoints, with both times reported — reaches and holds 70%, then reaches 20% | mirrors `TankLevelControlProfile` / C11. One setpoint proves the controller runs; two prove the process, since outflow follows Torricelli and the drain valve loses authority as the tank empties (§4.3) |
| H4 | `light-curtain-sorting`: **conservation** — `tall + short` equals the cartons emitted, after a drain phase, and nothing measured below the threshold is diverted | mirrors `LightCurtainSortingProfile` / C12. "Both counters advanced" passes while the diverter drops cartons on the floor or double-counts them, which is this scene's most likely failure |
| H5 | `roller-line-weighing`: cartons weighed (counted by scale rising edges), the scale returns to zero between them, and metal detections are non-zero **and strictly fewer** than cartons | mirrors `RollerLineWeighingProfile` / C13. "Metal was seen once" passes for a sensor that fires on everything — the exact confusion this scene exists to clear up |
| H6 | `pick-and-place-cell`: a full index → lower → grip → lift → traverse → release cycle, with the carton it placed reaching the outfeed; the drive's **actual speed measurably lagging its reference** during the ramp; real item codes read; and a seized gantry freezing where it is | mirrors `PickAndPlaceCellProfile`. The 200 ms E-stop contract is measured against `infeed.run`, not the belt's last revolution: a VFD asked to stop *ramps down*, which is why a real E-stop circuit removes power. The coast-down is timed separately, on its own Stop press |
| H7 | `heat-treat-station`: the **standing offset measured, not asserted** — the plant is held with the integral term switched off and the steady-state error recorded, then switched on and the error checked to close; then a failed element cools while the heater command is held at 100 % | mirrors `HeatTreatStationProfile`. Nothing else in the project demonstrates *why* integral action exists rather than stating that it does |

Needs no display — the spike behind UX-10 proved templates simulate headless
— so this runs in the same job as A/C/E, not behind `--gui`. Each check
spawns its own engine, drives it exactly the way a PLC would (writing
outputs, forcing the panel inputs a human operator would for
`start-stop-station`), and tears it down: the seam UX-42's tag-set check
does not reach, since that one only checks the tags exist, not that the
scene actually does anything when driven.

### I. With a PLC — manual, verified 2026-08-12

Not in the runner: it needs S7-PLCSIM Advanced, which CI does not have. Run by
hand, and **passing**:

```bash
# 1. PLCSIM Advanced: instance started, examples/tia/ downloaded to it
# 2. Terminal 1
godot --path engine/
# 3. Terminal 2
cd sidecar
python -m factoryforge_sidecar connect --driver plcsim-advanced \
    -o instance <your instance name> --mapping ../examples/plcsim_mapping.json
```

**All three Siemens drivers now drive the rigid-body 3D scene from a real
virtual S7-1500** — belt running, emitter pulsing, sensors reporting back,
pusher diverting tall cartons down the chute, both counters climbing.

| Driver | Command | Notes |
|---|---|---|
| `plcsim-advanced` | `-o instance <name> --mapping ../examples/plcsim_mapping.json` | Works in either communication mode; talks to the API, not the network |
| `opcua-client` | `-o url opc.tcp://<ip>:4840 --mapping ../examples/opcua_mapping.json` | Needs the CPU on the **Virtual Ethernet Adapter** and the OPC UA server enabled |
| `s7-snap7` | `-o host <ip> -o db <n> --mapping ../examples/snap7_mapping.json` | Needs the Virtual Ethernet Adapter and a **non-optimized** DB |

Cross-checked rather than taken on trust: after a snap7 run, reading `FF_IO`
straight off the CPU showed `CounterTall=7 CounterShort=7`, matching the
simulation's final state exactly — so the DInt writes really landed in the PLC
rather than only in the sidecar's copy.

Two setup traps that cost time here:

- **The PLCSIM instance name is the one in the control panel**, not necessarily
  the CPU name in TIA. A mismatch surfaces as `InstanceNotRunning`.
- **In Softbus (local) mode the virtual CPU has no IP at all**, so neither OPC UA
  nor snap7 can reach it however the TIA project is configured. The native API
  driver does not care.

### J. Not automated

Honest list of what this plan does **not** prove:

- **Physical hardware.** Everything in section I ran against a *virtual* S7-1500
  in PLCSIM Advanced. That exercises the real protocols and the real firmware
  behaviour the drivers were written for, but it is not a physical CPU on a
  real network, and it says nothing about PROFINET timing or cable-level faults.
- **Any non-Siemens controller.** OpenPLC over Modbus is implemented and
  unit-tested, never run against the real thing.
- **Long runs with a PLC.** The longest verified run is 45 seconds.
- **Packaging — but not for the reason this line used to give.** It said no
  binary had ever been exported. That stopped being true: both platforms build
  from clean runners and `check_release.py` runs 25 headless self-tests against
  the **exported binary**, which is a stronger check than anything in this plan
  (`docs/PACKAGING.md`). What is still unproven is the *distribution* — nothing
  has ever been tagged, published or downloaded by anyone, so "the archive a
  stranger gets works on their machine" remains untested. HP-09.
- **Visual correctness.** Screenshots are rendered and read by hand; nothing
  compares them automatically.
- **Long-run stability.** Nothing runs for hours.

---

## Results

**2026-08-30 — 55 passed, 0 failed, 2 skipped, 781s** (`python tools/test_plan.py`,
Godot 4.7.2-mono). The two skips are D1/D2, which need a display; both were run
separately (`--self-test=click`, `--self-test=dragpath`) and **PASS**.

Grown by `docs/BUILD_FLOW_PLAN.md`: C28 (the build loop).

Grown by `docs/LINE_PRIMITIVES_PLAN.md`: C27 (the five line primitives, two of
them checked against the solver rather than the dispatch) and an eighth scene
exercise in H.

Grown by `docs/COMPONENTS_AND_POLISH_PLAN.md`: C26 (the nine new parts, asserted
by effect) and H6/H7 (the two new scene exercises). The seven scene exercises:

```
H1 sorting-by-height      tall=6 short=6                       estop=47ms fault=45ms
H2 start-stop-station     batch=4 produced=4 counted=3         estop=45ms fault=47ms
H3 tank-level-control     sp70 reached 9.7s | sp20 reached 20.2s  estop=19ms fault=16ms
H4 light-curtain-sorting  tall=4 short=5 measured=13           estop=46ms fault=47ms
H5 roller-line-weighing   weighed=12 rejects=6 metal=4 out=12   estop=46ms fault=45ms
H6 pick-and-place-cell    placed=6 out=6 codes=[101,102,201]    estop=35ms
H7 heat-treat-station     P-only offset 14.2degC -> PI 0.0degC  estop=20ms
```

Two failures found by this run and fixed rather than re-run around:

* **A6** flagged `KeyBindings.Binding` as referenced nowhere outside its own
  file — true, because both readers reached it through `var`. Named at its use
  sites.
* **H6** failed with `placed=0`, and reproduced on about half of all runs. It
  was not the scene: a carton resting on a static deck settles a centimetre or
  two inside it (contact slop), then meets the **vertical end face of the next
  conveyor's collider** and wedges, stopping the queue on a belt that is still
  running. Every deck now carries a lead-in wedge at both ends
  (`ConveyorBelt.AddTransferRamps`). Six consecutive runs afterwards placed 6–7
  cartons each, against 0–1 before. **Any two conveyors placed end to end hit
  this**, so it was costing every user who built the most obvious thing there
  is.

Fixing that exposed a third, latent since long before this plan: **H5** began
failing about one run in six on "the checkweigher and the inductive sensor flag
the same cartons". Two cartons occasionally shared the weigh deck and read as
one peak. A real checkweigher line controls spacing and this one had none, so
the controller now holds the feed while the scale is loaded — in the exercise
and in `RollerLineWeighingProfile` alike. Eight consecutive runs afterwards gave
*identical* counts rather than merely passing more often.


**2026-08-23 — 47 passed, 0 failed, 417s** (`python tools/test_plan.py --gui`,
on Godot 4.7.1-mono).

Re-run the same day on **Godot 4.7.2-mono** after upgrading:
`--only A,B,C,E,G,H` — **41 passed, 0 failed, 342s**. Same checks minus D (needs
a display) and F (needs the sidecar's driver stack); nothing behaved differently
between the two engine builds. `project.godot` asks for feature `4.7`, so either
patch release runs the project.

Then again after `docs/LOOSE_ENDS_PLAN.md` landed, which added A6 and C21:
`--only A,B,C,E,G,H` — **43 passed, 0 failed, 344s**.

And after `UX_PLAN.md` Phase 0, which added C22: `--only A,B,C,E,G,H` —
**44 passed, 0 failed, 437s**. Separately, `check_release.py` runs 21 of the C
checks against the *exported Windows binary*, which is the gate a release has to
pass and is not part of this count.

Grown from the 2026-08-12 snapshot (20 passed) by Phases 2, 4, 5 and 6 of
`docs/UX_PLAN.md` landing in between: ten more headless self-tests (C10…C19,
the per-scene demo profiles, Run-mode click and hover, "Try this scene", and
the scene-tag-set fixture check), a pairwise Edit/Run self-test (C20), a
second robustness check for non-bit forcing (G7), and a whole new section —
H1…H8, all eight shipped scenes driven end to end through `try_scene.py`.

Notable: **F5 sorts 5 tall / 5 short on the rigid-body scene**, matching the
deterministic contract. That is not guaranteed and is not asserted — Jolt makes
no reproducibility promise, which is the whole reason `--deterministic` exists —
but it means emitter, sensors, pusher, chute and removers agree with the
scripted model on this layout.

### What building the plan found

Three real defects, none of which any existing test could have caught, because
none of them are in code any existing test executes:

1. **A second engine reported "ready" with no tag bus.** The bind failure was
   pushed as an error and then ignored: the scene rendered, the parts simulated,
   and no driver could ever connect. Anyone hitting this would go and debug
   their PLC. The startup line now says `[NO TAG BUS — port in use]` and the
   error names the likely cause. (**G4**)

2. **The engine could not be restarted promptly.** The socket from the previous
   instance is not always released by the time the new one asks, and the retry
   was a single 500 ms attempt — so closing the app and reopening it produced a
   silently unreachable engine. Now retried over ~3 s.

3. **Godot hangs when its stdout is an undrained pipe**, and it fills that
   buffer *before* binding the bus. Measured: with inherited stdout or a file
   the port opens in 0.25 s; with `subprocess.PIPE` and no reader it never opens
   at all. `subprocess.run` is fine because `communicate()` drains concurrently;
   a bare `Popen` is not. This is a trap for anyone scripting the engine.

And two of my own tests were wrong in ways worth recording, since both would
have shipped as false confidence:

- **E2 passed vacuously.** It compared two *undriven* deterministic runs, which
  both sorted nothing, so it asserted `(0,0) == (0,0)`. A determinism check that
  passes when the simulation does no work is not a check. It now drives both
  runs and requires the result to be non-zero as well as equal.
- **F5 tested the wrong thing.** It drove the physics scene with `--driver mock`
  and expected cartons to sort. The mock driver is a passthrough with no control
  logic, so an engine driven by it correctly does nothing — the failure was in
  my test, not the engine.

Every self-test added here was also checked by deliberately reintroducing the
bug it guards. `--self-test=scene` was confirmed to catch both historical
save/load defects: a sensor losing `visual_only` and a remover losing the tag it
counts into.
