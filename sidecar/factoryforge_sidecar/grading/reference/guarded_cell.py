"""`guarded-cell`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ._shared import Scanner


SCENE = "guarded-cell"


# --- guarded cell references ----------------------------------------------

async def _gc_body(bus, stop, *, latch_the_trip: bool, write_the_motor: bool,
                   mute_window: float) -> None:
    """One implementation, four behaviours.

    `latch_the_trip` is the one that matters. With it, a safety trip holds the
    coil off until somebody presses Start again -- the relay handing the coil
    back is a permissive and not a command. Without it, the coil follows "the
    cell should be running" and the machine restarts itself the moment the
    relay closes, which is the failure the whole cell exists to prevent.
    """
    scanner = Scanner(bus)
    state = {"safety_trip": True, "mute_until": 0.0, "eye": False,
             "push": False, "transfer": "idle", "wait": 0.0,
             "feed": 0.0, "emit": False, "now": 0.0}
    #: Push eye to the transfer station: 0.4 m at 0.5 m/s.
    PUSH_DELAY = 0.8

    async def body(dt: float) -> None:
        state["now"] += dt           # the scan clock; see `run_scan`
        now = state["now"]
        # Read Reset before the panel scan consumes it: this cell has two
        # things to reset, the relay's latch and the controller's, and one
        # button does both the way it does on a real cell.
        reset = scanner.bit("panel.reset")
        scanner.scan()

        # Neither of these is computed here. The relay decides whether its
        # contacts are closed and the scanner decides whether its field is
        # clear; recomputing either would be a second, unrated opinion about a
        # safety function.
        relay_closed = scanner.bit("relay.k1") and scanner.bit("relay.k2")
        field_clear = scanner.bit("scanner.stop")

        if not relay_closed or not field_clear:
            state["safety_trip"] = True
        if reset and relay_closed and field_clear:
            state["safety_trip"] = False
        if latch_the_trip and state["safety_trip"]:
            scanner.running = False

        running = scanner.running and not (latch_the_trip and state["safety_trip"])
        coil = running if latch_the_trip else (relay_closed and field_clear
                                               and scanner.running)

        # The edge of "a carton at the eye while the cell runs", not of the eye
        # alone. A carton the gate caught standing in the eye window -- short
        # of the field, so going in did not take it out -- is still blocking
        # the eye when the cell restarts, so the eye never rises again; muting
        # on the eye's own edge left it unmuted, it walked into the field and
        # the scanner stopped the cell for good. Whether a carton was standing
        # there came down to where the scans happened to fall, which on the
        # wall clock differs between Windows and Linux: on Windows the cell
        # tripped 0.5 s after its restart in five runs out of five, and the
        # suite's `contactor_fraction > 0.3` failed, while Linux CI passed.
        eye = scanner.bit("mute_eye.detect") and running
        if eye and not state["eye"]:
            state["mute_until"] = now + mute_window
        state["eye"] = eye
        mute = running and now < state["mute_until"]

        motor = scanner.bit("starter.aux")
        if motor:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["emit"] = not state["emit"]
                state["feed"] = 4.0 if state["emit"] else 0.3
        else:
            state["emit"] = False

        extended = scanner.bit("cylinder.extended")
        retracted = scanner.bit("cylinder.retracted")
        push = scanner.bit("push_eye.detect")
        if not motor:
            state["transfer"] = "idle" if retracted else "retracting"
        else:
            if push and not state["push"] and state["transfer"] == "idle" and retracted:
                state["transfer"] = "waiting"
                state["wait"] = PUSH_DELAY
            if state["transfer"] == "waiting":
                state["wait"] -= dt
                if state["wait"] <= 0.0:
                    state["transfer"] = "extending"
            elif state["transfer"] == "extending" and extended:
                state["transfer"] = "retracting"
            elif state["transfer"] == "retracting" and retracted:
                state["transfer"] = "idle"
        state["push"] = push

        writes = {
            # The operator's own Reset, passed through. The relay acts on the
            # rising edge only, so a level held true would close it once at
            # power-up and never again -- and a level that re-closed it by
            # itself would be the automatic restart the relay exists to refuse.
            "relay.reset": reset,
            "starter.coil": coil,
            "scanner.mute": mute,
            "emitter.emit": state["emit"],
            "cylinder.extend": state["transfer"] == "extending",
            "cylinder.retract": state["transfer"] == "retracting",
            "panel.green": running,
            "panel.red": state["safety_trip"] or scanner.tripped,
        }
        if write_the_motor:
            writes["belt.rotate"] = running
        await bus.write_many(writes)

    await run_scan(bus, stop, body)


async def _gc_good(bus, stop):
    """Latches the trip, so a closing relay starts nothing."""
    await _gc_body(bus, stop, latch_the_trip=True, write_the_motor=False,
                   mute_window=3.5)


async def _gc_autostart(bus, stop):
    """Holds the coil for as long as the cell *should* be running, so the motor
    restarts by itself the instant the relay closes. Nobody pressed anything."""
    await _gc_body(bus, stop, latch_the_trip=False, write_the_motor=False,
                   mute_window=3.5)


async def _gc_writesbelt(bus, stop):
    """Right about the relay and wrong about who runs the motor: it drives
    belt.rotate itself, which is the one tag this exercise forbids."""
    await _gc_body(bus, stop, latch_the_trip=True, write_the_motor=True,
                   mute_window=3.5)


async def _gc_tapedmute(bus, stop):
    """Bridges the scanner for twelve seconds a carton, past the scanner's own
    six-second limit, so the guard it is supposed to be muting is simply off."""
    await _gc_body(bus, stop, latch_the_trip=True, write_the_motor=False,
                   mute_window=12.0)


REFERENCES = {"good": _gc_good, "autostart": _gc_autostart,
              "writesbelt": _gc_writesbelt, "tapedmute": _gc_tapedmute}
