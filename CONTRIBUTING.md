# Contributing to FactoryForge

Nobody outside the project has contributed yet. You would be the first, so this
file tries to tell you the truth about what that costs rather than selling you
on it.

The project's whole argument is that Factory I/O is closed and this is not. That
argument is only worth anything if somebody who is not the author can actually
add a part or a driver. Two of the PRD's twelve-month success criteria are
exactly that, and both are unmet.

---

## The two ways in

| | Guide | Honest cost |
|---|---|---|
| **A part** — a conveyor, a sensor, a machine that does something the library cannot teach yet | [`docs/PART_AUTHORING.md`](docs/PART_AUTHORING.md) | One new C# class **plus eight edits to shared files**. See below. |
| **A driver** — a protocol the sidecar does not speak: MQTT, EtherNet/IP, BACnet, a PLC vendor's own API | [`docs/DRIVER_AUTHORING.md`](docs/DRIVER_AUTHORING.md) | One new Python module and one import line. No Godot, no C#, no 3D. |

**A driver is the cheap one and a part is not**, which is the opposite of what
you would guess from a 3D simulator. If you are choosing between them and either
would serve you, write the driver.

### What a part actually costs today

`docs/PART_AUTHORING.md` has a table, at the top, listing **everything a part
must touch**. It names eight integration points *outside* the new part's own
class:

* `PartCatalog.All` — its palette button, tooltip and self-test coverage
* `SceneEditor.CreatePartNode` — the node factory
* `PlacedPart.TagSuffixesByType` — the suffix cache the per-tick dispatch reads
* `PartTagManager.RegisterPartTags` — its tags
* `SceneEditor._PhysicsProcess` — the per-tick dispatch itself
* `PartProperties.Capture` / `Apply` — so its settings survive a save
* `PartPropertyInspectorUI.InspectNode` — its sliders
* `WholeBodyOperableTag` — optional, for Operate-mode clicking

Miss one and the part breaks *quietly*: it places and draws correctly and has no
I/O at all, or it has I/O that means nothing, or every setting reverts the moment
somebody saves. That table is a good piece of writing and the wrong artefact — a
checklist of eight places to remember is a design problem written down rather
than solved.

It has already cost something. The weighing conveyor registers a `.fault` tag
that nothing dispatches, so forcing it does nothing, unlike every other conveyor
in the library. Nobody decided that; it is what happens when registering a tag
and acting on it are edits to two different files.

**This is being fixed.** [`docs/HARDENING_PLAN.md`](docs/HARDENING_PLAN.md) HP-34
is the work to make a part own its own tags, properties and tick, so that adding
one is a new file plus a `PartCatalog` entry — two files instead of nine. It is
sized XL and has not started. If you want to add a part before it lands, read
that table carefully and expect the shared-file edits to conflict with anyone
else doing the same thing.

---

## Before you write anything

Read [`AGENTS.md`](AGENTS.md). It is the developer cheat sheet: absolute paths,
every command, and a numbered list of twenty-four things that have each cost
this project hours. Several of them will save you an afternoon directly —
notably that a failed `dotnet build` leaves the previous binary in place and
Godot runs it without complaint, and that a crashing self-test can still exit 0.

Then [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) for the setup. There is
no published release yet, so you build from source: Godot 4.7 mono, the .NET 8
SDK, Python 3.11+.

---

## Running the checks

```bash
python -m pytest -q          # the Python suite
python tools/test_plan.py    # everything: build, pytest, engine self-tests,
                             # determinism, the engine/sidecar seam, robustness
```

`test_plan.py` exits non-zero on any failure and needs no PLC, no Siemens
software and no GPU. Add `--gui` for the two checks that need a display.
[`docs/TEST_PLAN.md`](docs/TEST_PLAN.md) explains what each section covers and,
more usefully, what it does **not** prove.

A pull request that has not run the test plan is fine to open as a draft. One
that has run it and says what it printed is much easier to merge.

### Tests here assert effects, not existence

This project has repeatedly shipped a test that passed while the product was
broken — a determinism check that compared two undriven runs and asserted
`(0,0) == (0,0)`, a part-dispatch check that asserted nothing threw, a corrupt-
file check that never opened the file it wrote. So:

**Prove a new check by breaking the thing it checks.** Reintroduce the bug
deliberately, watch the check fail, then fix it again. A check that has only ever
been seen to pass is a check nobody knows the shape of. Say in the PR that you
did this.

---

## Commits

Lowercase type prefix, then a subject that says what changed in plain words —
not what file you edited:

```
fix: a carton that stops on a running belt can start again
feat: nine parts the library could not teach without
test: a brief may not name a tag its scene does not have
docs: the briefs, and the type every caller inferred away
```

Prefixes in use: `feat`, `fix`, `docs`, `test`, `chore`, `build`, `refactor`.

Put the reasoning in the body. Why the change was needed, what you tried that did
not work, and what the failure looked like from a user's seat. The commit log is
one of this project's better pieces of documentation and it is worth keeping that
way.

---

## Writing prose for this repository

The documentation has a voice, and it is not an accident:

* **Plain and specific.** Name the file, the line, the number, the failure.
* **Say what went wrong.** Several documents here record the author's own
  mistakes, including the ones that were embarrassing. That is deliberate — a
  correction that is hidden gets rediscovered.
* **No marketing language.** Nothing is seamless, powerful or cutting-edge. If a
  feature is good, say what it does and let the reader decide.
* **Do not claim something is verified unless it was run.** `PACKAGING.md` and
  `TEST_PLAN.md` both distinguish what was *executed* from what was *read*.

Emoji appear in `README.md` and the two authoring guides. They do not appear in
the plan documents. Match whatever the file you are editing already does; do not
introduce them to a file that has none.

---

## Scope — what this project is not

From [`docs/PRD.md`](docs/PRD.md), which is worth reading before proposing
anything large:

* **Not a Factory I/O clone.** No attempt to match its part count or driver list.
  Parts are chosen by what a student *cannot currently build*, not by what a
  competitor has.
* **Not a PLC.** The plant is simulated; the controller is external and stays
  external. Every protocol lives in the Python sidecar and the engine speaks only
  its own tag bus.
* **No validated physics claims.** It must behave plausibly, not certify a real
  line. Industrial virtual commissioning is an explicit non-user.
* **No accounts, no licence server, no internet requirement.** Removing that
  barrier is the entire reason the project exists.

The PRD also lists anti-goals — "signs we drifted". One of them is "a part
library only the maintainer can extend", which is why HP-34 exists and why this
file is blunt about the current cost.

---

## Where the open work is

[`docs/HARDENING_PLAN.md`](docs/HARDENING_PLAN.md), HP-01 through HP-52. Every
*feature* plan is closed; the completed ones are in
[`docs/history/`](docs/history/) as a record of why things were built the way
they were.

If you are looking for somewhere to start, the **release gate** in that file's
Sequencing section is the list that matters. Several of its items are sized `S`
and self-contained — a save that reports success after failing, an undo that
hands back a part at factory defaults, a rename that releases a force it should
have carried. All of them are on a student's first hour with the tool, and all of
them are invisible at the moment they happen.

---

## Reporting things

* **A bug, or a part you want** — open an issue; there are templates for both.
* **A security issue** — do not open an issue. See [`SECURITY.md`](SECURITY.md).
* **Behaviour in this repository** — see
  [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

One maintainer, working alone. Replies may not be fast. An issue that includes
the command you ran and what it printed will get a better answer than one that
does not, for the obvious reason.

---

## Licence

MIT. By contributing you agree your work is distributed under it. There is no
CLA.
