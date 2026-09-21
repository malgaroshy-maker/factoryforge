"""The sidecar CLI: the flags it advertises, and the values it hands to drivers.

Both halves are places where something looked like it worked and did not --
a documented flag that was parsed and never read, and driver options that
arrived as strings and stayed strings all the way into ctypes.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from factoryforge_sidecar.__main__ import demo


def demo_args(**overrides) -> SimpleNamespace:
    args = dict(driver="mock", mapping=None, host="127.0.0.1", port=0,
                tick=10, duration=None, option=None)
    args.update(overrides)
    return SimpleNamespace(**args)


async def test_demo_duration_shuts_the_run_down():
    """AGENTS.md gotcha 6 tells you to use `demo --duration N` "which shuts
    down cleanly". The subparser accepted the flag and nothing ever read it:
    the demo path awaited an Event nobody sets, forever.

    Timed against the wall clock, not by counting iterations -- under Windows'
    15.6ms asyncio resolution short sleeps return immediately (gotcha 2).
    """
    started = time.monotonic()
    assert await asyncio.wait_for(demo(demo_args(duration=1.0)), timeout=30) == 0
    elapsed = time.monotonic() - started
    assert elapsed >= 0.9, f"it returned after {elapsed:.2f}s without waiting"
    assert elapsed < 20, "it did not stop on its own"


async def test_demo_without_a_duration_runs_until_it_is_told_to_stop():
    """The other half of the same behaviour: no --duration still means forever,
    so a run you are watching is not cut short by a default."""
    task = asyncio.create_task(demo(demo_args()))
    try:
        await asyncio.sleep(1.5)
        assert not task.done(), "the demo stopped on its own with no --duration"
    finally:
        # demo() treats a cancel as the Ctrl-C it was written for: it shuts the
        # driver and the engine down and returns 0 rather than propagating.
        task.cancel()
        assert await asyncio.wait_for(task, timeout=10) == 0
