# FactoryForge — Review and Fix Plan

*Written 2026-08-22. A full-surface review: physics, graphics, models, menus,
functionality, performance, connectivity, workflow, ease of use, and project
hygiene. Nothing in here has been implemented — this document is the plan.*

---

## How this review was done

Not by reading alone. Everything claimed below was checked against the running
program or against a specific line of source.

| What | Result |
|---|---|
| `dotnet build` | succeeded, **0 warnings** |
| `python -m pytest -q` | **41 passed** |
| `python tools/test_plan.py` | **A1-A3, B1, C1-C4, E1-E3 all PASS** |
| GUI run, `--time-scale=1 --duration=12` | 931 bus ticks, `tall=0 short=0` |
| GUI run, `--time-scale=4 --duration=12` | 4642 bus ticks (3.99x of 1x) |
| GUI screenshot, start screen | reviewed |
| GUI screenshot, `roller_line_weighing` template | reviewed |
| Source read | tag bus (both sides), protocol, all 6 drivers, sidecar CLI, `Main.cs`, `SceneEditor.cs`, all editor UI, parts, view |

**The project is in good health.** The test plan is real, the determinism
contract holds, the build is clean, and `docs/TEST_PLAN.md` section I is a more
honest statement of what is unproven than most commercial products manage. The
findings below are gaps at the edges, not rot in the core.

Two candidate defects were **investigated and dismissed**, recorded here so
nobody re-opens them:

- *Time-scale catch-up cap.* `MaxCatchUpSteps = 5` in `Main.cs` looked like it
  would clip the 4x rate at 60 fps. Measured: 4x produced 3.99x the ticks of 1x.
  It does not bite at the frame rates this app actually runs at. Left alone.
- *C#/Python coercion divergence.* The two tag models were diffed rule by rule
  (bit accepts 0/1 only; int rejects bool; float rejects bool). They agree
  today. The problem is that nothing enforces it — see FF-29.

---

## Severity key

| | |
|---|---|
| **Critical** | Loses the user's work, or silently reports something false |
| **High** | Breaks an advertised workflow, or fails with no diagnostic |
| **Medium** | Real friction or waste, workaround exists |
| **Low** | Polish |

---

# A. Data loss

### FF-01 — "Clear" destroys the scene with no confirmation and deliberately erases undo
**Critical.** `engine/src/Editor/SceneEditor.cs:841`

`ClearAllPlacedParts()` frees every part, then calls `_history.Clear()` with the
comment *"its commands refer to parts that are now gone"*. The toolbar wires this
to a single button click (`SceneToolbarUI.cs:197`) sitting two buttons away from
Save. There is **no confirmation dialog anywhere in the codebase** — a grep for
`ConfirmationDialog` and `AcceptDialog` returns nothing.

So: twenty minutes of building, one mis-click, and the work is gone with Ctrl+Z
explicitly disabled for it. This is the most destructive action in the app and
the least protected.

**Fix.** Add a confirmation dialog for Clear. Separately, make Clear undoable by
pushing a single composite `ClearSceneCommand` that captures the part list and
can rebuild it, instead of dropping the history. If the composite command proves
awkward, the confirmation alone removes the Critical.

### FF-02 — No unsaved-changes guard on any exit path
**Critical.** `Main.cs:186-215`, `StartScreenUI.Reopen`

Going Home, loading a scene, choosing a template, and quitting all discard the
current scene immediately. The editor tracks no dirty flag.

**Fix.** An `IsDirty` flag on `SceneEditor`, set by every command that mutates the
scene and cleared on save. Prompt on Home / Load / template / quit. This and
FF-01 share the same dialog helper, so build them together.

---

# B. Connectivity

### FF-03 — The sidecar never reconnects to the tag bus, and does not notice it is gone
**Critical.** `sidecar/factoryforge_sidecar/tagbus.py:97`

`TagBusClient.run()` connects once. On `ConnectionClosed` or `OSError` it logs a
warning and returns. All four call sites create the task exactly once and never
retry or await it: `__main__.py:114`, `__main__.py:164`, `tools/drive_engine.py:38`,
`tools/drv_trace.py:27`.

Close the engine while `connect` is running and the runner task quietly
completes. The CLI is parked on `await asyncio.Event().wait()`, so the process
does not exit. **The driver stays up serving the last-known tag values.** A
Modbus master or OPC UA client on the other end keeps reading plausible, frozen
sensors, with no error raised anywhere.

For a simulator this is the worst possible failure shape: it is indistinguishable
from a line that has legitimately stopped.

