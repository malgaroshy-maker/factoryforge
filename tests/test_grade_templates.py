"""The grader's plants against the engine's (IP-19).

The grader marks a student's program against Python models of the scenes, and
the engine runs the same scenes in 3D. They are two simulations of one plant,
and nothing used to check that they agreed. This file checks the half of that
which can be checked without Godot:

* **The tag table.** Each graded scene must offer exactly the tags (id, type,
  kind) the engine registers for it, or a mapping written against the 3D scene
  fails to connect to the exam. The engine's side is
  `engine/fixtures/scene_tag_sets.json`: `--self-test=scenes` (test plan C19,
  and the release gate) loads every scene in the manifest and fails unless its
  tags equal that file. So engine == fixture there, and fixture == grader here.
  The fixture is also checked against the templates' part ids here, so a part
  renamed in a template fails this file directly, not only once someone reruns
  the engine.
* **The numbers.** A number a template sets is read from it
  (`grading/templates.py`), and a test below moves one and watches the model
  follow. A number a C# part owns is a named constant in the grader, and a
  test below reads it out of the C# source.
* **The disagreements.** Where the model and the template disagree today, the
  model has been left as it was pending a decision, and the table below pins
  both sides: changing either one fails here, so the difference cannot move
  without somebody looking at it.

None of this starts Godot, and none of it runs a graded window.

The templates are the ones the grader reads: `engine/templates/`, unless
`FACTORYFORGE_TEMPLATES` points somewhere else -- which is how a changed
template can be checked against this file without touching the real one.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))

from factoryforge_sidecar.grading import plant, registry, templates  # noqa: E402
from factoryforge_sidecar.grading.scenes import (  # noqa: E402
    accumulation_buffer as ab, batch_dosing as bd, guarded_cell as gc,
    light_curtain_sorting as lc, pick_and_place_cell as pp,
    roller_line_weighing as rw, sorting_by_height as sh, start_stop_station as ss)

TEMPLATES = ROOT / "engine" / "templates"
FIXTURE = ROOT / "engine" / "fixtures" / "scene_tag_sets.json"
PARTS_SRC = ROOT / "engine" / "src" / "Parts"


def _manifest() -> list[dict]:
    manifest = templates.template_dir() / "manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8"))


def _fixture() -> dict[str, set[tuple[str, str, str]]]:
    scenes = json.loads(FIXTURE.read_text(encoding="utf-8"))["scenes"]
    return {scene: {(t["id"], t["type"], t["kind"]) for t in tags}
            for scene, tags in scenes.items()}


def _templated() -> dict[str, dict]:
    """`{scene id: template json}` for every manifest scene built from one."""
    out = {}
    for entry in _manifest():
        if entry["path"]:
            name = entry["path"].removeprefix("res://templates/")
            out[entry["id"]] = json.loads(
                (templates.template_dir() / name).read_text(encoding="utf-8"))
    return out


# --- the chain this file relies on --------------------------------------

def test_the_fixture_is_what_the_engine_self_test_checks():
    """The comparison below is only worth anything if the fixture is what the
    engine is held to. `--self-test=scenes` fails for a manifest scene with no
    entry, and for any tag either side has that the other lacks
    (`engine/src/Sim/SceneTagSetSelfTest.cs`), so it is enough that the
    fixture covers the manifest exactly and that the self-test is run -- by
    the test plan and by the release gate."""
    assert set(_fixture()) == {entry["id"] for entry in _manifest()}

    selftest = (ROOT / "engine" / "src" / "Sim" / "SceneTagSetSelfTest.cs").read_text(
        encoding="utf-8")
    assert "res://fixtures/scene_tag_sets.json" in selftest
    plan = (ROOT / "tools" / "test_plan.py").read_text(encoding="utf-8")
    assert re.search(r'_self_test\(\s*"C19".*"scenes"\)', plan)
    gate = (ROOT / "tools" / "packaging" / "check_release.py").read_text(encoding="utf-8")
    assert '"scenes"' in gate


def test_every_graded_scene_is_a_scene_the_engine_ships():
    assert set(registry.rubrics()) <= {entry["id"] for entry in _manifest()}


# --- the tag table ------------------------------------------------------

@pytest.mark.parametrize("scene", sorted(registry.rubrics()))
def test_a_graded_scene_offers_the_tags_the_engine_registers(scene):
    """Ids, types and kinds, exactly: a tag the engine has and the exam does
    not is a mapping that works on the 3D scene and fails to connect here."""
    grader = {(t.id, t.type, t.kind) for t in registry.rubrics()[scene]["build"](1).tags}
    engine = _fixture()[scene]
    by_id = lambda tags: {tid: (ty, kind) for tid, ty, kind in tags}  # noqa: E731
    g, e = by_id(grader), by_id(engine)
    problems = [f"engine has {tid} ({ty}/{kind}), the grader does not"
                for tid, (ty, kind) in sorted(e.items()) if tid not in g]
    problems += [f"grader has {tid} ({ty}/{kind}), the engine does not"
                 for tid, (ty, kind) in sorted(g.items()) if tid not in e]
    problems += [f"{tid}: engine {e[tid][0]}/{e[tid][1]}, grader {g[tid][0]}/{g[tid][1]}"
                 for tid in sorted(set(g) & set(e)) if g[tid] != e[tid]]
    assert not problems, f"{scene}:\n  " + "\n  ".join(problems)


def test_the_fixture_names_the_parts_the_templates_place():
    """A tag's prefix is its part's id (AGENTS.md), so a part renamed in a
    template shows up as a prefix the template no longer has and an id the
    fixture has never heard of -- here, before the engine is ever run."""
    fixture = _fixture()
    templated = _templated()
    prefixes = {scene: {tid.split(".", 1)[0] for tid, _, _ in tags}
                for scene, tags in fixture.items()}
    # A type is tagless if no part of it, in any template, has a tag. Read
    # from the data rather than listed, so a new tagless part costs nothing.
    tagged_types = {part["type"] for scene, data in templated.items()
                    for part in data["parts"] if part["id"] in prefixes[scene]}
    problems = []
    for scene, data in sorted(templated.items()):
        ids = {part["id"]: part["type"] for part in data["parts"]}
        for prefix in sorted(prefixes[scene] - set(ids)):
            problems.append(f"{scene}: the fixture has {prefix}.* tags and the "
                            f"template places no part {prefix!r}")
        for part_id, part_type in sorted(ids.items()):
            if part_id not in prefixes[scene] and part_type in tagged_types:
                problems.append(f"{scene}: the template places {part_id!r} "
                                f"({part_type}) and the fixture has no {part_id}.* tag")
    assert not problems, "\n".join(problems)
    assert tagged_types, "found no tagged part at all -- the check is not looking"


def _analog_part_types() -> set[str]:
    """Every part type holding an `AnalogSignal`, read from the C# source."""
    found = set()
    for source in PARTS_SRC.glob("*.cs"):
        text = source.read_text(encoding="utf-8")
        if re.search(r"public\s+AnalogSignal\s+\w+\s*\{\s*get;\s*\}", text):
            assert re.search(rf"\bclass\s+{source.stem}\b", text), source
            found.add(source.stem)
    return found


