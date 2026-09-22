from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))
sys.path.insert(0, str(ROOT / "harness"))

from engine_stub import EngineStub            # noqa: E402
from factoryforge_sidecar import drivers      # noqa: E402
from factoryforge_sidecar.tagbus import TagBusClient  # noqa: E402
from scene import SortingScene                # noqa: E402

#: Tests use a fast tick so they finish quickly; production default is 10ms.
TEST_TICK_MS = 5

#: Files whose every test carries the `graded` marker (registered in pytest.ini).
#: The grader's tests drive whole scenes to a verdict and are most of the
#: suite's wall clock, so CI runs them as a job of their own (IP-04) and the
#: fast checks report in minutes rather than waiting behind them. Applied here,
#: keyed on the file, so a test added to test_grade.py is marked without anyone
#: remembering to.
GRADED_FILES = {"test_grade.py"}


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # tryfirst: `-m` is applied by pytest's own collection_modifyitems hook,
    # and the marker has to be on the item before that deselects anything.
    for item in items:
        if item.path.name in GRADED_FILES:
            item.add_marker(pytest.mark.graded)


@pytest.fixture
def scene() -> SortingScene:
    return SortingScene()


@pytest_asyncio.fixture
async def engine(scene):
    """A running engine stub on an ephemeral port."""
    stub = EngineStub(scene, host="127.0.0.1", port=0, tick_ms=TEST_TICK_MS)
    await stub.start()
    ticker = asyncio.create_task(stub._tick_loop())
    try:
        yield stub
    finally:
        ticker.cancel()
        await stub.stop()


@pytest_asyncio.fixture
async def bus(engine):
    """A sidecar bus client connected to the engine."""
    client = TagBusClient(engine.url)
    runner = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(client.connected.wait(), timeout=5)
        yield client
    finally:
        runner.cancel()


@pytest_asyncio.fixture
async def mock(bus):
    """A started mock driver, ready once the engine has described the scene."""
    driver = drivers.create("mock", bus)
    await driver.start()
    await driver.ready()
    try:
        yield driver
    finally:
        await driver.stop()
