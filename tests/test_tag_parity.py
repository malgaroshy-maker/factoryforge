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

import json
import math
from pathlib import Path

import pytest

from factoryforge_sidecar.tags import Tag, TagError, TagTable

ROOT = Path(__file__).resolve().parent.parent

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