def test_every_analog_input_a_template_places_is_in_engineering_units():
    """IP-16 lets an analog input publish raw S7 counts as an `int`. Every
    grader model publishes engineering units, as a float, so the assumption
    that every template uses the default is asserted rather than trusted.
    The fixture would catch the type change too, once regenerated; this says
    why."""
    analog = _analog_part_types()
    assert {"LevelTank", "HeatingStation", "FlowMeter", "MotorStarter",
            "WeighingConveyor"} <= analog, analog
    problems = [f"{scene}: {part['id']} ({part['type']}) "
                f"signal={part['properties']['signal']!r}"
                for scene, data in sorted(_templated().items())
                for part in data["parts"]
                if part["type"] in analog
                and part.get("properties", {}).get("signal", "engineering") != "engineering"]
    assert not problems, "\n".join(problems)


def test_the_loader_refuses_a_raw_analog_input(tmp_path, monkeypatch):
    """...and the grader itself refuses to model one, rather than publishing a
    float where the engine publishes counts."""
    copy = _copy_templates(tmp_path)
    _edit(copy / "tank_level_control.json", "tank", signal="s7_raw")
    monkeypatch.setenv(templates.TEMPLATES_ENV, str(copy))
    part = templates.template("tank-level-control").part("tank", "LevelTank")
    with pytest.raises(templates.TemplateError, match="s7_raw"):
        part.engineering_units()


# --- numbers the C# parts own -------------------------------------------

