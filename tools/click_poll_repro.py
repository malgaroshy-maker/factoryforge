"""Count how many Start clicks a polling controller actually sees (IP-31).

    python tools/click_poll_repro.py                      # Modbus master, 50 ms poll
    python tools/click_poll_repro.py --poll-ms 100        # OpenPLC's default period
    python tools/click_poll_repro.py --via bus            # the engine -> sidecar hop alone
    python tools/click_poll_repro.py --via opcua          # opcua-server + a 50 ms subscription
    python tools/click_poll_repro.py --gui                # a real window, vsync and all
    python tools/click_poll_repro.py --time-scale 4       # fast-forward; the poller is not

Build the engine first (`cd engine && dotnet build`): a failed build leaves the
previous binary in place and Godot runs it without complaint (gotcha 14).

What it does. Starts the engine headless on a free bus port with
`--self-test=buttons --press-train=N`, which makes N Start clicks through the
real Run-mode dispatch (`SceneEditor.PressControlAtRay` -> the panel's press
queue -> `ButtonPanel.StepPart`) at irregular 0.55-0.95 s intervals, and prints
how many rising edges the engine itself raised on `panel.start`. Then it
watches the same tag from the far side of a real transport and counts the
rising edges that arrive:

* `--via modbus` (default): the sidecar's `connect --driver modbus-tcp`, and
  this script as the Modbus master -- the role OpenPLC plays -- reading the
  discrete inputs every `--poll-ms` on a wall-clock schedule.
* `--via opcua`: the sidecar's `connect --driver opcua-server`, and this script
  as an OPC UA client subscribed with a `--poll-ms` publishing interval, the
  way `examples/nodered/factoryforge-flow.json` subscribes.
* `--via bus`: this script as the sidecar, reading the engine's `update`
  frames raw. No driver and no poll; what it misses, the engine never sent.

The number that matters is `seen / clicks`. A click is counted only if the
engine raised an edge for it (it always should -- the engine's own count is
checked too), so a miss is a click the engine made and the controller never
saw.

Never touches a PLC, PLCSIM or Node-RED. Every process it starts is one it
stops; Godot's stdout goes to a file (gotcha 15).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
SIDECAR = ROOT / "sidecar"
sys.path.insert(0, str(ROOT))

from run import find_godot  # noqa: E402

START_TAG = "panel.start"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def wait_for(predicate, timeout: float, what: str, proc: subprocess.Popen | None = None) -> None:
    """Bounded wait on a condition, measured in real time (gotcha 2)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"{what}: process exited early with {proc.returncode}")
        time.sleep(0.1)
    raise RuntimeError(f"{what}: not within {timeout:g}s")


def rising_edges(samples: list[bool]) -> int:
    edges, prev = 0, False
    for value in samples:
        if value and not prev:
            edges += 1
        prev = value
    return edges


# --- Modbus master ----------------------------------------------------------

