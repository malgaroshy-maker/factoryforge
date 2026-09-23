"""The scene templates the engine opens, read by the plant models (IP-19).

A plant model in `grading/scenes/` is a second simulation of a scene the
engine already simulates, and the two only mark the same exercise while they
agree about the plant. Until IP-19 every number the models took from a
template -- a belt's speed, a pump's rating, a tank's capacity -- was retyped
from `engine/templates/*.json` by hand, with a comment saying where it came
from. Retune the pump in the template and the engine pumped at the new rate
while the grader went on marking against the old one, and nothing noticed.
This is the tag-bus parity problem one level up, and it gets the same answer:
one source, read by both sides.

So a scene module asks for its template by scene id and reads what it needs by
part id:

    plant = template(SCENE)
    BD_RATED_FIRST = plant.part("pump", "DosingPump").number("rated_flow")

The part's type is checked along with its id, because a model of a dosing pump
means nothing against a part that has been swapped for something else, and
every property is required: a missing one is an error naming the file, the part
and the key, never a default guessed on the grader's side. The C# part has its
own default for an absent property, and a second copy of it here would be one
more number to drift.

Numbers that are *not* in a template -- a carton's size lives in `BoxPhysics.cs`,
a motor's inrush in `MotorStarter.cs` -- stay named constants in the scene
that uses them, each citing the file it mirrors. `tests/test_grade_templates.py`
checks those against the C# source, and checks every graded scene's tag table
against the tag set the engine registers.

**Where the templates are found.** The engine's `engine/templates/` is the one
copy. The grader reads it in place rather than shipping a second copy as
package data, because a copy is exactly the thing that drifts: the grader
would be marking against whatever was copied the day the wheel was built,
which is the bug this module exists to remove. The price is that the grader
needs to be able to see that directory, and it looks in this order:

1. `FACTORYFORGE_TEMPLATES`, if it is set: a directory holding `manifest.json`
   and the templates, exactly as `engine/templates/` does. The same override
   pattern the engine's `SidecarLocator` uses for `FACTORYFORGE_SIDECAR`.
2. In a frozen build (PyInstaller), `engine/templates/` inside the bundle. The
   release's engine keeps its templates inside the exported binary as
   `res://` resources, where Python cannot read them, so a frozen grader
   (IP-08) has to bundle the directory itself -- `--add-data` with the
   destination `engine/templates`.
3. In a checkout, `engine/templates/` at the root of the repository, found
   from this file's own location.

If none of those holds a manifest, the error says where it looked and what to
set. A `pip install` of the sidecar outside a checkout lands in case 1: it
finds no templates on its own, and says so, rather than marking against numbers
it made up.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

__all__ = ["TEMPLATES_ENV", "TemplateError", "Part", "Template", "template_dir",
           "manifest", "template"]

#: The override, checked first. See the module docstring.
TEMPLATES_ENV = "FACTORYFORGE_TEMPLATES"

#: Where the engine's own templates live, relative to the repository root and,
#: in a frozen build, relative to the bundle.
_RELATIVE = Path("engine") / "templates"

#: A template's path in the manifest is a Godot resource path.
_RES_PREFIX = "res://templates/"


class TemplateError(RuntimeError):
    """A template the grader needs is missing, or is not the plant it models."""


def _candidates() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    override = os.environ.get(TEMPLATES_ENV)
    if override:
        found.append((f"${TEMPLATES_ENV}", Path(override)))
        # An override that is set and wrong is an error, not a hint: falling
        # through to the checkout would mark against templates the person
        # running it explicitly said not to use.
        return found
    bundle = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle:
        found.append(("the frozen bundle", Path(bundle) / _RELATIVE))
        # No checkout fallback when frozen: __file__ lives in the bundle's
        # temporary directory, so "three levels up" is somewhere under %TEMP%
        # (found in IP-08), and a stray engine/templates there would be
        # marked against silently.
        return found
    # grading/ -> factoryforge_sidecar/ -> sidecar/ -> the repository root.
    found.append(("the checkout", Path(__file__).resolve().parents[3] / _RELATIVE))
    return found


def template_dir() -> Path:
    """The directory holding `manifest.json` and the scene templates."""
    tried = _candidates()
    for _, path in tried:
        if (path / "manifest.json").is_file():
            return path
    looked = "; ".join(f"{where}: {path}" for where, path in tried)
    raise TemplateError(
        f"the grader reads each scene's plant from the engine's templates and "
        f"cannot find them (looked in {looked}). Run it from a FactoryForge "
        f"checkout, or set {TEMPLATES_ENV} to a directory holding manifest.json "
        f"and the scene templates -- engine/templates/ in the repository")


def _read_json(path: Path):
    # UTF-8 explicitly: the manifest's briefs carry non-ASCII text, and the
    # platform default on Windows is not UTF-8.
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TemplateError(f"cannot read {path}: {exc}") from exc


@lru_cache(maxsize=None)
def _manifest(directory: Path) -> tuple[dict, ...]:
    return tuple(_read_json(directory / "manifest.json"))


def manifest() -> tuple[dict, ...]:
    """The engine's `manifest.json`: one entry per shipped scene."""
    return _manifest(template_dir())