def _cs(path: str, pattern: str) -> str:
    text = (ROOT / "engine" / "src" / path).read_text(encoding="utf-8")
    match = re.search(pattern, text)
    assert match, f"{path}: nothing matches {pattern!r} -- has the part changed?"
    return match.group(1)


#: `(value in the grader, C# file, pattern whose group is the C# value)`.
#: Each is a number no template sets, kept as a named constant in the grader
#: with a comment naming the file. This is what makes that comment true.
CSHARP_MIRRORS = [
    (plant.CARTON_LENGTH, "Parts/BoxPhysics.cs", r"float Length \{ get; set; \} = ([\d.]+)f"),
    (plant.CARTON_WIDTH, "Parts/BoxPhysics.cs", r"float Width \{ get; set; \} = ([\d.]+)f"),
    (plant.TALL_HEIGHT, "Parts/BoxPhysics.cs", r"IsTall \? ([\d.]+)f :"),
    (plant.SHORT_HEIGHT, "Parts/BoxPhysics.cs", r"IsTall \? [\d.]+f : ([\d.]+)f"),
    (plant.CARDBOARD_DENSITY, "Parts/BoxPhysics.cs", r"CartonDensity = ([\d.]+)f"),
    (plant.STEEL_DENSITY, "Parts/BoxPhysics.cs", r"MetalDensity = ([\d.]+)f"),
    (gc.GC_INRUSH_FACTOR, "Parts/MotorStarter.cs", r"InrushFactor = ([\d.]+)f"),
    (gc.GC_INRUSH_DECAY, "Parts/MotorStarter.cs", r"InrushDecay = ([\d.]+)f"),
    (lc.LC_LOWEST_BEAM, "Parts/LightArray.cs", r"baseY \+ ([\d.]+)f \+"),
    (lc.LC_CATCH, "Scenes/SortingScene.cs", r"PusherCatch = ([\d.]+);"),
    (pp.PP_CODES[0], "Parts/BarcodeScanner.cs", r"CodeShortCarton = (\d+);"),
    (pp.PP_CODES[1], "Parts/BarcodeScanner.cs", r"CodeTallCarton = (\d+);"),
    (pp.PP_CODES[2], "Parts/BarcodeScanner.cs", r"CodeMetal = (\d+);"),
    (sh.SORTING_PANEL_SETPOINT, "Editor/SceneEditor.DefaultScene.cs",
     r'ConfigureSetpoint\([\d.]+f, [\d.]+f, "s", ([\d.]+)f\)'),
]


@pytest.mark.parametrize("value,path,pattern", CSHARP_MIRRORS,
                         ids=[f"{p}:{v}" for v, p, _ in CSHARP_MIRRORS])
def test_a_number_the_part_owns_matches_the_csharp(value, path, pattern):
    csharp = float(_cs(path, pattern))
    assert csharp == pytest.approx(value, abs=1e-9), (
        f"{path} has {csharp:g} and the grader mirrors it as {value:g}")


# --- numbers the templates own ------------------------------------------

def _copy_templates(tmp_path: Path) -> Path:
    copy = tmp_path / "templates"
    shutil.copytree(TEMPLATES, copy)
    return copy


def _edit(path: Path, part_id: str, **properties) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    (part,) = [p for p in data["parts"] if p["id"] == part_id]
    part["properties"].update({k: str(v) for k, v in properties.items()})
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


_PROBE = """
import sys
sys.path.insert(0, {sidecar!r})
from factoryforge_sidecar.grading.scenes import batch_dosing as bd
from factoryforge_sidecar.grading.scenes import start_stop_station as ss
from factoryforge_sidecar.grading.scenes import heat_treat_station as ht
from factoryforge_sidecar.grading.scenes import guarded_cell as gc
print(bd.BD_RATED_FIRST, bd.BD_RATED_THEN, ss.SS_BELT_SPEED, ht.OVEN_POWER,
      gc.GC_MUTE_LIMIT)
"""


def _probe(env_dir: Path | None) -> list[float]:
    env = {k: v for k, v in os.environ.items() if k != templates.TEMPLATES_ENV}
    if env_dir is not None:
        env[templates.TEMPLATES_ENV] = str(env_dir)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(sidecar=str(ROOT / "sidecar"))],
        capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, result.stderr
    return [float(v) for v in result.stdout.split()]


