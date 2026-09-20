# Security

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting on
this repository (*Security → Report a vulnerability*), or contact the repository
owner, [@malgaroshy-maker](https://github.com/malgaroshy-maker), privately via
GitHub.

One maintainer, working alone: expect an acknowledgement rather than a same-day
fix. If you do not hear anything within two weeks, ping the same channel again
before going public.

## What is in scope

There is no server, no account system and no hosted component. The attack
surface is entirely local listeners and the files the app reads:

* the **tag bus** WebSocket server in the engine
* the **Python sidecar** and its protocol drivers
* the **Modbus TCP server** the Modbus driver stands up
* the **OPC UA server** the `opcua-server` driver stands up
* **scene files**, which are JSON the editor parses and will happily be shared
  between students

Out of scope: the third-party PLC or SCADA package on the other end of a driver,
and Godot and .NET themselves.

## What binds where, and what the defaults are

This is a teaching tool. Everything it listens on is meant to be reachable only
from the machine running it, and mostly is.

| Listener | Binds | State |
|---|---|---|
| Tag bus (engine) | `127.0.0.1:7411` | Loopback, deliberately. `engine/src/TagBus/TagBusServer.cs` |
| OPC UA server driver | `opc.tcp://127.0.0.1:4841/` | Loopback, deliberately. `drivers/opcua_server.py` |
| **Modbus TCP driver** | **`0.0.0.0:502`** | **A bug, being fixed — see below** |

### The Modbus default is wrong, and we know

`ModbusTcpServer` itself defaults to `127.0.0.1`. The **driver** overrides that
with `0.0.0.0` (`sidecar/factoryforge_sidecar/drivers/modbus_tcp.py`), and the
driver's default is the one that ships. So a user who picks the Modbus driver and
connects gets a **writable simulator with no authentication, reachable by anyone
on the same subnet** — which on a classroom or lab network is a real network,
not a hypothetical one.

Two things make it narrower than it sounds, and neither of them makes it
acceptable:

* Nothing listens until a user chooses Modbus and presses *Apply & Connect*. The
  driver dialog defaults to `plcsim-advanced`
  (`engine/src/Editor/DriverConnectionUI.cs`), and until then no sidecar is
  running at all.
* There is nothing of value behind it. Writing to it moves boxes in a simulated
  factory.

The fix is [HP-22](docs/HARDENING_PLAN.md) in the hardening plan: default to
`127.0.0.1`, make binding wider an explicit choice, and harden the hand-written
server while we are in there — length-validate frames, put a deadline on reads,
cap connections, and have `stop()` close established clients rather than only the
listener.

**Until that lands**, if you use the Modbus driver on a network you do not
control, pass an explicit host: `-o host 127.0.0.1`.

### The OPC UA server has no security policy

`opcua_server.py` selects `NoSecurity` unconditionally — anonymous and
unencrypted. That is defensible for a loopback endpoint and it is what the code
comment says, but the same comment implies certificates "can be configured", and
they cannot: nothing reads a certificate option. Either making it true or
removing the implication is HP-29.

## Other known issues with a security flavour

All of these are open items in [`docs/HARDENING_PLAN.md`](docs/HARDENING_PLAN.md)
rather than secrets:

* **A scene file is parsed without validation and destroys the open scene first.**
  `LoadSceneFromFile` clears the current scene *before* it has read, parsed or
  validated the new one, and malformed JSON throws rather than being rejected.
  Opening a scene somebody sent you can lose the scene you had. HP-02, HP-07.
* **Non-finite analog values.** `NaN` and infinity can be forced through the UI
  and coerced into a float tag, and can be decoded out of Modbus registers into a
  JSON payload the C# parser then rejects. HP-23.
* **Modbus frames are not length-validated.** An empty PDU indexes off the end;
  reads have no deadline, so a half-open client withholding bytes pins a
  coroutine; `stop()` leaves established sessions open. HP-22.

## Things that are not vulnerabilities

* **Releases are not code-signed**, so Windows SmartScreen warns on first run.
  That is a deliberate, documented choice, not a compromise — see
  [`docs/PACKAGING.md`](docs/PACKAGING.md#code-signing--not-signed-and-the-download-page-says-so).
  (There are also no published releases yet, so there is currently nothing to
  warn about.)
* **Forced tags override the PLC.** That is the feature. A forced tag is a value
  deliberately disagreeing with the simulation, which is how you test what your
  program does with a stuck sensor.
* **The driver writes to a PLC you point it at.** That is also the feature. Point
  it at a controller you own; `AGENTS.md` records what happened the one time a
  driver took more ownership of a CPU than it should have.

## Supported versions

There are no releases yet, so there is no version to support. `master` is the
only thing that exists. When the first tag lands (HP-09), this section gets a
real answer.
