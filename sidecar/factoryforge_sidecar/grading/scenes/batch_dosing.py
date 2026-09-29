"""`batch-dosing`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/batch_dosing.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (OperatorExam, PlantScene, Script, declare_stack_light,
                     fault_input, operator_exam_ends_by, pot_start)
from ..templates import template
from ._contract import mark_contract, summary_contract


SCENE = "batch-dosing"


# --- batch dosing --------------------------------------------------------
#
# Observable fact: litres. The plant integrates what the pump really delivered,
# and that number is not on the bus -- `meter.total` is an instrument the
# controller can zero, and `pump.speed` is a command rather than a delivery.
#
# How a program fakes it: by running the pump at a known speed for a known
# time. Twenty litres at 120 L/min is ten seconds, and a stopwatch gets it
# exactly right, once. So the exam reaches into the pump between the two
# batches and halves what it is rated for. Same command, half the flow. A batch
# that ends on litres takes twice as long and delivers the same; a batch that
# ends on seconds delivers half and never notices.
#
# The plant is read from the template the engine opens (IP-19): the pump's
# rating and ramp, the flow meter's time constant, the tank's capacity and
# drain. `DosingPump.cs`, `FlowMeter.cs` and `LevelTank.cs` hold the
# equations, which are mirrored below; the numbers are the template's. The
# meter's reset is a level rather than an edge, as `FlowMeter.cs` has it.
_PLANT = template(SCENE)
_PUMP = _PLANT.part("pump", "DosingPump")
_METER = _PLANT.part("meter", "FlowMeter").engineering_units()
_TANK = _PLANT.part("tank", "LevelTank").engineering_units()

BD_RATED_FIRST = _PUMP.number("rated_flow")
#: The exam's re-rating, and not a number from anywhere else: the second batch
#: runs on a pump rated for half of whatever the template rates it for.
BD_RERATE = 0.5
BD_RATED_THEN = BD_RATED_FIRST * BD_RERATE
BD_RAMP = _PUMP.number("ramp_rate")
BD_METER_DAMPING = _METER.number("damping")
BD_CAPACITY = _TANK.number("capacity")
BD_TANK_DRAIN_RATE = _TANK.number("drain_rate")
BD_TANK_FILL_RATE = _TANK.number("fill_rate")
BD_POT_START = pot_start(_PLANT)


# --- the pump fault (IP-12) -----------------------------------------------
#
# The brief ends on it: fail the pump mid-dose and `pump.speed` holds whatever
# was commanded while `meter.rate` collapses, and "the flow loop knows within
# two seconds". What it has to do about knowing is the least a dosing skid
# does: drop `pump.run` within those two seconds, and do not start dosing
# again by itself when the pump comes back -- a dose that ended on a fault
# waits for its operator.
#
# So once the second batch is over, the examiner starts a third one, fails the
# pump four seconds in -- mid-dose on any pot, at the halved rating -- and
# repairs it five seconds later. Nobody presses anything after that. The plant
# obeys the fault as `DosingPump.cs` does: the speed ramps to zero whatever the
# command, and nothing flows. The third batch is the fault's and is marked
# only on the fault; its litres are not "outside a batch", and the two batches
# before it are marked exactly as they were.

#: The second batch runs until this, as it ran until the end of the old
#: 80-second window.
BD_SECOND_ENDS = 80.0
#: The third batch starts here, and the pump fails this long into it...
BD_FAULT_BATCH_AT = 82.0
BD_FAULT_AFTER = 4.0
BD_FAULT_AT = BD_FAULT_BATCH_AT + BD_FAULT_AFTER
#: ...and is repaired this long after that.
BD_REPAIRED_AFTER = 5.0
BD_REPAIRED_AT = BD_FAULT_AT + BD_REPAIRED_AFTER
#: The brief's "within two seconds": `pump.run` has to have dropped by then.
BD_FAULT_NOTICED_WITHIN = 2.0
#: How long the exam watches a repaired pump that nobody restarted.
BD_WATCH_AFTER_REPAIR = 5.0
#: The end of that watch, and of the pump-fault test.
BD_FAULT_ENDS = BD_REPAIRED_AT + BD_WATCH_AFTER_REPAIR
#: Litres a repaired pump may still move before it counts as having restarted:
#: none, give or take the float.
BD_RESTART_TOLERANCE_L = 0.01


# --- the operator contract (IP-12) ------------------------------------------
#
# After the pump-fault test the examiner presses Reset and Start for a fourth
# batch, as it began the other three, and puts the E-stop sheet to it
# (`plant.OperatorExam`) while the pump is dosing. "Stopped" is the pump no
# longer run: `pump.run` false, or no speed, so the drive is not being driven
# -- a pump ramps down on its own once it is, as `DosingPump.cs` does, and the
# litres of that ramp are the drive's. The batch is the E-stop's, like the
# third is the fault's: marked on the contract alone, and its litres are
# neither "outside a batch" nor a repaired pump restarting by itself.

BD_ESTOP_BATCH_AT = BD_FAULT_ENDS + 0.5
BD_ESTOP_AT = BD_ESTOP_BATCH_AT + 1.5
#: The shortest window the whole exam fits in.
BD_EXAM_ENDS_BY = operator_exam_ends_by(BD_ESTOP_AT)
#: Batches that are the exam's and not the pot's, marked on something else.
BD_NOT_DOSES = ("fault", "estop")


class BatchDosingScene(PlantScene):
    name = "batch-dosing"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=BD_POT_START)
        self._declare(
            Tag("pump.run", "Dosing Pump (Run)", "bit", "output"),
            Tag("pump.speed", "Dosing Pump Speed (%)", "float", "output"),
            Tag("meter.reset", "Flow Meter Totaliser Reset", "bit", "output"),
            Tag("tank.fill", "Tank Fill Valve (%)", "float", "output"),
            Tag("tank.drain", "Tank Drain Valve (%)", "float", "output"),
            Tag("flow_gauge.value", "Flow Gauge", "float", "output"),
            Tag("total_display.value", "Total Display", "int", "output"),
            Tag("level_readout.value", "Level Readout", "int", "output"),
            Tag("pump.flow", "Dosing Pump Flow (L/min)", "float", "input"),
            Tag("pump.fault", "Dosing Pump Fault", "bit", "input"),
            Tag("meter.rate", "Flow Meter Rate (L/min)", "float", "input"),
            Tag("meter.total", "Flow Meter Total (L)", "int", "input"),
            Tag("tank.level", "Tank Level (%)", "float", "input"),
            fault_input("tank", "Tank Valve Fault"),
        )
        declare_stack_light(self.tags)

        self.rated = BD_RATED_FIRST
        self.percent = 0.0
        self.flow = 0.0
        self.meter_rate = 0.0
        self.meter_total = 0.0
        self.level = 0.0
        #: Ground truth: litres the pump has actually moved, ever.
        self.delivered = 0.0
        #: Seconds the tank's own fill valve was open at all.
        self.fill_valve_open_s = 0.0
        #: One entry per batch the examiner asked for.
        self.batches: list[dict] = []

        #: The pump fault's ground truth, and what the plant saw of the
        #: program's answer to it (IP-12). None until it happens.
        self.pump_faulted = False
        self.fault: dict = {"failed_at": None, "running_at_failure": None,
                            "run_dropped_at": None, "repaired_at": None,
                            "delivered_while_failed_L": 0.0,
                            "delivered_after_repair_L": 0.0,
                            "restarted_at": None}

        self.litres = float(self.rng.choice([18.0, 20.0, 22.0, 24.0]))
        self.script = Script([
            (0.3, self.panel.set_setpoint(self.litres)),
            (1.0, self._begin("first")),
            (32.0, self._end_batch),
            (33.0, self._halve_the_pump),
            (35.0, self._begin("then")),
            (BD_SECOND_ENDS, self._end_batch),
            (BD_FAULT_BATCH_AT, self._begin("fault")),
            (BD_FAULT_AT, self._fail_the_pump),
            (BD_REPAIRED_AT, self._repair_the_pump),
            (BD_ESTOP_BATCH_AT, self._begin("estop")),
        ])
        self.operator = OperatorExam(self, BD_ESTOP_AT, noun="pump",
                                     what="the pump")

    def _begin(self, name: str):
        def do() -> None:
            self.panel.press("reset")()
            self.panel.press("start")()
            self.batches.append({"name": name, "from": self.t,
                                 "delivered_at": self.delivered,
                                 "level_at": self.level,
                                 "rated": self.rated, "to": None})
        do.__name__ = f"begin the {name} batch"
        return do

    def _end_batch(self) -> None:
        self.panel.press("stop")()
        if self.batches:
            self.batches[-1]["to"] = self.t
            self.batches[-1]["delivered"] = self.delivered - self.batches[-1]["delivered_at"]
            self.batches[-1]["level_rise"] = self.level - self.batches[-1]["level_at"]
        # The vessel is emptied between batches, by hand, so the second one
        # starts from the same place as the first.
        self.level = 0.0

    def _halve_the_pump(self) -> None:
        """Re-rate the pump. Nothing on the bus says so: `pump.speed` is still
        a percentage of a maximum the controller is not told, and the only
        instrument that knows is the flow meter."""
        self.rated = BD_RATED_THEN

    def _fail_the_pump(self) -> None:
        """The motor fails. `DosingPump.cs`: the speed ramps to zero whatever
        `pump.run` and `pump.speed` say, and the fault contact reads true --
        written by the plant, as the engine's part writes it, not forced."""
        self.pump_faulted = True
        self.tags.set("pump.fault", True)
        running = self.bit("pump.run")
        self.fault["failed_at"] = round(self.t, 2)
        self.fault["running_at_failure"] = running
        if not running:
            self.fault["run_dropped_at"] = round(self.t, 2)

    def _repair_the_pump(self) -> None:
        self.pump_faulted = False
        self.tags.set("pump.fault", False)
        self.fault["repaired_at"] = round(self.t, 2)

    def step(self, dt: float) -> None:
        if self.fault["failed_at"] is not None and self.fault["run_dropped_at"] is None \
                and not self.bit("pump.run"):
            self.fault["run_dropped_at"] = round(self.t, 2)
        run = self.bit("pump.run") and not self.pump_faulted
        commanded = min(max(self.num("pump.speed"), 0.0), 100.0)
        target = commanded if run else 0.0
        self.percent += max(min(target - self.percent, BD_RAMP * dt), -BD_RAMP * dt)
        self.operator.driven = target > 0.0
        self.flow = self.rated * self.percent / 100.0
        self.delivered += self.flow / 60.0 * dt
        litres = self.flow / 60.0 * dt
        if self.pump_faulted:
            # The ramp down the tick the motor failed, and nothing after.
            self.fault["delivered_while_failed_L"] += litres
        elif self.fault["repaired_at"] is not None and self.t <= BD_FAULT_ENDS:
            self.fault["delivered_after_repair_L"] += litres
            if litres > 0.0 and self.fault["restarted_at"] is None:
                self.fault["restarted_at"] = round(self.t, 2)

        alpha = min(dt / BD_METER_DAMPING, 1.0)
        self.meter_rate += (self.flow - self.meter_rate) * alpha
        if self.bit("meter.reset"):
            self.meter_total = 0.0
        else:
            self.meter_total += self.meter_rate / 60.0 * dt

        drain = min(max(self.num("tank.drain"), 0.0), 100.0)
        # `LevelTank.cs` (`Step`): the tank's own fill valve adds its
        # `fill_rate` percent per second at full opening, on top of whatever
        # the pump offers. Until IP-29 this model never read `tank.fill`, so a
        # program that opened it raised the engine's tank and not this one.
        # The litres that valve lets in are not the pump's, and `delivered`,
        # which the dose is marked on, does not count them; the tank does.
        fill = min(max(self.num("tank.fill"), 0.0), 100.0)
        if fill > 0.0:
            self.fill_valve_open_s += dt
        rise = (self.flow / 60.0 / BD_CAPACITY * 100.0
                + BD_TANK_FILL_RATE * fill / 100.0)
        fall = (BD_TANK_DRAIN_RATE * drain / 100.0
                * (max(self.level, 0.0) / 100.0) ** 0.5)
        self.level = min(max(self.level + (rise - fall) * dt, 0.0), 100.0)

        self.tags.set("pump.flow", self.flow)
        self.tags.set("meter.rate", self.meter_rate)
        self.tags.set("meter.total", int(self.meter_total))
        self.tags.set("tank.level", self.level)

        if self.batches and self.batches[-1]["to"] is None:
            batch = self.batches[-1]
            batch["delivered"] = self.delivered - batch["delivered_at"]
            batch["level_rise"] = self.level - batch["level_at"]


