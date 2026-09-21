# Grading a PLC program against a scene

*All ten shipped scenes, graded end to end, headless, on Python models of the
plants rather than on the 3D engine. Verified on 2026-09-21: every scene's
`good` controller passes and every scene's deliberately wrong one fails, each
for that scene's own lesson. **Nobody has graded a real student's program with
any of them.** Those are two different claims and this file will say so until
the second one is true.*

Marking a PLC exercise by hand means watching a line run and forming an
opinion. It does not scale past a small class, it is not reproducible, and two
markers will not agree. `tools/grade.py` runs the exercise unattended and
returns a verdict, an exit code, and the evidence behind both.

```bash
python tools/grade.py --scene sorting-by-height --student a.patel --json marks/a.patel.json
```

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

Every check on every scene is written down twice: once as a check id in
`tools/grade.py`, and once here, as the plant fact it reads and the fake it
shuts.

| scene | the fact that decides the mark | how a program would fake it, and what stops that |
|---|---|---|
| `sorting-by-height` | which lane each carton ended in, against the height the scene gave it | pushing every second carton — the feed is shuffled pairs |
| `start-stop-station` | cartons that broke the eye between the Start press and the line stopping itself; millimetres of belt that moved while tripped | running for about the right length of time — the batch size is drawn from the seed and set twice |
| `tank-level-control` | the level trace: settled error, ripple and overshoot | "it reached the setpoint", true of float switches and of a valve slammed open — ripple and overshoot grade those, and the pot moves to a second level |
| `light-curtain-sorting` | each carton's measured height and the lane it ended in, against the rule in force when it was measured | a threshold written into the program — the pot is set twice, and the feed is eight shuffled heights rather than two |
| `roller-line-weighing` | each carton's true mass, whether it was weighed alone, and whether the program flagged it | rejecting on the inductive sensor — the limit moves below a tall cardboard carton, where metal and heavy stop agreeing |
| `pick-and-place-cell` | which cartons the gantry carried to the outfeed, and the rail position it let go of the others at | a sequence on timers — the run slows the axis down, and a timed release drops cartons at half rail |
| `accumulation-buffer` | cartons that physically passed the blade, per release | a release timed in seconds — the run doubles the drive's top speed |
| `heat-treat-station` | the temperature trace: settled error, ripple and overshoot | proportional-only parks short by an offset the plant's own numbers predict; a thermostat reaches setpoint and swings 9 °C |
| `guarded-cell` | the tick the contactor pulled in, and whether anybody had pressed Start since it last stopped | nothing — this one catches an accident, not a shortcut. Also: every tag the program wrote, because `belt.rotate` is the motor's |
| `batch-dosing` | litres the pump physically moved, per batch | a dose timed in seconds — the run re-rates the pump between the two batches |

Every scene also carries `controller.stayed_connected`,
`integrity.no_forced_tags` and `integrity.no_input_writes`, and every scene has
at least one check whose only job is to refuse a verdict about a plant that did
nothing — `line.ran`, `plant.moved`, `dose.ran`, `cell.cycled`. A test that
passes while the simulation does nothing is not a test (AGENTS.md gotcha 16),
and a grader is a test with a student's mark attached to it.

`tools/grade.py --list` prints the same set, with each scene's window and its
reference controllers.

### The exam changes the plant while the program is running

Six of the ten scenes are only gradeable because the run reaches in and changes
something physical that no tag reports:

* the **setpoint pot** moves, on the tank, the oven, the curtain and the scale
* the **drive's top speed** doubles, on the accumulation buffer
* the **gantry's travel speed** halves, on the pick and place cell
* the **pump's rating** halves, between the two batches of the dosing exercise
* the **gate** opens and shuts, on the guarded cell, with the operator taking
  the part out as they go in

None of those is visible as a value on the bus. The controller can only find
out by measuring — the encoder counting faster, the flow meter reading less,
the axis taking longer to arrive — which is exactly the difference between a
program written on feedback and one written on a stopwatch. A rubric that never
moved anything would mark both the same.

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

Those numbers are computed from `harness/scene.py`'s own geometry — belt speed,
sensor position, pusher travel and catch width — rather than written into the
grader. A change to the line changes the advice instead of quietly making it
wrong, and there is a test that fails if anyone hard-codes them back.

The three ways this exercise goes wrong are distinguishable from outside the
controller, and each gets its own sentence: the line never moved, the pusher
never moved, or the pusher moved at the wrong moment.

`--json` writes all of it, including the per-carton ledger — id, height, lane,
the second it landed. That file is the appeal record.

---

## Checking the grader itself

A grader that fails everybody looks exactly like a cohort that cannot program.

Every scene carries a `good` that must pass and at least one controller that is
deliberately wrong about that scene's own lesson and must fail. Every one of
them has been watched failing — a rubric only ever seen to pass is a rubric
nobody knows the shape of (AGENTS.md gotcha 24).

