# FactoryForge — Telling People What To Build

**Status:** done — BR-01 … BR-04.
**Started:** 2026-09-16, against `99653a6`.

Eight templates ship, every one of them chosen to teach something specific, and
**the app never says what.**

Open the heat-treat station and you get a hot plate on a pedestal, a gauge, a
beacon and a control panel. What are you supposed to do with it? The answer —
hold the plate at setpoint, measure the standing offset proportional control
leaves, then close it with integral action, then fail the element and watch the
output lie to you — exists, and it is written down, in
`tools/try_scene.py`. Which is a Python test harness. Which is the last place a
person learning PLC programming is going to look.

That is the whole gap. The scenes are good; the lesson attached to each one is
invisible from inside the product.

---

## BR-01 · A brief per template, in the manifest

Three fields, because a brief that is only prose gets skimmed:

* **`task`** — what to build, in two or three sentences.
* **`uses`** — the tags your program drives and reads, ` · `-separated.
* **`done`** — how you know it works, which is the part that turns "make the
  belt go" into something a person can check themselves.

It lives in `engine/templates/manifest.json` beside the title and the blurb,
because that file is already the single place that knows what templates ship
(UX-13) and a second list would drift from it.

The blurb and the task are different things and both are worth having: the
blurb says what the scene *is*, and it is what you read while choosing between
eight of them; the task says what to do with it.

## BR-02 · On the start screen

The template button's tooltip gains the task and the done-when. Choosing
between eight templates is exactly the moment somebody wants to know what each
one will ask of them.

## BR-03 · In the scene, on demand

`T`, or the toolbar's **📋 Task**, opens the brief over whatever is on screen.

On demand rather than on open: a panel that covers the scene the moment it
loads is a panel people learn to dismiss without reading. A scene somebody
built themselves has no lesson attached, and says so rather than opening blank.

## BR-04 · The check that keeps it honest

A brief is prose and nothing about prose can be checked — **except `uses`**,
which is not prose. It names tags, and a brief naming a tag the scene does not
have is a broken lesson: somebody follows it, cannot find the tag, and concludes
the app is wrong rather than the text.

That is also exactly how this goes stale. Nobody edits a template *intending*
to invalidate its brief; they rename a part or drop one, and the prose keeps
saying what it always said. So `--self-test=templates` checks every tag a brief
names against the scene the editor has just loaded — not against a second list
of what the scene ought to contain — and a shipped template with no brief at all
is a failure rather than a skip, the same way a scene with no tag-set fixture
is.

---

## Appendix A — work item index

| Item | Title | Status |
|---|---|---|
| BR-01 | A brief per template, in the manifest | **done** |
| BR-02 | The task on the start screen | **done** |
| BR-03 | `T` opens it in the scene | **done** |
| BR-04 | Every tag a brief names must exist | **done** |

## Progress log

- **2026-09-16** — all four landed.

  **The BR-04 check was proved by breaking it**, not by watching it pass: one
  tag in the heat-treat brief was changed from `oven.attemp` to `oven.attempt`,
  the test was run, and it failed with *"the brief names 'oven.attempt', which
  this scene does not have"*. A new check that has only ever been seen to pass
  is a check nobody knows the shape of.

  Three findings:

  * `SetAnchorsPreset(FullRect)` is **not** `SetAnchorsAndOffsetsPreset`. The
    first sets the anchors and leaves the control at its old zero size, so the
    centring container inside it centred within nothing and the card landed in
    the top-left corner on top of the parts palette — with no background, which
    made it look like a rendering fault rather than a layout one. `KeyHelpUI`
    had it right and was the answer.

  * **A6 failed on the first full run**, and correctly: `TemplateBrief` was
    referenced nowhere outside its own file *by name*, because every reader had
    written `var`. That check exists to find a type nothing uses; a type every
    caller infers away looks identical to it from the outside. Named at the use
    sites. `KeyBindings.Binding` was caught the same way once before, which
    makes this a habit worth naming rather than a one-off.

  * All eight briefs were written against the tag ids in
    `engine/fixtures/scene_tag_sets.json` rather than from memory of what each
    scene contains, and cross-checked before a line of C# was written. Three of
    the ids would have been wrong from memory.