def grade_batch_dosing(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: BatchDosingScene = watched.inner
    target = sim.litres
    #: The batches marked on the number. The third, if the window reached it,
    #: is the pump fault's, and is marked on the fault alone.
    batches = [b for b in sim.batches if b["name"] not in BD_NOT_DOSES]
    faulted = [b for b in sim.batches if b["name"] == "fault"]
    #: Litres after the batch was supposed to be over. A dose that overshoots
    #: by carrying on is a different mistake from one that overshoots by
    #: running fast, and only the plant can tell them apart.
    tail = sim.delivered - sum(b.get("delivered", 0.0) for b in sim.batches)

    report.evidence.update({
        "pot_litres": target,
        "batches": [{"name": b["name"], "rated_flow": b["rated"],
                     "delivered_L": round(b.get("delivered", 0.0), 2),
                     "level_rise_pct": round(b.get("level_rise", 0.0), 2),
                     "seconds": round((b["to"] or watched.sim_time) - b["from"], 1)}
                    for b in batches],
        "delivered_total_L": round(sim.delivered, 2),
        "meter_total_L": round(sim.meter_total, 2),
        "delivered_outside_a_batch_L": round(tail, 2),
        "pump_commanded_fraction": round(watched.held_true("pump.run"), 3),
        "tank_fill_valve_open_s": round(sim.fill_valve_open_s, 2),
    })

    first = batches[0].get("delivered", 0.0) if batches else 0.0
    report.add("dose.ran",
               first >= 5.0,
               f"the first batch moved {first:.1f} L (at least 5 L, or there is "
               f"nothing here to mark)")
    for index, batch in enumerate(batches, start=1):
        delivered = batch.get("delivered", 0.0)
        rise = batch.get("level_rise", 0.0)
        expected_rise = target / BD_CAPACITY * 100.0
        report.add(f"dose{index}.on_the_number",
                   abs(delivered - target) <= 1.5,
                   f"batch {index} delivered {delivered:.1f} L against a "
                   f"{target:g} L pot, with the pump rated "
                   f"{batch['rated']:g} L/min (within 1.5 L)")
        report.add(f"dose{index}.tank_agrees",
                   abs(rise - expected_rise) <= 1.5,
                   f"batch {index} raised a {BD_CAPACITY:g} L tank by "
                   f"{rise:.1f} %, and {target:g} L is {expected_rise:.1f} %")
    report.add("dose.cut_off",
               tail <= 1.0,
               f"{tail:.1f} L moved outside a batch (at most 1 L: the pump has "
               f"to stop when the batch does)")

    fault = _grade_pump_fault(sim, report, watched.sim_time, faulted)

    _batch_feedback(report, watched, sim, batches, target, tail)
    _pump_fault_feedback(report, fault)
    mark_contract(sim.operator, report, watched.sim_time)


def _grade_pump_fault(sim: BatchDosingScene, report: Report, window: float,
                      faulted: list[dict]) -> dict:
    """The pump fault (IP-12): `pump.run` dropped within the brief's two
    seconds of the pump failing mid-dose, and not a litre moved by the
    repaired pump that nobody restarted. The first reads a command, as the
    air receiver's alarm does -- a dead pump moves nothing whatever it is
    told, so the command is the only trace of the program noticing. The
    second is litres, the plant's own."""
    fault = dict(sim.fault)
    failed, repaired = fault["failed_at"], fault["repaired_at"]
    watched_to = window
    finished = repaired is not None and window >= BD_FAULT_ENDS - 1e-6
    short = (f"the {window:g}s window ended before the examiner finished the "
             f"pump-fault test, which needs {BD_FAULT_ENDS:g}s")
    dropped = fault["run_dropped_at"]
    lag = None if dropped is None or failed is None else round(dropped - failed, 2)
    report.evidence["pump_fault"] = {
        **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in fault.items()},
        "noticed_after_s": lag,
        "noticed_within_s": BD_FAULT_NOTICED_WITHIN,
        "batch": None if not faulted else {
            "from": round(faulted[0]["from"], 1),
            "delivered_L": round(faulted[0].get("delivered", 0.0), 2)},
    }

    if failed is None:
        report.add("fault.pump_stopped", False, short)
        report.add("fault.no_restart_after_repair", False, short)
        return {"fault": fault, "lag": None, "finished": False}

    if not fault["running_at_failure"]:
        report.add("fault.pump_stopped", False,
                   f"pump.run was not on when the pump failed at {failed:g}s, "
                   f"{BD_FAULT_AFTER:g}s into the batch started at "
                   f"{BD_FAULT_BATCH_AT:g}s, so the fault tested nothing")
    else:
        report.add("fault.pump_stopped",
                   lag is not None and lag <= BD_FAULT_NOTICED_WITHIN + 1e-9,
                   f"pump.run dropped {lag:.2f}s after the pump failed at "
                   f"{failed:g}s (within {BD_FAULT_NOTICED_WITHIN:g}s)"
                   if lag is not None else
                   f"pump.run was still on {watched_to - failed:.1f}s after the "
                   f"pump failed at {failed:g}s (at most "
                   f"{BD_FAULT_NOTICED_WITHIN:g}s)")

    if not finished:
        report.add("fault.no_restart_after_repair", False, short)
        return {"fault": fault, "lag": lag, "finished": False}
    after = fault["delivered_after_repair_L"]
    report.add("fault.no_restart_after_repair",
               after <= BD_RESTART_TOLERANCE_L,
               f"the repaired pump moved nothing from {repaired:g}s to the end "
               f"of the window, with nobody pressing Reset or Start"
               if after <= BD_RESTART_TOLERANCE_L else
               f"the pump was repaired at {repaired:g}s and started dosing "
               f"again by itself at {fault['restarted_at']:g}s, moving "
               f"{after:.1f} L with nobody pressing Reset or Start")
    return {"fault": fault, "lag": lag, "finished": True}


