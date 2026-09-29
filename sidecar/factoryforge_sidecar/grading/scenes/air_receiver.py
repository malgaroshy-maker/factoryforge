"""`air-receiver`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/air_receiver.py`.
"""

from __future__ import annotations

import math

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import OperatorExam, PlantScene, Script, operator_exam_ends_by, pot_start
from ..templates import TemplateError, template
from ._contract import mark_contract, summary_contract


SCENE = "air-receiver"


# --- air receiver ---------------------------------------------------------
#
# Observable facts: the receiver's pressure in bar, which is not on the bus --
# the bus carries a 4-20 mA card's raw count -- and the isolation valve's stem
# position, which is not on the bus either: `opened` and `closed` are two limit
# switches with a gap between them.
#
# The lesson is the one both parts exist for: a PLC sees counts and contacts,
# never bar and "open". Scale the transmitter's count the way the card defines
# it (0 at 4 mA, 27648 at 20 mA, `NORM_X` then `SCALE_X`) or the band you hold
# is somewhere else; and prove the valve by its feedback, allowing it the time
# it takes to travel and not a moment more, or a stuck valve goes unnoticed --
# or a valve still travelling is called stuck.
#
# How a program fakes it:
#
# * scaling that is nearly right. Dividing by 32767 instead of 27648 reads
#   16 % low, holds the receiver 16 % high, and looks fine on a trend nobody
#   has calibrated. The plant marks the true pressure against the pot's band,
#   and moves both the load (consumption) and the pot mid-run.
# * no discrepancy check. Nothing on the bus says the valve has seized, so the
#   exam seizes it: the station is stopped, the valve closes, it is seized
#   shut, and Start is pressed. The alarm has to come within the valve's
#   travel time plus a second.
# * a discrepancy check with no timer. `opened` is false for the whole two
#   seconds a healthy valve takes to open; a program that alarms on "commanded
#   and not opened" alarms on every start. The first start is a healthy one.
#
# The receiver is `engine/src/Parts/PressureTransmitter.cs` (`Step` :256), the
# count is `AnalogSignal.ToRaw` (:168), the valve `SolenoidValve.cs` (`Step`
# :309). Supply pressure, consumption, the loop's range and the valve's
# travel time are the template's.
_PLANT = template(SCENE)
_RECEIVER = _PLANT.part("receiver", "PressureTransmitter")
_VALVE = _PLANT.part("valve", "SolenoidValve")

AR_SUPPLY = _RECEIVER.number("supply_pressure")
AR_CONSUMPTION_FIRST = _RECEIVER.number("consumption")
AR_RANGE_MIN = _RECEIVER.number("range_min")
AR_RANGE_MAX = _RECEIVER.number("range_max")
if _RECEIVER.properties.get("signal") != "ma_4_20":
    raise TemplateError(f"{_RECEIVER.source}: the receiver's transmitter is wired "
                        f"{_RECEIVER.properties.get('signal')!r}; the grader's model of "
                        f"it publishes a 4-20 mA card's count")
AR_TRAVEL = _VALVE.number("travel_time")
AR_POT_START = pot_start(_PLANT)

#: `PressureTransmitter.cs` (`SupplyRate` :49, `ConsumptionRate` :52).
AR_SUPPLY_RATE = 0.5
AR_CONSUMPTION_RATE = 0.5
#: `AnalogSignal.cs` (:76, :79, :82, :85, :88): the S7 analog value table.
AR_FULL_SCALE = 27648
AR_OVERRANGE = 32511
AR_OVERFLOW = 32767
AR_UNDERRANGE = -4864
AR_UNDERFLOW = -32768
#: `SolenoidValve.cs` (`SwitchBand` :44): a limit switch makes within this
#: fraction of the stroke of its end.
AR_SWITCH_BAND = 0.02

#: The brief's band: load at the pot less this, unload at the pot.
AR_BAND = 0.5
#: How far outside that band the true pressure may stray and still be held --
#: a scan of overshoot at the fastest the receiver moves.
AR_BAND_SLACK = 0.25
#: The exam: the plant downstream draws harder, the pot moves, the station is
#: stopped, the valve seizes shut, and Start is pressed again.
AR_CONSUMPTION_THEN = 50.0
AR_LOAD_AT = 25.0
AR_POT_AT = 40.0
AR_POT_THEN = (4.5, 5.0, 5.5)
AR_STOP_AT = 55.0
AR_SEIZE_AT = 58.0
AR_RESTART_AT = 60.0
AR_FREE_AT = 66.0
#: When the band is marked: from a settle after Start until the Stop, less a
#: settle after each thing the exam changes.
AR_SETTLE = 5.0
#: The alarm's deadline after the command that the seized valve ignores.
AR_ALARM_WITHIN = AR_TRAVEL + 1.0

