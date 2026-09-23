"""The sidecar CLI: the flags it advertises, and the values it hands to drivers.

Both halves are places where something looked like it worked and did not --
a documented flag that was parsed and never read, and driver options that
arrived as strings and stayed strings all the way into ctypes.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from factoryforge_sidecar import drivers
from factoryforge_sidecar.__main__ import coerce_options, demo, main


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


# --- driver options, coerced once, at the boundary ----------------------------

def test_a_numeric_option_arrives_as_a_number():
    """`-o poll_interval 0.05` reached asyncio.sleep as a string and raised
    there -- inside the poller's task, where nothing observes it, while the
    connection loop went on reporting health."""
    assert coerce_options("opcua-client", [("poll_interval", "0.05")]) == \
        {"poll_interval": 0.05}
    assert coerce_options("s7-snap7", [("db", "3")]) == {"db": 3}
    assert coerce_options("modbus-tcp", [("port", "5020")]) == {"port": 5020}


def test_a_boolean_option_can_actually_be_switched_off():
    """`-o auto_map false` was a non-empty string, which is true."""
    assert coerce_options("opcua-client", [("auto_map", "false")]) == {"auto_map": False}
    assert coerce_options("opcua-client", [("auto_map", "true")]) == {"auto_map": True}
    assert coerce_options("opcua-client", [("auto_map", "0")]) == {"auto_map": False}


def test_a_string_option_is_left_exactly_as_typed():
    """Coercion follows the driver's own signature, so a URL, a host or a PLC
    instance name that happens to look numeric is not quietly converted."""
    assert coerce_options("opcua-client", [("url", "opc.tcp://192.168.1.20:4840")]) == \
        {"url": "opc.tcp://192.168.1.20:4840"}
    assert coerce_options("modbus-tcp", [("host", "127.0.0.1")]) == {"host": "127.0.0.1"}
    assert coerce_options("plcsim-advanced", [("instance", "1500")]) == \
        {"instance": "1500"}


def test_an_option_the_driver_does_not_declare_is_passed_through():
    """Drivers take **config, and an undeclared key is theirs to reject --
    not something to guess a type for."""
    assert coerce_options("mock", [("whatever", "12")]) == {"whatever": "12"}


def test_every_driver_can_be_asked_what_its_options_are():
    """Read from the signature rather than a table kept alongside it, so a
    driver that gains an option gets it coerced without anyone remembering."""
    assert drivers.option_types("s7-snap7")["db"] == "int"
    assert drivers.option_types("opcua-client")["auto_map"] == "bool"
    assert drivers.option_types("opcua-client")["poll_interval"] == "float"
    # `bus` is the driver's collaborator and `**config` is the catch-all;
    # neither is an option. s7-snap7 annotates both, so it is the driver where
    # leaving them in would actually show.
    assert "bus" not in drivers.option_types("s7-snap7")
    assert "config" not in drivers.option_types("s7-snap7")


@pytest.mark.parametrize("driver, pair", [
    ("opcua-client", ("poll_interval", "fast")),
    ("s7-snap7", ("db", "one")),
    ("opcua-client", ("auto_map", "maybe")),
])
def test_an_unparseable_option_is_refused_with_a_message(driver, pair):
    with pytest.raises(ValueError) as exc:
        coerce_options(driver, [pair])
    assert pair[0] in str(exc.value), "the message does not name the option"
    assert pair[1] in str(exc.value), "the message does not quote what was typed"


def test_the_cli_refuses_a_bad_option_rather_than_starting(capsys):
    """And it refuses *before* waiting for an engine, so the answer you get is
    about the option you typed and not about a port nothing is listening on.

    Port 1 has no engine on it, so without the check this returns 1 after the
    timeout rather than 2 straight away.
    """
    code = main(["connect", "--driver", "opcua-client", "--port", "1",
                 "--timeout", "0.5", "-o", "poll_interval", "fast"])
    assert code == 2, "a bad option was carried past the point of no return"
    assert "poll_interval" in capsys.readouterr().err


def test_the_coerced_value_is_what_the_driver_ends_up_holding(bus):
    """The point of the exercise: not that a dict has a float in it, but that
    the driver's attribute does."""
    options = coerce_options("opcua-client", [("poll_interval", "0.25"),
                                              ("auto_map", "false"),
                                              ("timeout", "20")])
    driver = drivers.create("opcua-client", bus, **options)
    assert driver.poll_interval == 0.25
    assert driver.auto_map is False
    assert driver.timeout == 20.0


