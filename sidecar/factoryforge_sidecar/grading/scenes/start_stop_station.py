"""`start-stop-station`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/start_stop_station.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (CARTON_LENGTH, ESTOP_LIMIT, Item, PlantScene, Script,
                     declare_stack_light, fault_input)
from ..templates import template


SCENE = "start-stop-station"


# --- start / stop station ----------------------------------------------
#
# Observable fact: how many cartons physically crossed the part-present eye
# between the Start press and the line stopping itself, and how far the belt
# travelled while the station was tripped.
#
# How a program fakes it: by running the belt for about the right length of
# time. A batch of five at a fixed feed rate is a stopwatch problem if the
# grader only ever asks for five. So the pot is a number drawn from the seed
# and the exam asks for it twice, with a different number the second time --
# and the count that decides the mark is crossings of the eye, which is a
# distance the belt really moved with a carton on it.
#
# The other half is the mushroom, and it is graded as belt travel: the line has
# to be off within 200 ms of the strike, has to stay off when the mushroom pops
# back out, has to ignore Start while latched, and has to come back only after
# Reset *and* Start. Every one of those is metres of belt, not the state of a
# lamp.

#: Read from the template (IP-19). Positions are world X, and the emitter
#: sits at 0, which is where a carton starts.
_PLANT = template(SCENE)
SS_BELT_SPEED = _PLANT.part("belt", "ConveyorBelt").number("speed")
#: IP-19 finding, left as it was pending a decision: the template places
#: `part_present` at x = 2.0. This model has always had it at 1.5.
SS_EYE_POS = 1.5
#: A carton's length along the belt (`BoxPhysics.cs`), which is how long it
#: holds the beam.
SS_EYE_WINDOW = CARTON_LENGTH
#: Where a carton *enters* the eye's window, which is the moment the beam
#: breaks and therefore the physical event a counter counts. Counting from the
#: middle of the window instead put the plant's ledger 0.1 m -- a fifth of a
#: second -- behind the sensor, so a correct controller that stopped the belt
#: on its fourth edge was marked as having made three.
SS_EYE_BREAK = SS_EYE_POS - SS_EYE_WINDOW / 2
#: IP-19 finding, left as it was pending a decision: the belt ends at x = 3.0
#: and the template's `counter` remover takes x = 3.0 to 3.5. This model
#: retires a carton at 2.8.
SS_REMOVER_POS = 2.8


class StartStopScene(PlantScene):
    name = "start-stop-station"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("belt.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("produced.value", "Produced (Display)", "int", "output"),
            Tag("part_present.detect", "Diffuse Sensor (Detect)", "bit", "input"),
            Tag("counter.count", "Remover (Count)", "int", "input"),
            fault_input("belt", "Conveyor Drive Fault"),
        )
        declare_stack_light(self.tags)

        self.items: list[Item] = []
        self.removed: list[Item] = []
        self._next_id = 1
        self._emit_edge = False

        #: Ground truth. Crossings of the eye, stamped with the sim time, so a
        #: batch can be counted from the Start press that began it.
        self.crossings: list[float] = []
        #: Metres the belt moved while the station was tripped -- struck
        #: mushroom, or latched after one.
        self.travel_while_tripped = 0.0
        self.tripped_since: float | None = None
        self.stop_lag: float | None = None
        #: The batch the examiner asked for, and when it asked.
        self.batches: list[dict] = []

        big = 40                       # unreachable, so the interlocks have room
        self.batch_size = self.rng.choice([3, 4, 5])
        self.script = Script([
            (0.5, self.panel.set_setpoint(big)),
            (1.0, self.panel.press("start")),
            (12.0, self.panel.strike()),
            (14.0, self.panel.release()),
            (15.0, self.panel.press("start")),     # must not restart: still latched
            (17.0, self.panel.press("reset")),
            (18.5, self.panel.press("start")),     # this one must
            (24.0, self.panel.press("stop")),
            (26.0, self.panel.press("reset")),
            (26.5, self.panel.set_setpoint(self.batch_size)),
            (27.0, self._begin_batch),
        ])

    def _begin_batch(self) -> None:
        self.panel.press("start")()
        self.batches.append({"target": self.batch_size, "from": self.t,
                             "crossings_at": len(self.crossings)})

    # --- the plant ---

    def step(self, dt: float) -> None:
        tripped = not self.panel.healthy
        if tripped and self.tripped_since is None:
            self.tripped_since = self.t
        elif not tripped and self.tripped_since is not None and self.panel.started_since(
                self.tripped_since):
            self.tripped_since = None

        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            self.items.append(Item(id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        running = self.bit("belt.rotate")
        if running:
            moved = SS_BELT_SPEED * dt
            if self.tripped_since is not None:
                self.travel_while_tripped += moved
                if self.stop_lag is None and not self.panel.healthy:
                    self.stop_lag = self.t - self.tripped_since
            for item in self.items:
                before = item.position
                item.position += moved
                if before < SS_EYE_BREAK <= item.position:
                    self.crossings.append(self.t)
        elif self.tripped_since is not None and self.stop_lag is None and not self.panel.healthy:
            self.stop_lag = self.t - self.tripped_since

        still = []
        for item in self.items:
            if item.position >= SS_REMOVER_POS:
                self.removed.append(item)
            else:
                still.append(item)
        self.items = still

        self.tags.set("part_present.detect",
                      self._eye(self.items, SS_EYE_POS, SS_EYE_WINDOW))
        self.tags.set("counter.count", len(self.removed))


def grade_start_stop(watched: Watched, engine: GradedEngine, report: Report,
                     duration: float) -> None:
    sim: StartStopScene = watched.inner
    batch = sim.batches[0] if sim.batches else None
    made = len(sim.crossings) - batch["crossings_at"] if batch else 0
    target = batch["target"] if batch else sim.batch_size
    # Anything that crossed the eye more than four seconds after the target was
    # met is a line that did not stop itself.
    overrun = 0
    if batch:
        hits = sim.crossings[batch["crossings_at"]:]
        if len(hits) >= target:
            at_target = hits[target - 1]
            overrun = sum(1 for h in hits if h > at_target + 4.0)

    report.evidence.update({
        "batch": {"target": target, "made": made, "overrun": overrun},
        "crossings": len(sim.crossings),
        "removed": len(sim.removed),
        "estop": {
            "travel_while_tripped_m": round(sim.travel_while_tripped, 3),
            "allowed_m": round(SS_BELT_SPEED * ESTOP_LIMIT, 3),
            "stop_lag_s": None if sim.stop_lag is None else round(sim.stop_lag, 3),
        },
        "belt_running_fraction": round(watched.held_true("belt.rotate"), 3),
        "presses": sim.panel.presses,
        "produced_display": sim.num("produced.value"),
    })

    allowed = SS_BELT_SPEED * ESTOP_LIMIT
    report.add("line.ran",
               len(sim.removed) >= 4,
               f"{len(sim.removed)} cartons reached the far end (at least 4 needed)")
    report.add("estop.stopped_the_belt",
               sim.travel_while_tripped <= allowed,
               f"the belt moved {sim.travel_while_tripped * 1000:.0f} mm while the "
               f"station was tripped (at most {allowed * 1000:.0f} mm, which is "
               f"{ESTOP_LIMIT * 1000:.0f} ms of belt)")
    report.add("batch.hit_the_number",
               made == target,
               f"the batch made {made} against a pot of {target}")
    report.add("batch.stopped_itself",
               overrun == 0,
               "the line stopped itself at the target" if not overrun
               else f"{overrun} more carton(s) went past the eye after the target was met")

    _start_stop_feedback(report, watched, sim, made, target, overrun, allowed)


def _start_stop_feedback(report, watched, sim, made, target, overrun, allowed) -> None:
    say = report.feedback.append
    belt = watched.held_true("belt.rotate")

    if belt == 0.0:
        say("The belt never ran. `belt.rotate` is a PLC output and nothing "
            "downstream matters until your program writes it.")
    if not sim.removed and belt > 0:
        say("The belt ran but nothing reached the far end. `emitter.emit` makes "
            "one carton on each RISING edge -- holding it true makes exactly one.")

    if sim.travel_while_tripped > allowed:
        say(f"The belt kept moving with the station tripped -- "
            f"{sim.travel_while_tripped * 1000:.0f} mm of it. `panel.estop` is "
            f"NORMALLY CLOSED: true means healthy, so the mushroom reads FALSE. "
            f"And the trip has to latch: releasing the mushroom must not restart "
            f"anything, and Start must do nothing until Reset has cleared it.")

    if made > target:
        say(f"The batch overran: {made} cartons for a pot of {target}. The pot is "
            f"read fresh, not latched at power-up -- this run set it twice.")
    elif made < target and belt > 0:
        say(f"The batch stopped {target - made} short of the pot. Count the "
            f"RISING edge of `part_present.detect`; it stays true for as long as "
            f"a carton sits in the beam, which at this belt speed is many scans.")
    elif overrun:
        say("The line reached its target and carried on. At the target it has to "
            "stop itself -- nobody presses Stop for it.")
    elif made == target and sim.travel_while_tripped <= allowed:
        say(f"The batch landed exactly: {made} of {target}, and the line stopped "
            f"itself. The E-stop held the belt inside "
            f"{ESTOP_LIMIT * 1000:.0f} ms and Start would not clear the latch.")


def _summary_start_stop(evidence: dict, out) -> None:
    batch, estop = evidence["batch"], evidence["estop"]
    out(f"batch of {batch['target']}: made {batch['made']}, "
        f"{batch['overrun']} past the target")
    out(f"{evidence['crossings']} cartons crossed the eye, "
        f"{evidence['removed']} reached the far end")
    out(f"belt travel while tripped {estop['travel_while_tripped_m'] * 1000:.0f} mm "
        f"(at most {estop['allowed_m'] * 1000:.0f} mm)")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Start / stop station",
    "task": ("Run a batch of the size the pot asks for and stop when it is "
             "made. The mushroom is normally closed and its trip latches: "
             "only Reset clears it, and Reset alone starts nothing. Reset "
             "also clears the batch count."),
    "build": StartStopScene,
    "observe": None,
    "grade": grade_start_stop,
    "summary": _summary_start_stop,
    "duration": 60.0,
    "references": ("good", "noestop", "runon"),
    "tags": ("belt.rotate, emitter.emit, produced.value, panel.green, "
             "panel.red are yours to write; panel.start, panel.stop, "
             "panel.reset, panel.estop, panel.setpoint, part_present.detect, "
             "counter.count are the line's."),
}
