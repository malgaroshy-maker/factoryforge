# FactoryForge — Reading What You Built, and Getting Around It

**Status:** done — NV-01 … NV-04.
**Started:** 2026-09-16, against `1d7379e`.

Building a line is now fast. Three small things about the minute *after* you
build one are not.

**You cannot see what anything is called.** A part's instance id **is its tag
prefix** — rename a pusher to `reject` and its tags become `reject.extend`,
`reject.extended`, `reject.retracted`. That makes the id the single most
important thing about a part the moment you stop building and start writing a
program against what you built, and the only way to read it was to click each
part in turn and look at the property panel. On a twelve-part line that is
twelve clicks and a memory test.

**There is no clipboard.** `Ctrl+D` copies within a scene. There was no way to
take a section of one line into another scene at all, which is exactly what
somebody does once they have built something worth reusing.

**There is no plan view.** Laying a line out is a plan-view job; inspecting one
is not. Hunting for either by dragging is the kind of small friction nobody
reports and everybody feels.

---

## NV-01 · Part names in the scene

`N`, or the toolbar's **🏷 Names**, floats each part's instance id over it.

The label is a **child of the part**, so it follows every move, drag, nudge and
rotation for free and is freed with the part it names — and it is a `Label3D`
rather than a 2D overlay for the same reason: an overlay would need a
projection per part per frame and would have to be told about every one of those
gestures.

Two details that matter more than they look:

* **It follows a rename.** A stale name on screen points at tags that no longer
  exist, which is worse than no name at all.
* **Nothing that measures a part can see it.** `PartBounds` accumulates
  `MeshInstance3D` only and a `Label3D` is not one, so the selection outline,
  the click box and the duplicate offset are all unchanged by a part having a
  name over it. The self-test asserts that last one specifically, by duplicating
  a named part and checking the step is still the belt's own length.

Fixed size on screen rather than scaled by distance: a label whose size depends
on how far away it is makes the near end of a line shout and the far end
unreadable, and the thing being read is a name, not a feature of the machine.

Off by default — a finished line with thirty names over it is harder to look at
than one without.

## NV-02 · Select all, copy, paste

`Ctrl+A` selects everything. `Ctrl+C` and `Ctrl+V` copy a selection and put it
down again, **across scenes** — which is the reason to have a clipboard at all
rather than only `Ctrl+D`.

The clipboard stores `PartInstanceData` — type, offset from the group's anchor,
rotation and captured properties — not references to the parts. So a copy
survives its originals being deleted, and survives loading a different scene.

Ids are deliberately **not** kept. A pasted part is a new part and mints a fresh
instance id; keeping the id would make it adopt the tags of whatever it was
copied from, and two parts would drive one belt. Each paste steps clear of the
last, so repeated pastes lay a row out instead of stacking in one cell, and the
whole paste is one undo step.

## NV-03 · Standard views

`1` iso · `2` top · `3` front · `4` side. They change the **angle and not the
subject** — the distance and the point being looked at are left alone — so
pressing one never loses what you were looking at.

Top is clamped to the camera's own pitch limit rather than being a true
straight-down view: at exactly −90° the yaw stops meaning anything and the up
vector becomes ambiguous, which makes the next drag snap to an angle nobody
chose.

## NV-04 · Key list, tests, docs

`KeyBindings` gains `N`, `Ctrl+A`, `Ctrl+C/V` and the four view keys, so `F12`
and the start screen learn them from the one table.
`--self-test=buildflow` grows sections for the clipboard and the names.

---

## Appendix A — work item index

| Item | Title | Status |
|---|---|---|
| NV-01 | Part names floating in the scene | **done** |
| NV-02 | Select all, copy and paste | **done** |
| NV-03 | Standard viewing angles | **done** |
| NV-04 | Key list, self-test, docs | **done** |

## Progress log

- **2026-09-16** — all four landed, and one thing was learned before any of
  them.

  The full test plan caught a gap the round before this one left behind: the
  eighth template shipped without its entry in
  `engine/fixtures/scene_tag_sets.json`, so `--self-test=scenes` failed with
  *"accumulation-buffer: no expectation"*. That is the fixture working exactly
  as designed — a scene with no stated I/O contract is a failure and not a skip.
  The fault was in the verification around it: the targeted runs after adding
  the template covered `templates` and `tryscene` and not `scenes`. Recorded
  here because the lesson is about which checks to run, not about the code.

  From this round itself:

  * Labels were **distance-scaled** at first and the near end of the line
    shouted while the far end was unreadable. `FixedSize` fixes it, and it is
    the right default for anything that is a name rather than a feature of the
    machine.

  * The clipboard stores **offsets from an anchor**, not absolute positions,
    for the same reason a group duplicate takes one offset: a copied belt, its
    sensor and its pusher have to stay lined up with each other wherever they
    land.