The asymmetry makes the gap obvious — `drivers/opcua_client.py:122` has a proper
`_connect_loop` with reconnection for the *PLC* side. The bus, which every driver
depends on, has none.

**Fix.**
1. Wrap the connect/pump in a retry loop with capped exponential backoff
   (0.5s to 5s), exiting only when the caller's `stop` event is set.
2. Emit `status` messages on drop and recovery, matching the OPC UA driver's
   vocabulary so the two read the same in a log.
3. On reconnect, discard the cached table and wait for a fresh `describe` —
   the epoch may have changed.
4. Drivers must be told: add a `bus_disconnected()` hook to the `Driver` ABC so
   a Modbus or OPC UA *server* driver can mark its points bad-quality rather
   than serving stale values. Default implementation: no-op.
5. Test (new section G): start engine, connect, kill engine, assert the sidecar
   logs the drop and retries; restart engine, assert it re-describes and resumes.

### FF-04 — Nothing in the UI ever shows whether a driver is connected
**High.** `TagBusServer.cs:35` (`HasClient`), `Main.cs:106`

`HasClient` is never read by any UI in the project — grep confirms zero
references outside the bus itself. `IsListening` is read exactly once, to append
`[NO TAG BUS — port in use, drivers cannot connect]` to a `GD.Print` line.

That message goes to **stdout**. Run the windowed build (or a packaged `.exe`,
which has no console at all) and a port conflict is completely invisible: the
scene renders, physics runs, everything looks healthy, and no controller can
ever reach it. The session notes already record this failure mode costing an
afternoon — it is still only diagnosable from a console.

**Fix.** A persistent status chip in the toolbar with three states: *no bus*
(red, port bind failed), *listening, no driver* (amber), *driver connected*
(green, showing the scene name and tag count). Drive it from `IsListening` and
`HasClient`. This is a small change with a large payoff — it makes the project's
central concern visible for the first time.

### FF-05 — The F4 wiring panel is ignored by three of the four PLC drivers
**High.** `engine/src/Editor/DriverConnectionUI.cs:266`

`DriverWiringUI` writes one mapping file (`MappingPath = "user://io_mapping.json"`,
`DriverWiringUI.cs:26`). `BuildSidecarArguments()` appends `--mapping` **only in
the `opcua-client` branch**. `s7-snap7`, `plcsim-advanced`, and `modbus-tcp` do
not get it.

So the advertised workflow — build a scene, wire it to PLC addresses in F4,
connect in F5 — silently drops the wiring for three of four drivers. The user
sees a successful connection and wrong or default addresses. `AGENTS.md` itself
shows `plcsim-advanced` being run with `--mapping` by hand, confirming it is
needed.

**Fix.** Hoist the mapping argument out of the switch so every driver that
accepts one receives it. Verify each driver actually reads `mapping_file` from
config; add a line to the F5 status label naming the mapping file in use, or
saying "no wiring file — using defaults".

### FF-06 — Auto-detect reports a green success when it detected nothing
**High.** `DriverConnectionUI.cs:198-241`

Three TCP probes (4840, 102, 502). If all three fail, the code falls through to:

```
SelectedDriver = "plcsim-advanced";
_statusLabel.Text = "Auto-Detected: Siemens PLCSIM Advanced Shared Memory API";
_statusLabel.AddThemeColorOverride("font_color", green);
```

Nothing was detected. PLCSIM Advanced was never probed — it is a shared-memory
API with no port to probe. A user with no PLC at all, on a machine with no
Siemens software installed, gets a green tick claiming a CPU was found.

In a project whose test plan contains an explicit "Not automated / honest list"
section, this is out of character and worth fixing on principle as much as on
function.

**Fix.** Make the fallback honest: amber, "No controller found on <ip>. Defaulting
to PLCSIM Advanced — it uses a shared-memory API with no network port, so it
cannot be probed. Check the instance name below." Better still, actually probe
it: the `plcsim-advanced` driver can enumerate running instances through the API,
so shell out to the sidecar for a real answer.

### FF-07 — Auto-detect mutates Godot UI nodes from a thread-pool continuation
**High.** `DriverConnectionUI.cs:79`, `:198`

`autoDetectHeaderBtn.Pressed += () => _ = RunAutoDetectAsync();` fires and
forgets. Inside, `await ProbePortAsync(...)` resumes on a thread-pool thread —
there is no synchronization context in Godot to capture. Every `_statusLabel.Text
= ...` and `_driverDropdown.Select(...)` after the first `await` therefore runs
**off the main thread**, which Godot does not permit for scene-tree nodes.

