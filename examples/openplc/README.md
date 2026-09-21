# OpenPLC — sorting by height

The same scene as [`../tia/`](../tia/), driven by an ordinary IEC 61131-3
program on the OpenPLC Runtime instead of an S7-1500. No Siemens software, no
licence, and nothing FactoryForge-specific in the program itself.

| File | What it is |
|---|---|
| `Sorting.st` | The program. Structured Text, located variables, one `CONFIGURATION`. Paste it into the OpenPLC web UI or hand it to `compile_program.sh`. |
| `mbconfig.cfg` | The Slave Device entry that points OpenPLC's Modbus master at the sidecar. The web UI writes this file for you; it is here so you can skip the web UI. |
| `verify_int32.py` | Forces the two 32-bit counters to values a carton count never reaches and asks the running OpenPLC what it decoded. A sorting run only ever exercises the low register. |

**The walkthrough is [`docs/OPENPLC.md`](../../docs/OPENPLC.md)** — install,
addressing, timing, troubleshooting, and what the run did and did not prove.

Verified on 21 Sep 2026 against OpenPLC_v3 `b5d4135`, built from source on
Ubuntu 26.04 under WSL2: **103 tall / 103 short** over a 630 s run, a perfect
split.

The short version:

```bash
# terminal 1 — the simulator, as Modbus slave on loopback
python -m factoryforge_sidecar demo --driver modbus-tcp -o port 5502

# terminal 2 — compile and run the PLC
cp Sorting.st  OpenPLC_v3/webserver/st_files/
cp mbconfig.cfg OpenPLC_v3/webserver/core/
cd OpenPLC_v3/webserver && ./scripts/compile_program.sh Sorting.st
cd core && sudo ./openplc
```

Two things that will cost you an hour if nobody tells you:

- **A FactoryForge Int tag is two Modbus registers**, 32-bit signed big-endian,
  high word first. `counter.short` is `3x0` *and* `3x1`. That is why the
  program declares `ShortHi` and `ShortLo` and puts them back together, and why
  `mbconfig.cfg` reads four input registers for two counters.
- **The Modbus driver binds `127.0.0.1` unless you tell it otherwise.** If
  OpenPLC is in a VM, a container or WSL and the simulator is on the host, they
  are not on the same loopback: add `-o host 0.0.0.0`, and understand that
  Modbus has no authentication before you do.