class ModbusPoller(threading.Thread):
    """FC02 reads on a fixed wall-clock schedule, the way a master's polling
    period works: the next read is due one period after the last one was due,
    not one period after it finished."""

    def __init__(self, port: int, address: int, count: int, period: float) -> None:
        super().__init__(daemon=True)
        self.port, self.address, self.count, self.period = port, address, count, period
        self.samples: list[bool] = []
        self.times: list[float] = []
        self.error: str | None = None
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                tid = 0
                due = time.perf_counter()
                while not self._halt.is_set():
                    tid = (tid + 1) & 0xFFFF
                    pdu = struct.pack(">BHH", 0x02, 0, self.count)
                    sock.sendall(struct.pack(">HHHB", tid, 0, len(pdu) + 1, 1) + pdu)
                    head = self._recv(sock, 7)
                    _, _, length, _ = struct.unpack(">HHHB", head)
                    body = self._recv(sock, length - 1)
                    if body[0] != 0x02:
                        raise RuntimeError(f"Modbus exception response {body.hex()}")
                    data = body[2:]
                    bit = (data[self.address // 8] >> (self.address % 8)) & 1
                    self.samples.append(bool(bit))
                    self.times.append(time.perf_counter())
                    due += self.period
                    delay = due - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
                    else:
                        due = time.perf_counter()   # fell behind; do not burst
        except Exception as exc:   # reported, not swallowed
            self.error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _recv(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Modbus server closed the connection")
            buf += chunk
        return buf


# --- OPC UA subscriber --------------------------------------------------------

class OpcUaWatcher(threading.Thread):
    def __init__(self, url: str, node_id: str, period_ms: int) -> None:
        super().__init__(daemon=True)
        self.url, self.node_id, self.period_ms = url, node_id, period_ms
        self.samples: list[bool] = []
        self.times: list[float] = []
        self.error: str | None = None
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    async def _main(self) -> None:
        from asyncua import Client

        watcher = self

        class Handler:
            def datachange_notification(self, node, val, data) -> None:
                watcher.samples.append(bool(val))
                watcher.times.append(time.perf_counter())

        client = Client(self.url, timeout=10)
        await client.connect()
        try:
            sub = await client.create_subscription(self.period_ms, Handler())
            await sub.subscribe_data_change(client.get_node(self.node_id))
            while not self._halt.is_set():
                await asyncio.sleep(0.1)
            await sub.delete()
        finally:
            await client.disconnect()


# --- raw tag-bus reader -------------------------------------------------------

class BusWatcher(threading.Thread):
    """Stands in for the sidecar: every `update` frame the engine sends, raw,
    with no coalescing and no driver in the way."""

    def __init__(self, port: int) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.samples: list[bool] = []
        self.times: list[float] = []
        self.error: str | None = None
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    async def _main(self) -> None:
        import websockets

        async with websockets.connect(f"ws://127.0.0.1:{self.port}/tagbus") as ws:
            while not self._halt.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    return   # the engine finished its train and quit
                msg = json.loads(raw)
                if msg.get("t") == "update" and START_TAG in msg.get("values", {}):
                    self.samples.append(bool(msg["values"][START_TAG]))
                    self.times.append(time.perf_counter())


# --- the run ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--clicks", type=int, default=50)
    parser.add_argument("--poll-ms", type=int, default=50,
                        help="Modbus polling period / OPC UA publishing interval")
    parser.add_argument("--via", choices=("modbus", "opcua", "bus"), default="modbus")
    parser.add_argument("--gui", action="store_true",
                        help="run the engine with a window (vsync-paced frames) instead of headless")
    parser.add_argument("--time-scale", type=float, default=1.0,
                        help="simulation rate (the toolbar's 0.25x-4x); pollers stay on wall clock")
    parser.add_argument("--second-after", type=float, default=None, metavar="SECONDS",
                        help="click twice: again this long after each click. Inside the "
                             "panel's hold that is one press; outside it, two")
    parser.add_argument("--seed", type=int, default=31)
    parser.add_argument("--keep-logs", action="store_true")
    args = parser.parse_args()

    godot = find_godot()
    if godot is None:
        print("Godot not found. Set $GODOT to the .NET build's executable.", file=sys.stderr)
        return 2

    logs = Path(tempfile.mkdtemp(prefix="ff_ip31_"))
    bus_port = free_port()
    # Generous: the train takes clicks * 0.75 s on average, plus two settles.
    bound = int(args.clicks * (1.0 if args.second_after is None else 1.5) + 40)

    engine_log = logs / "engine.log"
    sidecar_log = logs / "sidecar.log"
    engine_cmd = [godot] + ([] if args.gui else ["--headless"]) + [
        "--path", str(ENGINE), "--",
        "--self-test=buttons", f"--press-train={args.clicks}", f"--press-seed={args.seed}",
        f"--bus-port={bus_port}", f"--duration={bound}",
    ] + ([f"--time-scale={args.time_scale:g}"] if args.time_scale != 1.0 else []) + (
        # Wider gaps, so a second click never lands in the next pair's hold.
        [f"--press-second={args.second_after:g}", "--press-gap=1.0:1.4"]
        if args.second_after is not None else [])

    procs: list[subprocess.Popen] = []
    watcher = None
    handles = []
    try:
        eh = open(engine_log, "w", encoding="utf-8")
        handles.append(eh)
        engine = subprocess.Popen(engine_cmd, stdout=eh, stderr=subprocess.STDOUT)
        procs.append(engine)
        wait_for(lambda: f"bus on {bus_port}" in read_text(engine_log), 90, "engine bus", engine)

        if args.via == "bus":
            watcher = BusWatcher(bus_port)
            watcher.start()
        else:
            driver = "modbus-tcp" if args.via == "modbus" else "opcua-server"
            driver_port = free_port()
            opts = ["-o", "port", str(driver_port)] if args.via == "modbus" else [
                "-o", "endpoint", f"opc.tcp://127.0.0.1:{driver_port}/"]
            sh = open(sidecar_log, "w", encoding="utf-8")
            handles.append(sh)
            sidecar = subprocess.Popen(
                [sys.executable, "-m", "factoryforge_sidecar", "connect", "--driver", driver,
                 "--port", str(bus_port), "--duration", str(bound)] + opts,
                cwd=SIDECAR, stdout=sh, stderr=subprocess.STDOUT)
            procs.append(sidecar)

            if args.via == "modbus":
                pattern = re.compile(r"1x(\d+)\s+bit\s+\d+\s+" + re.escape(START_TAG) + r"\s*$", re.M)
                wait_for(lambda: pattern.search(read_text(sidecar_log)), 30, "Modbus map", sidecar)
                address = int(pattern.search(read_text(sidecar_log)).group(1))
                count = max(int(m) for m in re.findall(r"1x(\d+)\s+bit", read_text(sidecar_log))) + 1
                watcher = ModbusPoller(driver_port, address, count, args.poll_ms / 1000.0)
                print(f"Modbus master polling 1x0..1x{count - 1} every {args.poll_ms} ms; "
                      f"{START_TAG} is 1x{address}")
            else:
                wait_for(lambda: "OPC UA server:" in read_text(sidecar_log), 30, "OPC UA server", sidecar)
                watcher = OpcUaWatcher(f"opc.tcp://127.0.0.1:{driver_port}/",
                                       f"ns=2;s={START_TAG}", args.poll_ms)
                print(f"OPC UA client subscribed to {START_TAG} at {args.poll_ms} ms")
            watcher.start()

        wait_for(lambda: engine.poll() is not None, bound + 30, "press train", engine)
        time.sleep(0.5)
        watcher.stop()
        watcher.join(timeout=10)
    except RuntimeError as exc:
        print(f"ERROR {exc}\n  logs: {logs}", file=sys.stderr)
        return 2
    finally:
        for proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        for handle in handles:
            handle.close()

    text = read_text(engine_log)
    summary = re.search(r"press-train: presses=(\d+) expected=(\d+) edges=(\d+) hold_s=(\S+) "
                        r"high_ticks=(\S+) physics_hz=(\d+)", text)
    if summary is None:
        print(f"ERROR the engine printed no press-train summary\n  logs: {logs}", file=sys.stderr)
        print(text[-2000:], file=sys.stderr)
        return 2
    presses, expected, engine_edges = (int(summary.group(i)) for i in (1, 2, 3))

    if watcher.error:
        print(f"ERROR the watcher failed: {watcher.error}\n  logs: {logs}", file=sys.stderr)
        return 2

    seen = rising_edges(watcher.samples)
    missed = max(engine_edges - seen, 0)
    print(f"engine:  {presses} clicks, {engine_edges} edges on {START_TAG} (expected {expected}), "
          f"hold {summary.group(4)} s = {summary.group(5)} physics ticks at {summary.group(6)} Hz "
          f"({'headless' if not args.gui else 'windowed'})")
    if len(watcher.times) > 1 and args.via == "modbus":
        gaps = [(b - a) * 1000 for a, b in zip(watcher.times, watcher.times[1:])]
        print(f"poller:  {len(watcher.samples)} reads, interval mean {statistics.mean(gaps):.1f} ms, "
              f"max {max(gaps):.1f} ms")
        runs, run = [], 0
        for value in watcher.samples:
            if value:
                run += 1
            elif run:
                runs.append(run)
                run = 0
        if runs:
            print(f"         each press read high on {min(runs)}..{max(runs)} consecutive polls")
    else:
        print(f"watcher: {len(watcher.samples)} {START_TAG} values received")
    print(f"RESULT via={args.via} poll_ms={args.poll_ms} time_scale={args.time_scale:g} clicks={engine_edges} seen={seen} "
          f"missed={missed} miss_rate={missed / max(engine_edges, 1):.0%}")
    if seen > engine_edges:
        print(f"  more edges seen than made: a low gap was too short to be seen as low, "
              f"or a stale value was read")

    if args.keep_logs:
        print(f"  logs: {logs}")
    else:
        for path in logs.iterdir():
            path.unlink()
        logs.rmdir()
    return 0 if (engine_edges == expected and seen == engine_edges) else 1


if __name__ == "__main__":
    raise SystemExit(main())
