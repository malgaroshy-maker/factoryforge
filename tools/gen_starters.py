#!/usr/bin/env python3
"""Generate the starter programs in `examples/` for every graded scene (IP-10).

    python tools/gen_starters.py            # (re)write every starter
    python tools/gen_starters.py --check    # exit 1 if a committed file is stale

For each scene the grader marks, this writes

    examples/openplc/<scene>/   <scene>.st, mbconfig.cfg, README.md
    examples/tia/<scene>/       <scene>.scl, opcua_mapping.json,
                                snap7_mapping.json, plcsim_mapping.json, README.md

plus `examples/README.md`, the index. Every file is derived, and nothing in
them is typed by hand:

  * the tag set (id, type, kind) is `engine/fixtures/scene_tag_sets.json`, the
    file `--self-test=scenes` holds the engine to and `test_grade_templates.py`
    holds the grader to;
  * the Modbus addresses are the ones the sidecar's own `modbus-tcp` driver
    hands out for that tag set, asked for by building the driver and calling
    `rebuild()` -- not re-derived here, so there is one allocator, not two;
  * the task text is the scene's brief in `engine/templates/manifest.json`, and
    the units and ranges in the comments are the template's own properties.

The logic body of every program is empty, on purpose. The reference controllers
stay Python (`sidecar/factoryforge_sidecar/grading/reference/`), so the answer
is not one copy away. What the OpenPLC starters *do* contain is the plumbing a
32-bit value needs on a 16-bit wire -- see `_ST_HELPERS` -- because that is a
transport detail, not the exercise.

The output is deterministic: sorted everywhere, no timestamps, "\\n" line ends.
`tests/test_examples.py` regenerates everything in memory and compares it with
what is committed, so a scene that changes under a starter fails there.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))

FIXTURE = ROOT / "engine" / "fixtures" / "scene_tag_sets.json"
TEMPLATES = ROOT / "engine" / "templates"
EXAMPLES = ROOT / "examples"

#: What the tests point at when they want to feed a doctored fixture through
#: the same code path (AGENTS.md gotcha 24: prove the test can fail).
FIXTURE_ENV = "FACTORYFORGE_TAG_FIXTURE"

GENERATED_BY = "tools/gen_starters.py"

# ---------------------------------------------------------------------------
# The sorting line is the one scene with no template: the engine builds it in
# C# (engine/src/Editor/SceneEditor.DefaultScene.cs), so its parts are written
# out here. The pot's range is `ConfigureSetpoint(0.30f, 1.80f, "s", 0.90f)`
# in that file; `grading/scenes/sorting_by_height.py` copies the same 0.90.
# ---------------------------------------------------------------------------
_DEFAULT_SCENE_PARTS = {
    "conveyor": ("ConveyorBelt", {}),
    "emitter": ("Emitter", {"metal_every": "0"}),
    "pusher": ("PusherMechanism", {}),
    "sensor_low": ("PhotoelectricSensor", {}),
    "sensor_high": ("PhotoelectricSensor", {}),
    "stack_light": ("StackLight", {}),
    "panel": ("ButtonPanel", {"setpoint_min": "0.3", "setpoint_max": "1.8",
                              "setpoint_unit": "s", "setpoint": "0.9"}),
    "counter": ("SortingCounters", {}),
}

# ---------------------------------------------------------------------------
# One line per (part type, tag suffix): what the signal means to the program.
# Read off the part's DeclareTags and its doc comments in engine/src/Parts/.
# `{name}` is filled from the part's template properties, so a template that
# retunes a range retunes the comment. A (type, suffix) missing from this table
# is an error, not a blank: a new part gets a meaning written for it.
# ---------------------------------------------------------------------------
_MEANINGS: dict[tuple[str, str], str] = {
    ("ButtonPanel", "start"): "Start button, momentary: a short pulse per press -- latch it",
    ("ButtonPanel", "stop"): "Stop button, momentary: a short pulse per press",
    ("ButtonPanel", "reset"): "Reset button, momentary: a short pulse per press",
    ("ButtonPanel", "estop"): "E-stop, normally closed: TRUE = healthy, FALSE = pressed; stays pressed until pulled out",
    ("ButtonPanel", "setpoint"): "the operator's pot, {setpoint_min}..{setpoint_max} {setpoint_unit} (starts at {setpoint})",
    ("ButtonPanel", "green"): "green lamp on the panel",
    ("ButtonPanel", "red"): "red lamp on the panel",
    ("ConveyorBelt", "rotate"): "run the belt",
    ("ConveyorBelt", "fault"): "drive fault: TRUE = faulted; a faulted drive does not turn, whatever it is told",
    ("RollerConveyor", "rotate"): "run the roller deck",
    ("RollerConveyor", "fault"): "drive fault: TRUE = faulted; a faulted drive does not turn",
    ("WeighingConveyor", "rotate"): "run the weighing belt",
    ("WeighingConveyor", "weight"): "load cell: grams on the deck right now (everything on it, not one carton)",
    ("WeighingConveyor", "fault"): "drive fault: TRUE = faulted; a faulted drive does not turn",
    ("VariableConveyor", "run"): "run the drive",
    ("VariableConveyor", "speed"): "speed reference, 0..100 % of {max_speed} m/s; the drive ramps towards it",
    ("VariableConveyor", "actual"): "actual speed, % -- lags the reference while the drive ramps",
    ("VariableConveyor", "fault"): "drive fault: TRUE = faulted; a faulted drive does not turn",
    ("Emitter", "emit"): "rising edge = feed one carton; hold each level well over one poll",
    ("PhotoelectricSensor", "detect"): "TRUE while a carton is in the beam",
    ("InductiveSensor", "detect"): "TRUE while a METAL carton is in front of it; cardboard is invisible to it",
    ("Remover", "count"): "cartons this remover has taken off the line, running total",
    ("StackLight", "green"): "tower lamp, green",
    ("StackLight", "yellow"): "tower lamp, yellow",
    ("StackLight", "red"): "tower lamp, red",
    ("DigitalDisplay", "value"): "number shown on the display ({unit})",
    ("AnalogGauge", "value"): "needle position, scale {scale_min}..{scale_max} {unit}",
    ("AlarmBeacon", "beacon"): "rotating alarm beacon",
    ("AlarmBeacon", "horn"): "alarm horn",
    ("LevelTank", "fill"): "fill valve opening, 0..100 %",
    ("LevelTank", "drain"): "drain valve opening, 0..100 %",
    ("LevelTank", "level"): "tank level, % of capacity",
    ("LevelTank", "fault"): "valve fault: TRUE = a valve has seized and holds its opening, whatever you command",
    ("LightArray", "height"): "light curtain: height of the tallest blocked beam, m (0 when clear)",
    ("LightArray", "blocked"): "light curtain: TRUE while any beam is blocked",
    ("PusherMechanism", "extend"): "extend the pusher",
    ("PusherMechanism", "extended"): "pusher fully out",
    ("PusherMechanism", "retracted"): "pusher fully back",
    ("PusherMechanism", "fault"): "drive fault: TRUE = jammed; it freezes where it is",
    ("BarcodeScanner", "enable"): "arm the reader; FALSE switches it off -- and an output you never write is FALSE",
    ("BarcodeScanner", "code"): "last code read, held until the next: 101 short, 102 tall, 201 metal",
    ("BarcodeScanner", "read"): "one engine tick wide on each new read -- a polled link can miss it",
    ("BarcodeScanner", "present"): "TRUE while a carton is under the reader",
    ("PickPlaceArm", "target"): "rail position to travel to, 0..100 % of the {rail_length} m rail",
    ("PickPlaceArm", "lower"): "lower the cup (FALSE raises it)",
    ("PickPlaceArm", "grip"): "vacuum on",
    ("PickPlaceArm", "position"): "rail position now, 0..100 %",
    ("PickPlaceArm", "inposition"): "within {tolerance} % of the target it holds NOW -- stale on the scan you change the target",
    ("PickPlaceArm", "lowered"): "cup fully down",
    ("PickPlaceArm", "raised"): "cup fully up",
    ("PickPlaceArm", "holding"): "the vacuum really has a carton (not just: grip is on)",
    ("PickPlaceArm", "fault"): "drive fault: TRUE = faulted",
    ("RotaryEncoder", "count"): "measuring wheel pulses, {pulses_per_metre} per metre of belt travel",
    ("RotaryEncoder", "rate"): "pulse rate, pulses/s",
    ("RotaryEncoder", "reset"): "a LEVEL: held TRUE keeps the count at zero",
    ("StopGate", "raise"): "raise the blade into the lane (holds cartons on a running belt)",
    ("StopGate", "up"): "blade fully up",
    ("StopGate", "down"): "blade fully down",
    ("StopGate", "fault"): "drive fault: TRUE = faulted; the blade stays where it is",
    ("HeatingStation", "heater"): "heater power, 0..100 %",
    ("HeatingStation", "temperature"): "plate temperature, degC",
    ("HeatingStation", "attemp"): "within +/-{tolerance} degC of the station's own {target_temp} degC -- not the pot",
    ("HeatingStation", "fault"): "element failed: TRUE = it takes your command and heats nothing",
    ("AreaScanner", "mute"): "request muting; honoured for at most {mute_limit} s at a stretch",
    ("AreaScanner", "stop"): "protective field clear, normally closed: TRUE = clear, FALSE = someone is in it",
    ("AreaScanner", "warn"): "TRUE while the warning field is broken",
    ("AreaScanner", "muted"): "TRUE while muting is actually in force",
    ("PneumaticCylinder", "extend"): "extend coil (5/2 valve, no spring: the rod stays where it was last driven)",
    ("PneumaticCylinder", "retract"): "retract coil",
    ("PneumaticCylinder", "extended"): "reed switch at full stroke",
    ("PneumaticCylinder", "retracted"): "reed switch at home; mid-stroke neither reed is made",
    ("PneumaticCylinder", "fault"): "TRUE = the cylinder has seized",
    ("MotorStarter", "coil"): "contactor coil -- the contactor runs {load_tag}",
    ("MotorStarter", "aux"): "auxiliary contact: TRUE while the contactor has really pulled in",
    ("MotorStarter", "overload"): "thermal overload, normally closed: TRUE = OK, FALSE = tripped",
    ("MotorStarter", "current"): "motor current, A",
    ("SafetyRelay", "reset"): "reset: the relay closes on a RISING edge, and only with both channels healthy",
    ("SafetyRelay", "k1"): "safety contact K1: TRUE while the relay's outputs are closed (open, they hold {load_tag} FALSE)",
    ("SafetyRelay", "k2"): "safety contact K2: TRUE while the relay's outputs are closed",
    ("SafetyRelay", "cha"): "channel A as the relay sees it ({channel_a_tag})",
    ("SafetyRelay", "chb"): "channel B as the relay sees it ({channel_b_tag})",
    ("SafetyRelay", "fault"): "TRUE = the two channels disagreed; the relay latches this fault",
    ("SafetyGate", "closed"): "guard switch, normally closed: TRUE while the door is shut",
    ("SafetyGate", "lock"): "energise the solenoid lock (it only bites on a shut door)",
    ("SafetyGate", "locked"): "TRUE while the door is locked shut",
    ("DosingPump", "run"): "run the pump",
    ("DosingPump", "speed"): "speed reference, 0..100 %",
    ("DosingPump", "flow"): "measured flow, L/min -- the measurement, not the command",
    ("DosingPump", "fault"): "motor fault: TRUE = failed; it reports your command and delivers nothing",
    ("FlowMeter", "rate"): "measured flow rate, L/min",
    ("FlowMeter", "total"): "litres since the last reset, whole litres",
    ("FlowMeter", "reset"): "a LEVEL: held TRUE keeps the total at zero",
}

#: Tag-specific meanings where the part type alone says too little -- today only
#: the sorting line's counters and its sensors, which are told apart by height.
_TAG_MEANINGS: dict[tuple[str, str], str] = {
    ("sorting-by-height", "counter.tall"): "cartons that came down the chute, running total",
    ("sorting-by-height", "counter.short"): "cartons that reached the end of the belt, running total",
    ("sorting-by-height", "sensor_low.detect"): "low beam: TRUE while any carton is in it",
    ("sorting-by-height", "sensor_high.detect"): "high beam: TRUE while a TALL carton is in it",
    ("sorting-by-height", "stack_light.green"): "green tower lamp",
}

#: What a student must know about one scene's starter that its tag set does
#: not say. The sorting line is the one scene with a second, older program for
#: a different map, and mixing the two up drives the wrong coil.
_SCENE_NOTES = {
    "sorting-by-height": {
        "openplc": ("This is the line the 3D engine opens and the grader marks: "
                    "nineteen tags, with an operator panel and two fault contacts. "
                    "`factoryforge-sidecar demo` runs a different, ten-tag Python "
                    "scene whose Modbus map is NOT this one; the finished program "
                    "for that one is examples/openplc/Sorting.st."),
        "tia": ("This FF_IO has nineteen members, for the line the 3D engine opens "
                "and the grader marks. examples/tia/Sorting.scl is a finished "
                "program for the older ten-member FF_IO in "
                "examples/tia/FF_IO_datablock.md; the two DBs are not the same "
                "layout, so use one or the other."),
    },
}

#: A part that *commands* another tag, and holds it both ways. The program must
#: never write that tag, and on the guarded cell the grader marks exactly that
#: (`cell.never_wrote_the_motor`). A starter that wired it would fail the
#: exercise for the student: OpenPLC's master rewrites its whole coil block on
#: every poll, and the OPC UA driver forwards every mapped PLC output.
_COMMANDING_PARTS = {"MotorStarter": "load_tag"}

_TYPES_ST = {"bit": "BOOL", "int": "DINT", "float": "REAL"}
_TYPES_S7 = {"bit": "Bool", "int": "DInt", "float": "Real"}

#: IEC 61131-3 and SCL words a generated name must never collide with. ST and
#: SCL are case-insensitive, so the check is too.
_RESERVED = {
    "and", "or", "xor", "not", "mod", "if", "then", "else", "elsif", "end_if",
    "case", "of", "for", "to", "by", "do", "while", "repeat", "until", "exit",
    "return", "var", "end_var", "true", "false", "program", "function",
    "function_block", "configuration", "resource", "task", "at", "bool", "int",
    "dint", "real", "word", "dword", "time", "begin", "region", "continue",
}


# ---------------------------------------------------------------------------
# Scene data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TagInfo:
    id: str
    type: str          # bit | int | float
    kind: str          # output (PLC writes) | input (PLC reads)
    meaning: str
    in_brief: bool
    hands_off: str     # why the program must not write it, or ""

    @property
    def name(self) -> str:
        return pascal(self.id)

    @property
    def plc_writes(self) -> bool:
        return self.kind == "output"


@dataclass(frozen=True)
class Scene:
    id: str
    title: str
    template: str      # res:// path, or "" for the engine's built-in line
    task: str
    done: str
    tags: tuple[TagInfo, ...]

    @property
    def stem(self) -> str:
        return self.id.replace("-", "_")

    @property
    def fb_name(self) -> str:
        return "".join(part.capitalize() for part in self.id.split("-"))

    @property
    def wired(self) -> tuple[TagInfo, ...]:
        return tuple(t for t in self.tags if not t.hands_off)


def pascal(tag_id: str) -> str:
    """`sensor_high.detect` -> `SensorHighDetect`. The one naming rule, used for
    the ST variable, the S7 DB member and therefore the OPC UA NodeId."""
    return "".join(word[:1].upper() + word[1:]
                   for word in re.split(r"[._]", tag_id) if word)


def fixture_path() -> Path:
    return Path(os.environ.get(FIXTURE_ENV) or FIXTURE)


def load_fixture(path: Path | None = None) -> dict[str, list[dict]]:
    data = json.loads((path or fixture_path()).read_text(encoding="utf-8"))
    return data["scenes"]


def graded_scenes() -> list[str]:
    """The scenes the grader marks, asked of the grader itself."""
    from factoryforge_sidecar.grading import registry
    return sorted(registry.rubrics())


def _manifest() -> dict[str, dict]:
    entries = json.loads((TEMPLATES / "manifest.json").read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in entries}


def _parts(entry: dict) -> dict[str, tuple[str, dict]]:
    """`{instance id: (part type, properties)}` for one manifest scene."""
    if not entry["path"]:
        return _DEFAULT_SCENE_PARTS
    name = entry["path"].removeprefix("res://templates/")
    data = json.loads((TEMPLATES / name).read_text(encoding="utf-8"))
    return {part["id"]: (part["type"], dict(part.get("properties") or {}))
            for part in data["parts"]}


def _meaning(scene: str, tag_id: str, parts: dict) -> str:
    if (scene, tag_id) in _TAG_MEANINGS:
        return _TAG_MEANINGS[(scene, tag_id)]
    prefix, suffix = tag_id.split(".", 1)
    if prefix not in parts:
        raise KeyError(f"{scene}: {tag_id} belongs to no part called {prefix!r}")
    part_type, props = parts[prefix]
    key = (part_type, suffix)
    if key not in _MEANINGS:
        raise KeyError(f"{scene}: no meaning written for {part_type}.{suffix} "
                       f"({tag_id}); add one to _MEANINGS in {GENERATED_BY}")
    meaning = _MEANINGS[key].format(**props)
    if part_type == "Emitter" and props.get("metal_every", "0") not in ("0", ""):
        meaning += f"; every {_ordinal(int(props['metal_every']))} carton is steel"
    return meaning


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _hands_off(parts: dict) -> dict[str, str]:
    out = {}
    for instance, (part_type, props) in parts.items():
        prop = _COMMANDING_PARTS.get(part_type)
        if prop and props.get(prop):
            out[props[prop]] = (f"{instance}.coil's contactor drives it; a program "
                                f"that writes it fails this exercise")
    return out


def load_scenes(fixture: Path | None = None) -> list[Scene]:
    tag_sets = load_fixture(fixture)
    manifest = _manifest()
    scenes = []
    for scene_id in graded_scenes():
        entry = manifest[scene_id]
        parts = _parts(entry)
        hands_off = _hands_off(parts)
        uses = {u.strip() for u in entry["brief"]["uses"].split("·")}
        tags = tuple(
            TagInfo(t["id"], t["type"], t["kind"], _meaning(scene_id, t["id"], parts),
                    t["id"] in uses, hands_off.get(t["id"], ""))
            for t in sorted(tag_sets[scene_id], key=lambda t: t["id"]))
        for tag_id in hands_off:
            match = [t for t in tags if t.id == tag_id]
            if not match or match[0].kind != "output":
                raise ValueError(f"{scene_id}: {tag_id} is some part's load, but not "
                                 f"an output tag of this scene")
        _check_names(scene_id, tags)
        scenes.append(Scene(scene_id, entry["title"], entry["path"],
                            entry["brief"]["task"], entry["brief"]["done"], tags))
    return scenes


def _check_names(scene: str, tags) -> None:
    seen: dict[str, str] = {}
    for tag in tags:
        for name in (tag.name, tag.name + "Hi", tag.name + "Lo"):
            low = name.lower()
            if low in _RESERVED or low == "raw32" or low.startswith("ff_"):
                raise ValueError(f"{scene}: {tag.id} would be named {name}, a reserved word")
            if low in seen and seen[low] != tag.id:
                raise ValueError(f"{scene}: {tag.id} and {seen[low]} both become {name}")
            seen[low] = tag.id


# ---------------------------------------------------------------------------
# Modbus: the sidecar's own allocator, and OpenPLC's view of it
# ---------------------------------------------------------------------------

class _SilentBus:
    """Enough of a bus to construct the driver and call `rebuild`."""

    def on_describe(self, _handler) -> None:
        pass

    on_update = on_disconnect = on_describe

    async def status(self, *args, **kwargs) -> None:
        pass


def sidecar_modbus_map(scene_id: str, tags) -> dict[str, tuple[str, int, int]]:
    """`{tag id: (block, first address, width)}` exactly as `modbus-tcp` serves
    this tag set to a master on a sidecar started fresh against the scene."""
    from factoryforge_sidecar import drivers
    from factoryforge_sidecar.tags import Tag, TagTable

    table = TagTable([Tag(t.id, t.id, t.type, t.kind) for t in tags])
    driver = drivers.create("modbus-tcp", _SilentBus(), port=0)
    asyncio.run(driver.rebuild(scene_id, 1, table))
    return {m.tag_id: (m.block, m.address, m.width) for m in driver._by_tag.values()}


#: The four Modbus blocks, and how OpenPLC's master names the points it reads
#: from each (`updateBuffersIn_MB` / `updateBuffersOut_MB` in OpenPLC v3's
#: webserver/core/modbus_master.cpp): always from 100 up.
_BLOCKS = {
    "coils": ("0x", "%QX", "Coils"),
    "discrete_inputs": ("1x", "%IX", "Discrete_Inputs"),
    "input_registers": ("3x", "%IW", "Input_Registers"),
    "holding_registers": ("4x", "%QW", "Holding_Registers"),
}


@dataclass(frozen=True)
class Point:
    """One 1-bit or 16-bit Modbus point, and where OpenPLC puts it."""
    tag: TagInfo
    block: str
    slave: int          # the sidecar's address
    iec: str            # OpenPLC's located address
    half: str           # "" for a bit, "Hi" / "Lo" for a register

    @property
    def label(self) -> str:
        return f"{_BLOCKS[self.block][0]}{self.slave}"


def openplc_iec(block: str, index: int) -> str:
    """OpenPLC's address for the index-th point its master read from a block."""
    prefix = _BLOCKS[block][1]
    if prefix.endswith("X"):
        return f"{prefix}{100 + index // 8}.{index % 8}"
    return f"{prefix}{100 + index}"