Today it usually gets away with it. It is a real race, and the symptom when it
does not will be an unexplained crash that nobody connects to this button.

**Fix.** Route every post-await UI mutation through `CallDeferred`, or restructure
so the probing task returns a result and a single deferred callback applies it.
Also give the fire-and-forget task a `try/catch` so a probe exception is not
swallowed into a lost task.

### FF-08 — The Connect button is Windows-only
**High.** `DriverConnectionUI.cs:333`

`OS.CreateProcess("cmd.exe", ["/d", "/s", "/c", ...])`. There is a Linux export
preset (`export_presets.cfg` `[preset.1]`) and M6 claims "Windows and Linux
setup". On Linux this fails and the user gets "Could not start python" with no
explanation of why.

**Fix.** Branch on `OS.GetName()`: `cmd.exe /c` on Windows, `x-terminal-emulator`
/ `xterm -e` / plain `bash -c` on Linux and macOS. Keep the existing
clipboard-fallback path, which is already the right safety net.

### FF-09 — The developer's own PLC address and instance name ship as defaults
**Medium.** `DriverConnectionUI.cs:27-30`, and again at `:203`

```csharp
public string IpAddress    { get; private set; } = "192.168.1.20";
public string InstanceName { get; private set; } = "Sorting_PLC";
```

`192.168.1.20` is this machine's PLCSIM Advanced address, per `AGENTS.md`. Every
user everywhere gets it prefilled, including inside the auto-detect fallback.

**Fix.** Default the IP to empty with placeholder text `e.g. 192.168.1.20`, and
the instance to empty with placeholder `the name in the PLCSIM control panel`
(which is also where the documented `InstanceNotRunning` trap lives). Persist
whatever the user last entered to `user://` so it is filled in from the second
run onward.

### FF-10 — The engine's `hello` omits `tick_ms`, which the protocol spec requires
**Medium.** `engine/src/TagBus/TagBusServer.cs:144`

`docs/tag-bus.md:84` specifies:

```json
{ "t": "hello", "protocol": 0, "engine": "...", "tick_ms": 10 }
```

The Python reference engine complies (`harness/engine_stub.py:84` calls
`proto.hello(ENGINE_ID, self.tick_ms)`). The C# `Hello()` builds only three
fields. The sidecar falls through to `hello.get("tick_ms", DEFAULT_TICK_MS)` and
hardcodes 10 ms — while `TickMs` is an `[Export]` property that genuinely paces
the engine (`Main.cs:343`).

Change the engine's tick and the sidecar keeps flushing at the old rate,
violating `docs/tag-bus.md:162` ("the sidecar sends at most one write per tick").
The real engine is less spec-compliant than the stub it was ported from.

**Fix.** One line: add `["tick_ms"] = TickMs` to `Hello()`. Cover it in the
conformance test from FF-29.

### FF-11 — One bad value in a write batch silently drops the rest
**Medium.** `TagBusServer.cs:228`

`ApplyWrites` iterates the values calling `Tags.Set`, which throws
`ArgumentException` on a coercion failure. The throw unwinds to the catch in
`PumpClient` and logs `"tag bus: bad message"` — abandoning every remaining value
in that message, in dictionary order, naming none of them.

Note the care taken directly above it, where *unknown* tags are collected and
reported by name. Bad values deserve the same.

**Fix.** Try/catch per value inside the loop; collect failures; emit one
`status("warn", "bad_value", ...)` naming each rejected tag and why. Apply every
value that was valid.

---

# C. Simulation and physics

### FF-12 — Cartons that miss a remover live forever
**High.** `engine/src/Parts/Remover.cs:80` is the only despawn path

A `BoxPhysics` is freed in exactly two places: entering a `Remover` zone, and
`SceneEditor.ResetItems()`. There is no kill plane, no Y-threshold sweep, and no
cap on live bodies.

The floor is 7 m x 7 m (`StudioEnvironment.FloorExtent = 7.0f`). A carton that
runs off the end of a line, gets shoved wide by the diverter, or is emitted into
a scene with no remover falls off the edge and **falls forever** — a live
`RigidBody3D` with `ContinuousCd = true` doing broad- and narrow-phase work every
physics step, in perpetuity.

An emitter left pulsing in a half-built scene accumulates rigid bodies without
bound. This is exactly what a student building their first line will do, and the
README advertises "boxes outrun the diverter" as a feature.

**Fix.**
1. A kill plane: sweep bodies below `y = -2` each second and free them, counting
   them.
2. A configurable live-item cap (default ~200) that stops the emitter and raises
   a `status` warning rather than degrading silently.
