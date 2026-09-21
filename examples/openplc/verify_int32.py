"""Ask a running OpenPLC what it made of a 32-bit counter.

A sorting run is a weak test of a 32-bit transport. The counters reach about a
hundred, so the high register of every Int tag is zero from start to finish and
the half of the wire format HP-49 added is never exercised. This forces each
counter to a value a carton count never reaches and reads the reassembled DINT
back out of OpenPLC itself.

OpenPLC is the real runtime throughout. This script replaces the *engine*, not
the PLC: it builds the same EngineStub + SortingScene + modbus-tcp stack that
`factoryforge_sidecar demo` builds, and pins the two counters with a tag-bus
`force`. What it reads back is `Sorting.st`'s own `%MD0` / `%MD1`, published by
OpenPLC's Modbus slave server as holding registers 2048..2051.

Run OpenPLC first, with its slave server started on port 5020:

    cd OpenPLC_v3/webserver/core && sudo ./openplc &
    python -c "import socket; s=socket.create_connection(('127.0.0.1',43628)); \
               s.sendall(b'start_modbus(5020)\\n'); print(s.recv(100))"
    python examples/openplc/verify_int32.py

Needs pymodbus (`pip install -e "sidecar[dev]"`). Exits non-zero on a mismatch.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sidecar"))
sys.path.insert(0, str(ROOT / "harness"))

from engine_stub import EngineStub                    # noqa: E402
from scene import SortingScene                        # noqa: E402
from factoryforge_sidecar import drivers              # noqa: E402
from factoryforge_sidecar.tagbus import TagBusClient  # noqa: E402

#: 0 and 9 are what a run produces. The rest are the ones that tell you whether
#: the high register is carried at all, and whether the conversion in the ST
#: reinterprets the bit pattern or clamps it.
CASES = [0, 9, 70_000, 1_000_000, 2_147_483_647, -1, -2_147_483_648]


def _as_signed(hi: int, lo: int) -> int:
    value = (hi << 16) | lo
    return value - (1 << 32) if value >= (1 << 31) else value


def _read_openplc(host: str, port: int):
    from pymodbus.client import ModbusTcpClient

    client = ModbusTcpClient(host, port=port)
    client.connect()
    try:
        md = client.read_holding_registers(address=2048, count=4, device_id=0)
        iw = client.read_input_registers(address=100, count=4, device_id=0)
    finally:
        client.close()
    if md.isError() or iw.isError():
        raise RuntimeError(f"OpenPLC read failed: {md} / {iw}")
    return md.registers, iw.registers


async def main(args) -> int:
    sim = SortingScene()
    engine = EngineStub(sim, host="127.0.0.1", port=args.bus_port, tick_ms=10)
    await engine.start()
    ticker = asyncio.create_task(engine._tick_loop())
    bus = TagBusClient(engine.url)
    stop = asyncio.Event()
    runner = asyncio.create_task(bus.run(stop))
    await asyncio.wait_for(bus.connected.wait(), timeout=5)

    driver = drivers.create("modbus-tcp", bus, port=args.modbus_port)
    await driver.start()
    # Long enough for OpenPLC's master to notice the slave and poll it once;
    # measured, not counted (AGENTS.md gotcha 2).
    await asyncio.sleep(1.0)

    failures = []
    print(f"{'forced':>14}  {'3x0..3x3 on the wire':<30} "
          f"{'%MD0 / %MD1 in OpenPLC':<28} verdict")
    for value in CASES:
        await bus.force({"counter.short": value, "counter.tall": value})
        await asyncio.sleep(args.settle)
        md, iw = _read_openplc(args.host, args.openplc_port)
        short, tall = _as_signed(md[0], md[1]), _as_signed(md[2], md[3])
        ok = short == value and tall == value
        if not ok:
            failures.append((value, short, tall, iw))
        print(f"{value:>14}  {str(iw):<30} "
              f"{f'{short} / {tall}':<28} {'ok' if ok else 'MISMATCH'}")

    await bus.force(clear=["counter.short", "counter.tall"])
    await driver.stop()
    stop.set()
    try:
        await asyncio.wait_for(runner, timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        runner.cancel()
    ticker.cancel()
    await engine.stop()

    print()
    if failures:
        print(f"{len(failures)} mismatch(es): {failures}")
        return 1
    print("every case round-tripped")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                        help="where OpenPLC's Modbus slave server is")
    parser.add_argument("--openplc-port", type=int, default=5020)
    parser.add_argument("--modbus-port", type=int, default=5502,
                        help="where this script serves the scene, i.e. what "
                             "mbconfig.cfg points OpenPLC's master at")
    parser.add_argument("--bus-port", type=int, default=7411)
    parser.add_argument("--settle", type=float, default=0.6,
                        help="seconds to let a forced value reach the PLC; "
                             "two polling periods plus a scan")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
