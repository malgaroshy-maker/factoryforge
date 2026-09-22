"""Built-in stand-ins for a student, one module per graded scene.

A reference module defines two names, and `grading.registry` finds it by them:

* `SCENE` -- the scene id its controllers are written against.
* `REFERENCES` -- `{name: controller}`, where a controller is
  `async def controller(bus, stop)` and scans through `grading.lockstep.run_scan`
  so that `--lockstep` can put it on the plant's clock.

`_shared.py` holds what they are built from (the feeder, the panel's scan) and
the two controllers every scene has, `idle` and `forcer`. Why these exist at
all is written at the top of that file.
"""
