"""An alias: the headless sorting scene is `factoryforge_sidecar.sorting_scene`.

It moved into the sidecar package so that the grader, which marks this scene
and reads its geometry, can live there too and be frozen with the sidecar
(IP-18, IP-08). Everything that has always said `from scene import ...` with
`harness/` on its path keeps working unchanged through this file.

It is an alias and not a re-export, on purpose. Replacing this module in
`sys.modules` with the real one makes `import scene` return *that* module
object, so `scene.BELT_SPEED = x` changes the constant the belt actually reads
-- `tests/test_grade.py` does exactly that to prove the grader's advice follows
the line's geometry -- and `scene.SortingScene` is the one class there is.
"""

import sys

from factoryforge_sidecar import sorting_scene as _module

sys.modules[__name__] = _module
