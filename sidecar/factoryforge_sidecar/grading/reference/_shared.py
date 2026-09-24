"""What every reference controller is built from: the feeder, the panel's
scan, and the two controllers every scene shares. A helper and not a scene:
`grading.registry` skips every module here whose name starts with an underscore.
"""

from __future__ import annotations

import asyncio

from ..lockstep import run_scan


# --- reference controllers ---------------------------------------------
#
# Built-in stand-ins for a student, so the grader can be pointed at a known
# answer. Two reasons they exist. An instructor wants to know the rubric is
# alive before they trust a mark -- a grader that fails everything looks
# exactly like a cohort that cannot program. And a grader nobody can make PASS
# is the same bug as a test that passes while the simulation does nothing
# (AGENTS.md gotcha 16), which is why `good` is the first one.
#
# They connect over a real websocket through `TagBusClient` -- the same client
# `factoryforge-sidecar connect` uses -- so they cross the same seam a real
# controller does. They run in this process, which a real one never does.

#: Two of these are the same on every scene, so they are written once: a
#: controller that connects and does nothing must never be able to pass, and a
#: controller that forces its way to a flattering number must be disqualified
#: rather than failed. The rest are per scene, because a wrong answer is only
#: interesting when it is wrong about that scene's own lesson.
SHARED_REFERENCES = ("idle", "forcer")

#: The emitter makes one carton per rising edge. 1.8s apart is comfortably
#: more than the 1.2s a carton takes to clear the pusher.
EMIT_PULSE = 0.2
EMIT_GAP = 1.6


class Feed:
    """The emitter, pulsed on the scan: EMIT_PULSE high, then `gap` low.

    On the scan and not on `asyncio.sleep`, like everything else a reference
    controller times, so that `Lockstep` can put it on the plant's clock. A
    sleeping feeder would go on emitting in wall-clock seconds while the plant
    ran at whatever speed lockstep managed, and nothing would say so.
    """

    def __init__(self, gap: float = EMIT_GAP) -> None:
        self.gap = gap
        self.left = 0.0
        self.on = False

    def __call__(self, dt: float) -> bool:
        self.left -= dt
        if self.left <= 0.0:
            self.on = not self.on
            self.left = EMIT_PULSE if self.on else self.gap
        return self.on

    def hold(self) -> bool:
        """The feeder while the belt is stopped: low, and a whole gap owed
        before the next carton. Resuming mid-pulse instead would raise a
        second edge the moment the belt restarts, and put a carton on top of
        the one that was just made -- found on the wall clock, where the
        mushroom can land inside a pulse (IP-35)."""
        self.on, self.left = False, self.gap
        return False


class Scanner:
    """A reference controller's scan loop, with the panel already solved.

    Every scene here that has an operator station wants the same three things
    -- momentary edges, a normally-closed mushroom, a latch only Reset clears --
    and writing that four times would be four chances to write it differently.
    This is the same contract `Station` in `tools/try_scene.py` implements
    against the 3D engine, and it is deliberately the *correct* one: a wrong
    reference is wrong about its scene's lesson, not about the panel.
    """

    def __init__(self, bus, latch_estop: bool = True,
                 start_clears_trip: bool = False) -> None:
        """`latch_estop=False` never reads the mushroom as a trip at all.
        `start_clears_trip=True` latches it but lets Start alone clear it once
        the mushroom is out -- the wrong answer to "only Reset clears it",
        for the references whose lesson is exactly that."""
        self.bus = bus
        self.latch_estop = latch_estop
        self.start_clears_trip = start_clears_trip
        self.running = False
        self.tripped = False
        self._prev = {"start": False, "stop": False, "reset": False}

    def bit(self, tag_id: str) -> bool:
        value = self.bus.read(tag_id)
        return bool(value) if value is not None else False

    def num(self, tag_id: str) -> float:
        value = self.bus.read(tag_id)
        return float(value) if value is not None else 0.0

    @property
    def setpoint(self) -> float:
        return self.num("panel.setpoint")

    def scan(self) -> dict[str, bool]:
        now = {k: self.bit(f"panel.{k}") for k in ("start", "stop", "reset")}
        edges = {k: now[k] and not self._prev[k] for k in now}
        self._prev = now

        healthy = self.bit("panel.estop")
        if self.latch_estop and not healthy:
            self.tripped = True
        elif edges["reset"] or (self.start_clears_trip and edges["start"]):
            self.tripped = False

        if self.tripped or edges["stop"]:
            self.running = False
        elif edges["start"] and (healthy or not self.latch_estop):
            self.running = True
        edges["healthy"] = healthy
        return edges

    def lamps(self) -> dict:
        return {"panel.green": self.running, "panel.red": self.tripped}


async def _idle(bus, stop: asyncio.Event) -> None:
    """Connects and does nothing. The run that must not be able to pass."""
    await stop.wait()


async def _forcer(bus, stop: asyncio.Event) -> None:
    """Forces whatever counter the scene has, so the numbers read well.

    Generic, because every scene has something a controller would rather lie
    about than earn. It pins every simulator-owned counter it can see.
    """
    targets = {tag.id: 20 for tag in bus.table
               if tag.id.endswith((".count", ".total"))
               or tag.id in ("counter.tall", "counter.short")}
    run_tags = [t for t in ("conveyor.rotate", "belt.rotate", "buffer.run",
                            "infeed.rotate", "scale.rotate") if t in bus.table]
    feed = Feed() if "emitter.emit" in bus.table else None
    state = {"force_in": 0.0}

    async def body(dt: float) -> None:
        writes = {tag_id: True for tag_id in run_tags}
        if feed is not None:
            writes["emitter.emit"] = feed(dt)
        await bus.write_many(writes)
        state["force_in"] -= dt
        if state["force_in"] <= 0.0:
            await bus.force(targets or {"panel.green": True})
            state["force_in"] = 0.5

    await run_scan(bus, stop, body)


#: The two above, by the name `--reference` knows them by.
SHARED = {"idle": _idle, "forcer": _forcer}
