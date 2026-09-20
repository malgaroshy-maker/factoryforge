# FactoryForge — The Shop, and Editing More Than One Thing

**Status:** done — EN-01 … EN-02, ES-01 … ES-05.
**Started:** 2026-09-16, against `54787fd`.

Two unrelated gaps, both of them things you notice in the first minute and
neither of them a missing feature.

**The scene had no place.** The floor was a dark plane with a faint speckle on
it, and there were no walls at all — so a factory simulator opened on a machine
standing in a grey void. The specific casualty is *scale*: with nothing around
it, a conveyor could be two metres long or twenty and nothing on screen says
which. Every other decision a user makes — how much room a gantry needs, whether
a chute will reach, how big a buffer they are building — rests on a sense of
size the render was not giving them.

**Editing was one part at a time.** `BUILD_FLOW_PLAN.md` made *building* a line
fast: the part stays in your hand, `Ctrl+D` walks, the arrows nudge. But the
moment a line exists, everything you want to do to it is plural — move that
section two cells over, turn those four belts, delete the spur you changed your
mind about — and every one of those was one part per gesture, with one undo
step per part to unpick it afterwards.

---

## Phase 1 — The shop

### EN-01 · A floor somebody poured

Concrete with a control joint around every two-metre bay, a normal map so the
sun catches the surface, and worn patches smoother than the aggregate between
them. The bay is the point as much as the texture is: it is a two-metre ruler
lying under the machine, so a three-metre belt is visibly a three-metre belt.

Baked in code, like everything else here. This project ships no image assets
and should not start — a texture on disk is a licence to check, a file the
exporter has to be told about, and a thing that can go missing from a release
without the build noticing. The tile is seamless because its noise lattice
wraps, so twenty repeats across forty metres show no grid of joins but the ones
painted on deliberately.

### EN-02 · Walls to stand them against

Profiled steel cladding to a five-metre eaves, a darker painted dado below it
where the knocks go, and steel stanchions down each wall. Cladding of a known
height is the second ruler in the scene, after the floor bays.

Three things stop this boxing the user in:

* The panels are **one-sided and face inward**, so the camera can orbit outside
  the shop and see straight through the wall behind it. A double-sided wall
  would replace the void with a blank grey box, which is not an improvement.
* There is **no collision**. Giving the walls colliders would stop a carton
  that outran the line — which the README advertises as normal, and which the
  kill plane already handles — and would change the behaviour of every scene
  already authored against a floor that went on for forty metres.
* The tops are open to the sky, so the sky light and the fog are unchanged and
  the staging stays shared between the deterministic scene and the physics one.

## Phase 2 — A selection is a group

### ES-01 · Several parts selected at once

`Shift`+click adds and removes. The **primary** — the last one added — is what
the property panel, the rename box and `M` act on, because all three are about
one part by nature; it is drawn brighter than the rest so it is obvious which
part the panel belongs to.

Everything that can sensibly happen to several parts at once does: drag, arrow
nudge, `R`, `Ctrl+D`, `Del`. Each is **one undo step**, which is most of the
value — deleting a six-part spur and pressing `Ctrl+Z` should bring the spur
back, not one belt of it.

### ES-02 · Box select

`Ctrl`+drag on empty floor and everything the box covers is selected; add
`Shift` to keep what was already selected. `Ctrl`, because a plain left-drag on
empty floor is how the camera orbits and that is the most-used gesture in the
app — see ES-05. A box that never grew is still a click on empty
space, and that still means deselect — which is why the *press* can no longer
deselect on its own and the release has to decide.

Parts are tested by their projected bounding box, not by their origin. A
three-metre belt's origin is in the middle of it, so an origin test would refuse
a box drawn neatly around one end of a line and silently accept a belt whose
body is entirely outside the box.

### ES-03 · A group keeps its shape

A dragged group moves by one vector, not per part. A duplicated group takes one
offset — the widest member's — rather than each part's own, because a group is
a shape: duplicating a belt, its sensor and its pusher has to keep them lined up
with each other, and per-part offsets would take the copy apart.

