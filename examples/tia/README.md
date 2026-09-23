# Driving FactoryForge from TIA Portal + PLCSIM Advanced

End-to-end walkthrough: a ladder-equivalent SCL program running on a simulated
S7-1500 sorts boxes by height in the FactoryForge simulation, over OPC UA.

**You need S7-PLCSIM Advanced**, not the plain PLCSIM bundled with TIA Portal.
Only Advanced provides a virtual Ethernet adapter, and without it nothing
outside TIA Portal can reach the CPU.

**Starters for the other scenes.** Every graded scene has a folder here --
`batch-dosing/`, `guarded-cell/`, ... -- with an SCL source (a global DB
`FF_IO` holding every signal, and an empty FB) and the mapping file for each
Siemens driver: `opcua_mapping.json`, `snap7_mapping.json`,
`plcsim_mapping.json`. They are generated; the index is
[`../README.md`](../README.md). The walkthrough below uses the sorting scene's
own `FF_IO` and applies to all of them.

---

## 0. Sanity-check first, without TIA Portal

`examples/fake_plc.py` is a Python stand-in for the SCL program that exposes the
same `FF_IO` address space a real S7-1500 would. Run it first to confirm the
driver, the mapping file, and the timings all work — then you know any later
failure is TIA-side.

```bash
# terminal 1
python examples/fake_plc.py

# terminal 2
python -m factoryforge_sidecar demo \
    --driver opcua-client --mapping examples/opcua_mapping.json
```

Expect roughly one box every 3 s, split evenly:

```
t=4451    belt=2 tall=6 short=7 | rotate=1 emit=0 extend=0 green=1
```

## 1. TIA Portal

1. New project, add an **S7-1500** CPU (e.g. CPU 1516-3 PN/DP).
2. Create the global DB **`FF_IO`** exactly as described in
   [`FF_IO_datablock.md`](FF_IO_datablock.md) — including the two block
   properties, which are easy to miss and cause silent failure.
3. Add a function block **`Sorting`**, set its language to **SCL**, and paste
   [`Sorting.scl`](Sorting.scl). Its `VAR` and `VAR CONSTANT` blocks declare
   everything it needs.
4. In **OB1**, call `Sorting` with a new instance DB.
5. Enable the **OPC UA server** on the CPU (see `FF_IO_datablock.md`).
6. Compile.

## 2. PLCSIM Advanced

1. Start S7-PLCSIM Advanced.
2. Choose **PLCSIM Virtual Eth. Adapter** (not "PLCSIM"), which is what exposes
   the CPU to other software.
3. Give it an IP on your subnet, e.g. `192.168.0.10`. Note it down.
4. **Start** the virtual CPU.
5. Back in TIA Portal, download the project to it, then put the CPU in **RUN**.

## 3. Find the NodeIds

The namespace index is usually 3 but is not guaranteed. Check, don't guess:

```bash
python -m factoryforge_sidecar browse opc.tcp://192.168.0.10:4840
```

You should see your ten `FF_IO` variables. Copy the exact NodeId strings into
`examples/opcua_mapping.json` if they differ from the defaults.

If this fails to connect, the problem is almost always one of: the OPC UA server
not activated, the CPU not in RUN, or Windows Firewall blocking TCP 4840.

## 4. Run the simulation

```bash
python -m factoryforge_sidecar demo \
    --driver opcua-client \
    --mapping examples/opcua_mapping.json \
    -o url opc.tcp://192.168.0.10:4840
```

You should see boxes emitted every 3 s, the counters climbing, and roughly half
the boxes diverted:

```
t=1240    belt=2 tall=3 short=3 | rotate=1 emit=0 extend=0 green=1
```

## What "working" looks like

- `tall` and `short` both climb, roughly evenly (the default emit pattern
  alternates short/tall)
- `belt` stays low — boxes are leaving, not piling up
- `extend` blips to 1 shortly after each tall box passes the high sensor

## Troubleshooting

| Symptom | Cause |
|---|---|
| `no OPC UA node mapped for: ...` | Mapping ids don't match. Re-run `browse` and copy exactly, quotes included. |
| Connects, but every sensor stays `false` | DB is not **Writable from HMI/OPC UA**. This is the most common mistake. |
| `BadUserAccessDenied` on write | Same as above, or the DB has "Optimized block access" on with a restrictive setting. |
| Nothing moves, `rotate=0` | CPU is not in RUN, or OB1 never calls `Sorting`. |
| `tall` climbs but `short` stays 0 | Pusher is firing on every box. Check `SensorHigh` is mapped to the *high* sensor, not the low one. |
| Boxes pile up, nothing diverted | `PUSH_DELAY` is wrong for your timings, or `PusherExtend` isn't mapped. |
| Trial/licence errors from the CPU | The OPC UA server runtime licence. The unlicensed trial allows 100 variables; this DB uses 10, so it should not bite. |
| **Far fewer boxes appear than the emit interval implies** | OPC UA subscriptions *sample* — they do not catch every transition, and **an S7-1500 forces a 1000 ms publishing interval no matter what you request**. Anything held for less than a second can vanish. The driver therefore polls by default (`mode="poll"`), and `Sorting.scl` holds the emit level for 1.5 s. If you write your own logic, hold anything the simulator must see for well over a second, or rely on polling. |
| Boxes emitted but nothing is ever diverted | The pusher pulse is too short to survive the transport. Use `--driver opcua-client` with default polling (not `-o mode subscribe`), and consider raising `PUSH_HOLD` to `T#1S500MS`. |
| Occasional tall box slips through | Timing margin. The catch window is only 0.6 s wide. Raise `PUSH_HOLD`, or lower `BELT_SPEED` in `sidecar/factoryforge_sidecar/sorting_scene.py` (the Python `demo` scene; `harness/scene.py` is now only an alias of it, so editing that file changes nothing). |

## No PLCSIM Advanced licence?

Everything above also works with **OpenPLC over Modbus**, which is free:

```bash
python -m factoryforge_sidecar demo --driver modbus-tcp -o port 5020
```

The demo prints the Modbus address map on startup. Point OpenPLC at it as a
Modbus master. The logic is the same; only the addressing differs.
