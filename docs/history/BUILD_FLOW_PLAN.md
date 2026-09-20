# FactoryForge — Build Flow Plan

**Status:** done — BF-01 … BF-06.
**Started:** 2026-09-15, against `522a62a`.

Twenty-nine parts, and the act of putting six of them in a row is still six
trips to the palette.

That is the gap this plan is about. Everything so far has made the *parts*
better — what they teach, how they move, whether they can fail. Nothing has
looked at the minute a person spends actually building a line, and that minute
is the one every user spends first. Three things in it are worse than they need
to be, and none of them is a missing feature so much as a small, repeated tax:

1. **Placing a part disarms the palette.** Six conveyors in a line is six clicks
   on the same button, with a trip across the screen between each one. Every
   editor anybody has used keeps the tool armed until you put it down.

2. **`Ctrl+D` stacks.** The duplicate lands one grid cell over and is *not*
   selected, so the second press duplicates the original again, onto the same
   spot. Two presses put two parts in one cell, invisibly. And one cell is the
   wrong offset anyway: a belt is three cells long, so the copy lands on top of
   the thing it was copied from.

3. **There is no small adjustment.** A part can be dragged or moved with `M`,
   both of which are whole gestures. Shifting one part one cell to line it up
   with its neighbour needs the keyboard equivalent of a nudge, and the arrow
   keys do nothing at all.

None of this is hard. All of it is felt on the first scene anybody builds.

---

## Phase 1 — The build loop

### BF-01 · Placement stays armed

Placing a part re-arms the same part at the same rotation, so a line is
click · click · click. `Esc` or a right-click puts the tool down, as they
already do.

Committing a **move** still disarms — a move is one part going to one place,
and re-arming there would spawn a second copy of what you just moved.

### BF-02 · `Ctrl+D` lands clear, and selects what it made

The offset becomes the part's own footprint along its facing, measured from the
meshes (`PartBounds`) and rounded up to a whole grid cell — so a duplicated
belt lands end to end with its source and a duplicated sensor lands in the next
cell. The copy is then **selected**, which is what makes the second press
duplicate the copy rather than the original: `Ctrl+D Ctrl+D Ctrl+D` walks a line
across the grid instead of piling three parts into one cell.

### BF-03 · Arrow keys nudge

One grid cell per press, on the work plane, relative to the camera's heading so
"left" means left on screen rather than left in world X. Each press is its own
undo step, which is what a nudge is for: press it twice too far and `Ctrl+Z`
twice puts it back.

## Phase 2 — Saying so

### BF-04 · The armed part is visible

The palette button for the armed part stays lit while it is armed, and goes out
when it is put down. Without it, BF-01 makes the editor modal with nothing on
screen to say which mode it is in — which is a worse bug than the one it fixes.

### BF-05 · The key list

`KeyBindings` gains the nudge and says what `Esc` now puts down, so `F12` and
the start screen both learn it from the one table.

## Phase 3 — Tests and docs

### BF-06 · `--self-test=buildflow`

Asserts the loop rather than the calls: that a second click after a place makes
a *second part*, that `Esc` stops that happening, that two duplicates land in
two different cells, and that a nudge is one undo step.

---

## Appendix A — work item index

| Item | Title | Status |
|---|---|---|
| BF-01 | Placement stays armed | **done** |
| BF-02 | `Ctrl+D` lands clear and selects the copy | **done** |
| BF-03 | Arrow-key nudge | **done** |
| BF-04 | The armed part is visible in the palette | **done** |
| BF-05 | Key list | **done** |
| BF-06 | `--self-test=buildflow` | **done** |

## Progress log

- **2026-09-15** — plan written against `522a62a`.

- **2026-09-16** — all six landed.

  The two halves of BF-01 shipped together on purpose. Keeping the tool armed
  makes the editor *modal*, and a mode with nothing on screen to say which mode
  it is in is a worse bug than the one it fixes — so the palette button for the
  held part stays lit in safety yellow and goes out the moment the tool is put
  down. It follows a signal from the editor rather than remembering its own
  button press, because four things put the tool down that the palette never
  hears about: Escape, a right-click, entering Run mode, and committing a move.

  Findings:

  * **Committing a move had to stay the exception.** `M` reuses the placement
    preview to carry a part to its new cell, so a blanket re-arm would leave a
    ghost of what you just moved, ready to drop a second copy on the next
    click. The re-arm is conditional on the placement not being a move, and
    `--self-test=buildflow` asserts that specifically, because it is the one
    case where the feature and the bug look identical from the outside.

  * **Re-arming must not reset the rotation**, which is a trap rather than a
    bug that shipped: the obvious way to re-arm is to call `SetPlacementPart`
    again, and that zeroes the heading — so a line of belts turned 90° would
    come out with every one after the first lying straight. `ArmPreview(type,
    rotation)` is split out of it for exactly that reason, and the test places
    a turned part and checks the next one is still turned, so the obvious
    version cannot quietly come back.

  * **`Ctrl+D`'s old offset was wrong twice over.** One grid cell put the copy
    *inside* a three-cell belt, and because nothing selected the copy, the next
    press duplicated the original again and stacked a second part in the same
    cell with nothing on screen to say so. Both halves had to change: the
    offset is now the part's own footprint along its facing, rounded up to a
    whole cell, and `DuplicateCommand.Execute` selects what it made — inside
    the command rather than at the call site, so a *redo* selects it too.

  * The nudge maps screen directions through the camera's heading, snapped to
    the nearest quarter turn. A nudge that moved a part in world +X while the
    camera looked down -X would read as a bug rather than as a convention, and
    an unsnapped heading would slide the part off the grid by a fraction of a
    cell at every odd viewing angle. The test presses right, up, left, down and
    checks the part comes back to where it started, which is the claim that
    those four are one rotation rather than four unrelated directions.
