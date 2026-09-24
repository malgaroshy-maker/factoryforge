# Grading a PLC program against a scene

*All ten shipped scenes, graded end to end, headless, on Python models of the
plants rather than on the 3D engine. Verified on 2026-09-21: every scene's
`good` controller passes and every scene's deliberately wrong one fails, each
for that scene's own lesson. The six scenes IP-14 added on 2026-09-24 were
verified the same way, in lockstep at seeds 11 and 5, and so was `pivot-divert`
(IP-32), whose controllers were also run against the 3-D engine and agreed with
the model carton for carton. **Nobody has graded a real student's program with
any of them.** Those are two different claims and this file will say so until
the second one is true.*

Marking a PLC exercise by hand means watching a line run and forming an
opinion. It does not scale past a small class, it is not reproducible, and two
markers will not agree. `tools/grade.py` runs the exercise unattended and
returns a verdict, an exit code, and the evidence behind both.

<!-- from-source -->
From a source checkout (from the download, `.\factoryforge-sidecar grade` takes the same flags; see [Grading from the download](#grading-from-the-download)):

```bash
python tools/grade.py --scene sorting-by-height --student a.patel --json marks/a.patel.json
```
<!-- /from-source -->

Factory I/O has no answer to this at all, which is the reason it is worth
building properly rather than quickly.

---

## How it fits together

```
grade.py ── tag bus ── factoryforge-sidecar connect ── OPC UA / S7 / MQTT ── the PLC
(the exam)               (the student's own driver)                    (their program)
```

The grader *is* the engine. It owns the scene, opens a tag bus, and waits. The
student connects their own sidecar to it exactly as they would to the 3D
engine, with whatever driver reaches their controller. Nothing in the grader
drives the scene and nothing in it reaches into the controller.

That is the difference between this and `tools/try_scene.py`, which is the
closest thing that existed before. `try_scene.py` drives a scene the way a PLC
would, to prove the *scene* works; it spawns its own engine and supplies its
own control logic, so there is no seat in it for somebody else's program. The
grader is the same idea with the two roles swapped.

The tag bus serves **one** sidecar at a time, which is why the grader has to be
the engine rather than a second client. There is no seat for an observer.

### Running one

<!-- from-source -->
From a source checkout,

```bash
python tools/grade.py --scene sorting-by-height
```

prints the line the student needs:

```
  tag bus   ws://127.0.0.1:61812/tagbus
  exam seed 1288431   window 60s

  Connect your controller with:
    python -m factoryforge_sidecar connect --driver <yours> --port 61812 -o <options>
```
<!-- /from-source -->

From the release download, `.\factoryforge-sidecar grade --scene sorting-by-height`
prints the same, with the line reading `.\factoryforge-sidecar connect ...` --
`./factoryforge-sidecar connect ...` on Linux -- because there is no Python
there to run `-m` with, and neither PowerShell nor a Linux shell runs a program
from the current folder without the path (IP-36).

The port is chosen at runtime. It is not 7411, it is not configurable to a
default, and that is deliberate: a fixed port means two graded runs cannot
share a machine, which HP-53 removed project-wide for exactly that reason.
`--bus-port` takes a fixed one if a marking rig needs to publish it in advance;
7500–7510 is the range reserved for it.

| flag | |
|---|---|
| `--duration` | seconds to watch once the controller connects (default: the scene's own, 60–80). **Shortening it can fail a correct program**: the exam's second phase starts at a fixed moment, and a window that ends before a plant has settled in it marks a ramp as a hold |
| `--wait` | seconds to wait for a controller before giving up (default 120) |
| `--seed` | the feed pattern and the numbers the exam picks. Reported either way, so a mark is reproducible |
| `--json PATH` | the whole run, machine-readable; `-` for stdout |
| `--student` | a name for the report |
| `--quiet` | the `RESULT` line and nothing else |
| `--reference` | grade a built-in controller instead of waiting — see below |
| `--lockstep` | with `--reference` only: step the plant and the built-in controller together on the plant's clock — see below |

A student starting from scratch can use the starter for the scene
(`examples/openplc/<scene>/` or `examples/tia/<scene>/`). The grader offers
exactly the tags the engine registers, so a starter's Modbus addresses and OPC
UA mapping work unchanged against `tools/grade.py`: add `--port <the port it
prints>` to the same `factoryforge-sidecar connect` command.

### Grading from the download

The release zip carries the grader inside `factoryforge-sidecar`, so marking
needs neither Python nor a checkout. From the folder the zip extracts to:

    .\factoryforge-sidecar grade --list
    .\factoryforge-sidecar grade --scene sorting-by-height --student a.patel --json a.patel.json

(`./factoryforge-sidecar` on Linux.) It is the same grader as
<!-- from-source -->`python tools/grade.py` in a source checkout<!-- /from-source -->: the same flags, the same report, the same JSON and the
same exit codes (0 PASS, 1 FAIL, 2 ERROR, 3 DISQUALIFIED), because both run
the one `factoryforge_sidecar.grading`. The student connects exactly as before,
with their own `factoryforge-sidecar connect --driver … --port <the port it
prints>`.

To check the grader itself on a machine you have just unpacked it on:

    .\factoryforge-sidecar grade --scene sorting-by-height --reference good --lockstep
    .\factoryforge-sidecar grade --scene sorting-by-height --reference blind --lockstep

The first must PASS (exit 0) and the second FAIL (exit 1). The release gate runs
exactly this against every build before it ships.

Each scene's plant is read from the engine's templates, and the release keeps a
copy of them inside `factoryforge-sidecar`, taken from the same build as the
engine. To mark against a template you have changed, point
`FACTORYFORGE_TEMPLATES` at a directory holding `manifest.json` and the
templates. When it is set, it is the only place the grader looks.

### When the window opens

The plant does not move until a controller is there to drive it. The window
opens, and the plant starts, when a controller has connected **and** has been
sent the scene's tag list (`describe`), which is the end of the tag-bus
handshake. Until then the plant is frozen at time 0, however long the student
takes to type the connect command. `--wait` still limits that: if nobody
connects in time, the result is `ERROR` (exit 2).

This was not always true. Until IP-25 the plant started with the grader, and
the window was measured from then. In IP-06's experiment, a controller that
connected 8 s into a 20 s batch-dosing window was graded on 12 s. It missed
the examiner pressing Start at 1 s and scored 0.0 L on its first batch. The
report said nothing about why. The same run now scores 22.1 L and passes.

The window then waits for one more thing: the student's sidecar saying its
driver has reached the PLC (the tag bus's `controller` message, IP-30).
`factoryforge_sidecar connect` starts its driver after the describe, and an
OPC UA or S7 driver can take seconds to connect. In eight of the ten scenes the
examiner presses Start 1.0 s in, for 0.2 s (`Panel.PRESS`), so a window that
opened on the describe could be over the first Start before the PLC could see
it. "Ready" means the PLC can see the tags. A client driver (OPC UA client, S7,
PLCSIM) is ready once it is connected and has bound the tags. A server driver
(Modbus TCP, OPC UA server) is ready once the student's PLC or client has made
its first request. MQTT is ready once the broker has its subscriptions and the
retained inputs.

A controller that never sends that message, such as a hand-written client or a
sidecar from before IP-30, is still graded. After `--ready-wait` seconds
(default 30) the window opens on the describe instead. 30 s covers an OPC UA
driver's failed first attempt and a slow second one (10 s timeout, 5 s retry
delay, 10 s more). The plant is frozen until the window opens, so the wait
costs time and never any of the window.

The report says which event opened the window. The JSON has
`evidence.window` with `opened_on` (`"controller ready"`, or `"controller
described; no ready within 30s"`), `controller_described_at`,
`controller_ready_at`, `driver`, `ready_wait` and `opened_at`, all in wall
seconds since the grader started listening. It also has
`plant_seconds_before`, which is always 0. Each entry in `evidence.sessions`
keeps every readiness report it made. The printed report has a line under the
verdict:

```
  The controller connected 8.1s after the grader started listening, and its
  driver (opcua-client) reported ready at 11.3s; the plant and the 24s window
  started then.
```

If the window opened without the driver, the feedback says so. It also says
whether the sidecar never reported or was still reporting "not ready". A
driver that became ready only after the window opened is reported with how far
into the window that was, since the PLC saw nothing before then. So is a
driver that lost its PLC during the run. Lockstep runs are unchanged, because
their built-in controller is attached before the plant takes a step.

For a check without a PLC: `connect --driver mock -o connect_delay 3` stands
in for a driver that takes 3 s to connect.

Why the describe is the fallback and not some other moment: the protocol ends with it,
because a sidecar does not acknowledge a describe, so it is the latest moment
the grader can know a controller is present. Waiting for the controller's
first write instead would let a program choose when its own exam starts. It
would also never start the exam for a program that writes nothing, and that
program still has to be graded, as a FAIL.

The window opens only once. A controller that drops and reconnects finds the
plant still running, as a real line would be, and fails
`controller.stayed_connected`. Pausing the plant would let a program stop the
exam's clock by hanging up.

### Exit codes

| | | |
|---|---|---|
| **0** | `PASS` | every check met |
| **1** | `FAIL` | the program ran and got it wrong |
| **2** | `ERROR` | nothing to grade — nobody connected, or the run broke |
| **3** | `DISQUALIFIED` | tags were forced |

A marking script wants all four. "Their sidecar never started" is not the same
as "their program is wrong", and neither is the same as "they forced the
counters". Collapsing those into pass/fail loses the only information that
tells you what to do next.

---

## What it actually checks

The verdict comes from the cartons, not from the tags.

The scene knows how tall every carton it made was and which lane it ended in,
and there is no message on the tag bus that reaches any of that. Everything a
controller *can* touch — the counters, the sensor values, the actuator commands
— is recorded as **evidence**, because a student who fails needs it, but none
of it is the criterion. A criterion you can reach is a criterion you can fake.

Every check on every scene is written down twice: once as a check id in that
scene's own file under `grading/scenes/`, and once here, as the plant fact it
reads and the fake it shuts.

| scene | the fact that decides the mark | how a program would fake it, and what stops that |
|---|---|---|
| `sorting-by-height` | which lane each carton ended in, against the height the scene gave it; millimetres of belt that moved before Start, after the mushroom, and between its release and Reset-then-Start | pushing every second carton — the feed is shuffled pairs |
| `start-stop-station` | cartons that broke the eye between the Start press and the line stopping itself; millimetres of belt that moved while tripped, which lasts until Reset and then Start | running for about the right length of time — the batch size is drawn from the seed and set twice |
| `tank-level-control` | the level trace: settled error, ripple and overshoot | "it reached the setpoint", true of float switches and of a valve slammed open — ripple and overshoot grade those, and the pot moves to a second level |
| `light-curtain-sorting` | each carton's measured height and the lane it ended in, against the rule in force when it was measured | a threshold written into the program — the pot is set twice, and the feed is eight shuffled heights rather than two |
| `roller-line-weighing` | each carton's true mass, whether it was weighed alone, and whether the program flagged it | rejecting on the inductive sensor — the limit moves below a tall cardboard carton, where metal and heavy stop agreeing |
| `pick-and-place-cell` | which cartons the gantry carried to the outfeed, and the rail position it let go of the others at | a sequence on timers — the run slows the axis down, and a timed release drops cartons at half rail |
| `accumulation-buffer` | cartons that physically passed the blade, per release | a release timed in seconds — the run halves the drive's top speed |
| `heat-treat-station` | the temperature trace: settled error, ripple and overshoot | proportional-only parks short by an offset the plant's own numbers predict; a thermostat reaches setpoint and swings 9 °C |
| `guarded-cell` | the tick the contactor pulled in, and whether anybody had pressed Start since it last stopped | nothing — this one catches an accident, not a shortcut. Also: every tag the program wrote, because `belt.rotate` is the motor's; and whether a program that locks the gate lets the operator in once the cell has stopped |
| `batch-dosing` | litres the pump physically moved, per batch | a dose timed in seconds — the run re-rates the pump between the two batches |
| `star-delta-start` | the motor's speed at the instant the delta contacts closed, and whether star and delta ever conducted at the same instant | a changeover on a timer — the run loads the machine between its two starts, so the star run-up takes about twice as long |
| `cooling-tunnel` | the temperature trace, the seconds the product took to reach a dropped setpoint, and the overlap of heater power and delivered airflow | holding every setpoint on the heater alone, or with the fan left running under it — the recipe drops 60 C or more, which the room alone takes 15 s and more to take away |
| `air-receiver` | the receiver's true pressure in bar, against the band the pot sets; and the seconds from commanding a seized valve open to the alarm | scaling that is nearly right — the run raises the consumption and moves the pot — and a discrepancy check with no timer, which the first, healthy start shows up |
| `press-station` | where the ram was each tick, against the selector's position and the two-hand relay's verdict at that tick | driving MANUAL on left AND right — the examiner ties one palm down and presses the other a second later — or an automatic cycle that runs whatever the selector says; the selector is turned to OFF and MAN mid-run |
| `rotary-index` | the deck's angle when the pusher's plate met each carton, and whether the deck turned while the plate was out over it | pushing on a timer calibrated at the deck's rated speed — the run slows the deck to 40–60 % of it |
| `pivot-divert` | which lane each carton ended in, against the height the scene gave it, and the blade's angle at the moment each carton reached it | holding the blade for as long as a carton took at the rated belt speed — the run slows the belt to 60 or 70 % of it; and diverting every second carton — the feed is shuffled |
| `servo-positioning` | where the carriage came to rest, and whether it moved between the drive's fault clearing and the operator's Reset | acknowledging every error the moment it can be — the run faults the drive mid-move, clears the fault, and presses Reset only three seconds later |

Every scene also carries `controller.stayed_connected`,
`integrity.no_forced_tags` and `integrity.no_input_writes`, and every scene has
at least one check whose only job is to refuse a verdict about a plant that did
nothing — `line.ran`, `plant.moved`, `dose.ran`, `cell.cycled`. A test that
passes while the simulation does nothing is not a test (AGENTS.md gotcha 16),
and a grader is a test with a student's mark attached to it.

`grade --list` prints the same set, with each scene's window and its
reference controllers.

### The exam changes the plant while the program is running

Six of the ten scenes are only gradeable because the run reaches in and changes
something physical that no tag reports:

* the **setpoint pot** moves, on the tank, the oven, the curtain and the scale
* the **drive's top speed** halves, on the accumulation buffer
* the **gantry's travel speed** drops to 0.4 of the template's, 80 %/s to
  32 %/s, on the pick and place cell
* the **pump's rating** halves, between the two batches of the dosing exercise
* the **gate** opens and shuts, on the guarded cell, with the operator taking
  the part out as they go in
* the **machine's load** rises from 30 % of rated torque to 85, 90 or 95 %
  (from the seed) between the two starts of the star-delta starter
* the **receiver's consumption** doubles, and the **isolation valve seizes**
  shut while the station is stopped, on the air receiver
* the **mode selector** is turned from AUTO to OFF mid-cycle and then to MAN,
  and the **two-hand station** is worked a tied-down palm at a time, on the
  press station
* the **turntable's index speed** drops to 40, 50 or 60 % (from the seed), on
  the rotary index station
* the **belt's speed** drops to 60 or 70 % (from the seed), on the pivot
  diverter line, so a turned carton takes longer to slide off the blade
* the **servo drive faults** mid-move, and the fault clears three seconds
  before anybody presses Reset, on the servo positioning scene

None of those is visible as a value on the bus. The controller can only find
out by measuring — the encoder counting slower, the flow meter reading less,
the axis taking longer to arrive — which is exactly the difference between a
program written on feedback and one written on a stopwatch. A rubric that never
moved anything would mark both the same.

The operator panel is pressed the way a click presses it in the engine: each
button is held closed for 0.2 s, and then stays open at least as long before
the next press on it can close it again (`Panel.PRESS`, which is
`ButtonPanel.DefaultPressHold`, IP-34). Until IP-34 the examiner held 0.15 s,
against a click the engine had since raised to 0.2 s (IP-31).

**The operator contract, as belt travel.** Three scenes mark what the panel's buttons do to a belt: the start / stop
station, the sorting line and the guarded cell (which marks it on its motor
contactor instead). The first two share one ledger, `plant.TripLedger`, ticked
with the metres the belt really moved. It splits a trip into the time the
mushroom is in and the time after its release until a Reset edge and then a
Start edge. It also records every time the belt began to move with no Start
edge since it last stopped.

The sorting line's examiner presses Start at 1 s. From 16 s it strikes the
mushroom, releases it 2 s later, presses Start alone at 3 s, Reset at 4.5 s
and Start at 6 s, all timed from the strike. The strike waits, for up to 4 s,
until the belt is running and no tall carton is between the high beam and the
pusher. The brief allows a push timed on a clock after the beam, and a strike
that stopped such a carton in front of the plate would fail a correct program
for a carton the E-stop stranded. Without the wait, `good` fails
`sort.tall_diverted` on 21 of seeds 1–40; with it, it passes all 40. Four
checks:

* `line.started_by_start`: the belt never began to move without a Start
  edge since it last stopped. It catches a line that runs from power-up.
* `estop.stopped_the_belt`: at most 200 ms of belt (100 mm) after the strike,
  on a belt that was running when it came.
* `estop.latched_until_reset`: no belt at all between the release and Reset
  followed by Start. It catches a restart on the release, on Start alone and
  on Reset alone.
* `estop.restarted_after_reset`: the belt runs again within 1 s of that Start.

A window too short for the whole sheet, about 27 s, fails the last two rather
than passing them unexamined. The start / stop station keeps its single
`estop.stopped_the_belt` over both phases. Until IP-35 its trip ended on any
Start after the strike, so a station that restarted on Start alone passed
with 5 mm of belt, all of it the stop lag. It now fails with 1745 mm.

Everything else the exam changes, it changes the way the engine would. The
gate is the case worth spelling out. Its leaves have solenoid locks
(`guard_a.lock`, `guard_b.lock`), and in the engine a leaf locked shut cannot be
opened. The brief never mentions them, so a program written to the brief has
its gate opened at 22 s while the cell runs. A program that does lock the gate
is answered the way an operator answers a locked guard: they press Stop and
keep trying the handle for 5 s. If the lock is released once the motor has
stopped, which is guard locking done properly, the gate opens and the rest of
the exam runs from that moment: the gate stopping the cell, Reset starting
nothing, and Start restarting it. A lock that never lets go fails
`cell.guard_released_for_access`, because a guard that stays locked on a stopped
cell keeps out the people who have to clear it. Until IP-29 the grader opened
the gate through the lock. The accumulation buffer's drive used to double
rather than halve, too. That was only gradeable because the model carried every
carton at the buffer's speed, including the ones already on the 0.5 m/s
outfeed. A buffer driven faster than the outfeed backs its releases up against
it, in the engine and now in the model, so a correct release comes out short
at the higher speed.

### The feed patterns are shuffled, and that is the point

A line that alternates tall, short, tall, short can be sorted perfectly by a
program that pushes every second carton and never reads a sensor. It would pass
a grader that fed a fixed pattern, and it would fail on any real line.

So the sorting line feeds sixteen `[tall, short]` pairs, each pair shuffled and
cycled: unguessable, and any eight consecutive cartons still hold at least
three of each height, so the per-lane minimums stay reachable however the
shuffle lands. The curtain draws from eight heights shuffled in blocks, and the
checkweigher from all four mass classes shuffled in blocks of four — the second
of those also guarantees that the carton the two instruments disagree about
actually turns up, so a metal-sensing program cannot pass on a lucky draw.

Those two feeds are the exam's, not the scene's, and so is the pivot diverter
line's, which deals tall and short cartons two of each, shuffled in blocks. The
emitters in `light-curtain-sorting`, `roller-line-weighing` and `pivot-divert`
alternate a short and a tall carton, and the checkweigher's makes every third
one steel. `everyother` would pass the first and the third, and the second need
not produce the carton the rubric is about. The pivot diverter line's
`everyother` shows it: run against the 3-D engine's own alternation it sorts
every carton, and against the exam's shuffle it fails. Every other scene's feed
is the engine's own: short, tall, short, tall, with every `metal_every`-th
steel, which is also what decides the pick and place cell's barcode (101, 102
or 201, read off the carton). `tests/test_grade_templates.py` lists these three
exceptions by name, and holds every position the models use to the template's.

The seed is chosen at random unless you give one, and it is in the report
either way, so a disputed mark can be re-run exactly.

### Forcing is refused, not ignored

A forced tag is a value that disagrees with the simulation on purpose. It is
the right tool for fault injection — that is what `tools/try_scene.py` uses it
for — and the wrong tool for a graded run. A grader that did not look for it
could be beaten by four lines of Node-RED: force `counter.tall`, force
`counter.short`, done.

Two things are watched, because either alone has a hole:

* every `force` message that arrives, recorded with the tags it named. This
  catches a force that is set and cleared between two ticks.
* every tick, for a tag the table reports as pinned. This catches a force set
  before the grader started counting.

Either one ends the run as `DISQUALIFIED` (exit 3) rather than `FAIL`, and no
sorting verdict is reached at all. An instructor wants to tell "got it wrong"
apart from "tried it on".

A write aimed at a simulator-owned tag is a separate, milder thing: the engine
already refuses those, and the sidecar's own `bus.write()` raises before one
can be sent, so seeing one means somebody wrote a raw bus client. It fails a
check and says why. It is not a disqualification, because it does not work.

---

## What a student gets back

A bare FAIL teaches nothing. Every run prints the checks with their numbers,
then the cartons that went the wrong way, then what the evidence says about
why:

```
FAIL — a.patel   2 of 8 checks failed
  [ok] line.ran                       14 cartons reached a lane (at least 8 needed)
  [XX] sort.tall_diverted             6 tall carton(s) ran off the far end: [1, 4, 5, 8, 10, 12]
  [XX] sort.short_passed              3 short carton(s) went down the chute: [2, 9, 11]

  chute     4  (1 tall, 3 short)
  far end  10  (4 short, 6 tall)
  pusher fired 6x, 1.76s after the beam (needs 0.60–1.20s)
  misrouted:
    carton   1 (tall) -> far-end at 6.1s
    carton   2 (short) -> chute at 6.6s

  - The pusher fired 1.76s after the high beam broke. On this line the carton
    is in front of the plate from 0.90s to 1.50s after the beam, and the plate
    itself takes 0.30s to come out -- so command it between 0.60s and 1.20s.
```

Those numbers are computed from `sorting_scene.py`'s own geometry — belt speed,
sensor position, pusher travel and catch width — rather than written into the
grader. A change to the line changes the advice instead of quietly making it
wrong, and there is a test that fails if anyone hard-codes them back.

Every scene does the same, in its own units, because a diagnosis is only worth
printing when it distinguishes the ways the exercise actually goes wrong. These
four come from the seed-5 lockstep runs of `ponly`, the buffer's `timed`,
`autostart` and `metalonly`, made with the command given above the table in
*Checking the grader itself*. A test checks that each excerpt is how a line
of its run's feedback begins:

```
  - At 200C it parked 15.7C off and stayed there. An error that stops closing
    is a controller with no way to produce output from a small error [...]

  - The belt ran at 0.49 m/s for the first half of this run and 0.25 m/s for
    the second -- 51% of the speed -- and your releases went from 6.0 cartons
    to 3.0. That is a release timed in seconds. `panel.setpoint` is a window
    in ENCODER PULSES, which is a distance [...]

  - The motor started at 28.16s with nobody having pressed Start since it last
    stopped. That is automatic restart, and it is the failure this whole cell
    exists to prevent: the relay closing hands `starter.coil` back to your
    program, it does not command it. [...]

  - Every carton you got wrong is one where the scale and the inductive sensor
    disagree -- 2 of them in this run. A tall cardboard carton weighs 2160 g
    and a short steel one 4320 g [...]
```

Several of those pick between explanations rather than reciting one. A run
where every misrouted carton was measured under a single threshold is told it
latched the setpoint; a run where they are spread across both is told how the
curtain's beam ladder works instead. A batch that delivered nothing on its
second run is told about the totaliser, not about the pump's rating.

`--json` writes all of it: the per-carton ledger with heights, lanes and the
second each one landed; the measurement trace for a regulator; every release,
batch, drop and force. That file is the appeal record.

---

## Checking the grader itself

A grader that fails everybody looks exactly like a cohort that cannot program.

Every scene carries a `good` that must pass and at least one controller that is
deliberately wrong about that scene's own lesson and must fail. Every one of
them has been watched failing — a rubric only ever seen to pass is a rubric
nobody knows the shape of (AGENTS.md gotcha 24).

<!-- from-source -->
From a source checkout (from the download, `.\factoryforge-sidecar grade` takes the same flags; see [Grading from the download](#grading-from-the-download)):

```bash
python tools/grade.py --scene <id> --reference good    # must PASS  (exit 0)
python tools/grade.py --scene <id> --reference idle    # does nothing -> FAIL
python tools/grade.py --scene <id> --reference forcer  # forces counters -> DISQUALIFIED
```
<!-- /from-source -->

`idle` and `forcer` are shared, because a controller that does nothing and one
that lies are wrong everywhere. The rest belong to their scene.

Every number in this table comes from one lockstep run with seed 5 and the
scene's own window:

<!-- from-source -->
From a source checkout (from the download, `.\factoryforge-sidecar grade` takes the same flags; see [Grading from the download](#grading-from-the-download)):

```bash
python tools/grade.py --scene <scene> --reference <controller> --lockstep --seed 5 --json out.json
```
<!-- /from-source -->

A lockstep run is reproducible from its seed (see below), so these are the
numbers that command prints, not a sample of them.
`test_the_wrong_controller_table_quotes_a_lockstep_run` in
`tests/test_grade.py` re-runs every row that has a number and fails if any
number here differs from its run's JSON. It also fails if a row gains a number
it does not measure. Until IP-27 the numbers were older than lockstep, came
from more than one seed, and were read by no check. For example, `runon` was
quoted as counting to thirteen against a pot of four. In lockstep with seed 11,
whose pot is four, it makes 11 in the tests' 45 s window and 21 in the
scene's own 60 s window. Neither is thirteen.

| scene | wrong controller | what it fails on |
|---|---|---|
| `sorting-by-height` | `blind` | pushes on a timer — misrouted cartons |
| | `greedy` | plate held out — short cartons in the chute |
| | `nostart` | runs whenever the mushroom is out: the belt starts at 0.01 s with nobody having pressed Start, and again at 18.21 s when the mushroom is released |
| | `startalone` | the Start pressed at 20.17 s with no Reset restarts the line: 1480 mm of belt while the trip was latched |
| `start-stop-station` | `noestop` | 3245 mm of belt through a struck mushroom and its latch, where 100 mm is the limit |
| | `startalone` | 1745 mm of belt after Start alone cleared the latch, where 100 mm is the limit |
| | `runon` | makes 21 cartons against a pot of 5 |
| `tank-level-control` | `bangbang` | a pair of float switches parks 5.7 % and then 5.5 % off |
| | `fixedsp` | holds 70 % while the pot says 22 |
| `light-curtain-sorting` | `fixed` | every misrouted carton was measured under one of the two thresholds |
| | `everyother` | diverts on a count, never reads the height |
| `roller-line-weighing` | `metalonly` | gets exactly the cartons the two instruments disagree about wrong |
| | `fastfeed` | two on the deck read as one peak |
| `pick-and-place-cell` | `timed` | places 6 at 80 %/s; slowed to 32 %/s, it drops 6 cartons at 51.5 % of the rail |
| `accumulation-buffer` | `timed` | 6.0 cartons a release becomes 3.0 when the drive slows down |
| `heat-treat-station` | `ponly` | parks 10.0 °C short of 135 °C and 15.7 °C short of 200 °C |
| | `thermostat` | mean error 2.1 °C and 2.0 °C, swinging 8.3 °C and 8.1 °C peak to peak |
| `guarded-cell` | `autostart` | the motor starts at 28.16 s, just after the Reset at 28.00 s, with no Start pressed |
| | `writesbelt` | writes `belt.rotate`, the motor's own tag |
| | `tapedmute` | holds the bridge 6.05 s against the scanner's 6 s limit, which withdraws it 2 times |
| | `lockedshut` | locks the gate and never releases it, so the operator cannot get in after Stop |
| `batch-dosing` | `timed` | 22.6 L and then 11.3 L against the same 22 L pot |
| | `noreset` | the second batch is over before it starts |
| `star-delta-start` | `samescan` | drops star and energises delta in one scan; the breaker trips at 3.78 s |
| | `timed` | changes over at 66.6 % speed and draws 98.6 A on the loaded machine, against a pot of 85 % |
| `servo-positioning` | `autoack` | acknowledges at 22.01 s, as the fault clears, and the carriage moves 369 mm before the Reset at 25.0 s |
| | `noack` | never acknowledges, so the first fault stops the axis for good |
| `cooling-tunnel` | `heatonly` | never runs the fan, so the drop from 170 C to 80 C takes 19.6 s where 10 s is allowed |
| | `fight` | leaves the fan at a floor under the heater: both on together for 70.6 s |
| `air-receiver` | `by32767` | scales by the biggest INT rather than the card's full scale, reads low, and holds the receiver at 7.14 bar against a pot of 6 |
| | `nodiscrepancy` | trusts the valve; a seized one is never noticed |
| | `impatient` | alarms on "commanded and not opened" with no timer, at 1.01 s, while a healthy valve is still travelling |
| `press-station` | `andhands` | moves the ram 250 mm on a tied-down palm, because left AND right read true while the relay's permissive did not |
| | `ignoresmode` | carries on cycling with the selector at OFF: 650 mm of down-stroke |
| `rotary-index` | `timed` | pushes 4 cartons off at 75 deg once the deck is slowed to 33 deg/s |
| | `notretracted` | turns the deck home on "not extended": 382 deg of turning with the plate out over the deck |
| `pivot-divert` | `timed` | holds the blade on a stopwatch set at the rated belt, and once the belt is slowed to 0.35 m/s lets 3 tall cartons go on to the far end |
| | `unlatched` | wires the blade to the eye, so it is home again before the tall carton gets there |
| | `late` | swings the blade out as the carton reaches the post, as a pusher is fired: every carton lands in its lane, and 2 of them because the blade hit them at 0.5 m/s |
| | `everyother` | turns every second carton and never reads the tall eye: right on the engine's alternating emitter, wrong on the exam's shuffle |

`guarded-cell` also has a second right answer, `guardlock`. It is `good` plus
guard locking done properly: locked while the contactor can run and released
once it has stopped. It has to pass, and it shows that a program that locks the
gate can sit the whole exam.

These are built-in controllers that connect over a real websocket through the
same `TagBusClient` the sidecar uses, so they cross the same seam a real one
does. They run inside the grader's own process, which a real controller never
does — that is the one thing they do not prove. Run them before a marking
session; they are also what `tests/test_grade.py` asserts against.

### Two clocks, or one

A graded run is normally two machines on two wall clocks. The plant paces
itself against real time, and the controller scans whenever it scans. That is
the only honest way to grade a real PLC, because a real PLC does not wait for
the grader. It also means a run's numbers depend a little on how busy the
marking machine is. When the machine is loaded, both clocks get coarser, and
anything measured at a scan boundary moves with them. The stopwatch `timed`
batch is an example. It ends on a scan, and with the same seed on one machine
under load its first batch landed anywhere from 23.28 L to 23.52 L, against a
limit of 23.5 L. That was enough to fail `tests/test_grade.py` on a loaded CI
runner while the same code passed on its PR run (IP-06).

A built-in controller runs in the grader's own process, so it *can* be made to
wait. `--lockstep` does that:

<!-- from-source -->
From a source checkout (from the download, `.\factoryforge-sidecar grade` takes the same flags; see [Grading from the download](#grading-from-the-download)):

```bash
python tools/grade.py --scene <id> --reference good --lockstep
```
<!-- /from-source -->

Each scan of the built-in controller runs in the same order. The controller
reads its inputs and writes its outputs, and the plant applies those writes.
Then the plant advances by exactly one 50 ms scan, and its updates reach the
controller before the next scan begins. Nothing on that path reads the wall
clock. The controller still talks to the plant over the real websocket, so it
crosses the same seam as before. It just cannot fall behind or run ahead of
the plant. The run is therefore reproducible from its seed alone, and it
finishes much faster than real time. On an idle Windows machine, the
seventy-eight-second batch-dosing window took three seconds. The JSON report
says `"clock": "lockstep"` in its evidence.

It is refused without `--reference` (exit 2). A student's program is always
graded on the wall clock, exactly as before, because there is no way to make
their PLC wait. For the same reason, lockstep proves less than a wall-clock
run about the real-time path. The test suite keeps `idle` and `forcer` on the
wall clock for that reason: their verdicts do not depend on timing. It also
keeps one `good` run there, a sorting controller that connects 8 s late,
because when the window opens is a wall-clock question and lockstep never had
the problem. That run passes with 11 cartons sorted against the 8 needed.

---

## What this does not do

Read this part before promising it to a class.

**All ten scenes this was built for**, and a test asserts it. A second test
asserts the other direction against the engine's own manifest — nothing is
graded that no student can open — and a third requires any scene the engine
ships *without* a rubric to be admitted in this file, so one cannot appear on
the start screen and quietly go unmarked.

`palletising-cell` is one: it landed on the start screen while this was being
written and **has no rubric**. It needs a plant model with an articulated arm
and a pallet station, which is more machine than any of the ten here, and
nothing about the machinery below stops somebody writing it.

IP-14 added six more, each for parts no earlier scene used, and each with a
rubric from the start: `star-delta-start`, `servo-positioning`,
`cooling-tunnel`, `air-receiver`, `press-station` and `rotary-index`, which
between them place every catalog part except two. `VerticalLift` waits for a
second level (IP-15). `PivotDiverter` had no scene because it could not divert:
held at `divert`, its `diverted` tag read true while its blade's collider stayed
parked, so a carton passed it untouched. IP-32 fixed the part and added
`pivot-divert`, graded like the rest.

That scene's model is the one place a contact between two rigid bodies is
reduced to four numbers. A turned carton slides along the blade, driven by the
belt under it, so where it is on the blade scales with belt travel. The points
where the blade must be across, where it strikes, and where letting go releases
the carton were measured in the engine at two belt speeds rather than derived.
`grading/scenes/pivot_divert.py` records how, and refuses a template that moves
any part they depend on. Every reference controller was run against the 3-D
engine at 0.5 and 0.35 m/s and landed each carton in the same lane as the
model. That a late blade *strikes* the carton is read in the engine from how
early the chute counts it, not seen directly.

What "all ten" does *not* mean is that every scene is marked on everything its
brief describes; see the next four paragraphs.

**Python models of the plants, not the 3D engine.** The sorting line runs
`factoryforge_sidecar/sorting_scene.py`; the other nine run models that live
in `factoryforge_sidecar/grading/scenes/`.
All of them are 1-D kinematic plants with no physics. They are faithful about
sensor semantics, about timing, and — where the lesson is analog — about the
engine's own dynamics, which are copied from the C# part and the template that
configures it rather than invented. Since IP-29 that includes where every part
stands along the line and what it does there (IP-19 found half of them
somewhere else): Torricelli outflow, a 20-second thermal
time constant, a flow meter's 0.2 s damping, a relay's 0.5 s channel-sync
window, carton masses out of `BoxPhysics.cs`. But a carton in them cannot jam,
tip, or ride two centimetres low into the end face of the next conveyor
(AGENTS.md gotcha 23), a cylinder cannot be fouled, and nothing has a third
dimension. **A program that passes here is not guaranteed to work in the
rigid-body scene.** Grading against the real engine means a Godot install on
the marking machine, which is exactly the barrier `docs/PACKAGING.md` exists to
remove, and it has not been attempted.

**The models reproduce two parts' behaviour without their mechanism.** In the
engine, a safety relay holds the starter coil down by *forcing* the tag, and a
motor starter drives the belt the same way. A forced tag is this tool's
disqualification signal, so the guarded cell's model cannot use that mechanism
without tripping its own wire: the plant simply does not obey a command the
relay is not passing. The behaviour a student sees is identical and the
mechanism is not, and if the engine ever changes what forcing means for those
parts, this is where the two will part company.

**Fault injection is graded on two scenes, and not on the ten.** The servo
positioning scene faults its drive mid-move and marks what the program does
until the operator's Reset, and the air receiver seizes its isolation valve and
marks the alarm. The ten this section was written about are unchanged: every
one of them has a fault tag —
`tank.fault`, `oven.fault`, `pump.fault`, `stop.fault`, `gantry.fault` — and
half the briefs end on it: a seized valve keeps its opening while your command
reads zero, a failed element cools while the heater output reads 100 %, a dead
pump holds its speed reference while the flow collapses. Those are the best
lesson in several of these scenes and **none of them is marked**. The tags are
declared so the tag list matches the scene a student is handed; no exam script
raises one. `tools/try_scene.py` exercises all of them against the real engine
and is the right place to look for how each check should be written.

**The operator panel is graded on three scenes out of ten.** Every model has
a panel and the grader presses its buttons, but only `start-stop-station`,
`sorting-by-height` (since IP-35) and `guarded-cell` mark the operator
contract itself — the latching trip, Start that will not clear it, the relay
that starts nothing. On the other seven the panel is how the exam turns the pot and
starts the line, and a program that ignored Stop entirely would still pass
them. `tools/try_scene.py` checks the
full contract on all ten.

**One window is a sample, not a proof.** The windows run 60–80 seconds, which
is a dozen or two cartons or two settling steps. A program that misroutes one
carton in five hundred will pass, and a program whose timing is marginal may
pass one run and fail the next. Run it more than once with different seeds
before a mark is final; the seed is in the report so you can say which runs you
used.

**Three scenes mark an output rather than a plant fact, and cannot do otherwise.**
The air receiver's valve checks are an alarm lamp (`alarm.beacon`) timed
against a valve the plant seized: in the engine the isolation valve feeds
nothing, so there is no consequence to read instead.
The checkweigher's reject decision is a lamp (`panel.red`) and the guarded
cell's "never wrote `belt.rotate`" is a fact about the wire. There is no
physical consequence in either scene to read instead — the line has no reject
gate, and a motor tag nobody obeys leaves no trace in the cartons. Both are
still unfakeable in the way that matters (the lamp is checked against masses
the program never sees, at two limits; the write is recorded whether or not it
did anything), but they are the two places where the criterion is not something
the plant did.

**It cannot see the program.** Only what the program does. Copied code that
works gets the same mark as understood code that works. That is true of every
functional test and it is worth saying out loud on a document about assessment.

---

## Adding a graded scene

The grader is the `factoryforge_sidecar.grading` package, and a scene is two
files in it:

* `grading/scenes/<scene>.py` — the plant model, the rubric, the feedback and
  the summary. It defines `SCENE`, the id from `engine/templates/manifest.json`,
  and `RUBRIC`, the table `--list` prints and the run reads.
* `grading/reference/<scene>.py` — the built-in controllers, a `good` one and
  at least one that is wrong about the scene's own lesson. It defines `SCENE`
  and `REFERENCES`.

`grading/registry.py` finds both by those names, so no existing file changes:
not `core.py`, not `tools/grade.py`, not a list anywhere. A test fails if
`core.py`, or any other file the scenes are built on, names a scene. What the
plant models are built from (`Item`, `Script`, `Panel`, `PlantScene`) is in
`grading/plant.py`, and the reference controllers' scan loop and panel logic
are in `grading/lockstep.py` and `grading/reference/_shared.py`. The tests
still have to be written by hand, and so does this file's table.

---

## Reference

* `tools/grade.py` — the command, a shim over `factoryforge_sidecar.grading`
* `sidecar/factoryforge_sidecar/grading/` — the tool: `core.py` runs it,
  `scenes/` holds the plant models with their rubrics, and `reference/`
  holds their reference controllers
* `tests/test_grade.py` — what is claimed above, asserted
* `tools/try_scene.py` — the other side of the same seam: drives a scene
  against the real 3D engine to prove the scene works, including the fault
  injection and the full operator contract this does not grade
* `engine/templates/manifest.json` — each scene's own brief, which is what the
  rubrics are written against
* `docs/tag-bus.md` — the protocol, `force` included