@dataclass(frozen=True)
class Part:
    """One placed part, as the template places it. Values as the scene file
    stores them: position and rotation as floats, properties as strings."""

    id: str
    type: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    properties: dict = field(hash=False)
    source: str = ""

    def _where(self) -> str:
        return f"{self.source}: part {self.id!r} ({self.type})"

    def number(self, key: str) -> float:
        """A numeric property. Absent is an error, never a guessed default."""
        if key not in self.properties:
            raise TemplateError(f"{self._where()} has no {key!r} property, and "
                                f"the grader's model of it needs one")
        try:
            return float(self.properties[key])
        except (TypeError, ValueError) as exc:
            raise TemplateError(f"{self._where()}: {key!r} is "
                                f"{self.properties[key]!r}, not a number") from exc

    @property
    def x(self) -> float:
        """Where the part sits along the line.

        Every model here is one-dimensional and runs along +X, the way every
        graded template lays its line out, and a carton's position in a model
        is its world X. The emitter's own X is where a carton starts.
        """
        return self.position[0]

    def span(self) -> tuple[float, float]:
        """A deck's two ends along X: its centre plus and minus half its
        `size_x`. Refused for a rotated part, whose deck would not run along
        the line the model assumes."""
        if any(abs(r) > 1e-9 for r in self.rotation):
            raise TemplateError(f"{self._where()} is rotated {list(self.rotation)}; "
                                f"the grader's models assume a line along +X")
        half = self.number("size_x") / 2
        return self.x - half, self.x + half

    def engineering_units(self) -> "Part":
        """Assert this part's analog input is published in engineering units.

        IP-16 lets an analog input publish raw S7 counts instead, as an `int`.
        The plant models publish floats in engineering units, which is the
        default and what every graded template uses today -- but a default is
        an assumption, and this makes it a checked one. A template switched to
        raw counts is a scene the grader cannot mark until its model learns
        to publish them.
        """
        mode = self.properties.get("signal", "engineering")
        if mode != "engineering":
            raise TemplateError(f"{self._where()} publishes its analog input as "
                                f"{mode!r}; the grader's model publishes "
                                f"engineering units only")
        return self


@dataclass(frozen=True)
class Template:
    """A scene template, its parts by id."""

    scene: str
    path: Path
    parts: dict = field(hash=False)

    def part(self, part_id: str, part_type: str) -> Part:
        """The part placed as `part_id`, which must be a `part_type`."""
        found = self.parts.get(part_id)
        if found is None:
            raise TemplateError(f"{self.path.name} places no part {part_id!r} "
                                f"(it has {', '.join(sorted(self.parts))}); the "
                                f"grader's model of {self.scene} reads one")
        if found.type != part_type:
            raise TemplateError(f"{self.path.name}: part {part_id!r} is a "
                                f"{found.type}, and the grader models it as a "
                                f"{part_type}")
        return found


@lru_cache(maxsize=None)
def _template(directory: Path, scene: str) -> Template:
    entries = [e for e in _manifest(directory) if e.get("id") == scene]
    if not entries:
        raise TemplateError(f"{directory / 'manifest.json'} has no scene {scene!r}")
    resource = entries[0].get("path") or ""
    if not resource.startswith(_RES_PREFIX):
        raise TemplateError(f"scene {scene!r} is not built from a template "
                            f"(manifest path {resource!r}), so there is nothing "
                            f"to read its plant from")
    path = directory / resource[len(_RES_PREFIX):]
    data = _read_json(path)
    parts: dict[str, Part] = {}
    for raw in data.get("parts", []):
        part = Part(id=raw["id"], type=raw["type"],
                    position=tuple(float(v) for v in raw["position"]),
                    rotation=tuple(float(v) for v in raw.get("rotation", (0, 0, 0))),
                    properties=dict(raw.get("properties") or {}),
                    source=path.name)
        if part.id in parts:
            raise TemplateError(f"{path.name} places two parts called {part.id!r}")
        parts[part.id] = part
    return Template(scene=scene, path=path, parts=parts)


def template(scene: str) -> Template:
    """The template the engine opens for `scene`, the id the manifest uses."""
    return _template(template_dir(), scene)
