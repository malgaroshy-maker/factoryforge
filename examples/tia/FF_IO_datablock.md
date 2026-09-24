# Global DB `FF_IO` — the interface to the simulator

Create a **global data block** named `FF_IO` in TIA Portal with exactly these
members. Names matter: they become part of the OPC UA NodeId.

| Name | Data type | Direction | Meaning |
|---|---|---|---|
| `ConveyorRotate` | `Bool` | PLC → sim | Run the belt |
| `EmitterEmit` | `Bool` | PLC → sim | Rising edge spawns one box |
| `PusherExtend` | `Bool` | PLC → sim | Extend the pusher |
| `StackLightGreen` | `Bool` | PLC → sim | Green lamp |
| `SensorLow` | `Bool` | sim → PLC | Low diffuse sensor sees any box |
| `SensorHigh` | `Bool` | sim → PLC | High diffuse sensor sees a tall box only |
| `PusherExtended` | `Bool` | sim → PLC | Pusher fully out |
| `PusherRetracted` | `Bool` | sim → PLC | Pusher fully back |
| `CounterTall` | `DInt` | sim → PLC | Tall boxes diverted |
| `CounterShort` | `DInt` | sim → PLC | Short boxes passed |

`DInt` is Siemens' 32-bit signed integer and matches what the driver writes for
an `int` tag.

## Required DB settings

Right-click `FF_IO` → **Properties**:

1. **Attributes → uncheck "Optimized block access."**
   Not strictly required for OPC UA, but it keeps the NodeId strings stable and
   makes the DB reachable by snap7 later, which matters if you ever fall back
   from OPC UA.
2. **Attributes → check "Accessible from HMI/OPC UA"** and
   **"Writable from HMI/OPC UA"**. Without the writable flag the sidecar cannot
   push sensor values in, and every sensor will read as `false` forever.

## Enabling the OPC UA server

CPU **Properties → OPC UA → Server**:

1. Check **Activate OPC UA server**.
2. Note the endpoint — normally `opc.tcp://<cpu-ip>:4840`.
3. Under **Security**, allow **no security / anonymous** for a local test. The
   driver defaults to `NoSecurity`; tighten this if you ever expose it.
4. **Runtime licence:** set it to the smallest option. Unlicensed, the server
   runs a 100-variable trial — this DB has 10, so the trial is ample. The client
   side is always free.

## Finding the real NodeIds

The namespace index is usually 3 but is **not guaranteed**. Do not guess:

```
.\factoryforge-sidecar browse opc.tcp://192.168.0.1:4840
```

in the folder FactoryForge was extracted to (`./factoryforge-sidecar` on Linux).

It prints every variable with its exact NodeId string. Copy those into
`examples/opcua_mapping.json`.

Typical result:

```
ns=3;s="FF_IO"."ConveyorRotate"
```

Note the **quotes are part of the identifier** — they must survive into the JSON
file, which is why the mapping file escapes them as `\"`.