def modbus_layout(scene: Scene) -> tuple[list[Point], dict[str, tuple[int, int]]]:
    """Every point the starter declares, and `{block: (start, size)}` for
    mbconfig.cfg.

    Each block is polled as one contiguous range, starting at the lowest
    address a wired tag uses. A hands-off tag has to fall outside that range
    -- the master rewrites every coil in it on every poll -- and if one ever
    lands in the middle, this refuses rather than emitting a config that
    quietly writes it.
    """
    served = sidecar_modbus_map(scene.id, scene.tags)
    ranges: dict[str, tuple[int, int]] = {}
    for block in _BLOCKS:
        spans = [(served[t.id][1], served[t.id][1] + served[t.id][2])
                 for t in scene.wired if served[t.id][0] == block]
        if not spans:
            ranges[block] = (0, 0)
            continue
        start, end = min(s for s, _ in spans), max(e for _, e in spans)
        ranges[block] = (start, end - start)
        for tag in scene.tags:
            b, address, width = served[tag.id]
            if tag.hands_off and b == block and address < end and address + width > start:
                raise ValueError(f"{scene.id}: {tag.id} sits inside the {block} range "
                                 f"{start}..{end - 1} and would be written every poll")
        covered = set()
        for s, e in spans:
            covered.update(range(s, e))
        gaps = sorted(set(range(start, end)) - covered)
        if gaps:
            raise ValueError(f"{scene.id}: {block} has unmapped addresses {gaps} "
                             f"inside the polled range")

    points = []
    for tag in scene.wired:
        block, address, width = served[tag.id]
        start = ranges[block][0]
        for offset in range(width):
            half = "" if width == 1 else ("Hi" if offset == 0 else "Lo")
            points.append(Point(tag, block, address + offset,
                                openplc_iec(block, address + offset - start), half))
    return points, ranges


