"""`air-receiver`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..scenes.air_receiver import AR_BAND, AR_RANGE_MAX, AR_RANGE_MIN, AR_TRAVEL
from ._shared import Scanner, contract_references


SCENE = "air-receiver"

#: The card's full scale, and the number a student reaches for instead.
AR_CARD = 27648.0
AR_WRONG_CARD = 32767.0
#: Margin on the valve's travel before a missing `opened` is a fault.
AR_MARGIN = 0.5


async def _ar_body(bus, stop, *, full_scale: float, check: str) -> None:
    """One station, three ways to get it wrong.

    `full_scale` is what the program divides the raw count by. `check` is
    how it proves the isolation valve: "timed" (a discrepancy only once the
    travel time and a margin have run out, which alarms and stops the
    station: right), "never", or "instant" (the alarm shows "commanded and
    not opened", with no timer -- and, so it is wrong about exactly one
    thing, stops nothing).
    """
    scanner = Scanner(bus)
    state = {"loading": False, "commanded_for": 0.0, "fault": False}

    async def body(dt: float) -> None:
        edges = scanner.scan()
        if edges["reset"]:
            state["fault"] = False
        if state["fault"]:
            scanner.running = False

        raw = scanner.num("receiver.pressure")
        bar = AR_RANGE_MIN + raw / full_scale * (AR_RANGE_MAX - AR_RANGE_MIN)
        opened = scanner.bit("valve.opened")

        open_valve = scanner.running
        state["commanded_for"] = state["commanded_for"] + dt if open_valve else 0.0
        if (open_valve and not opened and check == "timed"
                and state["commanded_for"] > AR_TRAVEL + AR_MARGIN):
            state["fault"] = True
        alarm = state["fault"] or (check == "instant" and open_valve and not opened)
        if state["fault"]:
            open_valve = False
            scanner.running = False

        pot = scanner.setpoint
        if not scanner.running:
            state["loading"] = False
        elif bar <= pot - AR_BAND:
            state["loading"] = True
        elif bar >= pot:
            state["loading"] = False

        await bus.write_many({
            "receiver.supply": state["loading"],
            "valve.open": open_valve,
            "pressure_gauge.value": bar,
            "alarm.beacon": alarm,
            "alarm.horn": False,
            "panel.green": scanner.running,
            "panel.red": state["fault"] or scanner.tripped,
        })

    await run_scan(bus, stop, body)


async def _ar_good(bus, stop):
    """Scales by the card's 27648, holds the band two-point, and calls the
    valve failed only once its travel time and a margin have run out."""
    await _ar_body(bus, stop, full_scale=AR_CARD, check="timed")


async def _ar_by32767(bus, stop):
    """`good`, scaling by 32767 -- the biggest INT, not the card's full scale.
    It reads 16 % low and holds the receiver 16 % high."""
    await _ar_body(bus, stop, full_scale=AR_WRONG_CARD, check="timed")


async def _ar_nodiscrepancy(bus, stop):
    """`good` without the valve check: it commands the valve and trusts it."""
    await _ar_body(bus, stop, full_scale=AR_CARD, check="never")


async def _ar_impatient(bus, stop):
    """`good` with no timer on the valve check: commanded and not yet opened
    is a fault, which is every start, because a valve takes time to travel."""
    await _ar_body(bus, stop, full_scale=AR_CARD, check="instant")


REFERENCES = {"good": _ar_good, "by32767": _ar_by32767,
              "nodiscrepancy": _ar_nodiscrepancy, "impatient": _ar_impatient,
              **contract_references(_ar_good)}
