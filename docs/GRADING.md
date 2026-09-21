# Grading a PLC program against a scene

*One scene — sorting by height — graded end to end, headless, on the Python
model of the line rather than the 3D engine. Verified on 2026-09-21 against
five reference controllers: a correct one passes, a blind timer and a stuck
pusher fail with the cartons that went wrong, an idle one cannot pass, and one
that forces the counters is disqualified. **Nobody has graded a real student's
program with it.** Those are two different claims and this file will say so
until the second one is true.*

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
  feed seed 1288431   window 60s

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
| `--duration` | seconds to watch once the controller connects (default 60) |
| `--wait` | seconds to wait for a controller before giving up (default 120) |
| `--seed` | the feed pattern. Reported either way, so a mark is reproducible |
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

For sorting by height:

| check | |
|---|---|
| `controller.stayed_connected` | one session, still up at the end |
| `integrity.no_forced_tags` | nothing was pinned — see below |
| `integrity.no_input_writes` | nothing tried to write a simulator-owned tag |
| `line.ran` | at least 8 cartons reached a lane |
| `line.both_lanes` | at least 3 in each |
| `sort.tall_diverted` | no tall carton ran off the far end |
| `sort.short_passed` | no short carton went down the chute |
| `line.conservation` | everything fed is either sorted or still on the belt |

The first two of the sorting checks are there because the last two are
vacuously true of a line that never ran. A test that passes while the
simulation does nothing is not a test (AGENTS.md gotcha 16), and a grader is a
test with a student's mark attached to it.

### The feed pattern is shuffled, and that is the point

A line that alternates tall, short, tall, short can be sorted perfectly by a
program that pushes every second carton and never reads a sensor. It would pass
a grader that fed a fixed pattern, and it would fail on any real line.

So the grader feeds shuffled pairs: sixteen `[tall, short]` pairs, each pair
shuffled, cycled. The order is unguessable, and any eight consecutive cartons
still hold at least three of each height — so the per-lane minimums stay
reachable however the shuffle lands. The seed is chosen at random unless you
give one, and it is in the report either way, so a disputed mark can be re-run
exactly.

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

```bash
python tools/grade.py --reference good      # must PASS  (exit 0)
python tools/grade.py --reference blind     # pushes on a timer      -> FAIL
python tools/grade.py --reference greedy    # pusher held out        -> FAIL
python tools/grade.py --reference idle      # connects, does nothing -> FAIL
python tools/grade.py --reference forcer    # forces the counters    -> DISQUALIFIED
```

These are built-in controllers that connect over a real websocket through the
same `TagBusClient` the sidecar uses, so they cross the same seam a real one
does. They run inside the grader's own process, which a real controller never
does — that is the one thing they do not prove. Run them before a marking
session; they are also what `tests/test_grade.py` asserts against.

---

## What this does not do

Read this part before promising it to a class.

**One scene.** `sorting-by-height`, and `--list` will only ever show what is
really implemented. The rubric table in `tools/grade.py` is keyed by scene id
and adding a second one means writing its `build`, `observe` and `grade`
functions; the machinery is general, the marking is not. The other seven
shipped scenes are not gradable today, and a test fails if that sentence stops
being true without this file changing.

**The Python model of the line, not the 3D engine.** The grader runs
`harness/scene.py` — a 1-D kinematic model with no physics. It is the CI
regression scene and it is faithful about sensor semantics and timing, but a
carton in it cannot jam, tip, or ride two centimetres low into the end face of
the next conveyor (AGENTS.md gotcha 23). A program that passes here is not
guaranteed to work in the rigid-body scene. Grading against the real engine
means a Godot install on the marking machine, which is exactly the barrier
`docs/PACKAGING.md` exists to remove, and it has not been attempted.

**No marks for the operator panel.** The scene's task brief names
`panel.start`, `panel.stop` and `panel.estop`, and the headless scene has none
of them — it has the ten core tags and nothing else. So the half of the
exercise that is about interlocks, latching and a normally-closed E-stop is not
graded. `tools/try_scene.py` does check all of that, against the engine's own
template, and it is the right place to look for how that check should be
written when someone grades it.

**A sixty-second window is a sample, not a proof.** It is long enough for
around thirty cartons at a sensible feed rate. A program that misroutes one
carton in five hundred will pass, and a program whose timing is marginal may
pass one run and fail the next. Run it more than once with different seeds
before a mark is final; the seed is in the report so you can say which runs
you used.

**It cannot see the program.** Only what the program does. Copied code that
works gets the same mark as understood code that works. That is true of every
functional test and it is worth saying out loud on a document about assessment.

---

## Reference

* `tools/grade.py` — the tool, the rubric, and the reference controllers
* `tests/test_grade.py` — what is claimed above, asserted
* `tools/try_scene.py` — the other side of the same seam: drives a scene to
  prove the scene works, including the operator-panel half this does not grade
* `docs/tag-bus.md` — the protocol, `force` included