def test_the_plant_moves_with_its_template(tmp_path):
    """IP-19's own verification: retune the scene and the grader's plant is
    retuned with it. In a fresh interpreter, because the scene modules read
    their template once, when they are imported. Each number is moved away
    from whatever the checkout has, so the test cannot pass by the edit
    happening to be a no-op."""
    rated, _, speed, power, mute = _probe(None)

    copy = _copy_templates(tmp_path)
    _edit(copy / "batch_dosing.json", "pump", rated_flow=rated + 17)
    _edit(copy / "start_stop_station.json", "belt", speed=speed + 0.15)
    _edit(copy / "heat_treat_station.json", "oven", heater_power=power - 15)
    _edit(copy / "guarded_cell.json", "scanner", mute_limit=mute + 2)
    # The re-rating is the exam's: a fixed fraction of whatever the pump is
    # rated for, so it follows the rating.
    assert _probe(copy) == pytest.approx(
        [rated + 17, (rated + 17) * bd.BD_RERATE, speed + 0.15, power - 15, mute + 2])


def test_the_templates_are_found_in_a_checkout(monkeypatch):
    monkeypatch.delenv(templates.TEMPLATES_ENV, raising=False)
    assert templates.template_dir() == TEMPLATES


def test_a_missing_template_directory_says_what_to_set(tmp_path, monkeypatch):
    monkeypatch.setenv(templates.TEMPLATES_ENV, str(tmp_path / "nowhere"))
    with pytest.raises(templates.TemplateError, match=templates.TEMPLATES_ENV):
        templates.template_dir()


