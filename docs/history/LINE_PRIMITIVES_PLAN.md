# FactoryForge — Line Primitives Plan

**Status:** done — LP-01 … LP-22, plus LP-12, which the work found rather than planned.
**Started:** 2026-09-15, against `52c4ff1`.

`COMPONENTS_AND_POLISH_PLAN.md` took the library from fifteen parts to
twenty-four and made the scene look like machinery. What is left is a different
gap, and it is easier to see by asking what a student *cannot build* than by
counting parts.

Twenty-four parts, and a line built from them can only ever do one thing at a
time. There is no way to **hold** a carton while the belt keeps running, so
nothing can accumulate and nothing can be released one at a time — the single
most common job on a real conveyor. There is no way to **turn** a product, so
every line is a straight line. There is no way to know **how far** anything has
travelled, so every timing decision has to be a timer, which is the habit a
conveyor course exists to break. The thermal plant can only heat, so
split-range control cannot be taught. And the only safety input that shapes a
program is the guard door; the other one every operator meets — the two-hand
control — is missing entirely.

Five parts, then, chosen the same way the last nine were: each one is a
primitive the library cannot express rather than a variation on something it
already has.

Every part here must satisfy the same five contracts, enforced in the same
places:

| Contract | Where it is enforced |
|---|---|
| Origin sits on the work plane (`PartLayout.WorkPlaneY`) | `PartLayout`, and by eye in the editor |
| Instance id is a **tag prefix**, never a whole tag name | `SceneEditor._PhysicsProcess` dispatch |
| Settings live in `PartProperties`, or they are lost on save | `--self-test=scene` |
| Registration and dispatch agree on the suffix list | `PlacedPart.TagSuffixesByType` + `PartTagManager` |
| A part with a `.fault` tag can be failed and disobeys while failed | `--self-test=fault` |

---

## Phase 1 — Five new parts

### LP-01 · Blade Stop (`StopGate`)

**Teaches:** accumulation — holding product on a *running* belt, and choosing
when to let it go. Every other actuator in the library takes product off the
line; this is the one that keeps it on.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.raise` | Bit | Output | Lift the blade into the lane |
| `.up` | Bit | Input | Blade at the stop position |
| `.down` | Bit | Input | Blade clear of the lane |
| `.fault` | Bit | Input | Seized: the blade freezes at whatever height it had |

The blade is an `AnimatableBody3D` that rises through the deck line, so a
carton driven into it genuinely stops and the belt genuinely scuffs underneath
— the queue behind it builds up because the physics says so, not because a
script paused anything.

**Model:** a compact pneumatic body slung under the deck, a chromed rod and a
blade paddle that spans the lane, plus the standard fault beacon on a stalk.

### LP-02 · Transfer Turntable (`TurnTable`)

**Teaches:** a rotary index — direction change with two limit switches and a
motion that must finish before the next one starts.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.index` | Bit | Output | Turn to the index angle; drop it to return home |
| `.athome` | Bit | Input | Deck square with the infeed |
| `.atindex` | Bit | Input | Deck square with the outfeed |
| `.fault` | Bit | Input | Seized: the deck stops at whatever angle it had |

The deck is a rotating `AnimatableBody3D` and the carton is carried by
**friction**, not by being parented to it — so a deck that spins too fast
throws its load, which is the real constraint on how fast a transfer can index.

**Model:** a disc deck on a pedestal, with a radial lead-in collar so a carton
arriving from a belt rides on rather than into the rim — the same fix
`ConveyorBelt.AddTransferRamps` makes at a belt junction, for the same reason.

### LP-03 · Measuring Wheel Encoder (`RotaryEncoder`)

