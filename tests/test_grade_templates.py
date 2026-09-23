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
* **The positions and the behaviour (IP-29).** IP-19 found the models
  disagreeing with the templates about where half the parts were, and about
  some of what they did; it pinned both sides pending a decision. The user's
  decision was that the grader adopts the engine's values everywhere, so each
  of those is now asserted as agreement, worked out from the template and the
  C# part independently of the model -- and each behaviour the model gained is
  stepped here on its own. What the exam does on purpose that the engine would
  not (the shuffled feeds) is written down, with its reason, rather than
  pinned.

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
    roller_line_weighing as rw, servo_positioning as sv, sorting_by_height as sh,
    star_delta_start as sd, start_stop_station as ss)

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
    (pp.PP_DEFAULT_READ_HOLD, "Parts/ButtonPanel.cs", r"DefaultPressHold = ([\d.]+)f;"),
    (sh.SORTING_PANEL_SETPOINT, "Editor/SceneEditor.DefaultScene.cs",
     r'ConfigureSetpoint\([\d.]+f, [\d.]+f, "s", ([\d.]+)f\)'),
    # IP-29: the numbers the positions and behaviours the models adopted rest on.
    (plant.WORK_PLANE_Y, "Parts/PartLayout.cs", r"WorkPlaneY = ([\d.]+)f"),
    (plant.BELT_THICKNESS, "Parts/PartLayout.cs", r"BeltThickness = ([\d.]+)f"),
    (plant.VFD_DEAD_BAND, "Parts/VariableConveyor.cs", r"DeadBand = ([\d.]+)f"),
    (ab.AB_BLADE_THICKNESS, "Parts/StopGate.cs", r"BladeThickness = ([\d.]+)f"),
    (rw.RW_SCALE_AREA, "Parts/WeighingConveyor.cs",
     r"new Vector3\(Size\.X \* ([\d.]+)f, 0\.30f, Size\.Z\)"),
    (pp.PP_PICK_ZONE, "Parts/PickPlaceArm.cs",
     r"new BoxShape3D \{ Size = new Vector3\(([\d.]+)f, 0\.14f, 0\.28f\)"),
    (pp.PP_RAIL_Y, "Parts/PickPlaceArm.cs", r"const float RailY = ([\d.]+)f"),
    (pp.PP_COLUMN_DROP, "Parts/PickPlaceArm.cs", r"ColumnTopY = RailY - ([\d.]+)f"),
    (pp.PP_REST_STUB, "Parts/PickPlaceArm.cs", r"const float restStub = ([\d.]+)f"),
    (gc.GC_ROD_STUB, "Parts/PneumaticCylinder.cs",
     r"Mathf\.Max\(Extension \+ ([\d.]+)f, [\d.]+f\)"),
    (gc.GC_PLATE_THICKNESS, "Parts/PneumaticCylinder.cs",
     r"PlateThickness = ([\d.]+)f"),
    (gc.GC_GATE_CLOSED_BELOW, "Parts/SafetyGate.cs", r"IsClosed => _opening <= ([\d.]+)f"),
    # IP-14: the star-delta starter, which no template configures beyond its
    # rating, load and inertia.
    (sd.SD_PULL_IN, "Parts/StarDeltaStarter.cs", r"PullInTime = ([\d.]+)f"),
    (sd.SD_DROP_OUT, "Parts/StarDeltaStarter.cs", r"DropOutTime = ([\d.]+)f"),
    (sd.SD_OVERLOAD_SETTING, "Parts/StarDeltaStarter.cs", r"OverloadSetting = ([\d.]+)f"),
    (sd.SD_OVERLOAD_CLASS, "Parts/StarDeltaStarter.cs", r"OverloadClass = ([\d.]+)f"),
    (sd.SD_LOCKED_ROTOR_CURRENT, "Parts/StarDeltaStarter.cs", r"LockedRotorCurrent = ([\d.]+)f"),
    (sd.SD_MAGNETISING_CURRENT, "Parts/StarDeltaStarter.cs", r"MagnetisingCurrent = ([\d.]+)f"),
    (sd.SD_CURRENT_KNEE_SLIP, "Parts/StarDeltaStarter.cs", r"CurrentKneeSlip = ([\d.]+)f"),
    (sd.SD_RATED_SLIP, "Parts/StarDeltaStarter.cs", r"RatedSlip = ([\d.]+)f"),
    (sd.SD_STARTING_TORQUE, "Parts/StarDeltaStarter.cs", r"StartingTorque = ([\d.]+)f"),
    (sd.SD_PULL_OUT_TORQUE, "Parts/StarDeltaStarter.cs", r"PullOutTorque = ([\d.]+)f"),
    (sd.SD_PULL_OUT_SPEED, "Parts/StarDeltaStarter.cs", r"PullOutSpeed = ([\d.]+)f"),
    (sd.SD_FRICTION_TORQUE, "Parts/StarDeltaStarter.cs", r"FrictionTorque = ([\d.]+)f"),
    (sd.SD_CLASS_MULTIPLE, "Parts/ThermalOverload.cs", r"ClassMultiple = ([\d.]+)f"),
    (sv.SV_ENABLE_DELAY, "Parts/ServoAxis.cs", r"EnableDelay = ([\d.]+)f"),
    (sv.SV_QUICK_STOP, "Parts/ServoAxis.cs", r"QuickStopFactor = ([\d.]+)f"),
]