3. Surface both in the toolbar status chip from FF-04: "items: 34" and a warning
   colour when the cap is hit. A student whose line is leaking cartons should be
   told, not left to watch the frame rate rot.

### FF-13 — Reset does not reset the emitter, so the metal cadence drifts
**Medium.** `Emitter.cs:68` (`_emitted`), `SceneEditor.cs:304` (`ResetItems`)

`ResetItems()` handles `LevelTank`, `ButtonPanel` and `Remover`, and clears
`_emitEdges` and `_emitAlternate` — but never touches `Emitter._emitted`, which
drives `MetalEvery`. Reset a metal-sorting scene and the metal phase is wherever
the previous run left it.

For a project whose regression contract is reproducibility, "Reset does not fully
reset" is a correctness bug, not a nit.

**Fix.** Add an `Emitter.ResetCount()` and call it from the `ResetItems` loop
alongside the other part types.

### FF-14 — Forcing a tag while paused never reaches the PLC
**High.** `Main.cs:358`, `TagInspectorUI.cs:110`

`SimulationControls` implements pause as `Engine.TimeScale = 0`. In `Main._Process`
the accumulator advances by `delta`, which is now zero, so `steps` stays zero and
`if (steps > 0) _bus.SendUpdates();` never fires.

`TagInspectorUI.ToggleForce()` mutates the tag table and holds no reference to
the bus, so it cannot push the change itself. `ResetSimulation()` calls
`_bus.SendUpdates()` explicitly — evidence the author already knew changes made
outside the tick loop need a manual nudge.

Result: pause the line, force a sensor to exercise an interlock, and **the PLC
never sees it** until you un-pause. That is precisely the workflow pause exists
for, and precisely what the README sells: *"freeze the line mid-cycle to read
every sensor and actuator at that instant"*.

**Fix.** Call `_bus.SendUpdates()` unconditionally once per frame after the step
loop, not only when `steps > 0`. It already sends nothing when nothing changed,
so an idle scene still produces zero traffic and the delta-only guarantee holds.
Add a self-test: pause, force an input, assert the sidecar receives it.

---

# D. Performance

### FF-15 — The dispatch loop allocates a string for every tag of every part, every physics tick
**High.** `engine/src/Editor/SceneEditor.cs:907-1010`

`_PhysicsProcess` runs at 60 Hz over every placed part and builds tag ids with
string interpolation inline:

```csharp
if (node is ConveyorBelt belt && Tags.Contains($"{instanceId}.rotate"))
    belt.SetRunning((bool)Tags.Visible($"{instanceId}.rotate"));
```

A `ButtonPanel` alone does this five times per tick. A 30-part scene averaging
2.5 lookups per part is roughly **4,500 string allocations per second**, each
followed by two or three dictionary hashes (`Contains`, then `Visible` or `Set`).

It scales linearly with scene size — so it degrades exactly as the user builds
the larger factory the product exists to let them build.

**Fix.**
1. Cache the tag ids on `PlacedPart` at placement/load time. They are fixed for
   the life of the part; rebuild them on rename.
2. Collapse `Contains` + `Visible` into one `TryGetVisible(id, out value)` on
   `TagTable`, and `Contains` + `Set` into a `TrySet`.
3. Replace the `switch (partType)` string dispatch with an enum or a small
   interface (`IPartDispatch.Tick(dt, TagTable)`) implemented by each part —
   which also removes the growing switch every new part must be added to.

Item 3 is the larger refactor and is optional; items 1 and 2 remove essentially
all of the waste for a fraction of the effort.

### FF-16 — Every conveyor recomputes its transport vector every tick
**Medium.** `SceneEditor.cs:918`, `ConveyorBelt.cs:70`

`belt.SetRunning(...)` is called unconditionally each tick, and `SetRunning`
performs `(GlobalBasis * Direction).Normalized()` — a matrix multiply and a
square root — then writes `ConstantLinearVelocity`, whether or not anything
changed.

This is currently load-bearing: it is *why* rotating a running belt updates its
transport direction. So do not simply guard on the tag value.

**Fix.** Cache the last applied `(running, globalBasis)` pair and skip the work
when both are unchanged. Keeps rotation-while-running correct and makes the
common case free.

---

# E. Menus, workflow and ease of use

### FF-17 — The parts palette has no scroll and overflows the default window
**High.** `engine/src/Editor/PartPaletteUI.cs:19-24`

Fifteen buttons at 36 px, plus a title and 10 px margins, in a `PanelContainer`
whose `CustomMinimumSize` is `(180, 350)` — a *minimum*, not a maximum, so it
grows to roughly 580 px. Anchored top-left with a 20 px offset, it ends near
y=600.