**Teaches:** distance instead of time. Tracking product by counting pulses is
the thing a conveyor course exists to teach, and until now every timing
decision available here had to be a timer, which is the habit it exists to
break.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.count` | Int | Input | Pulses accumulated since the last reset |
| `.rate` | Float | Input | Pulses per second — belt speed, in the units a counter sees |
| `.reset` | Bit | Output | Zero the count while high |

The wheel reads the belt it is standing over: place it away from a conveyor and
it counts nothing and does not turn, because nothing is driving it. That is the
honest failure and it is visible — the wheel is the indicator.

**Model:** a side post with a sprung arm and a knurled wheel riding the belt
surface, turning at true surface speed.

### LP-04 · Cooling Fan (`CoolingFan`)

**Teaches:** split-range control. The heating station can only heat, so an
overshoot can only be waited out. Give the loop an actuator in the other
direction and the controller has to decide *which* one to use, and never both.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.run` | Bit | Output | Fan enable |
| `.speed` | Float | Output | Fan speed reference, 0–100 % |
| `.airflow` | Float | Input | Actual airflow after the ramp, 0–100 % |
| `.fault` | Bit | Input | Motor failed: the fan coasts to a stop and airflow falls to zero |

The fan finds `HeatingStation`s within `Reach` and adds to their loss term, so
the cooling is applied to the same first-order equation the heater drives —
one plant, two actuators, which is what makes the split-range interesting.

**Model:** a ducted axial fan on a stand, with a blade that spins at the
airflow it is actually delivering and a guard grille in front of it.

### LP-05 · Two-Hand Control (`TwoHandControl`)

**Teaches:** the permissive that the *safety relay* computes and the PLC only
reads — and, by doing so, what a tie-down defeats.

| Tag | Type | Kind | Meaning |
|---|---|---|---|
| `.left` | Bit | Input | Left palm button held |
| `.right` | Bit | Input | Right palm button held |
| `.valid` | Bit | Input | Both held, and pressed within `SyncWindow` of each other |

`valid` is not `left AND right`. Two buttons pressed a second apart is exactly
what a taped-down button looks like, and the relay refuses it — so a program
that ANDs the two bits itself passes a test that the real permissive fails.

A mouse has one pointer, so a click here means "a hand is on this button" and
holds it for `HoldTime` rather than for as long as a button is down. The
simultaneity rule is unchanged, which is the part that matters.

**Model:** a pedestal with two yellow palm buttons under shrouds, far enough
apart that one hand cannot reach both, and a green permissive lamp between them.

---

## Phase 2 — Menus

- **LP-10** A **SAFETY** palette group. The guard door is filed under OPERATOR
  next to the stack light, which is where a newcomer will not look for it, and
  the two-hand control belongs beside it rather than anywhere else.
- **LP-11** The palette's groups get an order that follows a line rather than
  the order parts happened to be written in.

## Phase 3 — Templates, tests, docs

- **LP-20** An **accumulation template** — cartons piling up behind a blade
  stop on a belt that never stops, released a batch at a time — with its
  engine-side demo profile and a `tools/try_scene.py` exercise. The release
  window is a number of **encoder pulses**, so it is a distance rather than a
  duration and stays the same size when the drive reference changes.
- **LP-21** `--self-test=lineparts`, asserting each new part by **effect**.
- **LP-22** README part table, `PART_AUTHORING.md`, `TEST_PLAN.md`.

---

## Appendix A — work item index

| Item | Title | Status |
|---|---|---|
| LP-01 | Blade stop | **done** |
| LP-02 | Transfer turntable | **done** |
| LP-03 | Measuring wheel encoder | **done** |
| LP-04 | Cooling fan | **done** |
| LP-05 | Two-hand control | **done** |
| LP-10 | SAFETY palette group | **done** |
| LP-11 | Palette group order | **done** |
| LP-12 | Run state cleared on reset | **done** (found, not planned) |
| LP-20 | Accumulation template | **done** |
| LP-21 | `--self-test=lineparts` | **done** |
| LP-22 | Docs | **done** |

## Progress log

- **2026-09-15** — plan written against `52c4ff1`. `dotnet build` green before
  any change.

