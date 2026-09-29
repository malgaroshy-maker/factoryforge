"""`star-delta-start`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/star_delta_start.py`.
"""

from __future__ import annotations

import math

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (OperatorExam, PlantScene, Script, declare_stack_light,
                     operator_exam_ends_by, pot_start)
from ..templates import template
from ._contract import mark_contract, summary_contract


SCENE = "star-delta-start"


# --- star-delta starter ---------------------------------------------------
#
# Observable fact: the speed the motor had when the delta contactor's contacts
# closed, and whether the star and delta contacts ever conducted at the same
# instant. The plant runs the three contactors in continuous time, exactly as
# `StarDeltaStarter.cs` does, so it knows both -- and neither is on the bus:
# `staraux` / `deltaaux` say where the contacts are *now*, not what they did
# between two polls.
#
# How a program fakes it: by changing over on a timer. A star run-up takes a
# fixed time only at a fixed load, and a timer calibrated on an empty machine
# changes over half way up the run-up of a loaded one -- which draws nearly the
# direct-on-line current the starter exists to avoid. So the exam loads the
# machine between its two starts (the template's `load_percent`, raised by the
# examiner's hand, as the inspector's "Motor Load" slider does in the engine).
# Nothing on the bus says so; only `speed` does.
#
# The second thing this exercise is about is the one every exam paper asks:
# star and delta together is a short circuit. It is the *contacts* that short,
# and contacts open slower than they close, so dropping star and energising
# delta in the same scan closes delta while star is still arcing, and the
# breaker trips. The model keeps each contactor as the interval its contacts
# conduct, as the C# part does, so a 15 ms overlap is seen at any tick size.
#
# Everything below is `engine/src/Parts/StarDeltaStarter.cs` and
# `ThermalOverload.cs`; the motor's rating, its load and its inertia are the
# template's (IP-19).
_PLANT = template(SCENE)
_MOTOR = _PLANT.part("motor", "StarDeltaStarter").engineering_units()

SD_FLA = _MOTOR.number("fla")
SD_LOAD_FIRST = _MOTOR.number("load_percent")
SD_INERTIA = _MOTOR.number("inertia")
#: The exam's loading of the machine between its two starts, in percent of
#: rated torque, drawn from the seed. The examiner's numbers, not the
#: template's: the empty machine reaches the pot's 85 % in star in 2.7 s, and
#: these take 4.7, 5.2 and 6.0 s -- and star can still carry each of them past
#: 89 %, so the pot stays reachable.
SD_LOADS_THEN = (85.0, 90.0, 95.0)
SD_POT_START = pot_start(_PLANT)

#: `StarDeltaStarter.cs` owns these; no template sets them. Contactor timing
#: (`PullInTime` :74, `DropOutTime` :79), the overload (`OverloadSetting` :83,
#: `OverloadClass` :86) and the motor's per-unit curve (:89-:96).
SD_PULL_IN = 0.030
SD_DROP_OUT = 0.045
SD_OVERLOAD_SETTING = 1.15
SD_OVERLOAD_CLASS = 10.0
SD_LOCKED_ROTOR_CURRENT = 6.0
SD_MAGNETISING_CURRENT = 0.35
SD_CURRENT_KNEE_SLIP = 0.24
SD_RATED_SLIP = 0.04
SD_STARTING_TORQUE = 2.0
SD_PULL_OUT_TORQUE = 2.6
SD_PULL_OUT_SPEED = 0.85
SD_FRICTION_TORQUE = 0.02
#: `ThermalOverload.cs` (`ClassMultiple` :24).
SD_CLASS_MULTIPLE = 6.0

#: How far under the pot a changeover may land and still count as "at speed",
#: in percent. A program that reads `speed` changes over on the first scan past
#: the pot; the motor gains well under a percent per scan near the top of a
#: star run-up.
SD_CHANGEOVER_SLACK = 3.0
#: A delta running motor that is "running" for `motor.ran`.
SD_RUNNING_SPEED = 90.0
#: Stop has to open the main contactor within this, the scan it takes to see
#: the press plus the contacts' own drop-out.
SD_STOP_LIMIT = 0.3

