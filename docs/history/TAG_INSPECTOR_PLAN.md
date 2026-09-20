# FactoryForge — The Panel a PLC Person Lives In

**Status:** done — TI-01 … TI-05.
**Started:** 2026-09-16, against `be392aa`.

The Tag Bus Inspector was written against an eight-tag demo and never revisited.
It is a flat list of every tag in the scene, in registration order, and that is
fine for eight tags and unusable for what this became: the shipped accumulation
buffer alone publishes **twenty-seven**, across ten machines, and finding
`stop.up` in it means scrolling past four other parts.

It is also the panel that is open the whole time. Everything else in the editor
is used in bursts — place a part, tune it, move on — and this one is read
continuously while a program is being written against the scene. A list that is
merely tolerable in the panel you glance at is a real cost in the panel you
live in.

Four things, three of them obvious and one of them not — plus one the work
turned up on its own.

---

## TI-01 · Search

A box over the list, filtering on the tag id. By **visibility, not by
rebuilding** — rebuilding recreates the box's own siblings under it and costs
focus on every keystroke, which makes a search box that cannot be typed into.
The parts palette learned that the hard way (CP-20) and this is the same shape
of panel.

## TI-02 · Grouped by machine

Everything before the first dot is the part, so the list groups by it and each
group collapses. That turns twenty-seven rows into ten headers you can shut.

Groups are collected before they are built rather than accumulated as a run of
matching prefixes. A part registers all of its tags in one go today, so a
run-length grouping would work by luck; one interleaved pair — a renamed part
re-added after another — would give one machine two headers and split its tags
between them.

While a filter is on, every surviving group is forced open: a collapsed group
would hide its own matches, and collapsing is something you do to a *full*
list.

## TI-03 · One half at a time

A button that cycles **All → what the PLC writes → what it reads**. The kinds
are from the controller's point of view, which is the direction a person
mapping addresses is working along: they are filling in outputs, or they are
filling in inputs, and almost never both at once.

One cycling button rather than three radio buttons because the panel is a fixed
440 px and a row of three does not fit beside a search box without squeezing
the box down to uselessness.

## TI-04 · A forced tag is impossible to miss

This is the one that is not obvious, and it is the one worth having.

A forced tag is a value that **disagrees with the simulation on purpose**. That
is exactly what you want while you are testing a rung, and it is a trap an hour
later: the belt will not start, the sensor reads true with nothing in front of
it, and the reason is a force nobody remembers setting. The panel already said
so — the row's button read `UNFORCE` — but only if you were looking at that row,
which you are not, because you are looking at the machine.

So the **name** is marked, not just the button; a line above the list says how
many are held; and one **Release all** button hands every one of them back. The
row only appears while something is forced, because an alarm that is always on
screen is a decoration.

---

## Appendix A — work item index

| Item | Title | Status |
|---|---|---|
| TI-01 | Search the tag list | **done** |
| TI-02 | Group by machine, collapsible | **done** |
| TI-03 | Filter by kind | **done** |
| TI-04 | Forced tags stand out, and release all | **done** |
| TI-05 | The panel clears the toolbar | **done** (found, not planned) |

## Progress log

- **2026-09-16** — all four landed, extending `--self-test=force` rather than
  adding a test beside it: the panel's forcing behaviour and its new filtering
  are the same panel, and a second test would have built a second copy of it.

  The checks are driven through the panel's **own controls**, found by what is
  on screen — a header by its tooltip, a row by the tooltip on its name button,
  the search box by its placeholder — rather than through test-only accessors
  into private state. That convention was already there for the Force button
  and it is worth keeping: a test that reaches past the UI can pass while the UI
  is unreachable.

  **TI-05** was not planned: the panel's own title has been hidden behind the
  scene toolbar this whole time, which only became obvious once there was a
  search box under it to notice was in the wrong place. The preset that
  positions the panel applies one margin to every side at once, so the top is
  nudged on its own rather than pushing the panel in from the right edge too.

  One trap worth recording: setting `LineEdit.Text` from code does **not** emit
  `text_changed`, so a test that only assigns the text filters nothing and
  passes for the wrong reason. The check raises the signal explicitly, which
  still drives the panel's real handler.