`project.godot` declares **no `[display]` section at all**, so the window is
Godot's default 1152x648. The palette therefore runs off the bottom of the
window in the default configuration, and there is no `ScrollContainer` — the
parts below the fold are simply unreachable.

The roadmap plans "more parts driven by what contributors ask for", which makes
this worse over time.

**Fix.** Wrap the button list in a `ScrollContainer`, cap its height against the
viewport, and group the fifteen parts under collapsible headings — *Transport*,
*Sensors*, *Actuators*, *Process*, *Operator* — which also makes a flat list of
fifteen scannable. Add a filter box once the count passes ~20.

### FF-18 — The start screen card is taller than the default window
**Medium.** `engine/src/Editor/StartScreenUI.cs:75`

`new PanelContainer { CustomMinimumSize = new Vector2(980, 640) }` inside a
`CenterContainer`, with 28 px margins. At the default 1152x648 that is 640 px of
card in 648 px of window — no margin for error, and any smaller window clips the
card. A `CenterContainer` clips symmetrically, so the Quit button and the key
list go first, with no way to scroll to them.

**Fix.** Set an explicit window size and a minimum window size in `project.godot`
(see FF-32), and put the card in a `ScrollContainer` so a small window degrades
to scrolling instead of clipping.

### FF-19 — The tag inspector truncates the tag ids it exists to show
**Medium.** `engine/src/Editor/TagInspectorUI.cs`

Confirmed in the screenshot: `conveyor.rot…`, `sensor_low.d…`, `weight_reado…`,
`metal_check.…`. The panel is the primary debugging surface and the identifier is
the one thing a user needs to read from it — it is also what they must type into
their PLC mapping.

**Fix.** Widen the name column, and add a tooltip carrying the full id plus type
and kind. Elide in the *middle* rather than the end (`weight_read…value`), since
the suffix is the discriminating part. Click-to-copy the id would make the
inspector-to-mapping path materially faster.

### FF-20 — `R` rotates only while placing, never a selected part
**Medium.** `SceneEditor.cs:179`

The guard is `keyEvent.Keycode == Key.R && _previewNode is not null`. To rotate a
part already in the scene you must press `M` (move), then `R`, then click to
re-commit. The README and the start screen key list both say simply "Rotate
before placing" / "rotation (R)", so the behaviour is at least documented — but
it is not what anyone expects from an editor.

**Fix.** Let `R` rotate `_selectedPart` in place when there is no active preview,
pushing a `RotateCommand` onto the history so it undoes cleanly.

### FF-21 — No duplicate, no multi-select, no keyboard save/open
**Medium.** `SceneEditor.cs:167-190`

The editor binds Ctrl+Z, Ctrl+Y, M, R, Esc, Del/Backspace. There is no Ctrl+D
duplicate, no Ctrl+C/Ctrl+V, no rubber-band or shift-click multi-select, and no
Ctrl+S / Ctrl+O.

Building a ten-metre conveyor run means placing ten belts by hand, one at a time,
and saving means reaching for the mouse every time.

**Fix.** In rough order of value: Ctrl+S / Ctrl+O; Ctrl+D duplicating the
selection one grid cell over; shift-click multi-select with move and delete
across the set; then box-select. Each is a command on the existing history, so
undo comes free.

### FF-22 — Template entries do not look clickable
**Low.** `StartScreenUI.cs`

In the screenshot the five templates, "Empty scene", "Open a saved scene…" and
"Quit" all render as flat text with no border, no background and no visible hover
affordance. They are the primary call to action on the first screen a new user
ever sees.

**Fix.** Give each template a panel background, a hover highlight and a focus
ring; make the whole card the click target. Keyboard focus order and
Enter-to-activate matter here too, since this is the one screen a user meets
before learning any of the keys.

### FF-23 — Nothing moves on first run, and nothing says why
**High.** Observed: both GUI runs ended `tall=0 short=0`

Launch the app, pick "Sorting by height", and the factory sits perfectly still.
Nothing writes `conveyor.rotate` or `emitter.emit` unless a sidecar is running,
and the app ships no in-engine demo driver.

There *is* a manual path — the tag inspector's Force buttons drive output tags
directly, and forcing `conveyor.rotate` genuinely runs the belt. But nothing on
the start screen or in the scene points at it, and the alternative (F5 → connect)
requires Python plus `pip install -e sidecar` first.

For a teaching tool aimed at students, a still factory is a poor first thirty
seconds, and the most likely reading is "it is broken".

