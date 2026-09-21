"""FactoryForge sidecar CLI.

    factoryforge-sidecar connect [--driver ...] [options]  drive a RUNNING engine
    factoryforge-sidecar demo    [--driver ...] [options]  run a scene + driver
    factoryforge-sidecar browse  <url>                     dump an OPC UA address space

`connect` is the one to use with the 3D engine. `demo` starts its *own* Python
scene on the bus port, which is right for a headless check with no Godot in the
picture and wrong for everything else: with the engine already running, `demo`
finds the port taken, and if it did bind it would drive the Python scene while
you watched the 3D one sit still. `connect` attaches to an engine that is
already listening and drives that.

`browse` exists because the hardest part of connecting to a real PLC is finding
the right NodeIds. An S7-1500 exposes tags as `ns=3;s="DB"."Member"`, and the
namespace index is not guaranteed to be 3. Browse first, then write the mapping.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# The harness lives outside the installed package; add it when running from a checkout.
_HARNESS = Path(__file__).resolve().parent.parent.parent / "harness"
if _HARNESS.is_dir() and str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))


# --- browse ---

async def browse(url: str, max_depth: int, timeout: float = 10.0) -> int:
    try:
        from asyncua import Client, ua
    except ImportError:
        print("asyncua is not installed. pip install 'factoryforge-sidecar[opcua]'")
        return 1

    print(f"connecting to {url} ...")
    client = Client(url=url, timeout=timeout)
    try:
        await client.connect()
    except Exception as exc:
        # asyncua raises plenty of exceptions whose str() is empty, so print the
        # type too -- "could not connect: " on its own is useless.
        detail = f"{type(exc).__name__}: {exc}".rstrip(": ")
        print(f"could not connect: {detail}")
        print("\nthings to check:")
        print("  - CPU in RUN and the OPC UA server activated")
        print("  - Windows Firewall allowing TCP 4840")
        print(f"  - a slow server may need longer: --timeout {timeout * 2:.0f}")
        return 1

    try:
        ns = await client.get_namespace_array()
        print("\nnamespaces:")
        for i, uri in enumerate(ns):
            print(f"  ns={i}  {uri}")
        print("\naddress space (variables only):\n")
        await _walk(client, client.nodes.objects, 0, max_depth, ua)
        print("\nUse the ns=...;s=... strings above in your mapping file.")
    finally:
        await client.disconnect()
    return 0


async def _walk(client, node, depth: int, max_depth: int, ua) -> None:
    if depth > max_depth:
        return
    try:
        children = await node.get_children()
    except Exception:
        return
    for child in children:
        try:
            name = (await child.read_browse_name()).Name
            node_class = await child.read_node_class()
        except Exception:
            continue
        # Skip the standard server tree; it is noise when hunting for PLC tags.
        if depth == 0 and name in ("Server", "Aliases", "Types", "Views"):
            continue
        indent = "  " * depth
        if node_class == ua.NodeClass.Variable:
            try:
                value = await child.read_value()
            except Exception:
                value = "?"
            print(f"{indent}{name:<28} {child.nodeid.to_string():<40} = {value!r}")
        else:
            print(f"{indent}{name}/")
            await _walk(client, child, depth + 1, max_depth, ua)


# --- demo ---

async def demo(args) -> int:
    from engine_stub import EngineStub          # type: ignore
    from scene import SortingScene              # type: ignore

    from . import drivers
    from .tagbus import TagBusClient

    sim = SortingScene()
    engine = EngineStub(sim, host=args.host, port=args.port, tick_ms=args.tick)
    await engine.start()
    print(f"engine listening on {engine.url}", flush=True)

    ticker = asyncio.create_task(engine._tick_loop())
    bus = TagBusClient(engine.url)
    runner = asyncio.create_task(bus.run())
    await asyncio.wait_for(bus.connected.wait(), timeout=5)

    config = dict(args.option or [])
    if args.mapping:
        config["mapping_file"] = args.mapping
    driver = drivers.create(args.driver, bus, **config)
    await driver.start()
    print(f"driver '{args.driver}' started: {config or 'defaults'}", flush=True)

    if args.driver == "modbus-tcp":
        await asyncio.sleep(0.5)
        print("\nModbus address map:\n" + driver.address_table())
    elif args.driver == "opcua-server":
        print(f"\nOPC UA server: {driver.endpoint}")
        print("connect Node-RED / UaExpert / Ignition here; node ids are ns=2;s=<tag id>")

    print("\nCtrl-C to stop.\n")
    printer = asyncio.create_task(_print_loop(sim, engine))
    try:
        # --duration was accepted by the subparser and never read, so the flag
        # AGENTS.md gotcha 6 tells you to use -- "use `demo --duration N`,
        # which shuts down cleanly" -- did not shut down at all. Same shape as
        # the `connect` path, which has always honoured it.
        if args.duration:
            await asyncio.sleep(args.duration)
        else:
            await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for task in (printer, runner, ticker):
            task.cancel()
        await driver.stop()
        await engine.stop()
    return 0


async def connect(args) -> int:
    """Attach a driver to an engine that is already running.

    Everything here is the same driver stack `demo` uses; the only difference is
    who owns the scene. That is the point — the drivers were always independent
    of which engine is on the other end of the bus, but until now the CLI gave
    you no way to say so, and the 3D engine could only ever be driven by the
    bespoke script in tools/.
    """
    from . import drivers
    from .tagbus import TagBusClient

    url = f"ws://{args.host}:{args.port}/tagbus"
    bus = TagBusClient(url)
    # Shut the client down by asking it to stop rather than cancelling it
    # mid-recv: a hard cancel leaves websockets' pending recv holding a
    # ConnectionClosedOK nobody retrieves, and asyncio prints it as an unhandled
    # task exception — so a perfectly clean run ends in a traceback.
    stop = asyncio.Event()
    runner = asyncio.create_task(bus.run(stop))

    try:
        await asyncio.wait_for(bus.connected.wait(), timeout=args.timeout)
    except asyncio.TimeoutError:
        runner.cancel()
        print(f"no engine listening on {url} after {args.timeout:g}s.\n"
              f"Start it first:  godot --path engine/", file=sys.stderr)
        return 1

    # The hello arrives before the scene description, so `connected` alone means
    # only that the socket is up. Waiting for the tag table avoids reporting
    # "scene None, 0 tags" a beat before the real answer turns up, and means the
    # driver starts against a table that already has something in it.
    for _ in range(int(args.timeout / 0.05)):
        if bus.scene is not None and len(bus.table) > 0:
            break
        await asyncio.sleep(0.05)

    config = dict(args.option or [])
    if args.mapping:
        config["mapping_file"] = args.mapping
    driver = drivers.create(args.driver, bus, **config)
    await driver.start()

    print(f"connected to scene '{bus.scene}' — {len(bus.table)} tags", flush=True)
    print(f"driver '{args.driver}' started: {config or 'defaults'}", flush=True)

    if args.driver == "modbus-tcp":
        await asyncio.sleep(0.5)
        print("\nModbus address map:\n" + driver.address_table(), flush=True)
    elif args.driver == "opcua-server":
        print(f"\nOPC UA server: {driver.endpoint}", flush=True)
        print("connect Node-RED / UaExpert / Ignition here; node ids are ns=2;s=<tag id>",
              flush=True)

    print("\nCtrl-C to stop.\n", flush=True)
    printer = asyncio.create_task(_bus_print_loop(bus))
    try:
        if args.duration:
            await asyncio.sleep(args.duration)
        else:
            await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        printer.cancel()
        await driver.stop()
        stop.set()
        try:
            await asyncio.wait_for(runner, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            runner.cancel()
    return 0


async def _bus_print_loop(bus) -> None:
    """Live status for a scene we do not own, so it reports what the bus shows
    rather than reaching into a local simulation object."""
    last = None
    while True:
        await asyncio.sleep(0.25)
        outputs = " ".join(
            f"{t.id.split('.')[-1]}={_short(bus.table.visible(t.id))}"
            for t in bus.table.by_kind("output")
        )
        inputs = " ".join(
            f"{t.id.split('.')[-1]}={_short(bus.table.visible(t.id))}"
            for t in bus.table.by_kind("input")
        )
        line = f"OUT {outputs} | IN {inputs}"
        if line != last:
            print(line, flush=True)
            last = line


def _short(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


async def _print_loop(sim, engine) -> None:
    """Live one-line status. Sleeps well above Windows' 15.6ms timer floor."""
    last = None
    while True:
        await asyncio.sleep(0.25)
        outputs = " ".join(
            f"{t.id.split('.')[-1]}={'1' if sim.tags.visible(t.id) else '0'}"
            for t in sim.tags.by_kind("output")
        )
        line = (f"t={engine.tick_count:<7} belt={len(sim.boxes)} "
                f"tall={len(sim.sorted_tall)} short={len(sim.sorted_short)} | {outputs}")
        if line != last:
            # flush: stdout is block-buffered when piped or redirected, and a
            # live status display that only appears on exit is useless.
            print(line, flush=True)
            last = line