def test_the_grader_falls_under_godots_default_gravity():
    """A carton the gantry lets go of falls under the project's gravity
    (`pp.GRAVITY`). The project does not set one, so it is Godot's 9.8."""
    project = (ROOT / "engine" / "project.godot").read_text(encoding="utf-8")
    assert "default_gravity" not in project
    assert pp.GRAVITY == 9.8


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


def _move(path: Path, part_id: str, x: float) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    (part,) = [p for p in data["parts"] if p["id"] == part_id]
    part["position"][0] = x
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


_PROBE = """
import sys
sys.path.insert(0, {sidecar!r})
from factoryforge_sidecar.grading.scenes import batch_dosing as bd
from factoryforge_sidecar.grading.scenes import start_stop_station as ss
from factoryforge_sidecar.grading.scenes import heat_treat_station as ht
from factoryforge_sidecar.grading.scenes import guarded_cell as gc
print(bd.BD_RATED_FIRST, bd.BD_RATED_THEN, ss.SS_BELT_SPEED, ht.OVEN_POWER,
      gc.GC_MUTE_LIMIT, ss.SS_EYE_POS, gc.GC_STATION_POS)
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
    rated, _, speed, power, mute, eye, station = _probe(None)

    copy = _copy_templates(tmp_path)
    _edit(copy / "batch_dosing.json", "pump", rated_flow=rated + 17)
    _edit(copy / "start_stop_station.json", "belt", speed=speed + 0.15)
    _edit(copy / "heat_treat_station.json", "oven", heater_power=power - 15)
    _edit(copy / "guarded_cell.json", "scanner", mute_limit=mute + 2)
    # IP-29: positions too, which until then the models kept of their own.
    _move(copy / "start_stop_station.json", "part_present", eye - 0.25)
    _move(copy / "guarded_cell.json", "cylinder", station + 0.1)
    # The re-rating is the exam's: a fixed fraction of whatever the pump is
    # rated for, so it follows the rating.
    assert _probe(copy) == pytest.approx(
        [rated + 17, (rated + 17) * bd.BD_RERATE, speed + 0.15, power - 15, mute + 2,
         eye - 0.25, station + 0.1])


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


# --- where the model and the template used to disagree (IP-19, IP-29) ----
#
# IP-19 found every one of these: a place the grader's model had never been the
# plant the template builds. IP-29 settled them the way the user decided -- the
# grader adopts the engine's values everywhere, because it has to mark the
# plant the student sees -- and each is now asserted as agreement. The
# template's side is worked out here from the template and the C# part, not
# through the model's own helpers, so a model that drifts back fails. The old
# model value is kept in the table as history, and as a second tripwire: a
# number that has gone back to what IP-19 found is named as such.

def _t(scene: str) -> templates.Template:
    return templates.template(scene)


def _field_edge(sign: int, key: str = "stop_radius") -> float:
    """Where the guarded cell's scanner field crosses the belt's centreline:
    `AreaScanner.cs` measures a carton's centre, flat, from the scanner."""
    cell = _t("guarded-cell")
    scanner = cell.part("scanner", "AreaScanner")
    off = scanner.position[2] - cell.part("belt", "ConveyorBelt").position[2]
    reach = math.sqrt(scanner.number(key) ** 2 - off ** 2)
    return scanner.x + sign * reach