**Fix.** Two complementary changes:
1. **A built-in demo mode** — an in-engine driver that runs the belt, pulses the
   emitter and fires the diverter, so the app demonstrates itself with nothing
   installed. Offer it on the start screen as "Watch it run" and expose it in the
   toolbar; disable it automatically the moment a real driver connects, so it can
   never fight a PLC for the same tags.
2. **An idle hint** in the scene: after ~5 seconds with no bus client and no
   forced tags, show a dismissible line — *"No driver connected. Press F5 to
   connect a PLC, or Force a tag in the inspector to drive it by hand."*

This is the single highest-value item in this document for adoption.

---

# F. Graphics and models

### FF-24 — The world is 7 m square and its edge is in shot
**Medium.** `engine/src/View/StudioEnvironment.cs:11`

`FloorExtent = 7.0f` gives a 7 m x 7 m floor plane and a matching voxel grid.
In the template screenshot the floor edge and the horizon behind it are plainly
visible above the machines. It reads as an unfinished diorama rather than a
factory, and it caps how large a line anyone can build. It is also the direct
cause of FF-12.

**Fix.** Decouple the three concepts: a large visual ground plane (or a fogged
infinite shader) so no edge is ever in frame; a build volume that the grid
displays and placement clamps to; and a kill plane well below both. Make the
build volume configurable per scene and store it in the scene file.

### FF-25 — Part scale is inconsistent between parts
**Medium.** `ButtonPanel.cs`, `ConveyorBelt.cs:17`

In the screenshot the control panel is close to as tall as the roller conveyor is
long, and its pedestal is a comparable visual mass to the whole transport line.
A belt segment is 3.0 m x 0.5 m by default; the panel is built to no shared
reference and reads as roughly 1.5 m wide.

Since the parts are all procedural, there is no modelling package to blame — the
numbers just were not set against a common reference.

**Fix.** Establish a dimension table in `PartLayout` (belt width 0.5 m, panel
0.4 m x 0.6 m, stack light 0.08 m diameter, and so on) sourced from real
equipment, and rebuild each part's mesh against it. Add a 1 m reference cube to
the scene-editor grid so drift is visible while authoring. Worth doing before
more parts land and multiply the inconsistency.

### FF-26 — Materials are flat colour with no surface detail
**Low.** `IndustrialMeshBuilder.cs`, `BoxPhysics.cs:70`

Every material is a `StandardMaterial3D` with an albedo colour and a roughness
scalar. No normal maps, no roughness maps, no wear, no texture on the cartons.
The README's "metallic shaders" is technically accurate (`Metallic = 0.75` on
metal items) but everything reads as untextured plastic under the flat sky.

The lighting setup is sound — filmic tonemap, SSAO, a key light with soft shadows
and a fill — so the shortfall is surface, not lighting.

**Fix, in value order.** A subtle triplanar noise roughness map across metal
parts; a cardboard albedo + normal on cartons (the one object the eye follows);
a belt tread normal map so the existing UV scroll actually reads as motion; a
darker, less saturated floor with a faint speckle to stop it flattening out.
Contact-hardening shadows or a small SSAO radius increase would seat the parts on
the floor. None of this requires external assets — all can be procedural, which
keeps the repo asset-free.

---

# G. Packaging, platform and project hygiene

### FF-27 — No LICENSE file, despite an MIT badge that links to one
**Critical (for an open-source project).** `README.md:8`

The badge reads `License: MIT` and links to `LICENSE`. There are 120 tracked
files and no license among them — the link 404s on GitHub. Without it the work is
under exclusive copyright by default, whatever the badge says, and nobody can
legally reuse it.

This matters more here than usual: the roadmap explicitly parked the Factory I/O
mod spike over open-source credibility concerns.

**Fix.** Add an MIT `LICENSE` naming Mahamed Algaroshy and the year. Five
minutes, and it is the cheapest item in this document.

### FF-28 — There is no CI, though the roadmap says there is
**High.** No `.github/` directory exists

M2 checks off *"Run the C# engine against the Python protocol tests in CI"*. The
check is real and it passes — but only when run by hand on this machine.

**Fix.** A GitHub Actions workflow on push and PR:
- `dotnet build` asserting zero warnings (A1)
- `pytest` (B1)
- the four headless engine self-tests (C1-C4) via a cached Godot 4.7.1-mono download
- `tools/drive_engine.py` determinism (E1-E2)

`tools/test_plan.py` already sequences all of this and exits non-zero correctly,
so the workflow is mostly "install Godot, run the script". Skip the `--gui`
checks on the runner.

