# FactoryForge — Component Expansion & Presentation Plan

**Status:** done — CP-01 … CP-33, all four phases.
**Started:** 2026-08-30, against `a98d818`.
**Work items:** CP-01 … CP-33, indexed in [Appendix A](#appendix-a--work-item-index).

The library reached fifteen parts and almost every one of them is a *bit*: a
belt that turns or does not, a cylinder that is out or in, a lamp that is lit or
dark. The two analog parts — the level tank and the light curtain — were added
last and are the only reason a student can write a PID here at all. Meanwhile
the presentation, which is what a person judges in the first ten seconds, is a
set of boxes with flat materials on a grey plane, and the palette that offers
them is a flat list of buttons that never says what any part does.

This plan does three things:

1. **Nine new parts**, each chosen because it teaches something the existing
   fifteen cannot: an analog drive, a handling robot, a code reader, a thermal
   process, a swinging deflector, a needle instrument, an annunciator, a
   maintained selector and an interlocked guard.
2. **Make the 3D look and move like machinery** — driven pulleys that turn,
   cylinders that accelerate and decelerate, lamps that actually cast light,
   floor markings, and a camera that frames what you selected.
3. **Make the menus answer the question the user is actually asking** — what is
   this part, what does it do, and which key does the thing I want.

Every part added here must satisfy the five contracts the existing library
already keeps, or it is not finished:

| Contract | Where it is enforced |
|---|---|
| Origin sits on the work plane (`PartLayout.WorkPlaneY`) | `PartLayout`, and by eye in the editor |
| Instance id is a **tag prefix**, never a whole tag name | `SceneEditor._PhysicsProcess` dispatch |
| Settings live in `PartProperties`, or they are lost on save | `--self-test=scene` |
| Registration and dispatch agree on the suffix list | `PlacedPart.TagSuffixesByType` + `PartTagManager` |
| A part with a `.fault` tag can be failed and disobeys while failed | `--self-test=fault` |

---

## Phase 1 — Nine new parts

### CP-01 · Variable-Speed Drive Conveyor (`VariableConveyor`)

**Teaches:** analog output on a *drive*, and the difference between commanded
and actual speed. Every belt in the library is a bit; a real line is full of
VFDs, and "ramp the belt to 40 % and read back what it is doing" is the first
analog loop most students meet.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.run` | Bit | Output | Drive enable |
| `.speed` | Float | Output | Speed reference, 0–100 % |
| `.actual` | Float | Input | Actual speed after the ramp, 0–100 % |
| `.fault` | Bit | Input | Drive fault; the belt stops and `actual` falls to zero |

The drive ramps at `AccelRate` %/s, so `actual` lags `speed` the way a real one
does and a controller that assumes the reference is instantly true overshoots.
Extends `ConveyorBelt`, so speed, friction, rotation handling and the fault
beacon are inherited rather than re-implemented.

**Model:** the standard belt deck plus a gearmotor block and a wall-mounted
drive enclosure at the head end, carrying an emissive readout of the actual
percentage.

### CP-02 · Pivot Arm Diverter (`PivotDiverter`)

**Teaches:** an actuator that sweeps rather than strokes, and the two-limit
sequence around it. The pusher shoves a box off the side; a pivot arm deflects a
*moving* box across the lane without stopping it, which is what most real
sorters do.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.divert` | Bit | Output | Swing the arm out |
| `.diverted` | Bit | Input | Arm at the diverting angle |
| `.home` | Bit | Input | Arm parked clear of the lane |
| `.fault` | Bit | Input | Seized: the arm freezes at whatever angle it had |

**Model:** a pivot post with a steel blade on an `AnimatableBody3D`, swinging
0° → `DivertAngle` (45° by default) on the physics clock, so a carton it meets
is genuinely deflected rather than passed through.

### CP-03 · Pick-and-Place Gantry (`PickPlaceArm`)

**Teaches:** coordinated motion — an analog axis, a vertical stroke and a
gripper that must be sequenced against each other, with interlocks that matter
(lowering while travelling drops the load).

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.target` | Float | Output | Carriage target, 0–100 % of the rail |
| `.lower` | Bit | Output | Extend the Z column |
| `.grip` | Bit | Output | Energise the vacuum cup |
| `.position` | Float | Input | Actual carriage position, 0–100 % |
| `.inposition` | Bit | Input | Within tolerance of target |
| `.lowered` / `.raised` | Bit | Input | Z limit switches |
| `.holding` | Bit | Input | Vacuum has an item |
| `.fault` | Bit | Input | Seized: the axis and column stop where they are |

The gripper genuinely picks up a `BoxPhysics`: the body is frozen and carried by
the cup, then released back into free physics — dropped, with whatever velocity
the carriage had — when the vacuum drops. Grabbing nothing leaves `.holding`
false, so a program that never checks it runs a whole cycle carrying air.

**Model:** two floor columns, a horizontal rail, a travelling carriage, a
telescoping Z column and a suction cup.

### CP-04 · Barcode Scanner (`BarcodeScanner`)

**Teaches:** an integer measurement arriving as a one-scan pulse, which is the
shape of nearly every real identification device, and the latch a program needs
in order to keep it.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.enable` | Bit | Output | Arm the scanner |
| `.code` | Int | Input | Code of the item under the head |
| `.read` | Bit | Input | One-scan pulse when a new item is read |
| `.present` | Bit | Input | Item in the read window |

Codes come from what the item actually is — `101` short carton, `102` tall
carton, `201` metal — so the scanner discriminates for a reason rather than
returning a number nobody can predict.

**Model:** an overhead camera housing on a bracket, with a red fan of scan light
that sweeps while enabled.

### CP-05 · Analog Gauge (`AnalogGauge`)

**Teaches:** nothing by itself — it is an instrument. It exists because the
library could display an integer count and could not display a float, so a tank
level, a drive speed or a temperature had nowhere to be read in the 3D scene.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.value` | Float | Output | Displayed reading, in the gauge's own units |

The needle sweeps 240° across a graduated plate between `ScaleMin` and
`ScaleMax`, with a red band above `AlarmAt` and a digital sub-readout under the
pivot.

### CP-06 · Alarm Beacon (`AlarmBeacon`)

**Teaches:** annunciation, and that a lamp is a load like any other output.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.beacon` | Bit | Output | Rotating beacon on |
| `.horn` | Bit | Output | Sounder on |

The beacon rotates a real `SpotLight3D` inside a frosted dome, so it sweeps
light across the parts near it rather than merely changing its own colour. The
horn pulses a visible diaphragm and lights its own ring — audio is a
`--headless`-hostile dependency this project does not otherwise carry, so the
horn is shown, not sounded.

### CP-07 · Heating Station (`HeatingStation`)

**Teaches:** a first-order lag with a disturbance — the classic PID plant, and a
better one than the tank because it has *thermal inertia* and an ambient loss
that fights you in only one direction.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.heater` | Float | Output | Heater power, 0–100 % |
| `.temperature` | Float | Input | Process temperature, °C |
| `.attemp` | Bit | Input | Within `Tolerance` of `TargetTemp` |
| `.fault` | Bit | Input | Element failed: power reads on, temperature falls |

`dT/dt = (P·k_heat − (T − T_ambient)·k_loss) / mass`, so the plant has a real
time constant, a real steady-state offset under pure P control, and an
asymmetric response — you can heat as fast as the element allows and can only
cool at the rate the room takes it away.

**Model:** a heated plate on a pedestal whose emission ramps from black through
deep red to orange with temperature, under a vented hood.

### CP-08 · Guard Door (`SafetyGate`)

**Teaches:** the guard interlock, which is the second safety input every student
meets and the first one that shapes a program rather than merely stopping it.
The line may not run with the gate open; the gate may not open while the line is
dangerous; and those two rules are enforced by *different things*.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.closed` | Bit | Input | Guard switch — **true while the door is shut** |
| `.lock` | Bit | Output | Solenoid lock; while energised the door will not open |
| `.locked` | Bit | Input | Lock energised *and* the door actually shut |

`closed` is wired the way a real guard switch is, normally closed, so a broken
circuit reads as an open guard and stops the line — exactly like the mushroom
beside it. The solenoid is what makes this more than a second E-stop: the
controller decides whether the door *may* be opened, and until it releases the
lock, pulling the handle does nothing. The refusal is silent on purpose, because
a real locked gate simply does not move.

**Model:** a yellow frame with a tinted polycarbonate door that slides on a
rail, a solenoid body on the post with a lamp that says whether it is energised,
and a pane that reddens as it opens so an open guard reads from across the scene.

### CP-09 · Selector Switch (`SelectorSwitch`)

**Teaches:** the third kind of operator input. Start and Stop pulse for one scan;
the mushroom latches until Reset; a selector *stays where it is put*, and the
controller reads a **position** rather than an edge. Auto/Manual programs are
built around exactly this and the library had no way to express it.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.position` | Int | Input | Which detent, 0-based |

An Int and not a set of mutually exclusive bits, because that is what the
hardware is: one switch in one position at a time. Three bits would invite a
program that handles two of them being true, a state the switch cannot produce.

**Model:** a panel-mounted rotary on a pedestal, with one tick per detent on the
escutcheon, a white flag on the knob (a plain cylinder looks identical at every
angle — the lesson the roller deck already taught), and the position's label
printed under the plate.

---

## Phase 2 — Make it look and move like machinery

- **CP-10** Conveyor head and tail **drums** that rotate at true surface speed,
  plus a gearmotor at the drive end. A belt whose only motion is a scrolling
  texture reads as a moving picture of a belt.
- **CP-11** Pusher stroke **easing** — a pneumatic cylinder accelerates off the
  seal and decelerates into the cushion; a linear `MoveToward` reads as a prop
  being slid by hand.
- **CP-12** Stack light and beacon lamps cast **real light** (`OmniLight3D`) and
  the domes are frosted rather than flat cylinders.
- **CP-13** Sensors get a **status LED** on the head that lights on detection,
  which is how a real sensor is diagnosed.
- **CP-14** Shop-floor **markings**: safety-yellow lane striping around the
  build volume, so the floor reads as a factory and the build area has an edge
  visible without the grid on.
- **CP-15** Environment pass: warmer key light, a touch of glow so emissive
  lamps read as lit rather than merely bright.
- **CP-16** Camera: **F** frames the selection, damped orbit motion, preset
  views.

## Phase 3 — Menus and discoverability

- **CP-20** Palette **search box** and per-part **tooltips** naming the tags the
  part will register. New groups for the robotics and analog parts.
- **CP-21** A **keyboard help overlay** (`F12`) listing every binding, built
  from one table so it cannot drift from the handlers.
- **CP-22** Part **descriptions** in the property inspector header.

## Phase 4 — Templates, tests, docs

- **CP-30** A **pick-and-place template** and a **heating template**, so the two
  hardest new parts ship with something that already works.
- **CP-31** Every new type added to `SceneSelfTest.AllTypes`, and a new
  `--self-test=newparts` covering the dispatch of all seven.
- **CP-32** README part table and `docs/PART_AUTHORING.md` updated.
- **CP-33** Palette, registration, dispatch and save/load kept in agreement by
  a test that walks the palette rather than a hand-written list.

---

## Appendix A — work item index

| Item | Title | Status |
|---|---|---|
| CP-01 | Variable-speed drive conveyor | **done** |
| CP-02 | Pivot arm diverter | **done** |
| CP-03 | Pick-and-place gantry | **done** |
| CP-04 | Barcode scanner | **done** |
| CP-05 | Analog gauge | **done** |
| CP-06 | Alarm beacon | **done** |
| CP-07 | Heating station | **done** |
| CP-08 | Guard door with a solenoid interlock | **done** |
| CP-09 | Maintained selector switch | **done** |
| CP-10 | Conveyor drums and gearmotor | **done** |
| CP-11 | Pusher stroke easing | **done** |
| CP-12 | Lamps cast real light | **done** |
| CP-13 | Sensor status LED | **done** |
| CP-14 | Shop-floor markings | **done** |
| CP-15 | Environment pass | **done** |
| CP-16 | Camera framing and damping | **done** |
| CP-20 | Palette search and tooltips | **done** |
| CP-21 | Keyboard help overlay | **done** |
| CP-22 | Part descriptions in the inspector | **done** |
| CP-30 | Templates for the new parts | **done** |
| CP-31 | Self-test coverage for the new parts | **done** |
| CP-32 | Docs | **done** |
| CP-33 | Palette/registration agreement test | **done** |

## Progress log

- **2026-08-30** — plan written against `a98d818`. `dotnet build` green before
  any change.

- **2026-08-30, Phase 1** — all seven parts landed and wired end to end:
  palette entry, placement factory, tag registration, per-tick dispatch,
  save/load capture, property-panel controls, Operate-mode click target, and
  (where the part has a `.fault` tag) the fault tool. Verified by
  `--self-test=scene` (22 types round-trip, every non-default setting compared
  after a re-save) and a new `--self-test=newparts`, which asserts *effects*
  rather than existence: the drive's actual speed lags its reference on the
  first tick and reaches it later; the diverter reports neither limit
  mid-sweep; a seized diverter freezes at the angle it had; the gantry travels
  to a commanded position and refuses to claim a hold it does not have; the
  scanner's read pulse does not repeat for the same carton; the gauge's needle
  really moves; the heater has a measurable time constant, and a failed element
  cools while its command still reads 100 %.

  Three findings worth recording:

  * A `VariableConveyor`'s `Speed` is **computed by the drive every tick**, so
    capturing it as a saved setting stores a sample and restores it as
    configuration. `PartProperties` now skips it for that subclass and the
    self-test asserts its absence.
  * `PickPlaceArm` first stood on two posts **on the lane centre line**, which
    made it unusable over any conveyor. It is a portal now: two legs per end,
    straddling the belt.
  * `PickPlaceArm.Position` would have hidden `Node3D.Position`. Renamed to
    `AxisPosition`.

- **2026-08-30, Phase 2** — drums on the conveyors that turn at true surface
  speed (and are hidden on the roller deck, which has none); a cushioned
  pusher stroke; stack-light lamps that cast real light; a status LED on every
  sensor head; muted safety-yellow floor markings around the build volume;
  glow in the environment so the emissive parts read as lit; and a damped
  orbit camera with proportional zoom, `F` to frame the selection, and an
  automatic frame whenever a scene is opened.

  The framing was the largest single user-visible fix in this phase: opening a
  template used to leave the camera at its default pose, which put a control
  panel across the whole frame with the line behind it — every template opened
  looking like the same close-up of a panel.

- **2026-08-30, Phase 3** — the palette is built from `PartCatalog` rather than
  a hand-written list, with a search box over labels, groups, summaries and tag
  suffixes, and a tooltip on every button naming the tags that part will
  register. The property inspector shows the same summary under the part's
  name. `KeyBindings` is now one table read by both the start screen's footer
  and a new `F12` overlay (also on the toolbar's `?`), which fixed a drift
  already present: `F` framed the selection and appeared in no key list.

- **2026-08-30, Phase 4** — two new templates (`pick-and-place-cell`,
  `heat-treat-station`), each with an engine-side demo profile and a
  `tools/try_scene.py` exercise. All seven scene exercises pass.

  The pick-and-place exercise found a real controller bug that would have
  shipped in both the demo profile and the driver: **`gantry.inposition`
  compares the axis to the target the machine currently holds**, and a target
  written this scan does not reach the machine until the next tick — so on the
  scan that issues a move, `inposition` still reports "arrived", at the place
  you are trying to leave. Both sequencers released every carton straight back
  onto the pick station, having never travelled. Both now check the position
  feedback against the destination the step wants. The comment explaining it is
  in both files, because it is the kind of mistake a student will make.

  Two smaller findings:

  * The shared 200 ms E-stop contract is measured against `infeed.run`, not
    against the belt's last revolution. A VFD asked to stop **ramps down**, and
    that is not a bug to hide — it is why a real E-stop circuit removes power or
    uses safe torque off. The coast-down is asserted separately, on its own Stop
    press, and reported in the RESULT line.
  * An integral term capped at ~11 % of the element cannot close an offset that
    needs a ~53 % standing output. The first tuning of the heat-treat controller
    had exactly that and read as "integral action does not work". Both the demo
    profile and the exercise now use gains that can actually supply the standing
    output, and the exercise *measures* the P-only offset (14.2 °C) and then
    that PI closes it (−0.0 °C) rather than asserting it.

  Verification at this point: `python -m pytest -q` — 73 passed. Headless
  self-tests scene, newparts, buttons, io, templates, layout, roller,
  lightcurtain, tank, startstop, operate, proppanel, partsettings, modes,
  tryscene, scenes, fault — all PASS. All seven `tools/try_scene.py` scenes
  PASS.

- **2026-08-30, two more parts (CP-08, CP-09)** — a **guard door** with a
  solenoid interlock and a **maintained selector switch**, taking the library to
  twenty-four. Both were missing primitives rather than variations on what was
  already there: the library had one safety input (the mushroom) and no
  operator input that holds a position.

  `--self-test=newparts` grew two sections for them, and both assert the thing
  that makes the part worth having: that a locked guard *refuses* the handle,
  and that a selector still reads the same many ticks after nobody touched it.

  One finding: `PartProperties.Apply` runs **before** the node enters the tree,
  and `SelectorSwitch._Ready` was assigning its default detent unconditionally —
  so a saved position was restored and then immediately overwritten. The field
  now starts at −1 meaning "nobody has said", and `_Ready` only defaults when it
  is still unset. The save/load self-test caught it, which is exactly the class
  of silent revert that test was written for.

- **2026-08-30, the belt-junction jam** — the full test plan failed H6 with
  `placed=0`, and the pick-and-place cell then reproduced it on roughly half of
  all runs. It was not the template.

  A rigid body resting on a static deck settles a centimetre or two *inside*
  it — the solver's contact slop, which is normal and cannot be tuned away. A
  carton riding 2 cm low meets the **vertical end face of the next conveyor's
  collider** head-on, wedges, and stops the queue behind it while the drive
  still reports 70 %. Boxes were logged stationary at x=1.90 on a deck that ends
  at x=2.00, velocity 0.00, for the rest of the run.

  Two conveyors in a line is the most obvious thing anybody builds from the
  palette, so the fix belongs to the part, not to the scene:
  `ConveyorBelt.AddTransferRamps` gives every deck a shallow collision wedge at
  each end, sloping from below deck level up to it. A carton arriving low rides
  up it; one at the correct height never touches it. Both ends get one, because
  a belt can be rotated or reversed in the editor.

  Three plausible fixes were tried first and are recorded because each looks
  right: leaving a gap between the decks (a carton drops into it), overlapping
  them (the entry face still blocks, because at first contact the horizontal
  overlap is smaller than the vertical one and the solver pushes *back*), and
  holding the upstream drive while a carton is indexed — which makes it strictly
  worse, since a carton stopped **on** the joint can never restart across it.

  After the fix, six consecutive runs placed 6–7 cartons each, against 0–1
  before. This is the most valuable single change in the plan: it was costing
  every user who put two belts in a row, and it presented as a scene bug.

  Also found along the way: **`DemoDriver` does not exist in headless runs** —
  `Main` builds it inside `BuildView`, which only runs with a display. Several
  minutes went into "the demo profile never picks" before noticing that
  `--headless --demo` drives nothing at all. Worth knowing before debugging a
  profile that way again.

- **2026-08-30, the checkweigher's missing spacing interlock** — with the belt
  jam fixed, the *pre-existing* roller-line exercise started failing about one
  run in six, on the assertion that the checkweigher and the inductive sensor
  flag the same cartons: "2 over limit, 3 metal".

  Two cartons occasionally shared the weigh deck. They read as **one** peak, so
  the run counted fewer cartons than it fed and one metal carton's reject merged
  into its neighbour's. The assertion was sound; the scene was feeding faster
  than a checkweigher can weigh.

  A real checkweigher line controls spacing, and this one had none — so the
  controller now provides it: hold the feed while the scale is loaded. Both the
  exercise and `RollerLineWeighingProfile` carry the same interlock, as the
  project requires the two to mirror each other. Eight consecutive runs
  afterwards produced *identical* counts (12 weighed, 6 rejected, 4 metal, 12
  out) rather than merely passing more often.

  Worth stating plainly: this was latent before this plan, not caused by it.
  What the plan changed was the timing enough to expose it — which is the useful
  half of having exercises that drive real physics.

- **2026-08-30, final verification** — `python tools/test_plan.py`:
  **55 passed, 0 failed, 2 skipped, 781s**. The two skips are the
  display-dependent checks; both were run separately and pass. That covers
  `pytest` (73), all 26 headless self-tests, determinism, the engine/sidecar
  seam, robustness, and all seven scene exercises driven end to end. Recorded in
  `docs/TEST_PLAN.md`.