# ---------------------------------------------------------------------------
# S7: the standard-access layout TIA gives a non-optimized global DB
# ---------------------------------------------------------------------------

def db_order(scene: Scene) -> list[TagInfo]:
    """PLC-written members first, then simulator-written ones, and inside each
    the 4-byte values before the Bools.

    That order is what keeps the two directions out of each other's bytes
    (AGENTS.md gotcha 19d): the simulator's first member is a DInt or Real,
    which TIA starts on a fresh even byte after the PLC's last Bool. S7 cannot
    write one bit, so a byte shared between directions is a byte the snap7
    driver read-modify-writes underneath the PLC.
    """
    def group(plc_writes: bool) -> list[TagInfo]:
        mine = [t for t in scene.wired if t.plc_writes == plc_writes]
        return ([t for t in mine if t.type != "bit"] + [t for t in mine if t.type == "bit"])
    return group(True) + group(False)


def s7_layout(members: list[tuple[str, str]]) -> tuple[dict[str, str], int]:
    """`{member: "DBX<byte>.<bit>" | "DBD<byte>"}` and the DB length, for
    `(name, S7 type)` in declaration order: Bools pack eight to a byte, a DInt
    or Real starts on the next even byte, and the DB ends on an even byte."""
    addresses: dict[str, str] = {}
    byte, bit = 0, 0              # next free position
    for name, s7_type in members:
        if s7_type == "Bool":
            addresses[name] = f"DBX{byte}.{bit}"
            bit += 1
            if bit == 8:
                byte, bit = byte + 1, 0
        else:
            if bit:
                byte, bit = byte + 1, 0
            byte += byte % 2
            addresses[name] = f"DBD{byte}"
            byte += 4
    if bit:
        byte += 1
    return addresses, byte + byte % 2


