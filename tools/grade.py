#!/usr/bin/env python3
"""Grade a student's PLC program against a scene, unattended.

    python tools/grade.py --list
    python tools/grade.py --scene sorting-by-height --student "a.patel"
    python tools/grade.py --scene sorting-by-height --json marks/a.patel.json

The grader is the `factoryforge_sidecar.grading` package; this file is the
path the docs, the tests and CI have always called, kept as a shim over it
(IP-18). The flags, the output, the JSON and the exit codes are the package's
and are unchanged:

    0  PASS           every check met
    1  FAIL           the program ran and got it wrong
    2  ERROR          nothing to grade -- nobody connected, or the run broke
    3  DISQUALIFIED   tags were forced

What grading is, and why it works the way it does, is the package docstring
and docs/GRADING.md.

The names imported below are the ones `tests/test_grade.py` reaches through
`grade.` from when this file was the whole grader. A scene added later needs
nothing here: the command line finds it through `grading.registry`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))

from factoryforge_sidecar.grading import main                      # noqa: E402
from factoryforge_sidecar.grading.core import (                    # noqa: E402,F401
    GradedEngine, Report, Watched, check_integrity, start_reference)
from factoryforge_sidecar.grading.lockstep import (                # noqa: E402,F401
    SCAN, Lockstep, LockstepClient)
from factoryforge_sidecar.grading.registry import (                # noqa: E402,F401
    SHARED_REFERENCES, reference_for, rubrics)
from factoryforge_sidecar.grading.scenes.guarded_cell import (     # noqa: E402,F401
    GC_MUTE_LIMIT)
from factoryforge_sidecar.grading.scenes.sorting_by_height import (  # noqa: E402,F401
    MIN_PER_LANE, MIN_SORTED, _beam_to_pusher_window, feed_pattern, grade_sorting,
    observe_sorting)

#: `{scene id: rubric}`, the same dict object the command line reads.
RUBRICS = rubrics()

if __name__ == "__main__":
    raise SystemExit(main())
