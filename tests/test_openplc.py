"""The OpenPLC example's located variables still match what the driver hands out.

`examples/openplc/Sorting.st` is a hand-written list of numbers -- `%IX100.2`,
`%IW102` -- and Modbus carries no tag names on the wire, so nothing at runtime
can notice when it stops matching the scene. The failure mode is a program that
was working and now drives the wrong device.

So this file rebuilds the *real* address map from the *real* sorting scene with
the *real* Modbus driver, translates it into OpenPLC's master addressing, and
compares that against the example as written. It needs no OpenPLC, no sockets
and no PLC: the allocator and the file are both here.

What it would catch:

  - a tag added to or removed from the sorting scene without the example
    following it;
  - an Int going back to one register (HP-49), which halves every counter
    address above it and reads half a number at the one below;
  - the address allocator changing the order it hands addresses out;
  - a typo in the example's own block or offset.

OpenPLC's Modbus master lays a slave device's points into its own buffers
starting at 100, in the order the device declares them:

    slave discrete input  n  ->  %IX(100 + n/8).(n mod 8)
    slave coil            n  ->  %QX(100 + n/8).(n mod 8)
    slave input register  n  ->  %IW(100 + n)
    slave holding reg (W) n  ->  %QW(100 + n)

That is `updateBuffersIn_MB` / `updateBuffersOut_MB` in OpenPLC v3's
`webserver/core/modbus_master.cpp`, and it is why every located address in the
example starts at 100.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from factoryforge_sidecar import drivers
from scene import SortingScene

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "openplc" / "Sorting.st"
MBCONFIG = ROOT / "examples" / "openplc" / "mbconfig.cfg"

#: `Name AT %QX100.0 : BOOL;` -- the declaration form, not the prose.
_LOCATED = re.compile(
    r"^\s*(\w+)\s+AT\s+(%[A-Z]+[\d.]+)\s*:\s*(\w+)\s*;", re.MULTILINE)

#: OpenPLC's master block for each of the four Modbus blocks, and whether the
#: block is addressed in bits or in words.
_BLOCK = {
    "discrete_inputs":    ("%IX", "bit"),
    "coils":              ("%QX", "bit"),
    "input_registers":    ("%IW", "word"),
    "holding_registers":  ("%QW", "word"),
}


def _openplc_address(block: str, offset: int) -> str:
    prefix, width = _BLOCK[block]
    if width == "bit":
        return f"{prefix}{100 + offset // 8}.{offset % 8}"
    return f"{prefix}{100 + offset}"


class _SilentBus:
    """Enough of a bus to construct a driver and call `rebuild`. No sockets:
    the address allocator is the only thing under test here."""

    def on_describe(self, _handler) -> None:
        pass

    on_update = on_disconnect = on_describe

    async def status(self, *args, **kwargs) -> None:
        pass


@pytest.fixture
def mapping():
    """The address map the driver really produces for the real sorting scene."""
    return drivers.create("modbus-tcp", _SilentBus(), port=0)


async def _scene_map(driver) -> dict[str, tuple[str, int, int]]:
    scene = SortingScene()
    await driver.rebuild(scene.name, 1, scene.tags)
    return {m.tag_id: (m.block, m.address, m.width) for m in driver._by_tag.values()}


def _without_comments(text: str) -> str:
    """ST's `(* ... *)`, blanked rather than deleted so line numbers survive.

    The example's header quotes addresses and quotes the emit-timer spelling it
    tells you not to use, so a check that reads the file has to know the
    difference between the program and the prose about it.
    """
    return re.sub(r"\(\*.*?\*\)",
                  lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S)


def _declared() -> dict[str, tuple[str, str]]:
    """`{IEC address: (variable name, IEC type)}` from the example program."""
    text = _without_comments(EXAMPLE.read_text(encoding="utf-8"))
    out = {}
    for name, address, type_ in _LOCATED.findall(text):
        assert address not in out, f"{address} is declared twice in {EXAMPLE.name}"
        out[address] = (name, type_)
    return out


async def test_every_scene_tag_is_declared_at_the_address_it_was_given(mapping):
    """One assertion per tag, named, so a failure says which one moved."""
    declared = _declared()
    for tag_id, (block, address, width) in sorted((await _scene_map(mapping)).items()):
        for offset in range(width):
            iec = _openplc_address(block, address + offset)
            assert iec in declared, (
                f"{tag_id} is at {block}[{address + offset}], which is {iec} "
                f"for OpenPLC's master -- and {EXAMPLE.name} declares nothing there"
            )


async def test_the_example_declares_nothing_the_scene_does_not_have(mapping):
    """The other direction. A leftover declaration reads a register no tag
    backs, which on this driver is a permanent zero and looks like a sensor
    that never fires."""
    live = set()
    for block, address, width in (await _scene_map(mapping)).values():
        live.update(_openplc_address(block, address + o) for o in range(width))
    extra = sorted(set(_declared()) - live)
    # %M addresses are the program's own memory, not the slave device's.
    extra = [a for a in extra if not a.startswith("%M")]
    assert not extra, f"{EXAMPLE.name} declares {extra}, which no scene tag maps to"


async def test_an_int_tag_takes_two_registers_and_both_halves_are_declared(mapping):
    """The wire-format change HP-49 made, from the third party's side.

    An Int is 32-bit signed big-endian across two registers. OpenPLC's Modbus
    master has no 32-bit point type, so the example has to declare both halves
    and put them back together. If the driver ever went back to one register
    per Int, `counter.tall` would move down by one and the reassembly would
    read one register belonging to `counter.short`.
    """
    declared = _declared()
    scene_map = await _scene_map(mapping)
    ints = {tag.id for tag in SortingScene().tags if tag.type == "int"}
    assert ints, "the sorting scene is supposed to have Int counters"

    for tag_id in sorted(ints):
        block, address, width = scene_map[tag_id]
        assert width == 2, (
            f"{tag_id} occupies {width} register(s); an Int tag is 32 bits and "
            "the example reassembles it from a high word and a low word")
        high = _openplc_address(block, address)
        low = _openplc_address(block, address + 1)
        assert high in declared and low in declared, (
            f"{tag_id} spans {high} and {low}; {EXAMPLE.name} declares "
            f"{[a for a in (high, low) if a in declared]}")
        # High word first: struct.pack(">i") puts it in the lower register.
        assert "Hi" in declared[high][0] and "Lo" in declared[low][0], (
            f"{tag_id}: {high} should hold the high word and {low} the low "
            f"word, but they are named {declared[high][0]} / {declared[low][0]}")


async def test_mbconfig_polls_far_enough_to_reach_every_tag(mapping):
    """A size that stops short of the map is the quietest way to lose a tag:
    OpenPLC simply never reads it, and the located variable sits at zero."""
    text = MBCONFIG.read_text(encoding="utf-8")
    sizes = {
        "discrete_inputs": int(re.search(r'Discrete_Inputs_Size = "(\d+)"', text)[1]),
        "coils": int(re.search(r'Coils_Size = "(\d+)"', text)[1]),
        "input_registers": int(re.search(r'Input_Registers_Size = "(\d+)"', text)[1]),
    }
    needed: dict[str, int] = {}
    for block, address, width in (await _scene_map(mapping)).values():
        needed[block] = max(needed.get(block, 0), address + width)

    for block, count in needed.items():
        assert sizes.get(block, 0) >= count, (
            f"mbconfig.cfg reads {sizes.get(block, 0)} of the {count} "
            f"{block} the scene uses")


def test_the_example_does_not_self_reset_the_emit_timer():
    """AGENTS.md gotcha 5. `tEmit(IN := NOT tEmit.Q, ...)` makes Q high for a
    single scan, and the statement that depends on it did not run on a real
    S7-1500. The example carries the warning; this checks the warning did not
    outlive the code it describes."""
    body = _without_comments(EXAMPLE.read_text(encoding="utf-8"))
    offenders = [line.strip() for line in body.splitlines() if "IN := NOT" in line]
    assert not offenders, offenders
