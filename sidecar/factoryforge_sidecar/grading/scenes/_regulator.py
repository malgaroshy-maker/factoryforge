"""The two regulators' shared half: the phase statistics, the rubric, the
feedback and the summary that `tank-level-control` and `heat-treat-station`
both mark on. A helper and not a scene: `grading.registry` skips every
module here whose name starts with an underscore.
"""

from __future__ import annotations

from ..core import GradedEngine, Report, Watched
from ..plant import PlantScene


# --- the two regulators -------------------------------------------------
#
# The tank and the oven are the same exercise against two different plants, and
# they are graded by the same three numbers, because "it reached the setpoint"
# is the claim both of them are easiest to fake.
#
# **Settled error.** The mean distance from the setpoint over the last seconds
# of a phase. Proportional control alone cannot make a standing output out of
# nothing, so it parks short of an oven's setpoint by an offset you can
# calculate from the plant -- and that offset is the whole reason integral
# action exists. Grading "did it get there" would pass it.
#
# **Ripple.** Peak to peak over the same window. An on/off controller does
# reach the setpoint. It reaches it every couple of seconds, from alternate
# sides, and a plant that loses heat to the room will do that forever. A mean
# error near zero says nothing about it and the peak-to-peak says everything.
#
# **Overshoot.** The furthest past the setpoint the measurement went on the way
# there. A controller that gets a beautiful settled number by slamming the
# plant to the far stop first is one that boils the tank dry on a real line.
#
# And the pot moves mid-run, to a second value drawn from the seed. Everything
# above can be had by a program that knows what number it is aiming at; none of
# it can be had by one that only knows the number it was written with.

#: Seconds at the end of a phase that count as "settled".
SETTLE_WINDOW = 8.0


class Regulator(PlantScene):
    """A single-measurement process with a setpoint on the panel's pot."""

    #: Filled in by the subclass: what the measurement is called, in words.
    measured = "the measurement"
    unit = ""

    def __init__(self, seed: int, setpoint: float = 0.0) -> None:
        super().__init__(seed, setpoint=setpoint)
        #: (sim time, measurement) every tick. Ground truth: no bus message
        #: reaches the trace, only the samples the controller happened to poll.
        self.trace: list[tuple[float, float]] = []
        #: {"setpoint", "from", "to"} per phase of the exam.
        self.phases: list[dict] = []

    def measure(self) -> float:                   # pragma: no cover - overridden
        raise NotImplementedError

    def _phase(self, setpoint: float, until: float):
        def do() -> None:
            if self.phases:
                self.phases[-1]["to"] = self.t
            self.panel.set_setpoint(setpoint)()
            self.phases.append({"setpoint": float(setpoint), "from": self.t,
                                "to": until})
        do.__name__ = f"setpoint {setpoint:g}"
        return do

    def record(self) -> None:
        self.trace.append((self.t, self.measure()))


def _phase_stats(trace: list[tuple[float, float]], phase: dict) -> dict:
    """Settled error, ripple and overshoot for one phase of a regulator run."""
    setpoint = phase["setpoint"]
    inside = [(t, v) for t, v in trace if phase["from"] <= t <= phase["to"]]
    if not inside:
        return {"setpoint": setpoint, "samples": 0, "settled_error": None,
                "ripple": None, "overshoot": None, "final": None}

    # The last phase of a run has no end until the run ends, so its nominal
    # `to` is far in the future. Clamped to the last sample, because the
    # settled window is measured backwards from the end: the first version of
    # this took "the last eight seconds" of a phase ending in the year 10000,
    # found nothing, and fell back to a single sample -- which has a settled
    # error of whatever that sample was and a ripple of exactly zero. Every
    # second-phase check in the run was being decided by one number.
    end = min(phase["to"], inside[-1][0])
    start = inside[0][1]
    approach = 1.0 if setpoint >= start else -1.0
    tail = [v for t, v in inside if t >= end - SETTLE_WINDOW]
    tail = tail or [inside[-1][1]]
    past = max((v - setpoint) * approach for _, v in inside)
    return {
        "setpoint": round(setpoint, 2),
        "samples": len(inside),
        "from": round(phase["from"], 1),
        "to": round(end, 1),
        "started_at": round(start, 2),
        "settled_error": round(sum(abs(v - setpoint) for v in tail) / len(tail), 2),
        "ripple": round(max(tail) - min(tail), 2),
        "overshoot": round(max(past, 0.0), 2),
        "final": round(inside[-1][1], 2),
    }