# --- the operator contract (IP-12) ------------------------------------------
#
# After the two starts, the examiner closes the breaker if a start tripped it
# (as it does before the second), presses Start a third time and puts the
# E-stop sheet (`plant.OperatorExam`) to the running motor. "Stopped" is the
# main contacts no longer conducting -- the motor de-energised; it coasts down
# on its own inertia, which is the machine's and not the program's. The
# starts, changeovers and stops are marked on the first `SD_TESTS_END`
# seconds, exactly as before; the third start is the E-stop's.

#: Where the window used to end.
SD_TESTS_END = 42.0
SD_BREAKER_AGAIN_AT = SD_TESTS_END + 0.2
SD_START_AGAIN_AT = SD_TESTS_END + 0.5
SD_ESTOP_AT = SD_TESTS_END + 1.0
SD_EXAM_ENDS_BY = operator_exam_ends_by(SD_ESTOP_AT)


class Contactor:
    """One contactor as the interval its main contacts conduct
    (`StarDeltaStarter.cs`, `Contactor`, :111). Continuous time, because what
    trips the breaker is two intervals overlapping, and a same-scan changeover
    overlaps for 15 ms -- less than a tick."""

    def __init__(self) -> None:
        self.coil = False
        self.conducts_from = math.inf
        self.conducts_until = math.inf

    def conducting_at(self, t: float) -> bool:
        return self.conducts_from <= t < self.conducts_until

    def command(self, coil: bool, now: float) -> None:
        if coil == self.coil:
            return
        self.coil = coil
        if coil:
            if self.conducting_at(now):
                self.conducts_until = math.inf
            else:
                self.conducts_from = now + SD_PULL_IN
                self.conducts_until = math.inf
        elif self.conducts_from <= now:
            self.conducts_until = now + SD_DROP_OUT
        else:
            self.conducts_from = math.inf
            self.conducts_until = math.inf


def _overlapped(a: Contactor, b: Contactor, t0: float, t1: float) -> bool:
    """`Overlapped` (:155): did the two conduct at one instant in (t0, t1]."""
    start = max(a.conducts_from, b.conducts_from, t0)
    end = min(a.conducts_until, b.conducts_until, t1 + 1e-9)
    return start < end


def _delta_torque(speed: float) -> float:
    """`DeltaTorque` (:415), per unit of rated."""
    low = SD_STARTING_TORQUE + (SD_PULL_OUT_TORQUE - SD_STARTING_TORQUE) * speed / SD_PULL_OUT_SPEED
    high = (1.0 - speed) / SD_RATED_SLIP
    return max(min(low, high), 0.0)


def _delta_current(slip: float) -> float:
    """`DeltaCurrent` (:425), per unit of full-load line current."""
    rotor = SD_LOCKED_ROTOR_CURRENT * slip / math.sqrt(slip * slip + SD_CURRENT_KNEE_SLIP ** 2)
    return math.sqrt(SD_MAGNETISING_CURRENT ** 2 + rotor * rotor)


class Overload:
    """`ThermalOverload.cs` (`Step` :44): heats as (I/Itrip)^2 - 1, cools at a
    third of that rate, trips latched at 1."""

    def __init__(self) -> None:
        self.state = 0.0
        self.tripped = False

    def step(self, current: float, trip_amps: float, trip_time: float, dt: float) -> bool:
        ratio = current / max(trip_amps, 0.01)
        scale = (SD_CLASS_MULTIPLE ** 2 - 1.0) * max(trip_time, 0.05)
        rate = (ratio * ratio - 1.0) / scale
        if rate < 0.0:
            rate /= 3.0
        self.state = min(max(self.state + rate * dt, 0.0), 1.0)
        if self.state < 1.0 or self.tripped:
            return False
        self.tripped = True
        return True

    def reset(self) -> None:
        self.tripped = False
        self.state = 0.0


