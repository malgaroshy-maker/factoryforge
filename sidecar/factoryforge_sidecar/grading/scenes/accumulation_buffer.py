"""`accumulation-buffer`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/accumulation_buffer.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import Item, PlantScene, Script


SCENE = "accumulation-buffer"


# --- accumulation buffer -------------------------------------------------
#
# Observable fact: how many cartons physically passed the blade stop, and in
# which release. The plant moves them and the plant counts them.
#
# How a program fakes it: by dropping the blade for a fixed number of seconds.
# At one belt speed that releases the right amount every time, and it is the
# obvious thing to write. So the exam reaches into the drive halfway through
# the run and *doubles the belt's top speed* -- a physical change no tag
# reports, visible only as the encoder counting faster. A release measured in
# pulses is a release measured in distance and lets out the same product; a
# release measured in seconds lets out twice as much.
#
# The other half is the blade itself: nothing may pass it while it is up, and
# the encoder has to have been counting the whole time, so "the line stopped"
# is ruled out as the explanation.
#
# Numbers from `engine/templates/accumulation_buffer.json`: 100 pulses per
# metre, a 0.26 m blade stroke at 2 m/s, a drive ramping at 60 %/s.
AB_BLADE_POS = 3.0
AB_EYE_POS = 3.15
AB_REMOVER_POS = 4.2
AB_PITCH = 0.22
AB_PULSES_PER_METRE = 100.0
AB_BLADE_TIME = 0.26 / 2.0
AB_RAMP = 60.0
#: The drive's top speed, before and after the exam changes it.
AB_SPEED_FIRST = 0.5
AB_SPEED_THEN = 1.0
AB_SPEED_CHANGES_AT = 40.0


class AccumulationScene(PlantScene):
    name = "accumulation-buffer"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
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
        )

        self.max_speed = AB_SPEED_FIRST
        self.actual_percent = 0.0
        self.blade = 0.0                 # 0 down, 1 up
        self.pulses = 0.0
        self.items: list[Item] = []
        self.released: list[Item] = []
        self._next_id = 1
        self._emit_edge = False

        #: Ground truth. One entry per time the blade was down, with the
        #: cartons that got past during it and the drive speed at the time.
        self.releases: list[dict] = []
        self._open: dict | None = None
        #: Cartons that got past a raised blade, which must be none.
        self.escaped: list[int] = []
        #: Metres of belt travelled while the blade was up, so "nothing got
        #: past" cannot be satisfied by a line that was not running.
        self.travel_while_held = 0.0
        self.speed_samples: dict[str, list[float]] = {"first": [], "then": []}

        self.window = float(self.rng.choice([100, 120, 140]))
        self.script = Script([
            (0.3, self.panel.set_setpoint(self.window)),
            (1.0, self.panel.press("start")),
            (AB_SPEED_CHANGES_AT, self._speed_up),
        ])

    def _speed_up(self) -> None:
        """Reach into the drive and change what 100 % means.

        The controller cannot read this anywhere. `buffer.speed` is its own
        command and `buffer.actual` is a percentage of a maximum it is not
        told -- the only thing that changes is how fast the encoder counts,
        which is exactly the instrument a release measured in distance uses
        and a release measured in seconds does not.
        """
        self.max_speed = AB_SPEED_THEN

    @property
    def phase(self) -> str:
        return "first" if self.t < AB_SPEED_CHANGES_AT else "then"

    def step(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            self.items.append(Item(id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        running = self.bit("buffer.run")
        reference = min(max(self.num("buffer.speed"), 0.0), 100.0) if running else 0.0
        self.actual_percent += max(min(reference - self.actual_percent,
                                       AB_RAMP * dt), -AB_RAMP * dt)
        speed = self.max_speed * self.actual_percent / 100.0 if running else 0.0
        self.tags.set("buffer.actual", self.actual_percent)
        self.speed_samples[self.phase].append(speed)

        target = 1.0 if self.bit("stop.raise") else 0.0
        rate = dt / AB_BLADE_TIME
        self.blade = (min(self.blade + rate, target) if target > self.blade
                      else max(self.blade - rate, target))
        up = self.blade >= 0.999
        self.tags.set("stop.up", up)
        self.tags.set("stop.down", self.blade <= 0.001)

        self.pulses += speed * AB_PULSES_PER_METRE * dt
        if self.bit("enc.reset"):
            self.pulses = 0.0
        self.tags.set("enc.count", int(self.pulses))

        moved = speed * dt
        if up:
            self.travel_while_held += moved

        # Queue behind the blade: each carton is stopped by whatever is in
        # front of it, and the leader by the blade when the blade is up.
        blocking = self.blade > 0.5
        ahead = None
        for item in sorted(self.items, key=lambda i: i.position, reverse=True):
            was = item.position
            limit = float("inf")
            if blocking and was < AB_BLADE_POS:
                limit = AB_BLADE_POS - 0.10
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
            self.releases.append(self._open)
            self._open = None

        self.tags.set("exit_eye.detect", self._eye(self.items, AB_EYE_POS))

        still = []
        for item in self.items:
            if item.position >= AB_REMOVER_POS:
                self.released.append(item)
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

    if len(first) >= 1 and len(then) >= 1 and abs(avg_first - avg_then) > 1.0:
        ratio = speed_then / speed_first if speed_first > 0 else 0.0
        say(f"The belt ran at {speed_first:.2f} m/s for the first half of this "
            f"run and {speed_then:.2f} m/s for the second -- {ratio:.1f} times "
            f"faster -- and your releases went from {avg_first:.1f} cartons to "
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


def _summary_accumulation(evidence: dict, out) -> None:
    first, then = evidence["first_speed"], evidence["second_speed"]
    out(f"pot: a {evidence['pulse_window_on_the_pot']:.0f}-pulse release window "
        f"({evidence['pulse_window_on_the_pot'] / 100:.2f} m of belt)")
    out(f"at {first['belt_m_per_s']:.2f} m/s: {first['releases']} release(s), "
        f"{first['mean_cartons']:.1f} cartons each")
    out(f"at {then['belt_m_per_s']:.2f} m/s: {then['releases']} release(s), "
        f"{then['mean_cartons']:.1f} cartons each")
    out(f"{evidence['released_total']} cartons out, "
        f"{evidence['belt_travel_while_held_m']:.1f} m of belt ran under a raised blade")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Accumulation buffer",
    "task": ("Let cartons pile up behind the blade stop on a belt that "
             "never stops, then release a batch. The pot is a release "
             "window in ENCODER PULSES, which is a distance -- and this "
             "run changes the drive's top speed halfway through."),
    "build": AccumulationScene,
    "observe": None,
    "grade": grade_accumulation,
    "summary": _summary_accumulation,
    "duration": 80.0,
    "references": ("good", "timed"),
    "tags": ("buffer.run, buffer.speed, outfeed.rotate, emitter.emit, "
             "stop.raise, enc.reset, count_display.value, panel.green, "
             "panel.red are yours to write; buffer.actual, enc.count, "
             "stop.up, stop.down, exit_eye.detect, released.count, "
             "panel.setpoint and the buttons are the line's."),
}