### FF-29 — `tests/test_engine_parity.py` does not exist
**High.** `engine/src/TagBus/Tag.cs:18`

The comment reads: *"Mirrors sidecar/factoryforge_sidecar/tags.py — the two must
stay in step, and tests/test_engine_parity.py checks that they do."* There is no
such file.

Two tag models, roughly 300 lines of coercion, epsilon and default rules,
mirrored by hand across two languages, with nothing enforcing agreement — and a
comment that actively tells the next contributor not to worry about it. They
agree today (diffed rule by rule). That is discipline, not a guarantee.

**Fix.** A shared JSON fixture — `tests/fixtures/tag_cases.json` — listing
`(type, input, expected | error)` triples including the subtle cases: `2` rejected
as a bit, `True` rejected as an int, `1` accepted as a bit, float epsilon at the
boundary. Then:
- a pytest that runs the fixture against the Python model;
- a `--self-test=parity` in the engine that runs the same fixture against the C#
  model and exits non-zero on divergence;
- both wired into `tools/test_plan.py` as a new section A4, and into CI.

Extend the same fixture idea to protocol conformance so FF-10 (`tick_ms`) can
never regress: assert the C# `hello`, `describe` and `update` messages carry
exactly the fields `docs/tag-bus.md` specifies.

### FF-30 — The launcher depends on a git-ignored file and a hardcoded `D:\` path
**High.** `run_factoryforge.bat:39`, `.gitignore:33`

`run_factoryforge.bat` is tracked. Its final step runs `scratch\live_driver.py` —
and `.gitignore` line 33 ignores `scratch/` entirely (`git check-ignore`
confirms). **A fresh clone cannot run the launcher**: it builds, starts Godot,
waits four seconds, then fails on a missing file.

It also hardcodes `D:\Godot_v4.7.1-stable_mono_win64\...` in two places — this
machine's Godot install, which nobody else has.

**Fix.** Move the live-driver demo out of `scratch/` into `tools/` and track it
(it is a useful thing, and FF-23 wants an in-engine equivalent anyway). Discover
Godot from a `GODOT` environment variable, then PATH, then a short list of common
install locations, with a clear error naming the variable if all fail. Add a
`--no-driver` flag so the launcher can just start the engine.

### FF-31 — No application icon
**Low.** `engine/project.godot` has no `config/icon`; no icon file in the repo

A packaged build ships with the default Godot robot in the taskbar, the window
title bar and the installer.

**Fix.** A simple procedural or hand-drawn 256x256 icon, referenced from
`project.godot` and from both export presets.

### FF-32 — No display settings at all in `project.godot`
**Medium.** `engine/project.godot`

No `[display]` section: no window size, no minimum size, no resizable or vsync
policy. The window is whatever Godot defaults to (1152x648), which is the direct
cause of FF-17 and FF-18.

**Fix.**
```ini
[display]
window/size/viewport_width=1600
window/size/viewport_height=900
window/size/window_width_override=1440
window/size/window_height_override=810
window/stretch/mode="canvas_items"
window/stretch/aspect="expand"
```
plus a minimum window size enforced at runtime. `canvas_items` stretch keeps the
UI legible on high-DPI displays, which is currently unhandled.

---

# H. What was checked and found healthy

Worth recording so this review is not read as an indictment.

- **The tag bus protocol design.** Epoch guarding on writes and forces, delta-only
  updates, one-sidecar-at-a-time enforcement, the port-rebind retry loop with a
  real explanation of why it exists. This is the strongest code in the project.
- **Coercion strictness.** Rejecting `2` as a bit and `bool` as an int, in both
  languages, with the reasoning written down. Easy to get lazily wrong; not wrong
  here.
- **The determinism contract.** Two solvers behind one tag interface, `E1`/`E2`
  passing with identical counts across runs. It works and it is genuinely useful.
- **Physics tuning.** Carton density at 150 kg/m³, friction chosen per material
  pair, `ContinuousCd` on, damping documented as air drag rather than a stability
  crutch, rotation deliberately left free so a shoved carton can tip. The pusher
  plate is an `AnimatableBody3D` with `SyncToPhysics` and its face is sized to the
  tallest carton so the push does not topple it. These are the decisions of
  someone who has watched the failure modes.
- **The self-test suite.** C1-C4 cover the paths that actually broke before,
  including a display-dependent click-path test kept specifically because the
  headless half passed while Run-mode clicking was dead.
- **`docs/TEST_PLAN.md` section I.** An explicit list of what is *not* proven,
  including "the longest verified run is 45 seconds" and "no binary has ever been
  exported". Keep this habit.

---

# I. What this review could not check