# --- entry point ---

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factoryforge-sidecar")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("drivers", help="list the protocol drivers this build can actually run")

    p_browse = sub.add_parser("browse", help="dump an OPC UA server's address space")
    p_browse.add_argument("url", help="e.g. opc.tcp://192.168.0.1:4840")
    p_browse.add_argument("--depth", type=int, default=4)
    p_browse.add_argument("--timeout", type=float, default=10.0,
                          help="seconds; a real S7-1500 needs more than asyncua's default 4")

    p_connect = sub.add_parser(
        "connect", help="drive an ALREADY RUNNING engine (use this with the 3D engine)")
    p_connect.add_argument("--driver", default="opcua-client",
                           help="mock | modbus-tcp | opcua-client | opcua-server | "
                                "plcsim-advanced | s7-snap7")
    p_connect.add_argument("--mapping", help="JSON tag->NodeId map (opcua-client)")
    p_connect.add_argument("--host", default="127.0.0.1", help="engine's tag bus host")
    p_connect.add_argument("--port", type=int, default=7411, help="engine's tag bus port")
    p_connect.add_argument("--duration", type=float,
                           help="stop after N seconds; without this it runs until Ctrl-C")
    p_connect.add_argument("--timeout", type=float, default=15.0,
                           help="seconds to wait for the engine")
    p_connect.add_argument("-o", "--option", nargs=2, action="append",
                           metavar=("KEY", "VALUE"),
                           help="driver option, e.g. -o url opc.tcp://...")

    p_demo = sub.add_parser("demo", help="run the built-in Python scene with a driver")
    p_demo.add_argument("--driver", default="opcua-server",
                        help="mock | modbus-tcp | opcua-client | opcua-server | plcsim-advanced | s7-snap7")
    p_demo.add_argument("--mapping", help="JSON tag->NodeId map (opcua-client)")
    p_demo.add_argument("--host", default="127.0.0.1")
    p_demo.add_argument("--port", type=int, default=7411)
    p_demo.add_argument("--tick", type=int, default=10, help="ms per simulation step")
    p_demo.add_argument("--duration", type=float,
                        help="stop after N seconds and shut down cleanly; "
                             "without this it runs until Ctrl-C")
    p_demo.add_argument("-o", "--option", nargs=2, action="append",
                        metavar=("KEY", "VALUE"),
                        help="driver option, e.g. -o url opc.tcp://...")

    args = parser.parse_args(argv)

    if args.command == "drivers":
        # Registered is not the same as usable: each protocol driver guards its
        # own third-party import and registers either way, so it can explain
        # itself at connect time. A frozen release makes the difference matter --
        # it bundles what was importable when it was built.
        from . import drivers as driver_registry

        report = driver_registry.usable()
        for name, ok in sorted(report.items()):
            print(f"{'OK     ' if ok else 'MISSING'} {name}")
        missing = [name for name, ok in report.items() if not ok]
        if missing:
            print()
            print(f"{len(missing)} driver(s) present but not usable: " + ", ".join(sorted(missing)))
            print("Install the matching extra: pip install -e \"sidecar[opcua,siemens,plcsim]\"")
        return 0
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    # asyncua is extremely chatty at INFO.
    logging.getLogger("asyncua").setLevel(logging.WARNING)

    try:
        if args.command == "browse":
            return asyncio.run(browse(args.url, args.depth, args.timeout))
        if args.command == "connect":
            return asyncio.run(connect(args))
        return asyncio.run(demo(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