# --- the package stands on its own, as the frozen release needs it to -------

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "sidecar" / "factoryforge_sidecar"


def _run_sidecar(args, cwd, env_extra, timeout=90):
    """`python -m factoryforge_sidecar ...` in a child with only what
    `env_extra` puts on its path. `subprocess.run` drains both pipes, so a
    chatty child cannot stall on a full one (AGENTS.md gotcha 15)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "FACTORYFORGE_TEMPLATES")}
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-m", "factoryforge_sidecar", *args],
                          cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def test_demo_runs_from_a_copy_of_the_package_outside_the_checkout(tmp_path):
    """`demo` used to import `engine_stub` and `scene` from the checkout's
    `harness/`, found relative to `__main__.py`. A checkout always has one, so
    every run from it passed, and a sidecar frozen from v1.0.0 -- which has no
    checkout around it -- failed `demo` with "No module named 'engine_stub'"
    (IP-08). A copy of the package somewhere else is the installed and frozen
    layout: no `harness/` anywhere above it."""
    shutil.copytree(PACKAGE, tmp_path / "factoryforge_sidecar",
                    ignore=shutil.ignore_patterns("__pycache__"))
    result = _run_sidecar(["demo", "--driver", "mock", "--port", "0", "--duration", "1"],
                          cwd=tmp_path, env_extra={"PYTHONPATH": str(tmp_path)})
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-2000:]
    assert "engine listening on ws://" in output
    assert "driver 'mock' started" in output
    # And it ends cleanly: cancelling the bus client mid-recv used to end every
    # run in "Task exception was never retrieved" and a traceback.
    assert "Traceback" not in output, output[-2000:]


def test_grade_is_the_grader_with_its_own_flags(capsys):
    """`grade` hands everything after it to `grading.main` untouched, so the
    flags are `tools/grade.py`'s, and `--help` names a command that runs."""
    assert main(["grade", "--list"]) == 0
    assert "sorting-by-height" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["grade", "--help"])
    assert "usage: factoryforge-sidecar grade" in capsys.readouterr().out


@pytest.mark.parametrize("reference, code, verdict", [
    ("good", 0, "PASS"),
    ("blind", 1, "FAIL"),
])
def test_grade_returns_the_graders_exit_code(capsys, reference, code, verdict):
    """0 PASS and 1 FAIL, the numbers a marking script reads. Lockstep and a
    fixed seed, so the mark cannot depend on how busy this machine is."""
    assert main(["grade", "--scene", "sorting-by-height", "--reference", reference,
                 "--lockstep", "--seed", "1", "--quiet"]) == code
    assert f"RESULT grade={verdict} scene=sorting-by-height" in capsys.readouterr().out


def test_grade_without_templates_is_an_error_not_a_traceback(tmp_path):
    """The grader reads each plant from the engine's templates. A sidecar that
    cannot see them -- a freeze without them, or an override pointing nowhere
    -- must say so on the grader's ERROR line with exit code 2, which a
    marking script handles, rather than die in a stack trace."""
    result = _run_sidecar(["grade", "--list"], cwd=tmp_path,
                          env_extra={"PYTHONPATH": str(ROOT / "sidecar"),
                                     "FACTORYFORGE_TEMPLATES": str(tmp_path / "none")})
    assert result.returncode == 2, result.stdout + result.stderr
    assert "RESULT grade=ERROR" in result.stderr
    assert "FACTORYFORGE_TEMPLATES" in result.stderr
    assert "Traceback" not in result.stderr
