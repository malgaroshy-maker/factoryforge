<!--
Thanks for this. The sections below are the ones that make a change quick to
review; delete any that genuinely do not apply rather than writing "n/a" in all
of them. CONTRIBUTING.md has the longer version.
-->

## What this changes

<!-- In plain words, and from a user's seat where that is possible. -->

## Why

<!--
The problem it solves. If you tried something that did not work first, say so —
that is usually the most useful paragraph in the whole PR, and this project's
commit log is full of them.
-->

## What you ran

<!--
Paste the output rather than describing it.

  python -m pytest -q
  python tools/test_plan.py        # --gui for the two display-dependent checks

Engine self-tests, if you touched the engine:

  "<GODOT>" --headless --path engine/ -- --self-test=scene
  "<GODOT>" --headless --path engine/ -- --self-test=<whatever covers your change>

A failed dotnet build leaves the previous binary in place and Godot runs it, so
"it worked when I ran it" is worth checking against the build result.
-->

```
```

## Checks

- [ ] `python -m pytest -q` passes
- [ ] `python tools/test_plan.py` passes, or I have said which section fails and why
- [ ] `dotnet build` in `engine/` is clean at **zero warnings** (A1 enforces this)

**If you added a test:**

- [ ] I broke the thing it checks, watched the test fail, and fixed it again

<!--
That last one is not ceremony. This project has shipped a determinism check that
compared two undriven runs and asserted (0,0) == (0,0), a dispatch check that
asserted nothing threw, and a corrupt-file check that never opened the file it
wrote. All three passed for weeks. A check that has only ever been seen to pass
is a check nobody knows the shape of.
-->

**If you added a part:**

- [ ] All eight integration points in `docs/PART_AUTHORING.md`'s table are done
- [ ] `--self-test=scene` round-trips it, and a self-test asserts what it **does**, not that its tags exist
- [ ] Settings it reads in `_Ready` are in `PartProperties`; values it *computes* every tick are deliberately not

**If you added a driver:**

- [ ] It registers whether or not its optional dependency imported, so it can explain itself at connect time instead of vanishing from `--help`
- [ ] Numeric and boolean `-o` options are coerced in `__init__` — they arrive as strings, and a type hint does not make them otherwise

## Docs

- [ ] I updated any document this change makes wrong, or there is none

<!--
Counts and status claims in this repository have gone stale four separate times.
If your change alters a number that is written down somewhere, that somewhere is
part of the change.

Documents in docs/history/ are closed records and are deliberately not updated.
-->

## Related

<!-- Issue number, or the HP-nn item in docs/HARDENING_PLAN.md this closes. -->
