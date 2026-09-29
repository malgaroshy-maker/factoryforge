"""`servo-positioning`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..scenes.servo_positioning import SV_STATION_A, SV_WINDOW
from ._shared import Scanner, contract_references


SCENE = "servo-positioning"

#: Move speed, mm/s, and the dwell at each station, s.
SV_SPEED = 400.0
SV_DWELL = 1.0


async def _sv_body(bus, stop, *, ack: str) -> None:
    """One shuttle, three ways of handling the drive's latched error.

    `ack` is "reset" (the operator's Reset, once the fault has gone: right),
    "auto" (pulse it whenever there is an error with no fault behind it:
    automatic restart) or "never".
    """
    scanner = Scanner(bus)
    state = {"to_b": True, "dwell": 0.0, "last_ack": False}

    async def body(dt: float) -> None:
        edges = scanner.scan()
        error = scanner.bit("axis.error")
        fault = scanner.bit("axis.fault")
        position = scanner.num("axis.position")
        dest = scanner.setpoint if state["to_b"] else SV_STATION_A

        if scanner.running and not error:
            # Arrival on the position feedback against the destination this
            # step wants -- not `inposition`, which is stale on the scan a new
            # target is written.
            if abs(position - dest) <= SV_WINDOW:
                state["dwell"] += dt
                if state["dwell"] >= SV_DWELL:
                    state["to_b"] = not state["to_b"]
                    state["dwell"] = 0.0
                    dest = scanner.setpoint if state["to_b"] else SV_STATION_A

        pulse = False
        if error and not fault:
            if ack == "reset":
                pulse = edges["reset"]
            elif ack == "auto":
                pulse = not state["last_ack"]
        state["last_ack"] = pulse

        await bus.write_many({
            "axis.enable": scanner.running,
            "axis.ack": pulse,
            "axis.target": dest,
            "axis.velocity": SV_SPEED,
            "position_display.value": int(round(position)),
            "tower.green": scanner.running and not error,
            "tower.yellow": not scanner.running and not error,
            "tower.red": error,
            "panel.green": scanner.running,
            "panel.red": error or scanner.tripped,
        })

    await run_scan(bus, stop, body)


async def _sv_good(bus, stop):
    """Holds on an error, acknowledges on the operator's Reset once the
    fault has gone, then carries on with the interrupted move."""
    await _sv_body(bus, stop, ack="reset")


async def _sv_autoack(bus, stop):
    """Acknowledges an error by itself the moment its cause has gone, so the
    carriage moves off with nobody having asked."""
    await _sv_body(bus, stop, ack="auto")


async def _sv_noack(bus, stop):
    """Never acknowledges: the first fault stops the axis for good."""
    await _sv_body(bus, stop, ack="never")


REFERENCES = {"good": _sv_good, "autoack": _sv_autoack, "noack": _sv_noack,
              **contract_references(_sv_good)}