`R` turns each part about **its own** centre rather than the group's. Turning a
line of belts should turn each belt, which is what somebody who selected five of
them and pressed `R` is asking for; swinging them about a shared pivot would
scatter them off the grid.

### ES-05 · Dragging a part no longer spins the view

Found while wiring the box: the orbit camera and the editor both listen on
`_UnhandledInput` and **neither claimed anything**, so a part drag reached both.
The part followed the cursor and the world turned underneath it at the same
time — which reads as the drag being broken rather than as two features
fighting, and it has been that way as long as dragging has existed.

The editor now claims the events it is actually using: a part drag and a
selection box. A plain left-drag on empty floor still reaches the camera and
still orbits, which is why the box needs `Ctrl` rather than taking the gesture
outright.

## Phase 3 — Saying so, and testing it

### ES-04 · The key list, the count, and the checks

`KeyBindings` gains `Shift`+click and `Ctrl`+drag. `--self-test=buildflow` grows a
section for the group edits.

One honest gap: **the projection half of ES-02 is not covered headless.** It
asks a camera where each part's box lands on screen, and a headless run has no
camera. What the test can and does cover is everything downstream — a selection
built the Shift+click way, and every group edit acting on it as one undo step.

---

## Appendix A — work item index

| Item | Title | Status |
|---|---|---|
| EN-01 | Concrete floor with bay joints | **done** |
| EN-02 | Clad shop walls and stanchions | **done** |
| ES-01 | Multiple selection, with a primary | **done** |
| ES-02 | Box select | **done** |
| ES-03 | Group move, rotate, duplicate, delete | **done** |
| ES-04 | Key list and self-test coverage | **done** |
| ES-05 | A part drag stops orbiting the camera | **done** (found, not planned) |

## Progress log

- **2026-09-16** — plan written against `54787fd`.

- **2026-09-16** — all of it landed.

  **ES-05 is the one worth reading.** It was not on the list: it turned up while
  deciding which mouse gesture the box should use, and the answer to that
  question was "whichever one the camera is not already using" — which led
  straight to the discovery that *nothing in this app claims an input at all*.
  The orbit camera and the editor both listen on `_UnhandledInput`, neither
  called `SetInputAsHandled`, so a part drag reached both: the part followed the
  cursor and the world turned underneath it at the same time. That has been true
  for as long as dragging has existed, and it reads as the drag being broken
  rather than as two features fighting. The same collision was in Run mode on
  the setpoint pot, which is the one gesture there that is a drag.

  The fix is narrow on purpose. The editor claims only the events it is using —
  a part drag, a selection box, a control press, a pot turn — so a plain
  left-drag on empty floor still reaches the camera and still orbits. That is
  also why the box is on `Ctrl`+drag rather than taking the plain gesture: the
  orbit is the most-used thing in the app and must not become collateral.

  Findings from the rest:

  * **The floor was too bright at first.** Base albedo 0.52 with the bay joints
    at 0.34 read as polished tile, not concrete — twenty bays of high-contrast
    joints look like a tiled floor, and a bright floor pulls the eye off the
    machine standing on it. Darkened to 0.34 with the joint softened to 0.55,
    which is a darker line rather than a black one.

  * **Every direct write to `_selectedPart` had to go.** Four places assigned it
    and updated the gizmo and the inspector themselves, which is fine when a
    selection is one part and wrong the moment it is a list: `--self-test=scene`
    caught it immediately, because `SelectPartForInspection` set the primary and
    left the selection empty, so rotate and duplicate had nothing to walk. They
    all route through `SelectOnly` now, and the primary is derived from the list
    rather than kept beside it.

  * **A freed part has to leave the selection.** The gizmo drops invalid nodes
    on its own, so nothing looked wrong — but a group edit would still have
    walked over a `PlacedPart` whose node was gone. `ForgetPart` removes it.

  * The duplicate offset for a group is the **widest member's**, not each
    part's own. A group is a shape; giving each part its own footprint offset
    takes the copy apart.