def _pump_fault_feedback(report, result: dict) -> None:
    say = report.feedback.append
    fault, lag = result["fault"], result["lag"]
    if fault["failed_at"] is None or not fault["running_at_failure"]:
        return
    if lag is None or lag > BD_FAULT_NOTICED_WITHIN + 1e-9:
        say(f"The pump failed at {fault['failed_at']:g}s and `pump.run` "
            + ("never dropped. " if lag is None else f"dropped {lag:.1f}s later. ")
            + "`pump.speed` goes on reading what you commanded, so it tells you "
              "nothing: `meter.rate` collapsing while you are commanding flow is "
              "the failure. Stop the pump when the meter says it is not "
              "delivering, within two seconds.")
    if result["finished"] and fault["delivered_after_repair_L"] > BD_RESTART_TOLERANCE_L:
        say(f"When the pump was repaired at {fault['repaired_at']:g}s it started "
            f"dosing again by itself and moved "
            f"{fault['delivered_after_repair_L']:.1f} L. A dose that ended on a "
            f"fault waits for its operator: latch the fault, and dose again only "
            f"after Reset and Start.")


def _batch_feedback(report, watched, sim, batches, target, tail) -> None:
    say = report.feedback.append
    if not batches or batches[0].get("delivered", 0.0) < 1.0:
        say("The pump moved nothing. `pump.run` has to be true and `pump.speed` "
            "is a percentage, not a bit -- and `meter.rate` is the only thing "
            "that knows what is actually being delivered.")
        return

    if len(batches) > 1:
        one, two = batches[0].get("delivered", 0.0), batches[1].get("delivered", 0.0)
        if two < 2.0 <= one:
            say(f"The second batch delivered {two:.1f} L and stopped almost "
                f"immediately. `meter.total` still held the first batch's "
                f"litres, so the new batch was over before it started. Zero the "
                f"totaliser before each one -- and its reset is a LEVEL, not an "
                f"edge, so hold it until `meter.total` reads back zero.")
        elif abs(one - target) <= 1.5 and abs(two - target) > 1.5:
            ratio = batches[1]["rated"] / batches[0]["rated"]
            say(f"The first batch landed on {one:.1f} L and the second on "
                f"{two:.1f} L, against the same {target:g} L pot. Between them "
                f"the pump was re-rated to {ratio:.0%} of what it was: the same "
                f"`pump.speed` now delivers {ratio:.0%} of the flow. A batch that "
                f"ends on seconds cannot see that. End it on `meter.total`, "
                f"which is litres, and the second batch takes "
                f"{1 / ratio:.0f} times as long and delivers the same.")

    for index, batch in enumerate(batches, start=1):
        delivered = batch.get("delivered", 0.0)
        if delivered > target + 1.5:
            say(f"Batch {index} overran by {delivered - target:.1f} L. The pump "
                f"takes time to stop and the meter is damped, so cutting at the "
                f"number arrives late -- taper the rate over the last few litres "
                f"so the cut-off does not carry you past it.")

    if sim.fill_valve_open_s > 0.0:
        say(f"The tank's own fill valve, `tank.fill`, was open for "
            f"{sim.fill_valve_open_s:.1f}s. That is a second inlet, and every "
            f"litre it lets in is one the pump did not dose and the flow meter "
            f"never saw -- so the tank reads more than the batch. A dose goes "
            f"in through the pump; leave `tank.fill` at zero.")

    if tail > 1.0:
        say(f"{tail:.1f} L went through the pump outside a batch. When the batch "
            f"is done the pump stops: `pump.run` false, not merely a lower speed.")

    if all(abs(b.get("delivered", 0.0) - target) <= 1.5 for b in batches) \
            and len(batches) > 1:
        say(f"Both batches landed on {target:g} L, at two different pump "
            f"ratings -- {batches[0]['rated']:g} and {batches[1]['rated']:g} "
            f"L/min -- and the tank agreed with the meter. That is a batch that "
            f"ends on a quantity.")