def test_a_template_that_lost_a_part_or_a_property_is_named(tmp_path, monkeypatch):
    copy = _copy_templates(tmp_path)
    data = json.loads((copy / "batch_dosing.json").read_text(encoding="utf-8"))
    for part in data["parts"]:
        if part["id"] == "pump":
            del part["properties"]["rated_flow"]
        if part["id"] == "meter":
            part["type"] = "InductiveSensor"
    (copy / "batch_dosing.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv(templates.TEMPLATES_ENV, str(copy))

    plant_ = templates.template("batch-dosing")
    with pytest.raises(templates.TemplateError, match="'pump'.*'rated_flow'"):
        plant_.part("pump", "DosingPump").number("rated_flow")
    with pytest.raises(templates.TemplateError, match="'meter' is a InductiveSensor"):
        plant_.part("meter", "FlowMeter")
    with pytest.raises(templates.TemplateError, match="no part 'doser'"):
        plant_.part("doser", "DosingPump")


def test_a_scene_built_in_csharp_has_no_template_to_read():
    with pytest.raises(templates.TemplateError, match="not built from a template"):
        templates.template("sorting-by-height")


# --- where the model and the template disagree today --------------------
#
# Found by IP-19. Each of these is a place the grader's model has never been
# the plant the template builds -- every one is a position -- and each was left
# exactly as it was, because moving it changes timings a mark can depend on
# and that is a decision, not a refactor. Both sides are pinned: fix the model,
# or move the part, and this fails and asks for the entry to go.

def _t(scene: str) -> templates.Template:
    return templates.template(scene)


def _field_edge(sign: int) -> float:
    """Where the guarded cell's protective field crosses the belt's centreline:
    `AreaScanner.cs` measures a carton's centre, flat, from the scanner."""
    scanner = _t("guarded-cell").part("scanner", "AreaScanner")
    reach = math.sqrt(scanner.number("stop_radius") ** 2 - scanner.position[2] ** 2)
    return round(scanner.x + sign * reach, 2)


DISAGREEMENTS = [
    # (constant, the model's value, the template's, how the template's is read)
    ("SS_EYE_POS", ss.SS_EYE_POS, 1.5, 2.0,
     lambda: _t("start-stop-station").part("part_present", "PhotoelectricSensor").x),
    ("SS_REMOVER_POS", ss.SS_REMOVER_POS, 2.8, 3.0,
     lambda: _t("start-stop-station").part("belt", "ConveyorBelt").span()[1]),
    ("LC_CURTAIN_POS", lc.LC_CURTAIN_POS, 1.2, 1.5,
     lambda: _t("light-curtain-sorting").part("height_gauge", "LightArray").x),
    ("LC_DIVERTER_POS", lc.LC_DIVERTER_POS, 2.2, 2.5,
     lambda: _t("light-curtain-sorting").part("diverter", "PusherMechanism").x),
    ("RW_METAL_EYE_POS", rw.RW_METAL_EYE_POS, 1.2, 1.5,
     lambda: _t("roller-line-weighing").part("metal_check", "InductiveSensor").x),
    ("RW_REMOVER_POS", rw.RW_REMOVER_POS, 3.3, 3.0,
     lambda: _t("roller-line-weighing").part("scale", "WeighingConveyor").span()[1]),
    ("PP_SCANNER_POS", pp.PP_SCANNER_POS, 1.4, 1.5,
     lambda: _t("pick-and-place-cell").part("scanner", "BarcodeScanner").x),
    ("PP_STATION_POS", pp.PP_STATION_POS, 2.9, 2.5,
     lambda: _t("pick-and-place-cell").part("atstation", "PhotoelectricSensor").x),
    ("AB_BLADE_POS", ab.AB_BLADE_POS, 3.0, 2.6,
     lambda: _t("accumulation-buffer").part("stop", "StopGate").x),
    ("AB_EYE_POS", ab.AB_EYE_POS, 3.15, 2.75,
     lambda: _t("accumulation-buffer").part("exit_eye", "PhotoelectricSensor").x),
    ("GC_MUTE_EYE_POS", gc.GC_MUTE_EYE_POS, 1.35, 1.0,
     lambda: _t("guarded-cell").part("mute_eye", "PhotoelectricSensor").x),
    ("GC_PUSH_EYE_POS", gc.GC_PUSH_EYE_POS, 2.8, 2.0,
     lambda: _t("guarded-cell").part("push_eye", "PhotoelectricSensor").x),
    ("GC_STATION_POS", gc.GC_STATION_POS, 3.2, 2.5,
     lambda: _t("guarded-cell").part("cylinder", "PneumaticCylinder").x),
    ("GC_LINE_END_POS", gc.GC_LINE_END_POS, 3.9, 3.0,
     lambda: _t("guarded-cell").part("belt", "ConveyorBelt").span()[1]),
    ("GC_FIELD_FROM", gc.GC_FIELD_FROM, 1.6, 1.04, lambda: _field_edge(-1)),
    ("GC_FIELD_TO", gc.GC_FIELD_TO, 2.4, 1.96, lambda: _field_edge(+1)),
]


@pytest.mark.parametrize("name,model,model_was,template_was,read", DISAGREEMENTS,
                         ids=[d[0] for d in DISAGREEMENTS])
def test_a_known_disagreement_has_not_moved_unnoticed(name, model, model_was,
                                                      template_was, read):
    template_is = read()
    assert (model, template_is) == (model_was, template_was), (
        f"{name}: the model has {model} (was {model_was}) and the template "
        f"{template_is} (was {template_was}). If they now agree, the finding is "
        f"resolved: load it from the template and delete this entry.")


@pytest.mark.parametrize("scene", sorted(set(registry.rubrics()) - {sh.SCENE}))
def test_the_pot_starts_where_the_model_has_always_started_it(scene):
    """One more disagreement, the same in every templated scene: the engine's
    pot starts at the template's `setpoint`, and the model's at 0 until the
    exam turns it, a fraction of a second in. Pinned for the same reason."""
    model = registry.rubrics()[scene]["build"](1).tags.value("panel.setpoint")
    panel = _t(scene).part("panel", "ButtonPanel")
    assert model == 0.0
    assert panel.number("setpoint") != 0.0


def test_the_pot_range_holds_every_setpoint_the_exam_turns_it_to():
    """A real pot stops at its end stops. An exam that turned it past one
    would be marking a setting the engine's panel cannot reach, so every
    number the examiner sets, across many seeds, is inside the template's
    `setpoint_min` .. `setpoint_max`. Only the script runs here: the plant is
    never stepped."""
    problems = []
    for scene, rubric in sorted(registry.rubrics().items()):
        if scene == sh.SCENE:
            continue
        panel = _t(scene).part("panel", "ButtonPanel")
        low, high = panel.number("setpoint_min"), panel.number("setpoint_max")
        for seed in range(40):
            sim = rubric["build"](seed)
            seen = set()
            for step in range(0, 100 * int(rubric["duration"]) + 1, 10):
                sim.script.run(step / 100)
                seen.add(sim.panel.setpoint_value)
            outside = sorted(v for v in seen - {0.0} if not low <= v <= high)
            if outside:
                problems.append(f"{scene} seed {seed}: {outside} outside {low:g}..{high:g}")
    assert not problems, "\n".join(problems[:20])