- **2026-09-15, Phase 1** — all five parts landed and wired through every one of
  the eight touchpoints: catalog entry, placement factory, tag registration,
  per-tick dispatch, save/load capture, property-panel controls, Operate-mode
  click target, and (for the three with a `.fault` tag) the fault tool.
  `--self-test=scene` round-trips 29 types with every non-default setting
  compared after a re-save.

  Four findings worth recording:

  * **A carton that stops on a running belt could never start again.** This is
    the most valuable thing in the plan and it was not a stop-gate bug. A belt
    drives its load through `ConstantLinearVelocity`, which is a *surface*
    velocity: it acts through contact friction, and contact friction does
    nothing to a body the solver has already put to sleep. So a carton held
    stationary against anything — a blade stop, the queue in front of it, a
    seized diverter — slept after a second or two, and the belt underneath it
    could not wake it. Drop the blade and the carton stays exactly where it is,
    on a visibly running belt, for ever. `BoxPhysics.CanSleep = false` fixes it,
    and it belongs to the carton rather than to the stop because the same thing
    was true of every accumulation the library can express. It shipped that way
    and nothing caught it, because until LP-01 there was no part that could hold
    a carton on a belt long enough to find out.

  * **The blade's stroke is not a round number.** The first default left it
    standing exactly as proud of the belt as the shortest carton is tall, so it
    caught the top edge and tipped the carton over the blade instead of holding
    it. The stroke is now sized to the carton plus margin, and the self-test
    uses a *short* carton on purpose, because that is the demanding case.

  * **The turntable's rim is the belt-junction problem in the round.** A carton
    riding two centimetres low — the solver's contact slop, the same fact
    `ConveyorBelt.AddTransferRamps` exists for — meets the vertical rim of a
    deck head-on and wedges. The deck gets a radial lead-in collar of twelve
    wedges, static rather than part of the deck, because a ramp that turns under
    a carton is a carton being kicked.

  * **The fan offers cooling rather than applying it.** Parts are dispatched in
    placement order, so a fan placed before its station would land on one side
    of the integration and a fan placed after it on the other. `AddCooling`
    accumulates and `Step` consumes, so the plant behaves the same whichever
    order somebody happened to click — and two fans on one station cool it
    twice, which is what two fans do.

- **2026-09-15, LP-12** — found while writing the reset path for the new parts:
  **`Ctrl+R` did not clear process state**. The heat-treat scene's plate stayed
  at whatever temperature the last run reached, so a reset started the next run
  from a place no experiment could reproduce — on a plant whose entire point is
  a time constant measured from ambient. The guard door also stayed open. Reset
  now clears everything a *run* accumulates (temperature, airflow, guard,
  blade, deck, encoder count, two-hand holds) and leaves alone everything a
  person *built*, including the selector switch, which is a mode choice and not
  run state.

- **2026-09-15, Phase 2** — the palette has a **SAFETY** group. The guard door
  was filed under OPERATOR next to the stack light, which is not where anybody
  looks for a guard, and the two-hand control belongs beside it rather than
  anywhere else. Groups render in catalog order, so the palette now reads down a
  line: transport, sensors, actuators, process, operator, safety.

- **2026-09-15, Phase 3** — `--self-test=lineparts` (C27), an eighth template
  (`accumulation-buffer`) with its engine-side demo profile and a
  `tools/try_scene.py` exercise, and the docs.

  The template is the one place the three transport parts have to work together,
  and writing its exercise is what made the encoder worth having rather than
  merely present: **the release window is a number of pulses, not a number of
  seconds**, so the run performs the same release at 40 % and at 80 % of the
  drive and compares. Six cartons in 6.0 s against seven in 3.0 s — same amount
  of product, half the time. A controller written with a timer would have
  released twice as much the moment somebody turned the drive up, and that is
  the mistake the scene exists to make visible rather than to assert.

  Two smaller findings:

  * Accumulated product travels **touching**, so the photo-eye downstream of the
    blade sees one long blockage rather than five cartons and counts one. The
    exercise counts at the remover instead. That is not a workaround — a real
    eye cannot separate two cartons with no gap either, which is why a real line
    counts where the product has been singulated.

  * The seized-stop leg had to borrow the heat-treat exercise's `manual`
    override. A standing drive fault trips the station, correctly — and a
    controller that trips proves nothing about whether its own *output* could
    have told it anything. The leg holds the line running by hand, with the
    fault standing and `stop.raise` on, and watches product keep escaping past a
    blade the controller believes is up.
