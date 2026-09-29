"""`press-station`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..scenes.press_station import PS_AUTO, PS_MAN
from ._shared import Scanner, contract_references


SCENE = "press-station"


async def _ps_body(bus, stop, *, permissive: str, obey_selector: bool) -> None:
    """One press, two ways to get its interlock wrong.

    `permissive` is what MANUAL moves the ram on: "valid" (the relay's
    verdict: right) or "and" (left AND right, worked out here). Without
    `obey_selector` the automatic cycle, once started, runs whatever the
    selector says.
    """
    scanner = Scanner(bus)
    state = {"auto": "up", "dwell": 0.0, "man": "up", "released": True}

    async def body(dt: float) -> None:
        scanner.scan()
        mode = int(scanner.num("mode.position"))
        at_bdc = scanner.bit("bdc.no")
        up = scanner.bit("ram.retracted")
        if permissive == "valid":
            hands = scanner.bit("hands.valid")
        else:
            hands = scanner.bit("hands.left") and scanner.bit("hands.right")

        automatic = scanner.running and (mode == PS_AUTO or not obey_selector)
        if obey_selector and mode != PS_AUTO:
            scanner.running = False

        down = False
        if automatic:
            phase = state["auto"]
            if phase == "up" and up:
                state["auto"] = "down"
            elif phase == "down" and at_bdc:
                state["auto"], state["dwell"] = "dwell", 0.0
            elif phase == "dwell":
                state["dwell"] += dt
                if state["dwell"] >= scanner.setpoint:
                    state["auto"] = "return"
            elif phase == "return" and up:
                state["auto"] = "down"
            down = state["auto"] in ("down", "dwell")
        else:
            state["auto"] = "up"
            if mode == PS_MAN and scanner.tripped:
                # The mushroom stops MANUAL as well (IP-12), and the stroke it
                # cut short does not resume: the hands come off and go on again.
                state["man"], state["released"] = "up", False
            elif mode == PS_MAN:
                # Hold-to-run, one stroke per press: down while the hands are
                # on and the bottom has not been reached; a new stroke only
                # once the hands have come off.
                if not hands:
                    state["released"] = True
                    state["man"] = "up"
                elif state["released"]:
                    state["released"] = False
                    state["man"] = "down"
                if state["man"] == "down" and at_bdc:
                    state["man"] = "up"
                down = state["man"] == "down"

        await bus.write_many({
            "ram.extend": down,
            "ram.retract": not down,
            "tower.green": automatic,
            "tower.yellow": mode == PS_MAN,
            "tower.red": scanner.tripped,
            **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _ps_good(bus, stop):
    """AUTO cycles on Start, OFF stops everything, MANUAL runs on the relay's
    `valid` and nothing else."""
    await _ps_body(bus, stop, permissive="valid", obey_selector=True)


async def _ps_andhands(bus, stop):
    """`good`, with MANUAL on left AND right -- the permissive worked out in
    the program, which a taped-down button satisfies."""
    await _ps_body(bus, stop, permissive="and", obey_selector=True)


async def _ps_ignoresmode(bus, stop):
    """`good`, except the automatic cycle, once started, runs whatever the
    selector says."""
    await _ps_body(bus, stop, permissive="valid", obey_selector=False)


REFERENCES = {"good": _ps_good, "andhands": _ps_andhands,
              "ignoresmode": _ps_ignoresmode, **contract_references(_ps_good)}
