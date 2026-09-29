# Graded on a real controller

Reports from `factoryforge-sidecar grade` against programs running on a
controller rather than on one of the grader's own Python references (IP-11).
Each JSON file is the grader's full report, unedited.

| Report | Program | Controller | Result |
|---|---|---|---|
| `sorting-by-height_Sorting-scl-v0.4_plcsim.json` | [`examples/tia/Sorting.scl`](../tia/Sorting.scl) v0.4, unchanged, with the ten-member `FF_IO` of [`FF_IO_datablock.md`](../tia/FF_IO_datablock.md) | S7-1500 1511-1 PN FW 2.9, PLCSIM Advanced V6.0 Upd1 | **FAIL** 10/15, seed 1 |
| `sorting-by-height_first-hour-scl_plcsim_seed1.json` … `seed3.json` | [`first_hour_sorting_by_height.scl`](first_hour_sorting_by_height.scl): the guide's first-hour program in SCL | the same CPU | **PASS** 15/15, seeds 1, 2, 3 |

All four were run on 2026-09-29 through the native driver, with the instance
in PLCSIM (Softbus) mode:

```
factoryforge-sidecar grade --scene sorting-by-height --seed <n> --bus-port <p> --json <report>
factoryforge-sidecar connect --driver plcsim-advanced -o instance <instance> --mapping <mapping> --port <p>
```

`Sorting.scl` used `examples/plcsim_mapping.json`; the first-hour program used
`examples/tia/sorting-by-height/plcsim_mapping.json`.

## What they show

**`Sorting.scl` sorts perfectly and fails the exam, and it should.** It was
written for the original brief, which had no operator panel: it runs the belt
from its first scan and never reads Start, the E-stop or the drive's fault
contact. Its sorting checks all pass (9 tall down the chute, 9 short past the
end, none misrouted, the pusher 0.98 s after the beam); the five checks it
fails are the panel and fault ones. The grader's feedback names each of them in
the terms the brief uses.

**The first-hour program passes on a Siemens CPU.** It had passed the grader's
Python models and, in its earlier form, a real OpenPLC (`docs/OPENPLC.md`).
Its current form, which also trips on `conveyor.fault` (IP-12), had not been
run on any real controller. Here it passed all fifteen checks at three seeds:
the belt stopped 20–30 mm after the mushroom (the limit is 100 mm), stayed
still through release, Start alone, and a drive fault clearing, and ran again
0.06–0.07 s after Reset-then-Start. It still has not been run on OpenPLC.

## Restart the controller before every graded run

Seeds 2 and 3 first **failed** `line.started_by_start`: the belt was running at
0.01 s. The previous run had ended with the line started, and a PLC keeps its
program state when a grader disconnects, so the next exam met a line already
running. A STOP → RUN restart of the CPU before each run (non-retained data
back to its start values) gave the passes in the table. This is the Siemens
half of the second paragraph of AGENTS.md gotcha 25, which OpenPLC showed
first; the guide's step 9 already restarts OpenPLC for the same reason.

## What is not here

- **A program written by a student.** Both programs were written by this
  project, from its own briefs. This is the grader meeting a real CPU's scan,
  not the grader meeting a real student.
- **OPC UA.** The instance was in Softbus mode, which nothing on the network
  can reach. IP-13's OPC UA half is still open.
- **OpenPLC.** IP-11's OpenPLC half is still open.