def _summary_batch(evidence: dict, out) -> None:
    out(f"pot: {evidence['pot_litres']:g} L")
    for batch in evidence["batches"]:
        out(f"{batch['name']:>6} batch: {batch['delivered_L']:.1f} L in "
            f"{batch['seconds']:.0f}s with the pump rated "
            f"{batch['rated_flow']:g} L/min, tank +{batch['level_rise_pct']:.1f} %")
    out(f"{evidence['delivered_total_L']:.1f} L through the pump in all, "
        f"{evidence['delivered_outside_a_batch_L']:.1f} of it outside a batch")
    fault = evidence.get("pump_fault", {})
    if fault.get("failed_at") is not None:
        noticed = fault["noticed_after_s"]
        out(f"pump failed at {fault['failed_at']:g}s: pump.run "
            + ("never dropped" if noticed is None else f"dropped {noticed:.2f}s later")
            + f", {fault['delivered_after_repair_L']:.1f} L after the repair")
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Batch dosing",
    "task": ("Dose the litres on the pot into the tank. Trim pump.speed "
             "until meter.rate is the rate you want, and end the batch on "
             "meter.total rather than on a clock -- this run re-rates the "
             "pump between the two batches. Then a third batch, and the pump "
             "fails mid-dose: drop pump.run within two seconds, and do not "
             "dose again by yourself when the pump comes back. The mushroom "
             "is normally closed, stops the pump within 200 ms and latches "
             "-- only Reset, then Start, doses again."),
    "build": BatchDosingScene,
    "observe": None,
    "grade": grade_batch_dosing,
    "summary": _summary_batch,
    "duration": BD_EXAM_ENDS_BY,
    "references": ("good", "timed", "noreset", "ignorefault", "noestop",
                   "startalone"),
    "tags": ("pump.run, pump.speed, meter.reset, tank.fill, tank.drain, "
             "flow_gauge.value, total_display.value, level_readout.value, "
             "panel.green, panel.red are yours to write; pump.flow, "
             "pump.fault, meter.rate, meter.total, tank.level, "
             "panel.setpoint and the buttons are the plant's."),
}
