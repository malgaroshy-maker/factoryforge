"""`batch-dosing`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/batch_dosing.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import PlantScene, Script, declare_stack_light, fault_input
from ..templates import template


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


class BatchDosingScene(PlantScene):
    name = "batch-dosing"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
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
        #: One entry per batch the examiner asked for.
        self.batches: list[dict] = []

        self.litres = float(self.rng.choice([18.0, 20.0, 22.0, 24.0]))
        self.script = Script([
            (0.3, self.panel.set_setpoint(self.litres)),
            (1.0, self._begin("first")),
            (32.0, self._end_batch),
            (33.0, self._halve_the_pump),
            (35.0, self._begin("then")),
        ])

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

    def step(self, dt: float) -> None:
        run = self.bit("pump.run") and not self.bit("pump.fault")
        commanded = min(max(self.num("pump.speed"), 0.0), 100.0)
        target = commanded if run else 0.0
        self.percent += max(min(target - self.percent, BD_RAMP * dt), -BD_RAMP * dt)
        self.flow = self.rated * self.percent / 100.0
        self.delivered += self.flow / 60.0 * dt

        alpha = min(dt / BD_METER_DAMPING, 1.0)
        self.meter_rate += (self.flow - self.meter_rate) * alpha
        if self.bit("meter.reset"):
            self.meter_total = 0.0
        else:
            self.meter_total += self.meter_rate / 60.0 * dt

        drain = min(max(self.num("tank.drain"), 0.0), 100.0)
        # IP-19 finding, left as it was pending a decision: the template gives
        # the tank a fill valve of its own (`fill_rate` 6 %/s), which
        # `LevelTank.cs` adds to what the pump delivers. This model has never
        # read `tank.fill`, so a program that opens it raises the engine's
        # tank and not this one.
        rise = self.flow / 60.0 / BD_CAPACITY * 100.0
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
    batches = sim.batches
    #: Litres after the batch was supposed to be over. A dose that overshoots
    #: by carrying on is a different mistake from one that overshoots by
    #: running fast, and only the plant can tell them apart.
    tail = sim.delivered - sum(b.get("delivered", 0.0) for b in batches)

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

    _batch_feedback(report, watched, sim, batches, target, tail)


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


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Batch dosing",
    "task": ("Dose the litres on the pot into the tank. Trim pump.speed "
             "until meter.rate is the rate you want, and end the batch on "
             "meter.total rather than on a clock -- this run re-rates the "
             "pump between the two batches."),
    "build": BatchDosingScene,
    "observe": None,
    "grade": grade_batch_dosing,
    "summary": _summary_batch,
    "duration": 80.0,
    "references": ("good", "timed", "noreset"),
    "tags": ("pump.run, pump.speed, meter.reset, tank.fill, tank.drain, "
             "flow_gauge.value, total_display.value, level_readout.value, "
             "panel.green, panel.red are yours to write; pump.flow, "
             "pump.fault, meter.rate, meter.total, tank.level, "
             "panel.setpoint and the buttons are the plant's."),
}