# --- the operator contract (IP-12) ------------------------------------------
#
# After the seized valve's test, with the station running again since the
# Reset and Start at AR_FREE_AT + 2, the E-stop sheet (`plant.OperatorExam`).
# "Stopped" is the station's two outputs dropped: the supply valve shut, so
# the receiver is not loading, and the isolation valve not commanded open --
# its spring closes it over the travel time, which is the valve's own doing.

#: Where the window used to end, and the E-stop test after it.
AR_TESTS_END = 75.0
AR_ESTOP_AT = AR_TESTS_END + 0.5
AR_EXAM_ENDS_BY = operator_exam_ends_by(AR_ESTOP_AT)


def to_raw(engineering: float, low: float, high: float) -> int:
    """`AnalogSignal.ToRaw` (:168): the S7-1500 analog value table."""
    span = high - low
    if not span > 1e-9 or not math.isfinite(engineering):
        return AR_OVERFLOW
    exact = AR_FULL_SCALE * (engineering - low) / span
    counts = math.floor(abs(exact) + 0.5) * (1 if exact >= 0 else -1)
    if counts > AR_OVERRANGE:
        return AR_OVERFLOW
    if counts < AR_UNDERRANGE:
        return AR_UNDERFLOW
    return int(counts)


class AirReceiverScene(PlantScene):
    name = "air-receiver"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=AR_POT_START)
        self._declare(
            Tag("receiver.supply", "Receiver 0 Supply Valve", "bit", "output"),
            Tag("receiver.pressure", "Receiver 0 Pressure (4-20 mA raw)", "int", "input"),
            Tag("receiver.wirebreak", "Receiver 0 Pressure Wire Break", "bit", "input"),
            Tag("valve.open", "Valve 0 Solenoid (Open)", "bit", "output"),
            Tag("valve.opened", "Valve 0 Opened Feedback", "bit", "input"),
            Tag("valve.closed", "Valve 0 Closed Feedback", "bit", "input", value=True),
            Tag("valve.fault", "Valve 0 Stuck", "bit", "input"),
            Tag("pressure_gauge.value", "Gauge 0 Value", "float", "output"),
            Tag("alarm.beacon", "Beacon 0 Light", "bit", "output"),
            Tag("alarm.horn", "Beacon 0 Horn", "bit", "output"),
        )
        self.pressure = 0.0
        self.consumption = AR_CONSUMPTION_FIRST
        self.opening = 0.0
        self.seized = False
        self.pot_then = float(self.rng.choice(AR_POT_THEN))

        # --- ground truth ---
        #: (sim time, true bar, pot) while the band is being marked.
        self.band_trace: list[tuple[float, float, float]] = []
        self.gauge_error_sum = 0.0
        self.gauge_samples = 0
        self.max_pressure = 0.0
        #: When the valve was commanded open while seized shut, and when the
        #: beacon first came on after that.
        self.stuck_command_at: float | None = None
        self.alarm_after_stuck_at: float | None = None
        #: Beacon on before the valve had seized, with the valve healthy.
        self.false_alarms: list[float] = []
        self._beacon = False
        self._quiet_since: list[float] = []

        self.script = Script([
            (1.0, self.panel.press("start")),
            (AR_LOAD_AT, self._draw_harder),
            (AR_POT_AT, self._move_the_pot),
            (AR_STOP_AT, self.panel.press("stop")),
            (AR_SEIZE_AT, self._seize_the_valve),
            (AR_RESTART_AT, self.panel.press("start")),
            (AR_FREE_AT, self._free_the_valve),
            (AR_FREE_AT + 1.0, self.panel.press("reset")),
            (AR_FREE_AT + 2.0, self.panel.press("start")),
        ])
        self.operator = OperatorExam(self, AR_ESTOP_AT, noun="station",
                                     what="the station")

    # --- the examiner ---

    def _draw_harder(self) -> None:
        """What the receiver's "Consumption" slider does in the engine: the
        plant downstream draws harder. No tag says so."""
        self.consumption = AR_CONSUMPTION_THEN

    def _move_the_pot(self) -> None:
        self.panel.set_setpoint(self.pot_then)()

    def _seize_the_valve(self) -> None:
        """The fault tool on the valve, as in the engine: `valve.fault` is an
        input nothing computes."""
        self.seized = True

    def _free_the_valve(self) -> None:
        self.seized = False

    def _marking_band(self) -> bool:
        t = self.t
        if not (1.0 + AR_SETTLE <= t < AR_STOP_AT):
            return False
        return not any(at <= t < at + AR_SETTLE for at in (AR_LOAD_AT, AR_POT_AT))

    # --- the plant ---

    def step(self, dt: float) -> None:
        # The valve: `SolenoidValve.Step`, spring return, seized in place.
        command = self.bit("valve.open")
        if not self.seized:
            rate = 1.0 / max(AR_TRAVEL, 0.05)
            target = 1.0 if command else 0.0
            self.opening += max(min(target - self.opening, rate * dt), -rate * dt)
        opened = self.opening >= 1.0 - AR_SWITCH_BAND
        closed = self.opening <= AR_SWITCH_BAND
        self.tags.set("valve.fault", self.seized)
        self.tags.set("valve.opened", opened)
        self.tags.set("valve.closed", closed)

        # The receiver: `PressureTransmitter.Step`.
        supply = self.bit("receiver.supply")
        self.operator.driven = supply or command
        inflow = AR_SUPPLY_RATE * max(AR_SUPPLY - self.pressure, 0.0) if supply else 0.0
        outflow = AR_CONSUMPTION_RATE * max(self.consumption, 0.0) / 100.0 * self.pressure
        self.pressure = max(self.pressure + (inflow - outflow) * dt, 0.0)
        self.max_pressure = max(self.max_pressure, self.pressure)
        broken = self.bit("receiver.wirebreak")
        self.tags.set("receiver.pressure",
                      AR_OVERFLOW if broken else to_raw(self.pressure, AR_RANGE_MIN, AR_RANGE_MAX))

        pot = self.panel.setpoint_value
        if self._marking_band():
            self.band_trace.append((self.t, self.pressure, pot))
            self.gauge_error_sum += abs(self.num("pressure_gauge.value") - self.pressure)
            self.gauge_samples += 1

        beacon = self.bit("alarm.beacon")
        if self.seized and command and self.stuck_command_at is None:
            self.stuck_command_at = round(self.t, 2)
        if beacon and self.stuck_command_at is not None and self.alarm_after_stuck_at is None:
            self.alarm_after_stuck_at = round(self.t, 2)
        if beacon and not self._beacon and self.t < AR_SEIZE_AT:
            self.false_alarms.append(round(self.t, 2))
        self._beacon = beacon