class StarDeltaScene(PlantScene):
    name = "star-delta-start"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=SD_POT_START)
        self._declare(
            Tag("motor.main", "Star-Delta 0 Main Contactor Coil", "bit", "output"),
            Tag("motor.star", "Star-Delta 0 Star Contactor Coil", "bit", "output"),
            Tag("motor.delta", "Star-Delta 0 Delta Contactor Coil", "bit", "output"),
            Tag("motor.mainaux", "Star-Delta 0 Main Aux Contact", "bit", "input"),
            Tag("motor.staraux", "Star-Delta 0 Star Aux Contact", "bit", "input"),
            Tag("motor.deltaaux", "Star-Delta 0 Delta Aux Contact", "bit", "input"),
            Tag("motor.breaker", "Star-Delta 0 Breaker OK (NC)", "bit", "input", value=True),
            Tag("motor.overload", "Star-Delta 0 Overload OK (NC)", "bit", "input", value=True),
            Tag("motor.speed", "Star-Delta 0 Motor Speed (%)", "float", "input"),
            Tag("motor.current", "Star-Delta 0 Line Current (A)", "float", "input"),
            Tag("current_gauge.value", "Gauge 0 Value", "float", "output"),
        )
        declare_stack_light(self.tags)

        self.load = SD_LOAD_FIRST
        self.load_then = float(self.rng.choice(SD_LOADS_THEN))
        self.clock = 0.0
        self.main, self.star, self.delta = Contactor(), Contactor(), Contactor()
        self.overload = Overload()
        self.breaker_tripped = False
        self.speed = 0.0
        self.current = 0.0

        # --- ground truth ---
        #: One entry per Start the examiner pressed.
        self.starts: list[dict] = []
        #: Every time the delta contacts began conducting with the main in:
        #: when, how fast the motor was turning, and the worst line current in
        #: the second after it.
        self.changeovers: list[dict] = []
        #: Sim times the breaker tripped on a star-delta short.
        self.shorts: list[float] = []
        self.overload_trips: list[float] = []
        #: One entry per Stop: when, and when the main contacts stopped
        #: conducting.
        self.stops: list[dict] = []
        self._was_delta = False
        self._was_main = False

        self.script = Script([
            (1.0, self._start("light")),
            (12.0, self._stop),
            (12.5, self._load_the_machine),
            (19.0, self._close_the_breaker),
            (20.0, self._start("loaded")),
            (38.0, self._stop),
            (SD_BREAKER_AGAIN_AT, self._close_the_breaker),
            (SD_START_AGAIN_AT, self.panel.press("start")),
        ])
        self.operator = OperatorExam(self, SD_ESTOP_AT, noun="motor", what="the motor")

    # --- the examiner ---

    def _start(self, which: str):
        def do() -> None:
            self.panel.press("start")()
            self.starts.append({"name": which, "at": self.t, "load": self.load,
                                "from_speed": round(self.speed * 100.0, 1),
                                "top_speed": 0.0, "reached_delta_at": None})
        do.__name__ = f"start the {which} machine"
        return do

    def _stop(self) -> None:
        self.panel.press("stop")()
        self.stops.append({"at": self.t, "main_open_at": None})

    def _load_the_machine(self) -> None:
        """What the "Motor Load" slider does in the engine: the machine the
        motor drives is heavier. No tag reports it."""
        self.load = self.load_then

    def _close_the_breaker(self) -> None:
        """A person at the panel. If the first start tripped the breaker, it is
        closed again (and the overload reset) so the second start is sat; the
        click on the starter does exactly this in the engine
        (`ResetBreaker`, :501)."""
        if self.breaker_tripped or self.overload.tripped:
            self.breaker_tripped = False
            self.overload.reset()

    # --- the plant: `StarDeltaStarter.Step` (:440) ---

    def step(self, dt: float) -> None:
        fed = not self.overload.tripped
        t0 = self.clock
        self.main.command(self.bit("motor.main") and fed, t0)
        self.star.command(self.bit("motor.star") and fed, t0)
        self.delta.command(self.bit("motor.delta") and fed, t0)
        self.clock += dt
        now = self.clock

        if not self.breaker_tripped and _overlapped(self.star, self.delta, t0, now):
            self.breaker_tripped = True
            self.shorts.append(round(self.t, 3))

        main = self.main.conducting_at(now)
        star_c = self.star.conducting_at(now)
        delta_c = self.delta.conducting_at(now)
        supplied = not self.breaker_tripped and main
        self.operator.driven = supplied
        star = supplied and star_c and not delta_c
        delta_run = supplied and delta_c and not star_c

        slip = 1.0 - self.speed
        torque = line = 0.0
        if delta_run:
            torque, line = _delta_torque(self.speed), _delta_current(slip)
        elif star:
            torque, line = _delta_torque(self.speed) / 3.0, _delta_current(slip) / 3.0
        relative = self.speed / (1.0 - SD_RATED_SLIP)
        load = max(self.load, 0.0) / 100.0 * (0.2 + 0.8 * relative * relative)
        net = torque - load - SD_FRICTION_TORQUE
        self.speed = min(max(self.speed + net / max(SD_INERTIA, 0.05) * dt, 0.0), 1.0)
        self.current = line * SD_FLA

        winding = self.current if star else self.current / math.sqrt(3.0)
        trip_amps = SD_FLA / math.sqrt(3.0) * SD_OVERLOAD_SETTING
        if self.overload.step(winding, trip_amps, SD_OVERLOAD_CLASS, dt):
            self.overload_trips.append(round(self.t, 3))

        self._record(main, delta_c)

        self.tags.set("motor.mainaux", main)
        self.tags.set("motor.staraux", star_c)
        self.tags.set("motor.deltaaux", delta_c)
        self.tags.set("motor.breaker", not self.breaker_tripped)
        self.tags.set("motor.overload", not self.overload.tripped)
        self.tags.set("motor.speed", self.speed * 100.0)
        self.tags.set("motor.current", self.current)

    def _record(self, main: bool, delta: bool) -> None:
        if self.t > SD_TESTS_END:
            return
        speed = self.speed * 100.0
        if self.starts:
            start = self.starts[-1]
            start["top_speed"] = max(start["top_speed"], speed)
            if (delta and main and speed >= SD_RUNNING_SPEED
                    and start["reached_delta_at"] is None):
                start["reached_delta_at"] = round(self.t, 2)
        if delta and main and not (self._was_delta and self._was_main):
            self.changeovers.append({"at": round(self.t, 3),
                                     "speed": round(speed, 1),
                                     "peak_current": round(self.current, 1),
                                     "start": len(self.starts)})
        if self.changeovers and self.t - self.changeovers[-1]["at"] <= 1.0:
            last = self.changeovers[-1]
            last["peak_current"] = round(max(last["peak_current"], self.current), 1)
        if self.stops and self.stops[-1]["main_open_at"] is None and not main:
            self.stops[-1]["main_open_at"] = round(self.t, 3)
        self._was_delta, self._was_main = delta, main


