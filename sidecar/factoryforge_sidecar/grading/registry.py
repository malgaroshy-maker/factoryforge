"""Which scenes the grader can mark, and which built-in controllers each has --
found rather than listed.

Every module in `grading/scenes/` whose name does not start with an underscore
is a scene: it defines `SCENE` (the id the engine's manifest uses) and
`RUBRIC`. Every such module in `grading/reference/` defines `SCENE` and
`REFERENCES`. Adding a graded scene is therefore two new files and no edit to
any file that already exists -- the bar HP-34 set for parts. `core.py` asks
this module, and never names a scene itself; a test holds it to that.

Discovery is lazy and happens once. The scene modules import `core`, so
importing them while `core` is still being imported would be a cycle; asking
for them on first use is what lets `core` import this module at the top.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from . import reference as _reference_package
from . import scenes as _scenes_package
from .reference._shared import SHARED, SHARED_REFERENCES

__all__ = ["SHARED_REFERENCES", "rubrics", "default_scene", "references",
           "reference_for", "reference_choices"]

_scene_modules: dict[str, ModuleType] | None = None
_rubrics: dict[str, dict] | None = None
_references: dict[str, dict] | None = None


def _modules(package: ModuleType) -> dict[str, ModuleType]:
    """`{SCENE: module}` for every public module in `package`, imported, in
    scene order. A name starting with an underscore is a helper shared by the
    modules beside it, such as `scenes/_regulator.py`, and not a scene."""
    names = sorted(info.name for info in pkgutil.iter_modules(package.__path__)
                   if not info.name.startswith("_"))
    if not names:
        # Nothing imports these modules by name, so a freezer that only follows
        # imports leaves every one of them out -- and PyInstaller's
        # `--collect-submodules factoryforge_sidecar` collects nothing, without
        # a word, when the Python running it cannot import the package. That
        # binary would offer no scene at all; say why instead.
        raise RuntimeError(f"no modules found in {package.__name__}; in a frozen "
                           f"build, they were not collected")
    found: dict[str, ModuleType] = {}
    for name in names:
        module = importlib.import_module(f"{package.__name__}.{name}")
        scene = module.SCENE
        if scene in found:
            raise RuntimeError(f"{module.__name__} defines scene {scene!r}, which "
                               f"another module in {package.__name__} already did")
        found[scene] = module
    return dict(sorted(found.items()))


def _scenes() -> dict[str, ModuleType]:
    global _scene_modules
    if _scene_modules is None:
        _scene_modules = _modules(_scenes_package)
    return _scene_modules


def rubrics() -> dict[str, dict]:
    """`{scene id: rubric}` for every scene this tool can mark."""
    global _rubrics
    if _rubrics is None:
        _rubrics = {scene: module.RUBRIC for scene, module in _scenes().items()}
    return _rubrics


def default_scene() -> str:
    """The scene `--scene` means when it is left out.

    Declared by that scene's module, as `DEFAULT = True`, rather than written
    here or in `core.py` -- which is where it was, as a string, until the test
    that holds `core.py` to naming no scene found it.
    """
    chosen = [scene for scene, module in _scenes().items()
              if getattr(module, "DEFAULT", False)]
    if len(chosen) != 1:
        raise RuntimeError(f"exactly one scene module must set DEFAULT = True; "
                           f"these do: {chosen}")
    return chosen[0]


#: `{scene: {name: controller}}`, plus the two shared ones. Every scene has a
#: `good` that must pass and at least one wrong answer that must fail for that
#: scene's own reason -- the rubric is only known to work when both have been
#: watched (AGENTS.md gotcha 24).
def references() -> dict[str, dict]:
    """`{scene id: {name: controller}}`, the per-scene ones only."""
    global _references
    if _references is None:
        _references = {scene: module.REFERENCES
                       for scene, module in _modules(_reference_package).items()}
    return _references


def reference_for(scene: str, kind: str):
    return references().get(scene, {}).get(kind) or SHARED.get(kind)


def reference_choices() -> tuple[str, ...]:
    names = set(SHARED_REFERENCES)
    for table in references().values():
        names |= set(table)
    return tuple(sorted(names))
