"""IP-30: the graded window opens when the student's driver is up.

Since IP-25 the window opened on `describe`, the end of the tag-bus
handshake. But `factoryforge_sidecar connect` starts its driver only after the
describe arrives, so however long an OPC UA or S7 driver took to reach its PLC
came out of the window -- and eight of ten scenes press Start 1.0 s in, for
0.15 s. The sidecar now tells the engine when its driver has reached the
controller (`controller` in docs/tag-bus.md), and the window waits for that,
falling back to the describe after a bounded wait for a controller that never
says.

Everything here is wall clock, on purpose: when the window opens is a
wall-clock question, and lockstep, which steps a reference in this process,
never had the problem.

The second half checks that each driver's "ready" means what docs/tag-bus.md
says it means, against a real Modbus master and real OPC UA peers -- not that
`start()` returned.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import struct

import pytest
from asyncua import Client, Server, ua

from factoryforge_sidecar import __main__ as cli
from factoryforge_sidecar import drivers
from factoryforge_sidecar.grading import core
from factoryforge_sidecar.tagbus import TagBusClient

#: How long the stand-in driver takes to reach its PLC. The task's own number,
#: and three times the 1.0 s at which start-stop-station presses Start.
CONNECT_DELAY = 3.0
#: start-stop-station presses Start at 1.0 s. Four seconds covers the press
#: and lets the run finish quickly; nothing here reads the verdict's checks.
WINDOW = 4.0


def _args(scene: str, duration: float, ready_wait: float | None = None):
    args = argparse.Namespace(
        scene=scene, seed=11, duration=duration, wait=30, quiet=True,
        reference=None, lockstep=False, bus_port=0, student=None, json_path=None)
    if ready_wait is not None:
        args.ready_wait = ready_wait
    return args


async def _grade_with(args, attach):
    """Run the grader, hand its engine to `attach` (which connects a
    controller and returns an awaitable that stops it), and return the report.
    Bounded on every path: a grader that died before binding must not leave
    the test waiting."""
    listening = asyncio.get_running_loop().create_future()
    grading = asyncio.create_task(
        core.run_grading(args, on_listening=listening.set_result))
    stop = None
    try:
        done, _ = await asyncio.wait({listening, grading}, timeout=15,
                                     return_when=asyncio.FIRST_COMPLETED)
        if grading in done:
            grading.result()
        assert listening in done, "the grader never bound its tag bus"
        stop = await attach(listening.result())
        return await asyncio.wait_for(grading, timeout=args.duration * 4 + 90)
    finally:
        if stop is not None:
            await stop()
        if not grading.done():
            grading.cancel()


async def _connect_cli(engine, monkeypatch, *options: tuple[str, str]):
    """`factoryforge_sidecar connect`, in process: the real CLI path a student
    runs, with the driver it creates captured so the test can ask what the
    stand-in PLC saw."""
    created = []
    real_create = drivers.create

    def capture(name, bus, **config):
        driver = real_create(name, bus, **config)
        created.append(driver)
        return driver

    monkeypatch.setattr(drivers, "create", capture)
    args = argparse.Namespace(
        driver="mock", option=[list(o) for o in options], mapping=None,
        host="127.0.0.1", port=engine.actual_port, timeout=15.0, duration=120.0)
    task = asyncio.create_task(cli.connect(args))

    async def stop():
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    stop.created = created
    return stop


# --- the window -------------------------------------------------------


async def test_a_driver_that_takes_three_seconds_to_connect_still_sees_the_first_start(
        monkeypatch, capsys):
    """The item's own test. A mock driver standing in for a PLC that takes 3 s
    to reach, through the real `connect` path. While it is "connecting" it
    drops sensor updates, as the OPC UA client drops them while disconnected.
    When it arrives it is handed the current inputs, which is what every real
    driver's bind does, and not the edges it missed. The examiner presses Start
    1.0 s into the window, for 0.15 s. If the window opened on the describe,
    that press would be over before the PLC was there (gotcha 24: checked by
    doing exactly that)."""
    stopper = {}

    async def attach(engine):
        stopper["stop"] = await _connect_cli(
            engine, monkeypatch, ("connect_delay", str(CONNECT_DELAY)))
        return stopper["stop"]

    report = await _grade_with(_args("start-stop-station", WINDOW), attach)
    window = report.evidence["window"]
    (driver,) = stopper["stop"].created

    # The point of all of it, first: the PLC saw the press.
    starts = [value for tag, value in driver.history if tag == "panel.start"]
    assert True in starts, (
        f"the stand-in PLC never saw Start pressed; it saw panel.start {starts} "
        f"(window: {window})")

    assert report.verdict != core.ERROR, report.headline
    assert window["opened_on"] == "controller ready", window
    assert window["driver"] == "mock"
    # Gotcha 16: it opened on the ready because the ready came late, not
    # because an instant one happened to arrive first.
    waited = window["controller_ready_at"] - window["controller_described_at"]
    assert waited >= CONNECT_DELAY * 0.9, window
    assert window["opened_at"] >= window["controller_ready_at"]
    assert window["plant_seconds_before"] == 0.0

    # The report says it opened on the driver, and does not apologise for it.
    assert not any("controller` report" in line or "without your driver" in line
                   for line in report.feedback), report.feedback
    core.print_summary(report, argparse.Namespace(duration=WINDOW, student=None))
    out = " ".join(capsys.readouterr().out.split())
    assert "its driver (mock) reported ready at" in out, out
    # And `connect` told the student, too, in the order it happened.
    assert out.index("driver not ready (mock)") < out.index("driver READY (mock)"), out


async def test_a_controller_that_never_says_ready_is_still_graded_and_the_report_says_why(
        capsys):
    """A hand-written client, or a sidecar from before IP-30, never sends a
    `controller` report. It is graded anyway: the window opens on the describe
    after `ready_wait`, on a plant that was frozen until then, so it gets the
    whole window. The report says which event opened it and why."""
    ready_wait = 2.0

    async def attach(engine):
        bus = TagBusClient(engine.url)          # no driver: never reports
        stop = asyncio.Event()
        runner = asyncio.create_task(bus.run(stop))
        await asyncio.wait_for(bus.connected.wait(), timeout=10)

        async def shutdown():
            stop.set()
            try:
                await asyncio.wait_for(runner, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                runner.cancel()
        return shutdown

    report = await _grade_with(_args("sorting-by-height", 3.0, ready_wait), attach)
    window = report.evidence["window"]

    # Graded -- a verdict about the program, not ERROR about the run.
    assert report.verdict in (core.PASS, core.FAIL), report.headline
    assert window["opened_on"] == "controller described; no ready within 2s", window
    assert window["controller_ready_at"] is None
    assert window["driver"] is None
    assert window["opened_at"] - window["controller_described_at"] >= ready_wait * 0.9
    assert window["plant_seconds_before"] == 0.0
    assert report.evidence["sim_seconds"] >= 3.0, "it was not graded on the whole window"
    assert any("never sent a `controller` report" in line
               for line in report.feedback), report.feedback

    core.print_summary(report, argparse.Namespace(duration=3.0, student=None))
    out = " ".join(capsys.readouterr().out.split())
    assert "Its sidecar did not say its driver was ready within 2s" in out, out


async def test_a_driver_that_never_connects_does_not_hang_the_exam(monkeypatch):
    """The bounded half. A sidecar that does report, but whose driver never
    gets there: the window opens after `ready_wait` anyway, and the report
    quotes what the driver last said rather than guessing."""
    async def attach(engine):
        return await _connect_cli(engine, monkeypatch, ("connect_delay", "600"))

    report = await _grade_with(_args("sorting-by-height", 2.0, ready_wait=2.0), attach)
    window = report.evidence["window"]

    assert report.verdict in (core.PASS, core.FAIL), report.headline
    assert window["opened_on"] == "controller described; no ready within 2s", window
    assert window["controller_ready_at"] is None
    reports = report.evidence["sessions"][0]["controller"]
    assert reports and reports[0]["ready"] is False, reports
    assert any("still saying it was not ready" in line and "simulated 600s delay" in line
               for line in report.feedback), report.feedback


# --- what each driver means by ready ------------------------------------


async def _told(engine, ready: bool, timeout: float = 10.0) -> dict:
    """Wait until the engine has been told `ready`, and return the report."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if engine.controller is not None and engine.controller["ready"] is ready:
            return engine.controller
        await asyncio.sleep(0.05)
    raise AssertionError(f"the engine was never told ready={ready}; "
                         f"it holds {engine.controller!r}")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_a_modbus_slave_is_ready_when_a_master_is_answered_not_when_it_listens(
        engine, bus):
    """A slave cannot reach anybody. Listening is where it starts, and the
    student's PLC may begin polling whenever its runtime starts, seconds later.
    So the engine hears "not ready" when the server binds, and "ready" only
    after a master has had a request answered."""
    driver = drivers.create("modbus-tcp", bus, host="127.0.0.1", port=0)
    await driver.start()
    try:
        told = await _told(engine, False)
        assert told["driver"] == "modbus-tcp"
        assert "no master has polled yet" in told["message"]

        # Connecting a socket is not a request answered.
        reader, writer = await asyncio.open_connection("127.0.0.1", driver.port)
        try:
            assert not driver.linked
            # Read coils: txn 1, protocol 0, length 6, unit 1, fc 1, addr 0, count 8.
            writer.write(struct.pack(">HHHBBHH", 1, 0, 6, 1, 1, 0, 8))
            await writer.drain()
            header = await asyncio.wait_for(reader.readexactly(7), timeout=5)
            _, _, length, _ = struct.unpack(">HHHB", header)
            pdu = await asyncio.wait_for(reader.readexactly(length - 1), timeout=5)
            assert pdu[0] == 1, f"the master's read was refused: {pdu!r}"

            told = await _told(engine, True)
            assert "a Modbus master reached" in told["message"]
        finally:
            writer.close()
    finally:
        await driver.stop()