```bash
python tools/grade.py --scene <id> --reference good    # must PASS  (exit 0)
python tools/grade.py --scene <id> --reference idle    # does nothing -> FAIL
python tools/grade.py --scene <id> --reference forcer  # forces counters -> DISQUALIFIED
```

`idle` and `forcer` are shared, because a controller that does nothing and one
that lies are wrong everywhere. The rest belong to their scene:

| scene | wrong controller | what it fails on |
|---|---|---|
| `sorting-by-height` | `blind` | pushes on a timer — misrouted cartons |
| | `greedy` | plate held out — short cartons in the chute |
| `start-stop-station` | `noestop` | 1500 mm of belt through a struck mushroom, where 100 mm is the limit |
| | `runon` | counts to thirteen against a pot of four |
| `tank-level-control` | `bangbang` | a pair of float switches parks 5.5 % off |
| | `fixedsp` | holds 70 % while the pot says 26 |
| `light-curtain-sorting` | `fixed` | every misrouted carton was measured under one of the two thresholds |
| | `everyother` | diverts on a count, never reads the height |
| `roller-line-weighing` | `metalonly` | gets exactly the cartons the two instruments disagree about wrong |
| | `fastfeed` | two on the deck read as one peak |
| `pick-and-place-cell` | `timed` | correct at 80 %/s, six cartons on the floor at 48 % of the rail once it slows |
| `accumulation-buffer` | `timed` | 5.7 cartons a release becomes 11.0 when the drive speeds up |
| `heat-treat-station` | `ponly` | parks 8.3 °C short at one setpoint and 16.5 at the other |
| | `thermostat` | mean error 2 °C, swing 8.8 °C |
| `guarded-cell` | `autostart` | the motor starts at 28.18 s, the tick the relay closed on Reset |
| | `writesbelt` | writes `belt.rotate`, the motor's own tag |
| | `tapedmute` | holds the bridge 6.1 s past a 6 s limit |
| `batch-dosing` | `timed` | 22.1 L and then 11.0 L against the same pot |
| | `noreset` | the second batch is over before it starts |

These are built-in controllers that connect over a real websocket through the
same `TagBusClient` the sidecar uses, so they cross the same seam a real one
does. They run inside the grader's own process, which a real controller never
does — that is the one thing they do not prove. Run them before a marking
session; they are also what `tests/test_grade.py` asserts against.

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

What "all ten" does *not* mean is that every scene is marked on everything its
brief describes; see the next four paragraphs.

**Python models of the plants, not the 3D engine.** The sorting line runs
`harness/scene.py`; the other nine run models that live in `tools/grade.py`.
All of them are 1-D kinematic plants with no physics. They are faithful about
sensor semantics, about timing, and — where the lesson is analog — about the
engine's own dynamics, which are copied from the C# part and the template that
configures it rather than invented: Torricelli outflow, a 20-second thermal
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

**Fault injection is not graded.** Every one of these scenes has a fault tag —
`tank.fault`, `oven.fault`, `pump.fault`, `stop.fault`, `gantry.fault` — and
half the briefs end on it: a seized valve keeps its opening while your command
reads zero, a failed element cools while the heater output reads 100 %, a dead
pump holds its speed reference while the flow collapses. Those are the best
lesson in several of these scenes and **none of them is marked**. The tags are
declared so the tag list matches the scene a student is handed; no exam script
raises one. `tools/try_scene.py` exercises all of them against the real engine
and is the right place to look for how each check should be written.

**The operator panel is graded on two scenes out of ten.** Every model has a
panel and the grader presses its buttons, but only `start-stop-station` and
`guarded-cell` mark the operator contract itself — the latching trip, Start
that will not clear it, the relay that starts nothing. On the other eight the
panel is how the exam turns the pot and starts the line, and a program that
ignored Stop entirely would still pass them. `tools/try_scene.py` checks the
full contract on all ten.

**One window is a sample, not a proof.** The windows run 60–80 seconds, which
is a dozen or two cartons or two settling steps. A program that misroutes one
carton in five hundred will pass, and a program whose timing is marginal may
pass one run and fail the next. Run it more than once with different seeds
before a mark is final; the seed is in the report so you can say which runs you
used.

**Two scenes mark an output rather than a plant fact, and cannot do otherwise.**
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

## Reference

* `tools/grade.py` — the tool, the ten plant models, the rubrics and the
  reference controllers
* `tests/test_grade.py` — what is claimed above, asserted
* `tools/try_scene.py` — the other side of the same seam: drives a scene
  against the real 3D engine to prove the scene works, including the fault
  injection and the full operator contract this does not grade
* `engine/templates/manifest.json` — each scene's own brief, which is what the
  rubrics are written against
* `docs/tag-bus.md` — the protocol, `force` included