def grade_star_delta(watched: Watched, engine: GradedEngine, report: Report,
                     duration: float) -> None:
    sim: StarDeltaScene = watched.inner
    pot = sim.panel.setpoint_value
    floor = pot - SD_CHANGEOVER_SLACK
    early = [c for c in sim.changeovers if c["speed"] < floor]
    slow_stops = [s for s in sim.stops
                  if s["main_open_at"] is None or s["main_open_at"] - s["at"] > SD_STOP_LIMIT]

    report.evidence.update({
        "pot_percent": pot,
        "starts": [{"name": s["name"], "at": round(s["at"], 2), "load_percent": s["load"],
                    "from_speed": s["from_speed"], "top_speed": round(s["top_speed"], 1),
                    "reached_delta_at": s["reached_delta_at"]} for s in sim.starts],
        "changeovers": sim.changeovers,
        "shorts_at": sim.shorts,
        "overload_trips_at": sim.overload_trips,
        "stops": sim.stops,
        "full_load_amps": SD_FLA,
    })

    ran = [s for s in sim.starts if s["reached_delta_at"] is not None]
    report.add("motor.ran",
               len(sim.starts) >= 2 and len(ran) == len(sim.starts),
               f"{len(ran)} of {len(sim.starts)} starts ran up to {SD_RUNNING_SPEED:g} % "
               f"in delta")
    report.add("changeover.no_short",
               not sim.shorts,
               "the star and delta contacts never conducted together"
               if not sim.shorts else
               f"star and delta conducted together and the breaker tripped "
               f"{len(sim.shorts)} time(s), at {sim.shorts}s")
    report.add("changeover.at_speed",
               bool(sim.changeovers) and not early,
               (f"every changeover to delta came at {floor:g} % or more, with the pot at "
                f"{pot:g} %: " + ", ".join(f"{c['speed']:g} %" for c in sim.changeovers))
               if sim.changeovers and not early else
               ("delta never closed" if not sim.changeovers else
                f"{len(early)} changeover(s) came early, at "
                + ", ".join(f"{c['speed']:g} % ({c['peak_current']:g} A)" for c in early)
                + f", against a pot of {pot:g} %"))
    report.add("stop.dropped_the_main",
               bool(sim.stops) and not slow_stops,
               f"every Stop opened the main contactor within {SD_STOP_LIMIT * 1000:.0f} ms"
               if sim.stops and not slow_stops else
               f"{len(slow_stops)} Stop press(es) left the main contactor in past "
               f"{SD_STOP_LIMIT * 1000:.0f} ms")

    _star_delta_feedback(report, sim, pot, early)
    mark_contract(sim.operator, report, watched.sim_time)