def _taken_at(scene: str, remover_id: str) -> float:
    """Where a `Remover` takes a carton riding the belt: `Remover.cs` fires on
    the first overlap of the two colliders, so as the carton's nose reaches
    the zone's near face -- provided the zone reaches above the deck, which is
    asserted rather than assumed."""
    remover = _t(scene).part(remover_id, "Remover")
    assert remover.position[1] + remover.number("zone_y") / 2 > (
        plant.WORK_PLANE_Y + plant.BELT_THICKNESS / 2), (scene, remover_id)
    return remover.x - remover.number("zone_x") / 2 - plant.CARTON_LENGTH / 2


def _emitter(scene: str) -> float:
    return _t(scene).part("emitter", "Emitter").x


AGREEMENTS = [
    # (constant, the model's value now, what IP-19 found it at, the template's)
    ("SS_EYE_POS", ss.SS_EYE_POS, 1.5,
     lambda: _t("start-stop-station").part("part_present", "PhotoelectricSensor").x),
    ("SS_REMOVER_POS", ss.SS_REMOVER_POS, 2.8,
     lambda: _taken_at("start-stop-station", "counter")),
    ("SS_START_POS", ss.SS_START_POS, None, lambda: _emitter("start-stop-station")),
    ("LC_CURTAIN_POS", lc.LC_CURTAIN_POS, 1.2,
     lambda: _t("light-curtain-sorting").part("height_gauge", "LightArray").x),
    ("LC_DIVERTER_POS", lc.LC_DIVERTER_POS, 2.2,
     lambda: _t("light-curtain-sorting").part("diverter", "PusherMechanism").x),
    ("LC_REMOVER_POS", lc.LC_REMOVER_POS, 3.0,
     lambda: _taken_at("light-curtain-sorting", "short_count")),
    ("LC_START_POS", lc.LC_START_POS, None, lambda: _emitter("light-curtain-sorting")),
    ("RW_METAL_EYE_POS", rw.RW_METAL_EYE_POS, 1.2,
     lambda: _t("roller-line-weighing").part("metal_check", "InductiveSensor").x),
    ("RW_REMOVER_POS", rw.RW_REMOVER_POS, 3.3,
     lambda: _taken_at("roller-line-weighing", "outfeed")),
    ("RW_START_POS", rw.RW_START_POS, None, lambda: _emitter("roller-line-weighing")),
    ("PP_SCANNER_POS", pp.PP_SCANNER_POS, 1.4,
     lambda: _t("pick-and-place-cell").part("scanner", "BarcodeScanner").x),
    ("PP_EYE_POS", pp.PP_EYE_POS, 2.9,
     lambda: _t("pick-and-place-cell").part("atstation", "PhotoelectricSensor").x),
    # The model picked at 2.9 and called that 0 % of the rail.
    ("PP_RAIL_FROM", pp.PP_RAIL_FROM, 2.9,
     lambda: (_t("pick-and-place-cell").part("gantry", "PickPlaceArm").x
              - _t("pick-and-place-cell").part("gantry", "PickPlaceArm")
              .number("rail_length") / 2)),
    ("PP_OUTFEED_X", pp.PP_OUTFEED_X, None,
     lambda: _t("pick-and-place-cell").part("outfeed", "Remover").x),
    ("PP_START_POS", pp.PP_START_POS, None, lambda: _emitter("pick-and-place-cell")),
    ("AB_BLADE_POS", ab.AB_BLADE_POS, 3.0,
     lambda: _t("accumulation-buffer").part("stop", "StopGate").x),
    ("AB_EYE_POS", ab.AB_EYE_POS, 3.15,
     lambda: _t("accumulation-buffer").part("exit_eye", "PhotoelectricSensor").x),
    ("AB_START_POS", ab.AB_START_POS, 0.0, lambda: _emitter("accumulation-buffer")),
    ("AB_REMOVER_POS", ab.AB_REMOVER_POS, 4.2,
     lambda: _taken_at("accumulation-buffer", "released")),
    ("GC_MUTE_EYE_POS", gc.GC_MUTE_EYE_POS, 1.35,
     lambda: _t("guarded-cell").part("mute_eye", "PhotoelectricSensor").x),
    ("GC_PUSH_EYE_POS", gc.GC_PUSH_EYE_POS, 2.8,
     lambda: _t("guarded-cell").part("push_eye", "PhotoelectricSensor").x),
    ("GC_STATION_POS", gc.GC_STATION_POS, 3.2,
     lambda: _t("guarded-cell").part("cylinder", "PneumaticCylinder").x),
    ("GC_LINE_END_POS", gc.GC_LINE_END_POS, 3.9,
     lambda: _taken_at("guarded-cell", "line_end")),
    ("GC_FIELD_FROM", gc.GC_FIELD_FROM, 1.6, lambda: _field_edge(-1)),
    ("GC_FIELD_TO", gc.GC_FIELD_TO, 2.4, lambda: _field_edge(+1)),
    # The model used the protective field as the warning field as well.
    ("GC_WARN_FROM", gc.GC_WARN_FROM, 1.6, lambda: _field_edge(-1, "warn_radius")),
    ("GC_WARN_TO", gc.GC_WARN_TO, 2.4, lambda: _field_edge(+1, "warn_radius")),
    ("GC_START_POS", gc.GC_START_POS, None, lambda: _emitter("guarded-cell")),
]