def snap7_addresses(scene: Scene) -> tuple[dict[str, str], int]:
    order = db_order(scene)
    by_name, length = s7_layout([(t.name, _TYPES_S7[t.type]) for t in order])
    out = {t.id: by_name[t.name] for t in order}
    first_input = next(t for t in order if not t.plc_writes)
    if first_input.type == "bit":
        raise ValueError(f"{scene.id}: the simulator writes no DInt or Real, so its "
                         f"Bools would share a byte with the PLC's")
    return out, length


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _wrap(text: str, width: int, indent: str) -> list[str]:
    return textwrap.wrap(" ".join(text.split()), width=width,
                         initial_indent=indent, subsequent_indent=indent)


_ASCII = {"\u2014": "--", "\u2013": "-", "\u00b7": ",", "\u00b0": "deg ", "\u2019": "'",
          "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u00d7": "x", "\u2026": "...",
          "\u2192": "->", "\u00b1": "+/-"}


def ascii_only(text: str, where: str) -> str:
    """PLC source files stay 7-bit: an em dash in a comment is not worth
    finding out how a given IDE's importer treats UTF-8."""
    for char, repl in _ASCII.items():
        text = text.replace(char, repl)
    bad = sorted({c for c in text if ord(c) > 127})
    if bad:
        raise ValueError(f"{where}: non-ASCII {bad!r}; add a folding to _ASCII")
    return text


def _direction(tag: TagInfo) -> str:
    return "PLC writes" if tag.plc_writes else "PLC reads"


def _open_scene(scene: Scene) -> str:
    if scene.template:
        return (f"pick \"{scene.title}\" on the start screen (or launch with "
                f"--scene={scene.template})")
    return "launch FactoryForge; this is the line it opens by default"


# ---------------------------------------------------------------------------
# OpenPLC: Structured Text
# ---------------------------------------------------------------------------

_ST_WORDS_TO_DINT = """\
(* The sidecar sends a 32-bit value as two registers, high word first
   (struct.pack(">i") in drivers/modbus_tcp.py). DWORD_TO_DINT reinterprets
   the bit pattern, so this is right for negative values too. *)
FUNCTION FF_WORDS_TO_DINT : DINT
  VAR_INPUT
    HI : WORD;
    LO : WORD;
  END_VAR
  FF_WORDS_TO_DINT := DWORD_TO_DINT(SHL(IN := WORD_TO_DWORD(HI), N := 16)
                                    OR WORD_TO_DWORD(LO));
END_FUNCTION
"""

_ST_WORDS_TO_REAL = """\
(* A REAL arrives the same way: IEEE-754 single precision, high word first.
   OpenPLC's DWORD_TO_REAL cannot rebuild it -- it converts the NUMBER, so
   16#3F800000 becomes 1065353216.0 rather than 1.0 -- so this takes the
   sign, exponent and mantissa apart by hand. Exact for every normal value;
   zero and the tiny subnormals (below 1.2E-38) come back as 0.0. *)
FUNCTION FF_WORDS_TO_REAL : REAL
  VAR_INPUT
    HI : WORD;
    LO : WORD;
  END_VAR
  VAR
    Exponent : INT;
    Value    : REAL;
  END_VAR
  Exponent := WORD_TO_INT(SHR(IN := HI, N := 7) AND 16#00FF);
  IF Exponent = 0 OR Exponent = 255 THEN
    Value := 0.0;
  ELSE
    Value := 1.0 + DWORD_TO_REAL(SHL(IN := WORD_TO_DWORD(HI AND 16#007F), N := 16)
                                 OR WORD_TO_DWORD(LO)) / 8388608.0;
    Exponent := Exponent - 127;
    WHILE Exponent > 0 DO
      Value := Value * 2.0;
      Exponent := Exponent - 1;
    END_WHILE;
    WHILE Exponent < 0 DO
      Value := Value / 2.0;
      Exponent := Exponent + 1;
    END_WHILE;
    IF (HI AND 16#8000) <> 0 THEN
      Value := -Value;
    END_IF;
  END_IF;
  FF_WORDS_TO_REAL := Value;
END_FUNCTION
"""

_ST_REAL_TO_DWORD = """\
(* The other direction: a REAL as the 32 bits IEEE-754 gives it, for
   splitting into two registers. REAL_TO_DWORD cannot do this either (it
   rounds the number). Exact for every normal value; anything smaller than
   1.2E-38 is sent as zero. *)
FUNCTION FF_REAL_TO_DWORD : DWORD
  VAR_INPUT
    IN : REAL;
  END_VAR
  VAR
    Magnitude : REAL;
    Exponent  : INT;
    Sign      : DWORD;
  END_VAR
  Sign := 0;
  Magnitude := IN;
  IF Magnitude < 0.0 THEN
    Sign := 16#80000000;
    Magnitude := -Magnitude;
  END_IF;
  Exponent := 127;
  WHILE Magnitude >= 2.0 AND Exponent < 254 DO
    Magnitude := Magnitude / 2.0;
    Exponent := Exponent + 1;
  END_WHILE;
  WHILE Magnitude < 1.0 AND Exponent > 1 DO
    Magnitude := Magnitude * 2.0;
    Exponent := Exponent - 1;
  END_WHILE;
  IF Magnitude < 1.0 THEN
    FF_REAL_TO_DWORD := Sign;
  ELSIF Magnitude >= 2.0 THEN
    FF_REAL_TO_DWORD := Sign OR 16#7F7FFFFF;
  ELSE
    FF_REAL_TO_DWORD := Sign
                        OR SHL(IN := INT_TO_DWORD(Exponent), N := 23)
                        OR REAL_TO_DWORD((Magnitude - 1.0) * 8388608.0);
  END_IF;
END_FUNCTION
"""


def _st_comment_block(lines: list[str]) -> list[str]:
    out = ["(* " + "-" * 72]
    out += [("   " + line) if line else "" for line in lines]
    out.append("   " + "-" * 72 + " *)")
    return out


