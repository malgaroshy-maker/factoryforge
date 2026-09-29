"""`accumulation-buffer`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/accumulation_buffer.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (BELT_THICKNESS, CARTON_LENGTH, EngineFeed, Item,
                     OperatorExam, PlantScene, Script, Vfd, declare_stack_light,
                     fault_input, operator_exam_ends_by, pot_start, remover_catch)
from ..templates import template
from ._contract import mark_contract, summary_contract


SCENE = "accumulation-buffer"


# --- accumulation buffer -------------------------------------------------
#
# Observable fact: how many cartons physically passed the blade stop, and in
# which release. The plant moves them and the plant counts them.
#
# How a program fakes it: by dropping the blade for a fixed number of seconds.
# At one belt speed that releases the right amount every time, and it is the
# obvious thing to write. So the exam reaches into the drive halfway through
# the run and *halves the belt's top speed* -- a physical change no tag
# reports, visible only as the encoder counting slower. A release measured in
# pulses is a release measured in distance and lets out the same product; a
# release measured in seconds lets out half as much.
#
# It halves rather than doubles, and until IP-29 it doubled. The drive only
# moves the buffer: a carton past the blade crosses onto the `outfeed` belt,
# which runs at its own 0.5 m/s, and the model used to ignore that and carry
# every carton at the buffer's speed. Once it does not, a buffer running at
# 1.0 m/s is feeding a 0.5 m/s outfeed: released cartons pile up on it and
# back up to the blade, so a release lets out what the OUTFEED can take, and
# a correct release measured in pulses lets out fewer at the higher speed.
# The engine does exactly that, so the exam cannot ask for it. Slower than
# the outfeed, the outfeed is never the limit, and the lesson is the same
# one: same distance, same product; same seconds, half the product.
#
# The other half is the blade itself: nothing may pass it while it is up, and
# the encoder has to have been counting the whole time, so "the line stopped"
# is ruled out as the explanation.
#
# Read from the template (IP-19, and IP-29 for the positions). As shipped: 100
# pulses per metre, a 0.26 m blade stroke at 2 m/s, a drive ramping at 60 %/s
# to 0.5 m/s, the blade at x = 2.6 on the buffer's 3 m deck, then a 1.2 m
# outfeed. Positions are world X.
_PLANT = template(SCENE)
_BUFFER = _PLANT.part("buffer", "VariableConveyor")
_ENCODER = _PLANT.part("enc", "RotaryEncoder")
_STOP = _PLANT.part("stop", "StopGate")
_OUTFEED = _PLANT.part("outfeed", "ConveyorBelt")

#: Where a carton starts: the emitter's X, 0.2 m in. Until IP-29 this model
#: started them at 0.
AB_START_POS = _PLANT.part("emitter", "Emitter").x
#: The blade and the exit eye. Until IP-29 this model had both 0.4 m further
#: on, the blade at the buffer deck's far end.
AB_BLADE_POS = _STOP.x
AB_EYE_POS = _PLANT.part("exit_eye", "PhotoelectricSensor").x
#: `StopGate.cs` (`BladeThickness`): a queued carton's nose rests against the
#: blade's upstream face, half this in front of the blade's centre line.
AB_BLADE_THICKNESS = 0.035
#: The buffer deck ends here and the outfeed deck takes over, at its own speed
#: and only while `outfeed.rotate` is true.
AB_BUFFER_FROM, AB_BUFFER_TO = _BUFFER.span()
AB_OUTFEED_SPEED = _OUTFEED.number("speed")
#: Where the `released` remover takes a carton (`plant.remover_catch`).
AB_REMOVER_POS = remover_catch(_PLANT.part("released", "Remover"), _OUTFEED.span()[1])
#: Centre to centre of two cartons queued nose to tail: they touch, so one
#: carton's length. This model used 0.22 m, a number of its own, until IP-29.
AB_PITCH = CARTON_LENGTH
AB_PULSES_PER_METRE = _ENCODER.number("pulses_per_metre")
AB_STROKE = _STOP.number("stroke")
AB_BLADE_TIME = AB_STROKE / _STOP.number("lift_speed")
#: The blade holds a carton back once its top edge is above the deck: the
#: parked blade's top sits a deck's thickness below the carrying surface
#: (`StopGate.cs`, `ParkedY`), so that is how far it has to rise.
AB_BLOCKS_AT = BELT_THICKNESS / AB_STROKE
AB_RAMP = _BUFFER.number("accel_rate")
#: The drive's top speed, before and after the exam changes it. The change is
#: the exam's, and not a number from anywhere else: it halves.
AB_SPEED_FIRST = _BUFFER.number("max_speed")
AB_SPEED_CHANGE = 0.5
AB_SPEED_THEN = AB_SPEED_FIRST * AB_SPEED_CHANGE
AB_SPEED_CHANGES_AT = 40.0
AB_POT_START = pot_start(_PLANT)

# --- the operator contract (IP-12) ------------------------------------------
#
# Once the releases have been marked -- over the old 80-second window, and
# only there -- the E-stop sheet (`plant.OperatorExam`). "Stopped" is the
# buffer's drive no longer driven and the outfeed off: a VFD coasts down its
# own ramp once `run` drops, which is the drive's stop and not the program's
# to hurry. The examiner strikes with the blade up and no release in
# progress, so a release is never the E-stop's to cut short; a program that
# stops the line holds its queue behind the blade, as the brief's buffer does.

#: The releases, the belt's speed and the travel under the blade are marked up
#: to here, where the window used to end.
AB_HOLDS_END = 80.0
AB_ESTOP_AT = AB_HOLDS_END + 0.5
AB_EXAM_ENDS_BY = operator_exam_ends_by(AB_ESTOP_AT)


class AccumulationScene(PlantScene):
    name = "accumulation-buffer"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=AB_POT_START)
        self._declare(
            Tag("buffer.run", "VFD Conveyor (Run)", "bit", "output"),
            Tag("buffer.speed", "VFD Conveyor Speed Ref (%)", "float", "output"),
            Tag("outfeed.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("stop.raise", "Stop (Raise)", "bit", "output"),
            Tag("enc.reset", "Encoder (Reset)", "bit", "output"),
            Tag("count_display.value", "Released (Display)", "int", "output"),
            Tag("buffer.actual", "VFD Conveyor Actual Speed (%)", "float", "input"),
            Tag("enc.count", "Encoder Count", "int", "input"),
            Tag("stop.up", "Stop (Blade Up)", "bit", "input"),
            Tag("stop.down", "Stop (Blade Down)", "bit", "input", value=True),
            Tag("exit_eye.detect", "Diffuse Sensor (Detect)", "bit", "input"),
            Tag("released.count", "Remover (Count)", "int", "input"),
            Tag("enc.rate", "Encoder Rate (pulses/s)", "float", "input"),
            fault_input("buffer", "VFD Conveyor Drive Fault"),
            fault_input("outfeed", "Conveyor Drive Fault"),
            fault_input("stop", "Stop Drive Fault"),
        )
        declare_stack_light(self.tags)

        self.drive = Vfd(AB_SPEED_FIRST, AB_RAMP)
        self.blade = 0.0                 # 0 down, 1 up
        self.pulses = 0.0
        self.items: list[Item] = []
        self.released: list[Item] = []
        self._next_id = 1
        self._emit_edge = False
        self._feed = EngineFeed(_PLANT.part("emitter", "Emitter"))

        #: Ground truth. One entry per time the blade was down, with the
        #: cartons that got past during it and the drive speed at the time.
        self.releases: list[dict] = []
        self._open: dict | None = None
        #: Cartons that got past a raised blade, which must be none.
        self.escaped: list[int] = []
        #: Cartons a queue backed up past the emitter pushed off the infeed
        #: end of the belt.
        self.spilled: list[int] = []
        #: Metres of belt travelled while the blade was up, so "nothing got
        #: past" cannot be satisfied by a line that was not running.
        self.travel_while_held = 0.0
        self.speed_samples: dict[str, list[float]] = {"first": [], "then": []}

        self.window = float(self.rng.choice([100, 120, 140]))
        self.script = Script([
            (0.3, self.panel.set_setpoint(self.window)),
            (1.0, self.panel.press("start")),
            (AB_SPEED_CHANGES_AT, self._change_the_drive),
        ])
        self.operator = OperatorExam(
            self, AB_ESTOP_AT, noun="belt", what="the buffer's drive",
            ready=lambda: self._open is None and self.blade >= 0.999)

    def _change_the_drive(self) -> None:
        """Reach into the drive and change what 100 % means.

        The controller cannot read this anywhere. `buffer.speed` is its own
        command and `buffer.actual` is a percentage of a maximum it is not
        told -- the only thing that changes is how fast the encoder counts,
        which is exactly the instrument a release measured in distance uses
        and a release measured in seconds does not.
        """
        self.drive.max_speed = AB_SPEED_THEN

    @property
    def phase(self) -> str:
        return "first" if self.t < AB_SPEED_CHANGES_AT else "then"

    def step(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            height, metal = self._feed.next()
            self.items.append(Item(height=height, metal=metal,
                                   position=AB_START_POS, id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        # `VariableConveyor.cs`: dropping `buffer.run` ramps the belt down
        # rather than stopping it dead.
        speed = self.drive.step(self.bit("buffer.run"), self.num("buffer.speed"), dt)
        self.tags.set("buffer.actual", self.drive.actual)
        marking = self.t <= AB_HOLDS_END
        if marking:
            self.speed_samples[self.phase].append(speed)
        outfeed = AB_OUTFEED_SPEED if self.bit("outfeed.rotate") else 0.0
        self.operator.driven = self.drive.driven or outfeed > 0.0

        target = 1.0 if self.bit("stop.raise") else 0.0
        rate = dt / AB_BLADE_TIME
        self.blade = (min(self.blade + rate, target) if target > self.blade
                      else max(self.blade - rate, target))
        up = self.blade >= 0.999
        self.tags.set("stop.up", up)
        self.tags.set("stop.down", self.blade <= 0.001)

        # `RotaryEncoder.cs` (`Step`): the rate is surface speed times pulses
        # per metre, and it reads the belt whether or not the count is held
        # in reset.
        rate = speed * AB_PULSES_PER_METRE
        self.pulses += rate * dt
        if self.bit("enc.reset"):
            self.pulses = 0.0
        self.tags.set("enc.count", int(self.pulses))
        self.tags.set("enc.rate", rate)

        if up and marking:
            self.travel_while_held += speed * dt

        # Each carton rides the deck its centre is on: the buffer's drive up
        # to the end of the buffer deck, the outfeed belt -- its own speed,
        # and only while `outfeed.rotate` -- after it. Until IP-29 every
        # carton here moved at the buffer's speed, past the blade included.
        #
        # Queue behind the blade: each carton is stopped by whatever is in
        # front of it, and the leader by the blade when the blade is up.
        blocking = self.blade > AB_BLOCKS_AT
        ahead = None
        for item in sorted(self.items, key=lambda i: i.position, reverse=True):
            was = item.position
            moved = (speed if was < AB_BUFFER_TO else outfeed) * dt
            limit = float("inf")
            if blocking and was < AB_BLADE_POS:
                limit = AB_BLADE_POS - AB_BLADE_THICKNESS / 2 - CARTON_LENGTH / 2
            if ahead is not None:
                limit = min(limit, ahead - AB_PITCH)
            item.position = min(item.position + moved, limit)
            ahead = item.position
            if was < AB_BLADE_POS <= item.position:
                if blocking:
                    self.escaped.append(item.id)
                elif self._open is not None:
                    self._open["cartons"] += 1

        if not blocking and self._open is None:
            self._open = {"from": round(self.t, 2), "phase": self.phase,
                          "cartons": 0, "pulses_at": self.pulses}
        elif blocking and self._open is not None:
            self._open["to"] = round(self.t, 2)
            self._open["pulses"] = round(self.pulses - self._open["pulses_at"], 1)
            self._open["seconds"] = round(self._open["to"] - self._open["from"], 2)
            if marking:
                self.releases.append(self._open)
            self._open = None

        self.tags.set("exit_eye.detect", self._eye(self.items, AB_EYE_POS))

        still = []
        for item in self.items:
            if item.position >= AB_REMOVER_POS:
                self.released.append(item)
            elif item.position < AB_BUFFER_FROM:
                # A queue backed up past the emitter has nowhere to go but off
                # the infeed end. On master this model kept them, at x = -5 m
                # behind a belt that starts at 0; the engine's emitter would
                # have been spawning cartons inside each other.
                self.spilled.append(item.id)
            else:
                still.append(item)
        self.items = still
        self.tags.set("released.count", len(self.released))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def grade_accumulation(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: AccumulationScene = watched.inner
    # A release worth comparing is one that let product out. A blade flicked
    # down for a tenth of a second is not a release, and averaging it in would
    # flatter a controller that did the job once.
    real = [r for r in sim.releases if r["cartons"] >= 1]
    first = [r for r in real if r["phase"] == "first"]
    then = [r for r in real if r["phase"] == "then"]
    avg_first, avg_then = _mean([r["cartons"] for r in first]), \
        _mean([r["cartons"] for r in then])
    moving = [s for s in sim.speed_samples["first"] if s > 0.01]
    moving_then = [s for s in sim.speed_samples["then"] if s > 0.01]
    speed_first, speed_then = _mean(moving), _mean(moving_then)

    report.evidence.update({
        "pulse_window_on_the_pot": sim.window,
        "releases": real[-12:],
        "first_speed": {"releases": len(first), "mean_cartons": round(avg_first, 2),
                        "belt_m_per_s": round(speed_first, 3)},
        "second_speed": {"releases": len(then), "mean_cartons": round(avg_then, 2),
                         "belt_m_per_s": round(speed_then, 3)},
        "escaped_a_raised_blade": sim.escaped[:20],
        "belt_travel_while_held_m": round(sim.travel_while_held, 2),
        "released_total": len(sim.released),
        "spilled_off_the_infeed_end": sim.spilled[:20],
        "blade_up_fraction": round(watched.held_true("stop.raise"), 3),
    })

    report.add("line.ran",
               len(sim.released) >= 6,
               f"{len(sim.released)} cartons reached the outfeed remover "
               f"(at least 6)")
    report.add("blade.held_everything",
               not sim.escaped,
               "nothing got past a raised blade" if not sim.escaped else
               f"{len(sim.escaped)} carton(s) passed a raised blade: "
               f"{sim.escaped[:10]}")
    report.add("blade.held_a_running_belt",
               sim.travel_while_held >= 3.0,
               f"{sim.travel_while_held:.1f} m of belt ran under a raised blade "
               f"(at least 3 m, so holding is the blade and not a stopped line)")
    report.add("release.happened_at_both_speeds",
               len(first) >= 1 and len(then) >= 1 and max(avg_first, avg_then) >= 2.0,
               f"{len(first)} release(s) at {speed_first:.2f} m/s and {len(then)} at "
               f"{speed_then:.2f} m/s, averaging {avg_first:.1f} and "
               f"{avg_then:.1f} cartons")
    if len(first) >= 1 and len(then) >= 1:
        report.add("release.same_size_at_both_speeds",
                   abs(avg_first - avg_then) <= 1.0,
                   f"{avg_first:.1f} cartons per release at {speed_first:.2f} m/s "
                   f"against {avg_then:.1f} at {speed_then:.2f} m/s "
                   f"(at most 1 apart)")

    _accumulation_feedback(report, watched, sim, first, then, avg_first, avg_then,
                           speed_first, speed_then)


def _accumulation_feedback(report, watched, sim, first, then, avg_first, avg_then,
                           speed_first, speed_then) -> None:
    say = report.feedback.append
    up = watched.held_true("stop.raise")

    if not sim.released:
        say("Nothing reached the outfeed. `buffer.run` and `outfeed.rotate` are "
            "yours, `buffer.speed` is a percentage reference, and "
            "`emitter.emit` makes one carton per RISING edge.")
        return
    if up == 0.0:
        say("The blade was never raised, so nothing ever accumulated. "
            "`stop.raise` holds product back on a belt that keeps running.")
    elif up > 0.97:
        say("The blade was up for essentially the whole run, so nothing was "
            "ever released.")

    if sim.escaped:
        say(f"{len(sim.escaped)} carton(s) got past while `stop.up` was true. "
            f"Read the limit switch rather than the command -- the blade takes "
            f"{AB_BLADE_TIME * 1000:.0f} ms to travel and `stop.raise` is true "
            f"for all of it.")

    if sim.spilled:
        say(f"{len(sim.spilled)} carton(s) went off the infeed end of the belt: "
            f"the queue behind the blade backed up past the emitter, which went "
            f"on making cartons into it. A buffer holds as many as fit between "
            f"the blade and the emitter; count what goes in against what passes "
            f"`exit_eye`, and stop feeding when it is full.")

    if len(first) >= 1 and len(then) >= 1 and abs(avg_first - avg_then) > 1.0:
        ratio = speed_then / speed_first if speed_first > 0 else 0.0
        how = (f"{ratio:.1f} times faster" if ratio >= 1.0
               else f"{ratio:.0%} of the speed")
        say(f"The belt ran at {speed_first:.2f} m/s for the first half of this "
            f"run and {speed_then:.2f} m/s for the second -- {how} -- "
            f"and your releases went from {avg_first:.1f} cartons to "
            f"{avg_then:.1f}. That is a release timed in seconds. "
            f"`panel.setpoint` is a window in ENCODER PULSES, which is a "
            f"distance: at {AB_PULSES_PER_METRE:.0f} pulses per metre, hold the "
            f"blade down until `enc.count` has advanced by that many and the "
            f"same length of product comes out at any speed.")
    elif len(first) < 1 or len(then) < 1:
        say("The run changed the drive's top speed halfway through and there was "
            "no release on one side of that change to compare. Cycle the blade "
            "more than once: accumulate, release, accumulate again.")
    elif not sim.escaped:
        say(f"The same pulse window released {avg_first:.1f} cartons at "
            f"{speed_first:.2f} m/s and {avg_then:.1f} at {speed_then:.2f} m/s. "
            f"A release timed in seconds would have let out "
            f"{avg_first * speed_then / max(speed_first, 0.01):.0f} the second "
            f"time.")


def _grade_and_mark(watched: Watched, engine: GradedEngine, report: Report,
                    duration: float) -> None:
    grade_accumulation(watched, engine, report, duration)
    mark_contract(watched.inner.operator, report, watched.sim_time)


def _summary_accumulation(evidence: dict, out) -> None:
    first, then = evidence["first_speed"], evidence["second_speed"]
    out(f"pot: a {evidence['pulse_window_on_the_pot']:.0f}-pulse release window "
        f"({evidence['pulse_window_on_the_pot'] / 100:.2f} m of belt)")
    out(f"at {first['belt_m_per_s']:.2f} m/s: {first['releases']} release(s), "
        f"{first['mean_cartons']:.1f} cartons each")
    out(f"at {then['belt_m_per_s']:.2f} m/s: {then['releases']} release(s), "
        f"{then['mean_cartons']:.1f} cartons each")
    summary_contract(evidence, out)
    out(f"{evidence['released_total']} cartons out, "
        f"{evidence['belt_travel_while_held_m']:.1f} m of belt ran under a raised blade")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Accumulation buffer",
    "task": ("Let cartons pile up behind the blade stop on a belt that "
             "never stops, then release a batch. The pot is a release "
             "window in ENCODER PULSES, which is a distance -- and this "
             "run changes the drive's top speed halfway through. The "
             "mushroom is normally closed, stops the drive and the outfeed "
             "within 200 ms and latches -- only Reset, then Start, restarts "
             "them."),
    "build": AccumulationScene,
    "observe": None,
    "grade": _grade_and_mark,
    "summary": _summary_accumulation,
    "duration": AB_EXAM_ENDS_BY,
    "references": ("good", "timed", "noestop", "startalone"),
    "tags": ("buffer.run, buffer.speed, outfeed.rotate, emitter.emit, "
             "stop.raise, enc.reset, count_display.value, panel.green, "
             "panel.red are yours to write; buffer.actual, enc.count, "
             "stop.up, stop.down, exit_eye.detect, released.count, "
             "panel.setpoint and the buttons are the line's."),
}