def grade_air_receiver(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: AirReceiverScene = watched.inner
    outside = [(t, p, pot) for t, p, pot in sim.band_trace
               if not pot - AR_BAND - AR_BAND_SLACK <= p <= pot + AR_BAND_SLACK]
    lows = [p for _, p, _ in sim.band_trace]
    worst = max(outside, key=lambda s: max(s[1] - s[2], s[2] - AR_BAND - s[1]), default=None)
    late = (None if sim.stuck_command_at is None or sim.alarm_after_stuck_at is None
            else round(sim.alarm_after_stuck_at - sim.stuck_command_at, 2))
    gauge_error = (sim.gauge_error_sum / sim.gauge_samples) if sim.gauge_samples else None

    report.evidence.update({
        "pot_first_bar": AR_POT_START,
        "pot_then_bar": sim.pot_then,
        "consumption_percent": [AR_CONSUMPTION_FIRST, AR_CONSUMPTION_THEN],
        "band_samples": len(sim.band_trace),
        "outside_band_s": round(len(outside) * 0.01, 2),
        "worst_outside": None if worst is None else
        {"at": round(worst[0], 2), "bar": round(worst[1], 2), "pot": worst[2]},
        "lowest_bar": round(min(lows), 2) if lows else None,
        "highest_bar": round(max(lows), 2) if lows else None,
        "max_pressure_bar": round(sim.max_pressure, 2),
        "gauge_mean_error_bar": None if gauge_error is None else round(gauge_error, 3),
        "stuck_command_at": sim.stuck_command_at,
        "alarm_after_stuck_at": sim.alarm_after_stuck_at,
        "alarm_took_s": late,
        "alarm_within_s": AR_ALARM_WITHIN,
        "false_alarms_at": sim.false_alarms[:10],
    })

    report.add("receiver.loaded",
               bool(sim.band_trace) and max(lows) >= AR_POT_START - AR_BAND - AR_BAND_SLACK,
               f"the receiver reached {max(lows) if lows else 0.0:.2f} bar while the "
               f"band was being marked")
    report.add("receiver.held_the_band",
               bool(sim.band_trace) and not outside,
               f"the receiver stayed within pot - {AR_BAND:g} .. pot bar (+/- "
               f"{AR_BAND_SLACK:g}) for all {len(sim.band_trace) * 0.01:.0f}s marked"
               if sim.band_trace and not outside else
               (f"the receiver was outside its band for {len(outside) * 0.01:.1f}s; worst "
                f"{worst[1]:.2f} bar with the pot at {worst[2]:g}" if worst else
                "the band was never marked"))
    report.add("valve.stuck_was_caught",
               late is not None and late <= AR_ALARM_WITHIN,
               (f"the valve was commanded open at {sim.stuck_command_at:g}s while seized "
                f"shut, and the alarm came {late:.2f}s later (at most "
                f"{AR_ALARM_WITHIN:g}s)") if late is not None else
               (f"the valve was commanded open at {sim.stuck_command_at}s while seized "
                f"shut, and the alarm never came" if sim.stuck_command_at is not None else
                "the valve was never commanded open while it was seized"))
    report.add("valve.no_false_alarm",
               not sim.false_alarms,
               "the alarm never came on while the valve was healthy"
               if not sim.false_alarms else
               f"the alarm came on {len(sim.false_alarms)} time(s) with a healthy valve, "
               f"first at {sim.false_alarms[0]:g}s")

    _air_feedback(report, sim, outside, worst, gauge_error)
    mark_contract(sim.operator, report, watched.sim_time)


def _air_feedback(report, sim, outside, worst, gauge_error) -> None:
    say = report.feedback.append
    if not sim.band_trace or max(p for _, p, _ in sim.band_trace) < 1.0:
        say("The receiver never charged. `receiver.supply` opens its supply valve; "
            "`receiver.pressure` is a raw count from a 4-20 mA card, 0 at 4 mA and "
            "27648 at 20 mA.")
        return
    if worst is not None:
        ratio = worst[1] / worst[2] if worst[2] else 0.0
        say(f"The receiver sat at {worst[1]:.2f} bar with the pot at {worst[2]:g}. "
            + (f"That is {ratio:.2f} times the pot -- the ratio 32767/27648 is 1.19, "
               f"which is scaling by 32767. The card's full scale is 27648 counts: "
               f"`NORM_X(MIN := 0, VALUE := raw, MAX := 27648)`, then `SCALE_X` to "
               f"{AR_RANGE_MIN:g}..{AR_RANGE_MAX:g} bar."
               if 1.1 < ratio < 1.3 else
               f"Load at the pot less {AR_BAND:g} bar and unload at the pot, reading "
               f"the pot every scan -- this run raises the consumption and then moves "
               f"the pot."))
    if sim.stuck_command_at is not None and (sim.alarm_after_stuck_at is None or
                                             sim.alarm_after_stuck_at - sim.stuck_command_at
                                             > AR_ALARM_WITHIN):
        say(f"The valve was seized shut when Start was pressed at "
            f"{sim.stuck_command_at:g}s, and `valve.opened` never came -- nothing "
            f"else on the bus says so. Start a timer when you command the valve; if "
            f"the feedback has not arrived when it runs out ({AR_TRAVEL:g}s of travel "
            f"plus a margin), the valve has failed: alarm and stop.")
    if sim.false_alarms:
        say(f"The alarm came on at {sim.false_alarms[0]:g}s with a healthy valve. "
            f"A valve takes {AR_TRAVEL:g}s to travel, and for all of it neither "
            f"`opened` nor `closed` is made -- 'commanded and not opened' is true "
            f"on every start. The discrepancy is only a fault once the travel time "
            f"has run out.")


def _summary_air(evidence: dict, out) -> None:
    out(f"pot {evidence['pot_first_bar']:g} bar, then {evidence['pot_then_bar']:g}; "
        f"consumption {evidence['consumption_percent'][0]:g} % then "
        f"{evidence['consumption_percent'][1]:g} %")
    out(f"receiver held {evidence['lowest_bar']}..{evidence['highest_bar']} bar while marked, "
        f"{evidence['outside_band_s']:g}s outside the band; gauge off by "
        f"{evidence['gauge_mean_error_bar']} bar on average")
    out(f"valve seized, commanded at {evidence['stuck_command_at']}s, alarm at "
        f"{evidence['alarm_after_stuck_at']}s; false alarms: "
        f"{evidence['false_alarms_at'] or 'none'}")
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Air receiver",
    "task": ("Keep the receiver between the pot less 0.5 bar and the pot, "
             "reading a raw 4-20 mA count, and prove the isolation valve open "
             "by its feedback within its travel time. This run draws harder, "
             "moves the pot, and seizes the valve. The mushroom is normally "
             "closed, drops the supply and the valve within 200 ms and "
             "latches -- only Reset, then Start, runs the station again."),
    "build": AirReceiverScene,
    "observe": None,
    "grade": grade_air_receiver,
    "summary": _summary_air,
    "duration": AR_EXAM_ENDS_BY,
    "references": ("good", "by32767", "nodiscrepancy", "impatient", "noestop",
                   "startalone"),
    "tags": ("receiver.supply, valve.open, pressure_gauge.value, "
             "alarm.beacon, alarm.horn, panel.green, panel.red are yours to "
             "write; receiver.pressure (a raw count), receiver.wirebreak, "
             "valve.opened, valve.closed, valve.fault, panel.setpoint and "
             "the buttons are the plant's."),
}