def render_st(scene: Scene) -> str:
    points, ranges = modbus_layout(scene)
    by_tag: dict[str, list[Point]] = {}
    for p in points:
        by_tag.setdefault(p.tag.id, []).append(p)

    wide_in = [t for t in scene.wired if t.type != "bit" and not t.plc_writes]
    wide_out = [t for t in scene.wired if t.type != "bit" and t.plc_writes]

    head = [
        f"FactoryForge starter -- {scene.title} (scene {scene.id})",
        "OpenPLC Runtime v3, IEC 61131-3 Structured Text, over Modbus TCP.",
        f"Generated by {GENERATED_BY} from the scene's tag set; see README.md",
        "beside this file. Write your program in section 2 below.",
        "",
        "THE TASK",
        *_wrap(scene.task, 72, "  "),
        "",
        "DONE WHEN",
        *_wrap(scene.done, 72, "  "),
        "",
        *([*_wrap("NOTE: " + _SCENE_NOTES[scene.id]["openplc"], 72, "  "), ""]
          if scene.id in _SCENE_NOTES else []),
        "HOW IT IS WIRED",
        "  FactoryForge is the Modbus slave; OpenPLC is the master and polls it.",
        "  mbconfig.cfg beside this file is that Slave Device. The addresses",
        "  below are the ones the sidecar serves for this scene when it is",
        "  started AFTER the scene is open:",
        "",
        "    factoryforge-sidecar connect --driver modbus-tcp -o port 5502",
        "",
        "  It prints its address map on startup; that map is the authority, and",
        "  the table below must match it line for line. Change scene and the",
        "  sidecar keeps every address it has handed out and puts new tags above",
        "  them -- restart it after changing scene.",
        "",
        "  OpenPLC numbers each block it polls from 100: slave coil n is",
        "  %QX(100 + n/8).(n mod 8), slave input register n is %IW(100 + n).",
    ]
    if ranges["coils"][0]:
        head += [
            f"  This scene's coil block starts at 0x{ranges['coils'][0]}, not 0x0, so",
            f"  %QX100.0 is slave coil {ranges['coils'][0]} -- see NOT WIRED below.",
        ]
    head += [
        "",
        "  An INT or REAL tag is 32 bits and takes TWO registers, high word",
        "  first. Section 1 rebuilds the inputs and section 3 splits the outputs;",
        "  your program works with the plain DINT and REAL variables.",
        "",
        "  In the sidecar's order (block, then address; addresses are decimal):",
        "",
        "  MODBUS   REGS  OPENPLC                TAG",
    ]
    served = sidecar_modbus_map(scene.id, scene.tags)
    order = {"coils": 0, "discrete_inputs": 1, "holding_registers": 2, "input_registers": 3}
    for tag in sorted(scene.tags, key=lambda t: (order[served[t.id][0]], served[t.id][1])):
        block, address, width = served[tag.id]
        label = f"{_BLOCKS[block][0]}{address}"
        pts = by_tag.get(tag.id, [])
        iec = " + ".join(p.iec for p in pts) if pts else "(not wired)"
        head.append(f"  {label:<8} {width:<5} {iec:<22} {tag.id}")
    hands_off = [t for t in scene.tags if t.hands_off]
    if hands_off:
        head += ["", "NOT WIRED"]
        for t in hands_off:
            head += _wrap(f"{t.id}: {t.hands_off}. It is left out of the coil block, "
                          f"so OpenPLC never writes it.", 72, "  ")

    lines = _st_comment_block(head) + [""]
    if any(t.type == "int" for t in wide_in):
        lines += [_ST_WORDS_TO_DINT]
    if any(t.type == "float" for t in wide_in):
        lines += [_ST_WORDS_TO_REAL]
    if any(t.type == "float" for t in wide_out):
        lines += [_ST_REAL_TO_DWORD]

    lines += [f"PROGRAM {scene.stem}"]

    def located(title: str, block_filter) -> list[str]:
        rows = [p for p in points if block_filter(p)]
        if not rows:
            return []
        out = [f"    (* --- {title} " + "-" * max(4, 62 - len(title)) + " *)"]
        width = max(len(p.tag.name + p.half) for p in rows)
        idw = max(len(p.tag.id) for p in rows)
        for p in rows:
            decl = f"{p.tag.name + p.half:<{width}} AT {p.iec:<9} : {'BOOL' if not p.half else 'WORD'};"
            note = p.tag.meaning if not p.half else (
                f"{'high' if p.half == 'Hi' else 'low'} word of {p.tag.name}")
            out.append(f"    {decl}  (* {p.tag.id:<{idw}} {p.label:<5} {note} *)")
        return out

    lines += ["  VAR"]
    lines += located("PLC writes: coils", lambda p: p.block == "coils")
    lines += located("PLC writes: holding registers, two per value",
                     lambda p: p.block == "holding_registers")
    lines += located("PLC reads: discrete inputs", lambda p: p.block == "discrete_inputs")
    lines += located("PLC reads: input registers, two per value",
                     lambda p: p.block == "input_registers")
    lines += ["  END_VAR", ""]

    lines += [
        "  (* A second VAR block, and not for tidiness: matiec will not mix located",
        "     and unlocated declarations in one. Your own variables go here too. *)",
        "  VAR",
    ]
    wide = wide_in + wide_out
    if wide:
        width = max(len(t.name) for t in wide)
        for title, group in (("PLC reads", wide_in), ("PLC writes", wide_out)):
            if not group:
                continue
            lines.append(f"    (* --- {title}, in engineering units " + "-" * 34 + " *)")
            idw = max(len(t.id) for t in group)
            for t in group:
                decl = f"{t.name:<{width}} : {_TYPES_ST[t.type]};"
                lines.append(f"    {decl}  (* {t.id:<{idw}} {t.meaning} *)")
    if wide_out:
        lines.append("    Raw32 : DWORD;  (* scratch for splitting a 32-bit output *)")
    lines += ["  END_VAR", ""]

    lines += ["  (* ===== 1. Inputs: rebuild each 32-bit value from its two registers ===== *)"]
    for t in wide_in:
        fn = "FF_WORDS_TO_REAL" if t.type == "float" else "FF_WORDS_TO_DINT"
        lines.append(f"  {t.name} := {fn}(HI := {t.name}Hi, LO := {t.name}Lo);")
    if not wide_in:
        lines.append("  (* none in this scene *)")
    lines += [""]

    lines += ["  (* ===== 2. Your program ================================================"]
    lines += _wrap(f"The task is at the top of this file and in the scene's Task panel. "
                   f"Every signal above can be used by name here: the bits directly, "
                   f"the 32-bit values through {', '.join(t.name for t in wide) or 'nothing'}.",
                   72, "     ")
    lines[-1] += " *)"
    lines += [""]

    lines += ["  (* ===== 3. Outputs: split each 32-bit value into its two registers ===== *)"]
    for t in wide_out:
        if t.type == "float":
            lines.append(f"  Raw32 := FF_REAL_TO_DWORD({t.name});")
        else:
            lines.append(f"  Raw32 := DINT_TO_DWORD({t.name});")
        lines.append(f"  {t.name}Hi := DWORD_TO_WORD(SHR(IN := Raw32, N := 16));")
        lines.append(f"  {t.name}Lo := DWORD_TO_WORD(Raw32);")
    if not wide_out:
        lines.append("  (* none in this scene *)")
    lines += ["", "END_PROGRAM", "", ""]

    lines += [
        "CONFIGURATION Config0",
        "",
        "  RESOURCE Res0 ON PLC",
        "    (* 20 ms, as in examples/openplc/Sorting.st: every scan spent waiting",
        "       comes out of the scene's timing margins. *)",
        "    TASK Main(INTERVAL := T#20ms, PRIORITY := 0);",
        f"    PROGRAM Inst0 WITH Main : {scene.stem};",
        "  END_RESOURCE",
        "",
        "END_CONFIGURATION",
    ]
    return ascii_only("\n".join(lines) + "\n", f"{scene.id}.st")


def render_mbconfig(scene: Scene) -> str:
    _, ranges = modbus_layout(scene)
    lines = [
        "# OpenPLC Runtime v3 -- Modbus master (\"Slave Devices\") configuration for",
        f"# the FactoryForge scene {scene.id}. Generated by {GENERATED_BY}.",
        "#",
        "# Copy to OpenPLC_v3/webserver/core/mbconfig.cfg (the runtime reads it from",
        "# its working directory), or enter the same numbers in the web UI's",
        "# \"Slave Devices -> Add new device\" page. Sizes count registers, and an",
        "# Int or Float tag is TWO registers.",
    ]
    if ranges["coils"][0]:
        lines += [
            "#",
            f"# Coils start at {ranges['coils'][0]}, not 0: the master rewrites every coil in",
            "# its block on every poll, and the coils below that belong to a part, not",
            "# to the program. See the NOT WIRED note in the .st file.",
        ]
    lines += [
        "#",
        "# 127.0.0.1 is right when OpenPLC and the sidecar share a machine. If",
        "# OpenPLC runs in WSL or a VM, use the host's address here and start the",
        "# sidecar with -o host 0.0.0.0.",
        "",
        "Num_Devices = \"1\"",
        "",
        "# 50 ms, not OpenPLC's default 100: the polling period is paid twice, once",
        "# for a sensor to reach the PLC and once for a coil to reach the scene.",
        "Polling_Period = \"50\"",
        "Timeout = \"1000\"",
        "",
        "device0.name = \"FactoryForge\"",
        "device0.slave_id = \"1\"",
        "device0.protocol = \"TCP\"",
        "device0.address = \"127.0.0.1\"",
        "device0.IP_Port = \"5502\"",
        "",
    ]
    for block in ("discrete_inputs", "coils", "input_registers"):
        start, size = ranges[block]
        key = _BLOCKS[block][2]
        lines += [f"device0.{key}_Start = \"{start}\"", f"device0.{key}_Size = \"{size}\"", ""]
    lines += ["device0.Holding_Registers_Read_Start = \"0\"",
              "device0.Holding_Registers_Read_Size = \"0\"", ""]
    start, size = ranges["holding_registers"]
    lines += [f"device0.Holding_Registers_Start = \"{start}\"",
              f"device0.Holding_Registers_Size = \"{size}\""]
    return ascii_only("\n".join(lines) + "\n", f"{scene.id} mbconfig.cfg")


