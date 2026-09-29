"""`batch-dosing`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..scenes.batch_dosing import BD_RATED_FIRST
from ._shared import Scanner, contract_references


SCENE = "batch-dosing"


# --- batch dosing references ----------------------------------------------

#: How a reference notices the pump has failed, from the meter alone (IP-12):
#: it has been commanding flow for FAULT_ARM_S -- past the pump's ramp and the
#: meter's damping -- and the meter has read under FAULT_RATE for
#: FAULT_CONFIRM_S. The lowest rate a healthy dose ever asks for is the taper's
#: floor, 25 L/min, so 5 is a pump delivering nothing. At the scene's 300 %/s
#: ramp and 0.2 s damping that trips about a second after the failure, inside
#: the brief's two. It reads `meter.rate`, not `pump.fault`, because the
#: brief's lesson is that the measurement knows and the speed does not.
FAULT_ARM_S = 1.0
FAULT_RATE = 5.0
FAULT_CONFIRM_S = 0.3


async def _bd_body(bus, stop, *, by_litres: bool, zero_the_meter: bool,
                   open_loop: bool = False, notice_fault: bool = True) -> None:
    scanner = Scanner(bus)
    #: The dose rate the inner loop aims for, and what a stopwatch would make
    #: of it: 20 L at 100 L/min is twelve seconds. Right once.
    DOSE_RATE = 100.0
    CREEP_LITRES = 4.0
    KP, KI = 0.25, 1.0
    state = {"phase": "zero", "speed": 0.0, "integral": 0.0, "since": 0.0,
             "seconds": 0.0, "commanding": 0.0, "starved": 0.0, "fault": False}

    async def body(dt: float) -> None:
        edges = scanner.scan()
        target = scanner.setpoint
        total = scanner.num("meter.total")
        rate = scanner.num("meter.rate")

        # The pump fault latches until Reset, like the mushroom; a Start
        # while it is latched starts nothing.
        if edges["reset"]:
            state["fault"] = False
        if edges["start"] and not state["fault"]:
            state["phase"] = "zero" if zero_the_meter else "dose"
            state["integral"] = 0.0
            state["seconds"] = 0.0
        if not scanner.running or state["fault"]:
            state["phase"] = "idle"

        zeroing = False
        dosing = False
        if scanner.running:
            if state["phase"] == "zero":
                # A level, not an edge: hold it until the totaliser reads back
                # zero, so this batch counts from zero rather than from what
                # the last one left.
                zeroing = True
                if total <= 0.0:
                    state["phase"] = "dose"
            elif state["phase"] == "dose":
                dosing = True
                state["seconds"] += dt
                # The stopwatch answer is calibrated against the pump's
                # nameplate, not against a dose rate it never reaches: twenty
                # litres at 120 L/min is ten seconds, and at full speed that is
                # exactly right -- once.
                # x1.03 because this controller was calibrated on the line,
                # the way a student would calibrate it: the pump ramps and the
                # command lands a scan late, so the first batch came out short
                # until the number was nudged. That calibration is the whole
                # trap -- it is a measurement of one pump on one day.
                # It was x1.07, tuned against wall-clock scans. In lockstep
                # (IP-06) that put the first batch on 23.4999999999996 L against
                # a 23.5 L limit, inside by rounding; x1.03 lands it mid-band.
                seconds_for = (target / BD_RATED_FIRST * 60.0 * 1.03 if open_loop
                               else target / DOSE_RATE * 60.0)
                done = (total >= target if by_litres
                        else state["seconds"] >= seconds_for)
                if done:
                    state["phase"] = "done"
                    dosing = False

        flow_setpoint = 0.0
        if dosing and open_loop:
            state["speed"] = 100.0
        elif dosing:
            remaining = max(target - total, 0.0)
            taper = (1.0 if remaining >= CREEP_LITRES
                     else max(remaining / CREEP_LITRES, 0.25))
            flow_setpoint = DOSE_RATE * (taper if by_litres else 1.0)
            error = flow_setpoint - rate
            if 0.5 < state["speed"] < 99.5:
                state["integral"] = min(max(state["integral"] + error * KI * dt,
                                            -100.0), 100.0)
            state["speed"] = min(max(error * KP + state["integral"], 0.0), 100.0)
        else:
            state["speed"] = 0.0
            state["integral"] = 0.0

        # The flow loop knowing: flow commanded for long enough, and none
        # arriving. Checked after this scan's command, so the pump stops on
        # the scan that notices.
        commanding = dosing and state["speed"] >= 10.0
        state["commanding"] = state["commanding"] + dt if commanding else 0.0
        starved = commanding and state["commanding"] >= FAULT_ARM_S and rate < FAULT_RATE
        state["starved"] = state["starved"] + dt if starved else 0.0
        if notice_fault and state["starved"] >= FAULT_CONFIRM_S:
            state["fault"] = True
            state["phase"] = "idle"
            dosing = False
            state["speed"] = 0.0
            state["integral"] = 0.0

        lamps = scanner.lamps()
        lamps["panel.red"] = lamps["panel.red"] or state["fault"]
        lamps["panel.green"] = lamps["panel.green"] and not state["fault"]
        await bus.write_many({
            "meter.reset": zeroing,
            "pump.run": dosing,
            "pump.speed": state["speed"],
            "tank.fill": 0.0,
            "tank.drain": 0.0,
            "flow_gauge.value": rate,
            "total_display.value": int(total),
            "level_readout.value": int(round(scanner.num("tank.level"))),
            **lamps})

    await run_scan(bus, stop, body)


async def _bd_good(bus, stop):
    """Zeroes the totaliser, trims the pump against the meter, and ends the
    batch on litres."""
    await _bd_body(bus, stop, by_litres=True, zero_the_meter=True)


async def _bd_timed(bus, stop):
    """Runs the pump flat out for the number of seconds the pot's litres take
    at the pump's nameplate flow. Exactly right until the pump is re-rated,
    and then exactly half."""
    await _bd_body(bus, stop, by_litres=False, zero_the_meter=True,
                   open_loop=True)


async def _bd_noreset(bus, stop):
    """Ends on litres, correctly, and never zeroes the totaliser -- so the
    second batch is over before it starts."""
    await _bd_body(bus, stop, by_litres=True, zero_the_meter=False)


async def _bd_ignorefault(bus, stop):
    """`good`, with the flow loop that notices a dead pump taken out (IP-12).
    Every batch lands on its number; when the pump fails it goes on
    commanding flow that never arrives, and when the pump is repaired it
    finishes the dose by itself, with nobody having pressed anything."""
    await _bd_body(bus, stop, by_litres=True, zero_the_meter=True,
                   notice_fault=False)


REFERENCES = {"good": _bd_good, "timed": _bd_timed,
              "noreset": _bd_noreset, "ignorefault": _bd_ignorefault,
              **contract_references(_bd_good)}
