"""Runs engine/fixtures/tag_cases.json against the Python tag model.

The C# engine mirrors sidecar/factoryforge_sidecar/tags.py by hand (see the
comment on engine/src/TagBus/Tag.cs). Nothing enforced that agreement before
this -- both sides were diffed by eye once and left to drift. This test and its
C# counterpart (`godot --self-test=parity`, engine/src/Sim/TagParitySelfTest.cs)
run the identical fixture against both models, so a future edit to one side's
coercion rules that forgets the other fails a test instead of shipping quietly.
See FF-29.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from engine_stub import EngineStub            # noqa: E402
from factoryforge_sidecar.tags import Tag, TagError, TagTable  # noqa: E402

import check_protocol                         # noqa: E402

FIXTURE = json.loads(
    (ROOT / "engine" / "fixtures" / "tag_cases.json").read_text(encoding="utf-8")
)

#: JSON cannot spell a non-finite float, so a fixture case that needs one names
#: it instead. Both readers of the fixture decode the same three names.
SPECIALS = {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}


def _value(case: dict, key: str):
    if f"{key}_special" in case:
        return SPECIALS[case[f"{key}_special"]]
    return case[key]


def _probe(tag_type: str, value=None) -> Tag:
    return Tag(id="case", name="case", type=tag_type, kind="output", value=value)


@pytest.mark.parametrize("case", FIXTURE["coerce"],
                         ids=lambda c: f"{c['type']}:{c.get('input', c.get('input_special'))!r}")
def test_coerce(case: dict) -> None:
    tag = _probe(case["type"])
    if case.get("error"):
        with pytest.raises(TagError):
            tag.coerce(_value(case, "input"))
    else:
        assert tag.coerce(_value(case, "input")) == case["expect"]


@pytest.mark.parametrize(
    "case", FIXTURE["differs"],
    ids=lambda c: f"{c['type']}:{c['current']!r}->{c['candidate']!r}",
)
def test_differs(case: dict) -> None:
    tag = _probe(case["type"], case["current"])
    if case.get("error"):
        with pytest.raises(TagError):
            tag.differs(case["candidate"])
    else:
        assert tag.differs(case["candidate"]) == case["expect"]


@pytest.mark.parametrize(
    "case", FIXTURE["store"],
    ids=lambda c: f"{c['type']}:{c['current']!r}<-{c['write']!r}",
)
def test_store(case: dict) -> None:
    """The epsilon has to suppress the *store*, not only the comparison.

    HP-18.3: both tables reported "no change" for a sub-epsilon move and then
    stored the new value anyway, so the reference crept and a signal drifting by
    1e-9 a scan published on every scan after all. Not reachable over the wire
    -- an output's stored value is not observable there and an input's is
    driven only by the scene -- so it lives here, in the shared model fixture.
    """
    table = TagTable([Tag("case", "case", case["type"], "output", case["current"])])
    assert table.set("case", case["write"]) == case["expect_changed"]
    assert table.value("case") == case["expect_stored"]


# --- server behaviour, over the wire (HP-19) --------------------------------


class _ParityScene:
    """A scene that exists only to declare one tag of every role.

    `server_cases.json` names tags by role rather than by id, and the sorting
    scene declares no float tag at all -- so the Python half of the comparison
    needs a table that does. Nothing moves: every case drives the tags itself.
    """

    name = "tag-bus-parity"

    def __init__(self) -> None:
        self.tags = TagTable([
            Tag("probe.bit", "Probe Bit", "bit", "output"),
            Tag("probe.count", "Probe Count", "int", "output"),
            Tag("probe.level", "Probe Level", "float", "output"),
            Tag("probe.detect", "Probe Detect", "bit", "input"),
            Tag("probe.tally", "Probe Tally", "int", "input"),
            Tag("probe.reading", "Probe Reading", "float", "input"),
        ])

    def tick(self, dt: float) -> None:
        pass


@pytest_asyncio.fixture
async def parity_engine():
    stub = EngineStub(_ParityScene(), host="127.0.0.1", port=0, tick_ms=5)
    await stub.start()
    ticker = asyncio.create_task(stub._tick_loop())
    try:
        yield stub
    finally:
        ticker.cancel()
        await stub.stop()


SERVER_CASES = check_protocol.load_server_cases()


@pytest.mark.parametrize("case", SERVER_CASES, ids=lambda c: c["name"])
async def test_server_case(parity_engine, case: dict) -> None:
    """Run one shared server case against the Python engine.

    The identical case runs against the C# engine through
    `python tools/check_protocol.py`. That is the whole of HP-19: the two
    servers answer one file, rather than being diffed by eye and left to drift
    the way the two `Tag` models were before FF-29.
    """
    problems, skipped = await check_protocol.run_server_cases(
        parity_engine.url, timeout=5.0, cases=[case], settle=2.0)
    assert not skipped, skipped
    assert not problems, "; ".join(problems)