def _md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    return out


def _wrap_bullet(text: str) -> list[str]:
    lines = textwrap.wrap(text, width=78, initial_indent="- ", subsequent_indent="  ",
                          break_on_hyphens=False)
    return lines


def render_openplc_readme(scene: Scene) -> str:
    points, ranges = modbus_layout(scene)
    wide = [t for t in scene.wired if t.type != "bit"]
    rows = []
    for t in scene.tags:
        pts = [p for p in points if p.tag.id == t.id]
        if not pts:
            rows.append([f"`{t.id}`", _direction(t), t.type, "not wired", "--",
                         t.hands_off, "yes" if t.in_brief else ""])
            continue
        modbus = " + ".join(p.label for p in pts)
        iec = " + ".join(f"`{p.tag.name}{p.half}` {p.iec}" for p in pts)
        if t.type != "bit":
            iec += f" -> `{t.name}` : {_TYPES_ST[t.type]}"
        rows.append([f"`{t.id}`", _direction(t), t.type, modbus, iec, t.meaning,
                     "yes" if t.in_brief else ""])
    ui_rows = []
    for block, label in (("discrete_inputs", "Discrete Inputs (%IX100.0)"),
                         ("coils", "Coils (%QX100.0)"),
                         ("input_registers", "Input Registers (%IW100)"),
                         ("holding_registers", "Holding Registers - Write (%QW100)")):
        start, size = ranges[block]
        ui_rows.append([label, str(start), str(size)])
    ui_rows.insert(3, ["Holding Registers - Read (%IW100)", "0", "0"])

    lines = [
        f"# {scene.title} -- OpenPLC starter",
        "",
        f"*Generated by `{GENERATED_BY}` from the scene's tag set. Do not edit by",
        "hand; regenerate. The program's logic section is yours.*",
        "",
        f"**The task.** {scene.task}",
        "",
        f"**Done when.** {scene.done}",
        "",
        "| File | What it is |",
        "|---|---|",
        f"| [`{scene.stem}.st`]({scene.stem}.st) | Every I/O point declared at its located address, with an empty logic section. |",
        "| [`mbconfig.cfg`](mbconfig.cfg) | The Slave Device entry that points OpenPLC's Modbus master at the sidecar. |",
        "",
        *([f"**Note.** {_SCENE_NOTES[scene.id]['openplc']}", ""]
          if scene.id in _SCENE_NOTES else []),
        "The walkthrough for OpenPLC itself -- installing, compiling, timing,",
        "troubleshooting -- is [`docs/OPENPLC.md`](../../../docs/OPENPLC.md). This page",
        "is only what is different about this scene.",
        "",
        "## Run it",
        "",
        f"1. Open the scene in FactoryForge: {_open_scene(scene)}.",
        "2. **Then** start the sidecar as the Modbus slave. The order matters: the",
        "   addresses below are the ones it hands out for this scene's tags, and a",
        "   sidecar that was already running when you changed scene keeps its old",
        "   addresses and puts the new tags above them.",
        "",
        "   ```bash",
        "   factoryforge-sidecar connect --driver modbus-tcp -o port 5502",
        "   ```",
        "",
        "   (From a source checkout: `cd sidecar && python -m factoryforge_sidecar connect ...`.",
        "   If OpenPLC runs in WSL or a VM, add `-o host 0.0.0.0` and read the warning",
        "   it prints.) Check the address map it prints against the table below.",
        f"3. Compile `{scene.stem}.st` in OpenPLC (web UI *Programs -> Upload*, or copy it",
        f"   into `webserver/st_files/` and run `./scripts/compile_program.sh {scene.stem}.st`)",
        "   and copy `mbconfig.cfg` to `webserver/core/`,",
        "   or enter these numbers in *Slave Devices -> Add new device*",
        "   (Generic Modbus TCP device, port 5502, slave id 1):",
        "",
        *_md_table(["Block", "Start", "Size"], ui_rows),
        "",
        "4. Start the PLC. To have it marked instead, run the grader in place of the",
        "   3D scene -- same tags, same addresses -- and pass the port it prints:",
        "",
        "   ```bash",
        f"   python tools/grade.py --scene {scene.id}",
        "   factoryforge-sidecar connect --driver modbus-tcp --port <bus port it printed> -o port 5502",
        "   ```",
        "",
        "## I/O",
        "",
        "`PLC writes` is an output (a motor, a valve, a lamp); `PLC reads` is an input",
        "(a sensor, a button, a measurement). *Brief* marks the signals the task names;",
        "the rest are there to be used, such as each drive's fault contact. Modbus",
        "addresses are decimal, as the sidecar prints them: `0x10` is coil ten.",
        "",
        *_md_table(["Tag", "Direction", "Type", "Modbus", "In the program", "Meaning", "Brief"], rows),
        "",
        "## Three things this scene's wiring does not forgive",
        "",
        *_wrap_bullet(
            "**A 32-bit value is two registers**, high word first. "
            + (f"This scene has {len(wide)}: " + ", ".join(f"`{t.id}`" for t in wide) + ". "
               if wide else "")
            + "The program's first and last sections move them between the registers and "
              "plain DINT/REAL variables, so your logic never sees a register. OpenPLC's "
              "`DWORD_TO_REAL` converts the *number* (`16#3F800000` becomes 1065353216.0, "
              "not 1.0), so the starter rebuilds a REAL bit by bit instead. "
              "[`../check_starters.sh`](../check_starters.sh) compiles the "
              "helpers with OpenPLC's own matiec and checks them against two million "
              "bit patterns."),
        "- **Anything polled can be missed if it is short.** OpenPLC's master reads",
        "  every 50 ms. A button press in the 3D scene is one physics tick (about 17 ms)",
        "  on the tag bus, so a click can fall between two polls; the grader holds each",
        "  press for 0.15 s. The same goes the other way: hold an output you want the",
        "  scene to see for well over one polling period.",
        "- **An output you never write is FALSE.** The master rewrites the whole coil",
        "  block on every poll, including coils your program does not touch.",
    ]
    if any(t.hands_off for t in scene.tags):
        lines += [
            "",
            "## Not wired",
            "",
        ]
        for t in scene.tags:
            if t.hands_off:
                lines += [f"`{t.id}`: {t.hands_off}. The coil block starts above it, so "
                          "OpenPLC never writes it."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# TIA Portal: SCL source and the three Siemens mapping files
# ---------------------------------------------------------------------------

def render_scl(scene: Scene) -> str:
    order = db_order(scene)
    width = max(len(t.name) for t in order)
    lines = [
        'DATA_BLOCK "FF_IO"',
        "{ S7_Optimized_Access := 'FALSE' }",
        "VERSION : 0.1",
        "NON_RETAIN",
        "   VAR ",
    ]
    for t in order:
        decl = f"{t.name} : {_TYPES_S7[t.type]};"
        lines.append(f"      {decl:<{width + 8}} // {t.id} ({_direction(t)}) {t.meaning}")
    lines += ["   END_VAR", "", "", "BEGIN", "", "END_DATA_BLOCK", "", ""]

    lines += [
        f'FUNCTION_BLOCK "{scene.fb_name}"',
        "{ S7_Optimized_Access := 'TRUE' }",
        "VERSION : 0.1",
        "",
        "// " + "-" * 75,
        f"// FactoryForge starter -- {scene.title} (scene {scene.id})",
        f"// Generated by {GENERATED_BY}; see README.md beside this file.",
        "//",
        "// THE TASK",
        *_wrap(scene.task, 75, "//   "),
        "//",
        "// DONE WHEN",
        *_wrap(scene.done, 75, "//   "),
        "//",
        *([*_wrap("NOTE: " + _SCENE_NOTES[scene.id]["tia"], 75, "// "), "//"]
          if scene.id in _SCENE_NOTES else []),
        '// Every signal is a member of the global DB "FF_IO" above: read and write',
        '// them as "FF_IO".<Name>, e.g. "FF_IO".' + order[0].name + ". Its comments say which",
        "// way each one runs. Call this block from OB1 (drag it into Main; TIA creates",
        "// the instance DB).",
    ]
    hands_off = [t for t in scene.tags if t.hands_off]
    if hands_off:
        lines.append("//")
        lines.append("// NOT IN FF_IO")
        for t in hands_off:
            lines += _wrap(f"{t.id}: {t.hands_off}. It has no member and no mapping, "
                           f"so nothing the PLC does can write it.", 75, "//   ")
    lines += [
        "// " + "-" * 75,
        "",
        "BEGIN",
        "",
        "    // Your program.",
        "",
        "END_FUNCTION_BLOCK",
    ]
    return ascii_only("\n".join(lines) + "\n", f"{scene.id}.scl")


def _json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _hands_off_note(scene: Scene) -> dict:
    notes = {}
    for t in scene.tags:
        if t.hands_off:
            notes[f"_not_mapped_{t.id}"] = (
                f"{t.id} is deliberately absent: {t.hands_off}. The driver will "
                f"report it as unmapped, which is correct.")
    return notes


def render_opcua_mapping(scene: Scene) -> str:
    folder = f"examples/tia/{scene.id}"
    data = {
        "_comment": (f"FactoryForge tag id -> S7-1500 OPC UA NodeId for the scene {scene.id}. "
                     f"Generated by {GENERATED_BY} to match {scene.stem}.scl's FF_IO DB. "
                     "The namespace index is usually 3 but is NOT guaranteed: run "
                     "'factoryforge-sidecar browse opc.tcp://<cpu-ip>:4840' and fix ns= if "
                     "it differs. The double quotes are part of the Siemens identifier."),
        "_usage": (f"factoryforge-sidecar connect --driver opcua-client --mapping "
                   f"{folder}/opcua_mapping.json -o url opc.tcp://<cpu-ip>:4840"),
        **_hands_off_note(scene),
    }
    for t in sorted(scene.wired, key=lambda t: t.id):
        data[t.id] = f'ns=3;s="FF_IO"."{t.name}"'
    return _json(data)


def render_snap7_mapping(scene: Scene) -> str:
    addresses, length = snap7_addresses(scene)
    folder = f"examples/tia/{scene.id}"
    data = {
        "_comment": (f"FactoryForge tag id -> absolute address inside FF_IO for the snap7 "
                     f"driver, scene {scene.id}. Generated by {GENERATED_BY} from "
                     f"{scene.stem}.scl's member order; DBX<byte>.<bit> for a Bool, DBD<byte> "
                     "for a DInt or Real, as TIA's Offset column shows them."),
        "_usage": (f"factoryforge-sidecar connect --driver s7-snap7 -o host <cpu-ip> "
                   f"-o db <FF_IO's DB number> --mapping {folder}/snap7_mapping.json"),
        "_requires": ("FF_IO must NOT have optimized block access (the source sets "
                      "S7_Optimized_Access := 'FALSE'); an optimized DB has no byte "
                      f"addresses at all. FF_IO is {length} bytes."),
        "_layout": ("PLC-written members first, then simulator-written ones, each group "
                    "with its DInts/Reals before its Bools, so no byte holds both "
                    "directions (AGENTS.md gotcha 19d). Reorder the DB and these move."),
        **_hands_off_note(scene),
    }
    for t in sorted(scene.wired, key=lambda t: t.id):
        data[t.id] = addresses[t.id]
    return _json(data)


def render_plcsim_mapping(scene: Scene) -> str:
    folder = f"examples/tia/{scene.id}"
    data = {
        "_comment": (f"FactoryForge tag id -> PLCSIM Advanced tag name for the native API "
                     f"driver, scene {scene.id}. Generated by {GENERATED_BY}. Dotted and "
                     "without quotes, as examples/plcsim_mapping.json has them."),
        "_usage": (f"factoryforge-sidecar connect --driver plcsim-advanced -o instance "
                   f"<name> --mapping {folder}/plcsim_mapping.json"),
        **_hands_off_note(scene),
    }
    for t in sorted(scene.wired, key=lambda t: t.id):
        data[t.id] = f"FF_IO.{t.name}"
    return _json(data)


def render_tia_readme(scene: Scene) -> str:
    addresses, length = snap7_addresses(scene)
    rows = []
    for t in db_order(scene):
        rows.append([f"`{t.id}`", _direction(t), f"`{t.name}`", _TYPES_S7[t.type],
                     addresses[t.id], t.meaning, "yes" if t.in_brief else ""])
    for t in scene.tags:
        if t.hands_off:
            rows.append([f"`{t.id}`", _direction(t), "--", "--", "--", t.hands_off,
                         "yes" if t.in_brief else ""])
    folder = f"examples/tia/{scene.id}"
    lines = [
        f"# {scene.title} -- TIA Portal starter",
        "",
        f"*Generated by `{GENERATED_BY}` from the scene's tag set. Do not edit by",
        "hand; regenerate. The function block's body is yours.*",
        "",
        f"**The task.** {scene.task}",
        "",
        f"**Done when.** {scene.done}",
        "",
        "| File | What it is |",
        "|---|---|",
        f"| [`{scene.stem}.scl`]({scene.stem}.scl) | SCL source: the global DB `FF_IO` with every signal, and an empty FB `\"{scene.fb_name}\"`. |",
        "| [`opcua_mapping.json`](opcua_mapping.json) | For `--driver opcua-client`: tag id -> `ns=3;s=\"FF_IO\".\"...\"`. |",
        "| [`snap7_mapping.json`](snap7_mapping.json) | For `--driver s7-snap7`: tag id -> `DBX`/`DBD` offset in `FF_IO`. |",
        "| [`plcsim_mapping.json`](plcsim_mapping.json) | For `--driver plcsim-advanced`: tag id -> `FF_IO.<Name>`. |",
        "",
        *([f"**Note.** {_SCENE_NOTES[scene.id]['tia']}", ""]
          if scene.id in _SCENE_NOTES else []),
        "The Siemens walkthrough -- PLCSIM Advanced, the OPC UA server, finding",
        "NodeIds -- is [`../README.md`](../README.md). This page is only what is",
        "different about this scene.",
        "",
        "## Set it up",
        "",
        "1. In a project with an S7-1500 CPU: *External source files -> Add new",
        f"   external file*, pick `{scene.stem}.scl`, then right-click it -> *Generate",
        f"   blocks from source*. That creates `FF_IO` and `\"{scene.fb_name}\"`. One",
        "   exercise per CPU: this `FF_IO` replaces any other one.",
        f"2. Call `\"{scene.fb_name}\"` from OB1 (drag it into `Main`; TIA creates the",
        "   instance DB).",
        "3. Check `FF_IO`'s properties: *Optimized block access* off (the source sets",
        "   it), *Accessible from HMI/OPC UA* and *Writable from HMI/OPC UA* on for",
        "   every member (TIA's default). Without *Writable* every sensor reads FALSE",
        "   forever.",
        "4. Compile, download, RUN.",
        "",
        "## Run it",
        "",
        f"Open the scene in FactoryForge first ({_open_scene(scene)}), then attach",
        "the sidecar with whichever driver reaches your CPU:",
        "",
        "```bash",
        f"factoryforge-sidecar connect --driver opcua-client --mapping {folder}/opcua_mapping.json -o url opc.tcp://<cpu-ip>:4840",
        f"factoryforge-sidecar connect --driver s7-snap7 -o host <cpu-ip> -o db <FF_IO's number> --mapping {folder}/snap7_mapping.json",
        f"factoryforge-sidecar connect --driver plcsim-advanced -o instance <name> --mapping {folder}/plcsim_mapping.json",
        "```",
        "",
        "(From a source checkout: `python -m factoryforge_sidecar` in place of",
        "`factoryforge-sidecar`, run from the repository root so the paths resolve.)",
        f"To have it marked, run `python tools/grade.py --scene {scene.id}` in place",
        "of the 3D scene and add the `--port` it prints to the same command.",
        "",
        "The OPC UA namespace index is usually 3, not always: run",
        "`factoryforge-sidecar browse opc.tcp://<cpu-ip>:4840` once and fix `ns=` in",
        "the mapping if yours differs.",
        "",
        "## FF_IO",
        "",
        "In declaration order. `PLC writes` is an output (a motor, a valve, a lamp);",
        "`PLC reads` is an input (a sensor, a button, a measurement). The two",
        "directions never share a byte, so the snap7 driver never rewrites a bit your",
        f"program owns. `FF_IO` is {length} bytes. *Brief* marks the signals the task",
        "names.",
        "",
        *_md_table(["Tag", "Direction", "Member", "Type", "Offset", "Meaning", "Brief"], rows),
        "",
        "`int` travels as a `DInt` and `float` as a `Real`: the OPC UA driver writes",
        "Int32 and Float, and a member of any other type is refused as a type mismatch.",
        "Hold anything the scene must see for well over a second on OPC UA: an",
        "S7-1500 forces a 1000 ms publishing interval on subscriptions, which is why the",
        "driver polls by default.",
    ]
    if any(t.hands_off for t in scene.tags):
        lines += ["", "## Not in FF_IO", ""]
        for t in scene.tags:
            if t.hands_off:
                lines += [f"`{t.id}`: {t.hands_off}. It has no member and no mapping, so",
                          "the sidecar reports it as unmapped -- which is correct."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

def render_index(scenes: list[Scene]) -> str:
    rows = []
    for s in scenes:
        rows.append([s.title, f"`{s.id}`",
                     f"[openplc/{s.id}/](openplc/{s.id}/)",
                     f"[tia/{s.id}/](tia/{s.id}/)"])
    lines = [
        "# Examples",
        "",
        f"*The starter table below is generated by `{GENERATED_BY}`; edit that,",
        "not this file.*",
        "",
        "## Starter programs, one per graded scene",
        "",
        "Each graded scene has a starter for your own PLC IDE: every input and output",
        "declared, with a comment saying what it is, and **no logic**. Write the",
        "program, connect it, and run the grader against it. The answers are not",
        "here; the reference controllers the grader checks itself with stay Python.",
        "",
        *_md_table(["Scene", "Id", "OpenPLC (Modbus TCP)", "TIA Portal (S7-1500)"], rows),
        "",
        "**OpenPLC** -- free, no licence, IEC 61131-3 ST. Each folder has `<scene>.st`",
        "and the `mbconfig.cfg` that points OpenPLC's Modbus master at FactoryForge:",
        "",
        "```bash",
        "factoryforge-sidecar connect --driver modbus-tcp -o port 5502",
        "```",
        "",
        "**TIA Portal** -- an SCL source with the global DB `FF_IO` and an empty FB,",
        "plus a mapping file for each Siemens driver:",
        "",
        "```bash",
        "factoryforge-sidecar connect --driver opcua-client --mapping examples/tia/<scene>/opcua_mapping.json -o url opc.tcp://<cpu-ip>:4840",
        "factoryforge-sidecar connect --driver s7-snap7 -o host <cpu-ip> -o db <n> --mapping examples/tia/<scene>/snap7_mapping.json",
        "factoryforge-sidecar connect --driver plcsim-advanced -o instance <name> --mapping examples/tia/<scene>/plcsim_mapping.json",
        "```",
        "",
        "Open the scene **before** starting the sidecar, and restart the sidecar if",
        "you change scene: the Modbus driver hands out addresses once, in sorted",
        "tag-id order, and keeps them for as long as it runs. To be marked, start",
        "`python tools/grade.py --scene <id>` in place of the 3D scene and add",
        "`--port <the port it prints>` to the connect command; the grader offers the",
        "same tags, so the same starter and the same addresses work against both.",
        "(From a source checkout, `python -m factoryforge_sidecar` stands in for",
        "`factoryforge-sidecar`.)",
        "",
        "The starters are checked by `tests/test_examples.py`: each one's declarations",
        "against the tag set the engine registers for its scene",
        "(`engine/fixtures/scene_tag_sets.json`), the OpenPLC addresses against what",
        "the sidecar's Modbus driver really serves, and every file against a fresh",
        "run of the generator.",
        "",
        "**`sorting-by-height` has two OpenPLC programs, for two different maps.**",
        "`openplc/Sorting.st` is a complete, verified program for the ten-tag Python",
        "scene that `factoryforge-sidecar demo` runs. `openplc/sorting-by-height/` is",
        "the starter for the line the 3D engine opens and the grader marks, which also",
        "has an operator panel and two fault contacts: nineteen tags, so the Modbus",
        "addresses differ. Use each with the scene it was written for.",
        "",
        "## Everything else here",
        "",
        "| Path | What it is |",
        "|---|---|",
        "| [`openplc/Sorting.st`](openplc/Sorting.st), [`openplc/mbconfig.cfg`](openplc/mbconfig.cfg) | A finished sorting program for OpenPLC, verified at 103 tall / 103 short. Walkthrough: [`docs/OPENPLC.md`](../docs/OPENPLC.md). |",
        "| [`openplc/verify_int32.py`](openplc/verify_int32.py) | Checks the 32-bit counter path through a running OpenPLC. |",
        "| [`tia/Sorting.scl`](tia/Sorting.scl), [`tia/FF_IO_datablock.md`](tia/FF_IO_datablock.md) | A finished sorting program for an S7-1500, verified on PLCSIM Advanced. Walkthrough: [`tia/README.md`](tia/README.md). |",
        "| [`opcua_mapping.json`](opcua_mapping.json), [`snap7_mapping.json`](snap7_mapping.json), [`plcsim_mapping.json`](plcsim_mapping.json) | The three Siemens drivers' maps for that ten-member `FF_IO`. |",
        "| [`fake_plc.py`](fake_plc.py) | A Python stand-in for the S7's OPC UA server, to test the path without TIA. |",
        "| [`nodered/factoryforge-flow.json`](nodered/factoryforge-flow.json) | A Node-RED flow that replaces the PLC entirely. |",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def render_all(fixture: Path | None = None) -> dict[str, str]:
    """`{path relative to the repo root: text}` for every generated file."""
    scenes = load_scenes(fixture)
    out: dict[str, str] = {"examples/README.md": render_index(scenes)}
    for s in scenes:
        op = f"examples/openplc/{s.id}"
        tia = f"examples/tia/{s.id}"
        out[f"{op}/{s.stem}.st"] = render_st(s)
        out[f"{op}/mbconfig.cfg"] = render_mbconfig(s)
        out[f"{op}/README.md"] = render_openplc_readme(s)
        out[f"{tia}/{s.stem}.scl"] = render_scl(s)
        out[f"{tia}/opcua_mapping.json"] = render_opcua_mapping(s)
        out[f"{tia}/snap7_mapping.json"] = render_snap7_mapping(s)
        out[f"{tia}/plcsim_mapping.json"] = render_plcsim_mapping(s)
        out[f"{tia}/README.md"] = render_tia_readme(s)
    return dict(sorted(out.items()))


def starter_dirs(scenes: list[str]) -> list[Path]:
    return [EXAMPLES / kind / s for s in scenes for kind in ("openplc", "tia")]


def stale(files: dict[str, str]) -> list[str]:
    """Paths whose committed text differs from *files*, plus files in a starter
    folder the generator no longer writes. Line endings are normalised: git on
    Windows checks these out with CRLF, and that is not a difference."""
    problems = []
    for rel, text in files.items():
        path = ROOT / rel
        if not path.exists():
            problems.append(f"{rel}: missing")
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(f"{rel}: differs from a fresh generation")
    wanted = {ROOT / rel for rel in files}
    for folder in sorted({(ROOT / rel).parent for rel in files if rel.count("/") >= 3}):
        for path in sorted(folder.iterdir()):
            if path not in wanted:
                problems.append(f"{path.relative_to(ROOT).as_posix()}: not generated")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if any committed starter is stale")
    parser.add_argument("--fixture", type=Path, default=None,
                        help=f"tag-set fixture to read (default {FIXTURE.relative_to(ROOT)})")
    args = parser.parse_args(argv)

    files = render_all(args.fixture)
    if args.check:
        problems = stale(files)
        for p in problems:
            print(p)
        print(f"{len(files)} files, {len(problems)} stale")
        return 1 if problems else 0

    written = 0
    for rel, text in files.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if old != text:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            written += 1
    print(f"{len(files)} files, {written} written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