async def test_an_opcua_server_is_ready_when_a_client_touches_a_tag(engine, bus):
    """Node-RED or a SCADA package connects to the OPC UA server driver when it
    connects, so readiness is an external client reading, writing or
    subscribing to one of the scene's tags. The driver's own writes, one per
    sensor change through the server's internal session, must not count."""
    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}/factoryforge/"
    driver = drivers.create("opcua-server", bus, endpoint=endpoint, publish_interval=20)
    await driver.start()
    try:
        await _told(engine, False)
        deadline = asyncio.get_running_loop().time() + 10
        while not driver._nodes and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert driver._nodes, "the server never published its tags"

        # Internal: the driver writing a sensor into its own address space.
        await driver.push({"sensor_low.detect": True})
        assert not driver.linked, "the driver's own write counted as a client"

        client = Client(url=endpoint)
        await client.connect()
        try:
            # A session alone is not it either: nothing has touched a tag yet.
            assert not driver.linked
            node = client.get_node(ua.NodeId("sensor_low.detect", driver.idx))
            await node.read_value()
            told = await _told(engine, True)
            assert "sensor_low.detect" in told["message"]
        finally:
            await client.disconnect()
    finally:
        await driver.stop()


async def test_an_opcua_client_is_ready_only_once_its_tags_are_bound_and_not_after_it_loses_the_plc(
        engine, bus, monkeypatch):
    """Connected is not ready: until the map is in, push() has no node to
    write a Start press to. So ready has to come after the bind, which this
    records at the moment the driver says it. And a PLC that goes away is
    reported, because a driver that loses its controller mid-exam is exactly
    what the report has to be able to say."""
    deadline = asyncio.get_running_loop().time() + 10
    while len(bus.table) == 0 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert len(bus.table), "the engine never described its scene"
    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}/fakeplc/"
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    idx = await server.register_namespace("urn:fakeplc")
    folder = await server.nodes.objects.add_folder(ua.NodeId("plc", idx), "PLC")
    mapping = {}
    for tag in bus.table:
        variant = {"bit": ua.VariantType.Boolean, "int": ua.VariantType.Int32,
                   "float": ua.VariantType.Float}[tag.type]
        node = await folder.add_variable(ua.NodeId(tag.id, idx), tag.id,
                                         bus.table.value(tag.id), variant)
        await node.set_writable()
        mapping[tag.id] = f"ns={idx};s={tag.id}"
    await server.start()

    driver = drivers.create("opcua-client", bus, url=endpoint, mapping=mapping,
                            publish_interval=20)
    bound_when_ready = []
    real_link = driver.link

    async def watched_link(reached, detail):
        if reached:
            bound_when_ready.append(len(driver._nodes))
        await real_link(reached, detail)

    monkeypatch.setattr(driver, "link", watched_link)
    stopped = False
    try:
        await driver.start()
        told = await _told(engine, True)
        assert bound_when_ready and bound_when_ready[0] == len(mapping), (
            f"ready was reported with {bound_when_ready} of {len(mapping)} tags bound")
        assert f"{len(mapping)} of {len(mapping)} tags bound" in told["message"]

        await server.stop()
        stopped = True
        told = await _told(engine, False, timeout=20)
        assert "not connected" in told["message"] or "lost" in told["message"], told
    finally:
        await driver.stop()
        if not stopped:
            await server.stop()


def test_the_mock_drivers_connect_delay_arrives_from_the_cli_as_a_number():
    """`-o connect_delay 3` is a string until the driver's own signature says
    otherwise (gotcha 19b)."""
    assert cli.coerce_options("mock", [["connect_delay", "3"]]) == {"connect_delay": 3.0}
