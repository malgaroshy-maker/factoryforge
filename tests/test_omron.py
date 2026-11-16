"""Tests for Omron CX-Simulator driver by Ismail Lowkey."""

from __future__ import annotations

import asyncio
try:
    import pytest
except ImportError:
    pytest = None

from factoryforge_sidecar import drivers
from factoryforge_sidecar.drivers.omron_cxsim import OmronCXSimulatorDriver, _NativeOmronSimulatorEngine
from factoryforge_sidecar.tags import Tag, TagTable


def test_omron_driver_is_registered():
    """Verify that 'omron-cxsim' is registered in available drivers."""
    assert "omron-cxsim" in drivers.available()


def test_native_engine_address_parser():
    """Verify address parser handles Omron CIO, WR, HR, AR, DM, TIM, CNT."""
    engine = _NativeOmronSimulatorEngine()

    # CIO bit
    area, word, bit = engine._parse_address("0.05")
    assert area == engine.CIO_OFFSET
    assert word == 0
    assert bit == 5

    area, word, bit = engine._parse_address("CIO100.12")
    assert area == engine.CIO_OFFSET
    assert word == 100
    assert bit == 12

    # WR bit
    area, word, bit = engine._parse_address("W10.02")
    assert area == engine.WR_OFFSET
    assert word == 10
    assert bit == 2

    # HR bit
    area, word, bit = engine._parse_address("H5.00")
    assert area == engine.HR_OFFSET
    assert word == 5
    assert bit == 0

    # DM word
    area, word, bit = engine._parse_address("D200")
    assert area == engine.DM_OFFSET
    assert word == 200

    # Timer flag
    area, word, bit = engine._parse_address("T15")
    assert area == engine.TIM_FLAG_OFFSET
    assert word == 15


def test_auto_assign_omron_addresses():
    """Verify auto-assignment of Omron addresses to tags."""
    table = TagTable()
    table.add(Tag(id="sensor.part", name="Part Sensor", type="bit", kind="input", value=False))
    table.add(Tag(id="sensor.height", name="Height Sensor", type="bit", kind="input", value=False))
    table.add(Tag(id="motor.belt", name="Belt Motor", type="bit", kind="output", value=False))
    table.add(Tag(id="pusher.extend", name="Pusher", type="bit", kind="output", value=False))
    table.add(Tag(id="counter.total", name="Counter", type="int", kind="output", value=0))

    class DummyBus:
        def on_describe(self, fn): pass
        def on_update(self, fn): pass
        def on_disconnect(self, fn): pass

    driver = OmronCXSimulatorDriver(DummyBus())
    driver._auto_assign_omron_addresses(table)

    # Inputs should be mapped to CIO 0.00, 0.01
    assert driver.mapping["sensor.part"] == "0.00"
    assert driver.mapping["sensor.height"] == "0.01"

    # Outputs should be mapped to CIO 100.00, 100.01
    assert driver.mapping["motor.belt"] == "100.00"
    assert driver.mapping["pusher.extend"] == "100.01"

    # Integer should be mapped to DM
    assert driver.mapping["counter.total"] == "D0"


def test_omron_rollover_channel():
    """Verify input rolls over from 0.11 -> 1.00 and output rolls from 100.07 -> 101.00."""
    table = TagTable()
    for i in range(15):
        table.add(Tag(id=f"in.{i}", name=f"In {i}", type="bit", kind="input", value=False))
    for j in range(10):
        table.add(Tag(id=f"out.{j}", name=f"Out {j}", type="bit", kind="output", value=False))

    class DummyBus:
        def on_describe(self, fn): pass
        def on_update(self, fn): pass
        def on_disconnect(self, fn): pass

    driver = OmronCXSimulatorDriver(DummyBus())
    driver._auto_assign_omron_addresses(table)

    # Inputs: 0.00 to 0.11, then 1.00
    assert driver.mapping["in.0"] == "0.00"
    assert driver.mapping["in.11"] == "0.11"
    assert driver.mapping["in.12"] == "1.00"
    assert driver.mapping["in.13"] == "1.01"

    # Outputs: 100.00 to 100.07, then 101.00
    assert driver.mapping["out.0"] == "100.00"
    assert driver.mapping["out.7"] == "100.07"
    assert driver.mapping["out.8"] == "101.00"
    assert driver.mapping["out.9"] == "101.01"