@pytest.mark.parametrize("name,model,model_was,read", AGREEMENTS,
                         ids=[a[0] for a in AGREEMENTS])
def test_the_model_puts_each_part_where_the_template_does(name, model, model_was, read):
    template_is = read()
    assert model == pytest.approx(template_is, abs=1e-9), (
        f"{name}: the model has {model}, the template {template_is:g}"
        + (f" -- and {model_was} is the number IP-19 found the model at, before "
           f"IP-29 made it read the template" if model == model_was else ""))


def test_no_disagreement_is_left_pinned():
    """IP-19 pinned sixteen positions and the pot here as disagreements, both
    sides, pending a decision; IP-29 was the decision. This keeps the file
    from growing a new pin by habit: a disagreement found from now on is a
    bug to fix, or a deliberate exam choice to write down in
    `EXAM_NOT_ENGINE` below with its reason -- not a number to freeze."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert "DISAGREEMENTS" + " = [" not in source
    assert "def test_a_known_" + "disagreement" not in source


@pytest.mark.parametrize("scene", sorted(set(registry.rubrics()) - {sh.SCENE}))
def test_the_pot_starts_where_the_template_sets_it(scene):
    """`ButtonPanel.cs` publishes the template's `setpoint`, clamped to its
    plate, from the first tick. The model used to start every pot at 0 until
    the exam turned it -- a pot no student ever sees."""
    model = registry.rubrics()[scene]["build"](1).tags.value("panel.setpoint")
    panel = _t(scene).part("panel", "ButtonPanel")
    low, high = panel.number("setpoint_min"), panel.number("setpoint_max")
    expected = min(max(panel.number("setpoint"), low), high)
    assert model == pytest.approx(expected)
    assert expected != 0.0, "the check cannot tell this apart from the old model"


#: What the exam does that the engine's plant would not do by itself, each on
#: purpose. These are the examiner's hand, not the machine's, and the list is
#: what docs/GRADING.md's "The feed patterns are shuffled" section describes.
EXAM_NOT_ENGINE = {
    "light-curtain-sorting": "feeds eight carton heights, shuffled; the "
                             "template's emitter alternates two, which "
                             "`everyother` would pass",
    "roller-line-weighing": "feeds the four mass classes shuffled in blocks; "
                            "the template's emitter makes every third steel and "
                            "alternates heights, which need not produce the "
                            "carton the two instruments disagree about",
}


def test_the_exam_choices_are_written_down():
    doc = (ROOT / "docs" / "GRADING.md").read_text(encoding="utf-8")
    for scene in EXAM_NOT_ENGINE:
        assert scene in doc
    assert "shuffled" in doc


# --- behaviour the engine has and the model used to lack (IP-29) ---------
#
# Each is stepped here on the model alone, no bus, and each fails if the model
# goes back to what it did before.

def _step(sim, seconds: float, writes: dict | None = None, dt: float = 0.01) -> None:
    for _ in range(int(round(seconds / dt))):
        for tag_id, value in (writes or {}).items():
            sim.tags.set(tag_id, value)
        sim.tick(dt)


def test_the_dosing_tank_fills_through_its_own_valve():
    """`LevelTank.cs` adds `fill_rate` %/s at a full fill valve to whatever the
    pump offers. The model used to ignore `tank.fill`, so a program that
    opened it filled the engine's tank and not the grader's."""
    sim = bd.BatchDosingScene(1)
    _step(sim, 2.0, {"tank.fill": 50.0})
    assert sim.level == pytest.approx(bd.BD_TANK_FILL_RATE * 0.5 * 2.0, rel=1e-6)
    # The pump moved nothing, and the dose is marked on the pump's litres.
    assert sim.delivered == 0.0
    assert sim.fill_valve_open_s == pytest.approx(2.0)


def test_past_the_blade_a_carton_rides_the_outfeed():
    """The outfeed is its own belt: its own speed, and only while
    `outfeed.rotate`. The model used to carry every carton at the buffer
    drive's speed, the outfeed's command ignored."""
    sim = ab.AccumulationScene(1)
    sim.script = plant.Script([])
    carton = plant.Item(position=ab.AB_BUFFER_TO + 0.3, id=99)
    sim.items.append(carton)
    buffer = {"buffer.run": True, "buffer.speed": 100.0}
    _step(sim, 3.0, {**buffer, "outfeed.rotate": False})
    assert carton.position == pytest.approx(ab.AB_BUFFER_TO + 0.3)
    assert sim.drive.actual > 90.0, "the buffer drive did not run -- no test"
    start = carton.position
    _step(sim, 0.5, {**buffer, "outfeed.rotate": True})
    assert carton.position - start == pytest.approx(ab.AB_OUTFEED_SPEED * 0.5, rel=1e-6)


def test_the_exam_slows_the_buffer_below_the_outfeed():
    """Why the accumulation exam halves the drive rather than doubling it: the
    outfeed has to be able to take whatever a release lets out, or a correct
    release measured in pulses comes out short at the faster speed."""
    assert ab.AB_SPEED_THEN < ab.AB_SPEED_FIRST <= ab.AB_OUTFEED_SPEED


def _guarded_until(seconds: float, lock: bool) -> gc.GuardedCellScene:
    sim = gc.GuardedCellScene(1)
    _step(sim, seconds, {"guard_a.lock": lock, "guard_b.lock": lock})
    return sim


def test_a_locked_guard_does_not_open_for_the_examiner():
    """`SafetyGate.Toggle` refuses the handle while the solenoid holds the leaf
    shut. The model used to report the lock and open the gate through it at
    22 s whatever the program asked for."""
    sim = _guarded_until(22.0 + gc.GC_ACCESS_PATIENCE + 1.0, lock=True)
    assert all(leaf.closed for leaf in sim.leaves.values())
    assert sim.tags.value("guard_a.locked") and sim.tags.value("guard_b.locked")
    access = sim.access
    assert access["refused"] == ["guard_a", "guard_b"]
    assert access["stop_pressed_at"] == pytest.approx(22.0, abs=0.02)
    assert access["opened_at"] is None and access["gave_up_at"] is not None
    assert [t for t, what in sim.panel.presses if what == "stop"] == [
        pytest.approx(22.0, abs=0.02)]


def test_an_unlocked_guard_opens_on_the_first_pull():
    """...and the exam every program written to the brief sits is unchanged:
    no lock, the gate opens at 22 s, and nobody presses Stop."""
    sim = _guarded_until(23.0, lock=False)
    assert not any(leaf.closed for leaf in sim.leaves.values())
    assert sim.access["refused"] == [] and sim.access["stop_pressed_at"] is None
    assert sim.access["opened_at"] == pytest.approx(22.0, abs=0.02)


def test_a_gate_leaf_slides():
    """`SafetyGate.Step`: a leaf takes `travel / slide_speed` to slide, and
    reads shut while it is within 2 % of shut. The model used to flip the
    contact the moment the examiner pulled the handle."""
    travel = gc.GC_GATE_TRAVEL_TIME["guard_a"]
    leaf = gc.GateLeaf(travel)
    assert leaf.toggle()
    opening = 0
    while leaf.opening < 1.0:
        leaf.step(0.01)
        opening += 1
    assert opening * 0.01 == pytest.approx(travel, abs=0.011)
    assert leaf.toggle()
    shutting = 0
    while not leaf.closed:
        leaf.step(0.01)
        shutting += 1
    assert shutting * 0.01 == pytest.approx(travel * (1 - gc.GC_GATE_CLOSED_BELOW), abs=0.011)
    assert shutting > 50, "a leaf that shuts in under half a second is not sliding"


def test_the_cylinder_valve_remembers_where_it_was_sent():
    """`PneumaticCylinder.Step` is a 5/2 double-solenoid valve: drop the coil
    and the rod goes on to the end the spool points at. The model used to
    stop the rod where it was."""
    sim = gc.GuardedCellScene(1)
    sim.script = plant.Script([])
    _step(sim, 0.05, {"cylinder.extend": True})
    assert 0.0 < sim.extension < gc.GC_STROKE
    _step(sim, gc.GC_ROD_TIME, {"cylinder.extend": False})
    assert sim.tags.value("cylinder.extended")


def test_a_scanner_code_is_what_the_carton_is():
    """`BarcodeScanner.cs` reads 101, 102 or 201 off the carton itself, and the
    template's emitter makes them short, tall, short, tall with every
    `metal_every`-th steel -- the host's alternation starts short
    (`SceneEditor.NextAlternate`). The model used to deal codes out shuffled."""
    metal_every = int(_t("pick-and-place-cell").part("emitter", "Emitter")
                      .number("metal_every"))
    assert metal_every > 0
    expected = [201 if n % metal_every == 0 else (102 if n % 2 == 0 else 101)
                for n in range(1, 13)]
    sim = pp.PickPlaceScene(3)
    sim.script = plant.Script([])
    codes = []
    for _ in range(12):
        _step(sim, 0.02, {"emitter.emit": True})
        _step(sim, 0.02, {"emitter.emit": False})
        codes.append(int(sim.items[-1].measured))
    assert codes == expected
    assert codes == [pp.pp_code(i) for i in sim.items]

# --- the parts IP-14 put in scenes, stepped on their own ------------------
#
# Each is the model of one C# part, driven here with no bus and no exam, and
# asserted against the behaviour the part's own file describes.

def _quiet(sim):
    sim.script = plant.Script([])
    return sim


def test_a_star_delta_changeover_in_one_scan_is_a_short():
    """`StarDeltaStarter.cs`: contacts close in `PullInTime` and conduct for
    `DropOutTime` after the coil drops, so star off and delta on in the same
    tick overlap -- and waiting for `staraux` to fall does not."""
    sim = _quiet(sd.StarDeltaScene(1))
    _step(sim, 2.0, {"motor.main": True, "motor.star": True})
    assert sim.tags.value("motor.staraux") and sim.speed > 0.3
    _step(sim, 0.05, {"motor.star": False, "motor.delta": True})
    assert sim.breaker_tripped and not sim.tags.value("motor.breaker")

    sim = _quiet(sd.StarDeltaScene(1))
    _step(sim, 2.0, {"motor.main": True, "motor.star": True})
    _step(sim, sd.SD_DROP_OUT + 0.02, {"motor.star": False})
    assert not sim.tags.value("motor.staraux")
    _step(sim, 1.0, {"motor.delta": True})
    assert not sim.breaker_tripped and sim.tags.value("motor.deltaaux")


def test_a_star_run_up_is_slower_on_a_loaded_machine_and_in_proportion_to_inertia():
    """The torque-speed curve and `LoadTorque` (:431): the time to the pot's
    speed in star grows with the load, and scales with the template's
    `inertia` exactly, since inertia only divides the net torque."""
    def run_up(load: float | None = None) -> float:
        sim = _quiet(sd.StarDeltaScene(1))
        if load is not None:
            sim.load = load
        t = 0.0
        while sim.speed * 100.0 < sd.SD_POT_START and t < 30.0:
            _step(sim, 0.01, {"motor.main": True, "motor.star": True})
            t += 0.01
        return t
    light, loaded = run_up(), run_up(max(sd.SD_LOADS_THEN))
    assert light < 3.5 and loaded > 1.6 * light
    assert sd.SD_INERTIA == _t("star-delta-start").part("motor", "StarDeltaStarter").number("inertia")


def test_a_servo_error_latches_and_clears_only_on_an_edge_after_its_cause():
    """`ServoAxis.Step` (:267): a fault latches `error` and quick-stops the
    carriage; an ack while the fault stands does nothing, holding ack high is
    not a second edge, and a fresh edge once the fault has gone clears it --
    after which the drive resumes towards the target it holds."""
    drive = sv.ServoDrive()
    run = lambda secs, **kw: [drive.step(**{"enable": True, "ack": False, "fault": False,  # noqa: E731
                                            "target": 800.0, "velocity": 400.0,
                                            "dt": 0.01, **kw})
                              for _ in range(int(round(secs / 0.01)))]
    run(1.0)
    assert drive.ready and 300.0 < drive.position < 800.0
    run(0.5, fault=True)
    stopped = drive.position
    assert drive.error and not drive.ready and drive.velocity == 0.0
    run(0.5, fault=True, ack=True)
    assert drive.error, "an acknowledge while the fault stands cleared it"
    run(0.5, ack=True)
    assert drive.error and drive.position == stopped, "a held ack is not an edge"
    run(0.02)
    run(0.02, ack=True)
    assert not drive.error and drive.ready
    run(3.0)
    assert drive.position == pytest.approx(800.0) and drive.in_position

    drive = sv.ServoDrive()
    run(0.5, target=sv.SV_STROKE + 1.0)
    assert drive.error and "outside" in drive.error_text


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


# --- a frozen grader (IP-08's follow-ups) ------------------------------

def test_a_frozen_grader_looks_only_in_its_bundle(monkeypatch, tmp_path):
    # In a PyInstaller build __file__ sits in the bundle's temporary
    # directory, so the checkout fallback would resolve to somewhere under
    # %TEMP% and mark against whatever engine/templates happened to be there.
    monkeypatch.delenv(templates.TEMPLATES_ENV, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    where = [name for name, _ in templates._candidates()]
    assert where == ["the frozen bundle"], where


def test_a_frozen_grader_tells_the_student_a_command_the_release_has(monkeypatch, capsys):
    from types import SimpleNamespace
    from factoryforge_sidecar.grading import core
    args = SimpleNamespace(quiet=False, duration=60, wait=120)
    engine = SimpleNamespace(url="ws://127.0.0.1:1/tagbus", actual_port=1)
    rubric = {"title": "t", "task": "t", "tags": "t"}

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    core._announce(args, engine, rubric, seed=1)
    frozen = capsys.readouterr().out
    assert "factoryforge-sidecar connect" in frozen and "python -m" not in frozen

    monkeypatch.delattr(sys, "frozen", raising=False)
    core._announce(args, engine, rubric, seed=1)
    assert "python -m factoryforge_sidecar connect" in capsys.readouterr().out


def test_a_scanner_read_is_held_the_way_the_engine_holds_it():
    """`BarcodeScanner.StepOutput` (IP-31): one read is one rising edge, held
    high for `ReadHold` and low at least as long, with the code register set
    on the tick the edge rises. The model used to raise `scanner.read` for a
    single tick, which the engine no longer does and a polling driver misses."""
    sim = pp.PickPlaceScene(1)
    sim.script = plant.Script([])
    sim.items.append(plant.Item(position=pp.PP_SCANNER_POS, id=99))
    sim.items[-1].measured = 102.0
    dt, highs, edges, last = 0.01, 0, 0, False
    for _ in range(100):
        sim.tags.set("scanner.enable", True)
        sim.tick(dt)
        read = bool(sim.tags.get("scanner.read").value)
        if read and not last:
            edges += 1
            assert sim.tags.get("scanner.code").value == 102
        highs += read
        last = read
    assert edges == 1
    # Against the engine's number, not the model's own constant: a check that
    # compares the model with itself passes whatever the model says (it did,
    # with the hold shrunk to one tick). CSHARP_MIRRORS pins 0.2 to the C#.
    assert highs * dt == pytest.approx(0.2, abs=dt)