def _star_delta_feedback(report, sim, pot, early) -> None:
    say = report.feedback.append
    if not sim.starts or all(s["top_speed"] < 5.0 for s in sim.starts):
        say("The motor never turned. Start is `motor.main` with `motor.star`: the "
            "main contactor feeds the windings, the star contactor ties their far "
            "ends together, and the motor runs up on a third of its torque.")
        return
    if sim.shorts:
        say(f"The breaker tripped at {sim.shorts[0]}s: star and delta conducted at "
            f"the same instant, which is a three-phase short across the supply. "
            f"Contacts close in {SD_PULL_IN * 1000:.0f} ms and take "
            f"{SD_DROP_OUT * 1000:.0f} ms to stop conducting, so dropping "
            f"`motor.star` and energising `motor.delta` in one scan closes delta "
            f"while star is still arcing. Wait for `motor.staraux` to fall before "
            f"energising delta -- that dead time is what a star-delta timing relay "
            f"has built in.")
    if early:
        first = early[0]
        loads = {s["name"]: s["load"] for s in sim.starts}
        say(f"Delta closed at {first['speed']:g} % speed and drew {first['peak_current']:g} A "
            f"-- {first['peak_current'] / SD_FLA:.1f} times full load -- where the pot "
            f"asked for {pot:g} %. The machine was loaded to {sim.load_then:g} % between "
            f"the two starts (it ran at {loads.get('light', SD_LOAD_FIRST):g} % first), "
            f"so its star run-up took about twice as long. A changeover timed in "
            f"seconds is a measurement of one load on one day; change over when "
            f"`motor.speed` reaches the pot.")
    if any(s["main_open_at"] is None for s in sim.stops):
        say("Stop did not open the main contactor. Stop drops all three coils.")
    if sim.overload_trips:
        say(f"The thermal overload tripped at {sim.overload_trips[0]}s. In star the "
            f"winding carries the whole line current; a motor left in star too long, "
            f"or stalled, heats it.")
    if (not sim.shorts and not early and sim.changeovers
            and all(s["reached_delta_at"] is not None for s in sim.starts)):
        say(f"Clean: {len(sim.changeovers)} changeovers, each at the pot's speed, with "
            f"the load at {SD_LOAD_FIRST:g} % and at {sim.load_then:g} %, and star and "
            f"delta never together.")


def _summary_star_delta(evidence: dict, out) -> None:
    out(f"pot: change over at {evidence['pot_percent']:g} % speed")
    for start in evidence["starts"]:
        out(f"{start['name']:>7} start at {start['at']:g}s, load {start['load_percent']:g} %: "
            f"top speed {start['top_speed']:g} %"
            + (f", running in delta by {start['reached_delta_at']:g}s"
               if start["reached_delta_at"] is not None else ", never ran in delta"))
    for c in evidence["changeovers"]:
        out(f"changeover at {c['at']:g}s: {c['speed']:g} % speed, {c['peak_current']:g} A peak")
    shorts = evidence["shorts_at"]
    out(f"star-delta shorts: {shorts if shorts else 'none'}")
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Star-delta starter",
    "task": ("Start the motor in star, change over to delta when it is up to "
             "the speed on the pot, and never let star and delta conduct "
             "together. This run loads the machine between its two starts. "
             "The mushroom is normally closed, opens the main contactor within "
             "200 ms and latches -- only Reset, then Start, runs the motor "
             "again."),
    "build": StarDeltaScene,
    "observe": None,
    "grade": grade_star_delta,
    "summary": _summary_star_delta,
    "duration": SD_EXAM_ENDS_BY,
    "references": ("good", "samescan", "timed", "noestop", "startalone"),
    "tags": ("motor.main, motor.star, motor.delta, current_gauge.value, "
             "tower.green/yellow/red, panel.green, panel.red are yours to "
             "write; motor.mainaux, motor.staraux, motor.deltaaux, "
             "motor.breaker, motor.overload, motor.speed, motor.current, "
             "panel.setpoint and the buttons are the plant's."),
}