def grade_regulator(watched: Watched, engine: GradedEngine, report: Report,
                    duration: float, *, settled: float, ripple: float,
                    overshoot: float, moved: float) -> None:
    sim: Regulator = watched.inner
    stats = [_phase_stats(sim.trace, phase) for phase in sim.phases]
    values = [v for _, v in sim.trace]
    span = (max(values) - min(values)) if values else 0.0
    unit = sim.unit

    report.evidence.update({
        "phases": stats,
        "travel": round(span, 2),
        "limits": {"settled": settled, "ripple": ripple, "overshoot": overshoot},
        "trace": [[round(t, 1), round(v, 2)] for t, v in sim.trace[::50]],
    })

    # Gotcha 16, in its process-control form: every settling check below is
    # vacuously true of a plant that never left where it started.
    report.add("plant.moved",
               span >= moved,
               f"{sim.measured} travelled {span:.1f}{unit} over the run "
               f"(at least {moved:g}{unit} needed for the rest to mean anything)")

    for index, phase in enumerate(stats, start=1):
        sp = phase["setpoint"]
        if phase["settled_error"] is None:
            report.add(f"hold{index}.settled", False,
                       f"no samples in the phase at {sp:g}{unit}")
            continue
        report.add(f"hold{index}.settled",
                   phase["settled_error"] <= settled,
                   f"at {sp:g}{unit}: settled {phase['settled_error']:.1f}{unit} "
                   f"from setpoint over the last {SETTLE_WINDOW:g}s "
                   f"(at most {settled:g}{unit})")
        report.add(f"hold{index}.steady",
                   phase["ripple"] <= ripple,
                   f"at {sp:g}{unit}: {phase['ripple']:.1f}{unit} peak to peak "
                   f"while holding (at most {ripple:g}{unit})")
        report.add(f"hold{index}.overshoot",
                   phase["overshoot"] <= overshoot,
                   f"at {sp:g}{unit}: went {phase['overshoot']:.1f}{unit} past the "
                   f"setpoint on the way (at most {overshoot:g}{unit})")

    _regulator_feedback(report, sim, stats, settled, ripple, overshoot, span, moved)


def _regulator_feedback(report, sim, stats, settled, ripple, overshoot,
                        span, moved) -> None:
    say = report.feedback.append
    unit = sim.unit

    if span < moved:
        say(f"{sim.measured.capitalize()} barely moved ({span:.1f}{unit}). Nothing "
            f"below this line means anything until the plant is actually being "
            f"driven -- check that the run command and the actuator are both "
            f"getting written.")
        return

    # Said first, and once, because it explains every other number below it:
    # the pot moved a long way and the measurement did not follow.
    deaf = (len(stats) > 1 and stats[0]["final"] is not None
            and stats[1]["final"] is not None
            and abs(stats[1]["setpoint"] - stats[0]["setpoint"]) > 10.0
            and abs(stats[1]["final"] - stats[0]["final"]) < 5.0)
    if deaf:
        say(f"The pot went from {stats[0]['setpoint']:g}{unit} to "
            f"{stats[1]['setpoint']:g}{unit} and {sim.measured} stayed at "
            f"{stats[1]['final']:g}{unit}. That is a setpoint written into the "
            f"program rather than read off `panel.setpoint` -- read it every "
            f"scan, not once at startup, and turning the knob re-tunes the line "
            f"instead of needing a download.")

    for index, phase in enumerate(stats, start=1):
        if phase["settled_error"] is None:
            continue
        sp = phase["setpoint"]
        if deaf:
            continue
        if phase["ripple"] > ripple:
            say(f"At {sp:g}{unit} the measurement swung {phase['ripple']:.1f}{unit} "
                f"peak to peak. It reaches the setpoint -- from alternate sides, "
                f"forever. On/off is not control here: the actuator modulates, so "
                f"write it a number between 0 and 100 instead of an edge.")
        elif phase["settled_error"] > settled:
            say(f"At {sp:g}{unit} it parked {phase['settled_error']:.1f}{unit} off "
                f"and stayed there. An error that stops closing is a controller "
                f"with no way to produce output from a small error -- either a "
                f"deadband that stops it acting once it is near, or proportional "
                f"action on its own, whose output IS the error times the gain and "
                f"so cannot reach zero while the plant still needs an output. "
                f"Integral action is the term that supplies one out of nothing.")
        if phase["overshoot"] > overshoot:
            say(f"At {sp:g}{unit} it went {phase['overshoot']:.1f}{unit} past the "
                f"setpoint before coming back. Full output until the setpoint "
                f"arrives is a plant with no brakes; back the actuator off as the "
                f"error closes.")

    if len(stats) > 1 and stats[0]["settled_error"] is not None \
            and stats[1]["settled_error"] is not None \
            and stats[0]["settled_error"] <= settled < stats[1]["settled_error"]:
        say(f"The first setpoint was held and the second was not. `panel.setpoint` "
            f"is the pot, and this run turned it: read it every scan rather than "
            f"latching it at startup or writing the number into the program.")


def _summary_regulator(evidence: dict, out) -> None:
    for index, phase in enumerate(evidence["phases"], start=1):
        if phase["settled_error"] is None:
            out(f"hold {index}: setpoint {phase['setpoint']:g} — no samples")
            continue
        out(f"hold {index}: setpoint {phase['setpoint']:g}, ended {phase['final']:g}, "
            f"settled {phase['settled_error']:g} off, ripple {phase['ripple']:g}, "
            f"overshoot {phase['overshoot']:g}")
    out(f"measurement travelled {evidence['travel']:g} over the run")