- **Real PLC hardware.** No PLCSIM Advanced instance was running; the Siemens
  paths were read, not exercised. Section H of the test plan covers them manually.
- **Long-running stability.** Longest run here was 12 seconds. FF-12 predicts a
  leak that only shows over minutes — worth a deliberate 30-minute soak once the
  kill plane lands, to confirm it.
- **Packaging.** Export templates are not installed on this machine, so no binary
  was produced. `docs/PACKAGING.md` is honest about this already.
- **Any non-Windows platform.** FF-08 is inferred from the source, not observed
  failing on Linux.
- **High-DPI and small-screen behaviour.** FF-17 and FF-18 are derived from
  measured control sizes against Godot's documented default window, not from
  running at 1152x648.

---

# J. The plan

Ordered so that each phase ships something independently useful, cheapest
high-value work first. Estimates assume the full-time solo pace the roadmap
assumes.

### Phase 0 — Stop the bleeding (half a day)
Cheap, mechanical, and two of them make the README stop making false claims.

| | |
|---|---|
| FF-27 | Add `LICENSE` |
| FF-10 | `tick_ms` in `hello` — one line |
| FF-13 | `Emitter.ResetCount()` in `ResetItems` |
| FF-14 | Unconditional `SendUpdates()` — one line, fixes the pause workflow |
| FF-09 | Clear the hardcoded PLC defaults |
| FF-30 | Track the live driver, discover Godot from the environment |

### Phase 1 — Never lose the user's work (1 day)
| | |
|---|---|
| FF-01 | Confirm on Clear; make it undoable |
| FF-02 | Dirty flag and prompt on Home / Load / template / quit |

### Phase 2 — Make connectivity honest and durable (2-3 days)
The heart of the product, and where the silent failures live.

| | |
|---|---|
| FF-03 | Bus reconnect with backoff, status messages, `bus_disconnected()` hook |
| FF-04 | Connection status chip in the toolbar |
| FF-05 | Pass `--mapping` to every driver that takes one |
| FF-06 | Honest auto-detect fallback |
| FF-07 | `CallDeferred` for post-await UI mutation |
| FF-11 | Per-value write errors, reported by name |

### Phase 3 — First run that sells itself (2 days)
| | |
|---|---|
| FF-23 | Built-in demo mode + idle hint |
| FF-22 | Real buttons on the start screen |
| FF-17 | Scrollable, grouped palette |
| FF-18, FF-32 | Window and display settings, scrollable start card |
| FF-19 | Readable, copyable tag ids |

### Phase 4 — Simulation integrity and performance (2 days)
| | |
|---|---|
| FF-12 | Kill plane, item cap, item count in the status chip |
| FF-15 | Cached tag ids, single-lookup `TryGetVisible`/`TrySet` |
| FF-16 | Skip unchanged conveyor transport recomputation |

### Phase 5 — Editor workflow (2 days)
| | |
|---|---|
| FF-20 | `R` rotates a selected part |
| FF-21 | Ctrl+S / Ctrl+O, Ctrl+D duplicate, multi-select |

### Phase 6 — Guardrails, so none of this comes back (2 days)
| | |
|---|---|
| FF-29 | Shared parity fixture, `--self-test=parity`, protocol conformance |
| FF-28 | GitHub Actions running the whole test plan |
| New G-series | Kill the engine mid-run and assert the sidecar notices (covers FF-03) |
| New | Force-while-paused self-test (covers FF-14) |

### Phase 7 — Look like a product (3 days)
| | |
|---|---|
| FF-24 | Ground plane, build volume, kill plane properly separated |
| FF-25 | Shared dimension table; rebuild parts against it |
| FF-26 | Procedural surface detail on metal, cartons, belt, floor |
| FF-31 | Application icon |
| FF-08 | Cross-platform sidecar launch |

**Total: roughly 14-15 working days.**

Phases 0-2 are the ones worth doing regardless of what else happens — they are
where the false claims and the silent failures are. Phase 3 is the one that most
changes whether a student who downloads this ever gets to the second screen.

---

## A note on sequencing

Phase 6 deliberately comes *after* the fixes rather than before. Writing the
parity fixture and the disconnect test first would be the more orthodox order,
but the fixes in Phases 0-4 are each small and individually verifiable by hand,
and several of them (FF-03, FF-14) change the very behaviour those tests would
assert. Building the guardrails once the behaviour is settled avoids writing each
test twice.

The exception is FF-29's protocol conformance test, which should be written
alongside FF-10 in Phase 0 while the spec divergence is fresh — it is the one
test whose absence directly allowed the bug.
