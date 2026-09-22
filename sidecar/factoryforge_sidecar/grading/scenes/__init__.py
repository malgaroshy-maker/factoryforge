"""One module per graded scene: its plant model, its rubric, its feedback and
its summary.

A scene module defines two names, and `grading.registry` finds it by them:

* `SCENE` -- the scene id, exactly as `engine/templates/manifest.json` has it.
* `RUBRIC` -- `{"title", "task", "build", "observe", "grade", "summary",
  "duration", "references", "tags"}`: what `build(seed)` returns is the plant
  the grader owns, `grade(watched, engine, report, duration)` marks a run of
  it, and `references` names the built-in controllers in
  `grading/reference/<same module name>.py`, `good` first.

Exactly one of them also sets `DEFAULT = True`: the scene `--scene` means when
it is left out.

A module whose name starts with an underscore is a helper shared by the scenes
beside it (`_regulator.py`, the tank and the oven), not a scene.

The plant models follow three rules, written out at the top of
`grading/plant.py`; the one that matters most is that the verdict comes from
the plant's own ledger and never from a tag.
"""
