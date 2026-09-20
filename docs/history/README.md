# Closed plans

Thirteen documents that are finished. Every item in every one of them is done,
checked off, and shipped. They live here so that a new contributor opening
`docs/` can tell at a glance which documents are asking something of them and
which are not.

**These are records, not instructions.** Read one to find out *why* a thing was
built the way it was — which is usually the question you actually have, and
almost never answerable from the code. Do not read one to find out what the
project does today.

The live documents are one directory up: [`../PRD.md`](../PRD.md),
[`../ROADMAP.md`](../ROADMAP.md), [`../TEST_PLAN.md`](../TEST_PLAN.md),
[`../PART_AUTHORING.md`](../PART_AUTHORING.md),
[`../DRIVER_AUTHORING.md`](../DRIVER_AUTHORING.md),
[`../GETTING_STARTED.md`](../GETTING_STARTED.md),
[`../PACKAGING.md`](../PACKAGING.md), [`../tag-bus.md`](../tag-bus.md), and
[`../HARDENING_PLAN.md`](../HARDENING_PLAN.md), which is the only open one.

## The numbers in here are the numbers of their day

`SESSION-NOTES.md` says "41 tests passing" and `PLAN.md`'s status table says the
same. The suite is 73. Both were correct when they were written and neither has
been rewritten, because a completed plan that gets quietly updated stops being a
record of anything. The same applies to part counts, milestone states and the
claim in `PLAN.md` that it is a "living document" — it was, and it is not now.

If you want a current number, run the command that produces it.

## What is in here

| Document | What it was for |
|---|---|
| [`PLAN.md`](PLAN.md) | The original technical plan — architecture, the sidecar split, the scene format, the decisions the rest of the project inherited |
| [`FIX_PLAN.md`](FIX_PLAN.md) | FF-01…FF-29, written 2026-08-22. The first full-surface review: a fresh clone that could not run, a sidecar serving stale values, and the shared parity fixture that came out of it |
| [`UX_PLAN.md`](UX_PLAN.md) | UX-01…UX-46, the largest of them. The app reviewed against its own claims — the start screen, the templates, the launcher that finds Godot on somebody else's machine, the per-scene exercises |
| [`LOOSE_ENDS_PLAN.md`](LOOSE_ENDS_PLAN.md) | LE-01…LE-12. What the UX sweep left behind: claims without code, controls with no effect, and a billboard label nothing ever constructed |
| [`OPERATOR_PLAN.md`](OPERATOR_PLAN.md) | OP-01…OP-10, plus FI-01 which the work found on its own. Operating a line by hand, and the fault tool — the thing that first let a command and reality disagree |
| [`COMPONENTS_AND_POLISH_PLAN.md`](COMPONENTS_AND_POLISH_PLAN.md) | CP-01…CP-33. Nine parts, fifteen to twenty-four, each chosen for something the library could not previously teach, plus the presentation pass beside them |
| [`LINE_PRIMITIVES_PLAN.md`](LINE_PRIMITIVES_PLAN.md) | LP-01…LP-22. Five more parts, chosen by asking what a student *could not build*: accumulation, a rotary index, distance instead of time, cooling, and a permissive a tie-down cannot defeat |
| [`BUILD_FLOW_PLAN.md`](BUILD_FLOW_PLAN.md) | BF-01…BF-06. Twenty-nine parts, and putting six of them in a row was still six trips to the palette |
| [`SHOP_AND_SELECTION_PLAN.md`](SHOP_AND_SELECTION_PLAN.md) | EN-01…EN-02, ES-01…ES-05. A floor and walls to stand the line against, and a selection that is a group |
| [`NAMES_AND_VIEWS_PLAN.md`](NAMES_AND_VIEWS_PLAN.md) | NV-01…NV-04. Showing every part's name, because the name is the tag prefix a PLC program is written against; the clipboard; the standard views |
| [`TAG_INSPECTOR_PLAN.md`](TAG_INSPECTOR_PLAN.md) | TI-01…TI-05. A tag list you can find things in, and a force you cannot forget |
| [`TASK_BRIEFS_PLAN.md`](TASK_BRIEFS_PLAN.md) | BR-01…BR-04. Eight templates that each taught something specific, and an app that never said what |
| [`SESSION-NOTES.md`](SESSION-NOTES.md) | The narrative: what happened per session and why, including the mistakes and what they cost |

Two caveats on that table, since the point of this directory is being able to
trust it:

**`FIX_PLAN.md` still opens with "nothing in here has been implemented".** It was
never restamped. Its items did land — the parity fixture, the reconnect, the
status chip — with one exception: **FF-08, cross-platform sidecar launch, is
still open**, and it is why `.github/workflows/test-plan.yml` runs
`--only A,B,C,E,H` instead of the whole plan. That work is now HP-44.

**`OPERATOR_PLAN.md` and `SESSION-NOTES.md` were linked from nothing at all**
before this directory existed. They are two of the more useful documents here,
which is an argument for the directory rather than against it.
