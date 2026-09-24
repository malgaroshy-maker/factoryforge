"""What the release says, against what the release has (IP-36, IP-38).

`tools/packaging/release_text.py` is the release gate's reader of shipped text:
it finds commands the extracted folder cannot run (`python ...`, `tools/...`,
`pip install`, `godot`) outside a marked from-source region, and relative
links to files the folder does not have. `check_release.py` runs it against
the built archive; this file runs it against the same file set computed from
the checkout (`build_release.payload_files`, no Godot needed), so a doc edit
that would fail the gate fails here first -- and holds the reader itself to
the cases it must and must not flag, each one seen failing (gotcha 24).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "packaging"))
import release_text as rt  # noqa: E402


def _build_release():
    if "build_release" not in sys.modules:
        spec = importlib.util.spec_from_file_location("build_release",
                                                      ROOT / "tools" / "build_release.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["build_release"] = module
        spec.loader.exec_module(module)
    return sys.modules["build_release"]


def _flagged(text: str, rel: str = "docs/X.md") -> list[int]:
    return [line for line, _ in rt.command_problems(rel, text)]


# --- commands -----------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "```bash\npython tools/grade.py --scene x\n```\n",
    "```\npython -m factoryforge_sidecar connect\n```\n",
    "```\ncd sidecar && factoryforge-sidecar demo\n```\n",
    "```\npip install -e sidecar\n```\n",
    "```\ngodot --path engine/\n```\n",
    "```\ntools/grade.py --list\n```\n",
    "```\ndotnet build\n```\n",
    "```\n$ pytest -q\n```\n",
    "Run `python tools/grade.py --scene x` in place of the scene.\n",
    "Run `tools/grade.py --scene x` in place of the scene.\n",
    "Then python run.py starts it.\n",
    "    python -m factoryforge_sidecar demo\n",
])
def test_a_command_the_release_cannot_run_is_flagged(text):
    assert _flagged(text), text


@pytest.mark.parametrize("text", [
    "```\n.\\factoryforge-sidecar grade --scene sorting-by-height\n```\n",
    "```\n./factoryforge-sidecar connect --driver modbus-tcp -o port 5502\n```\n",
    "The reference controllers stay Python.\n",
    "`tools/grade.py` is the source-tree shim over the same grader.\n",
    "It is what `pytest` prints.\n",
    "```\ncd OpenPLC_v3/webserver && ./scripts/compile_program.sh Sorting.st\n```\n",
    "Godot 4.7 renders it.\n",
])
def test_what_the_release_can_run_or_only_names_a_file_is_not(text):
    assert not _flagged(text), text


def test_a_program_file_is_not_read_as_instructions():
    assert not _flagged('"""Usage: python fake_plc.py"""\n', "examples/fake_plc.py")
    assert _flagged("(* run python tools/grade.py *)\n", "examples/x.st")


def test_a_json_string_is_read_too():
    assert _flagged('{"_usage": "cd sidecar && python -m factoryforge_sidecar connect"}\n',
                    "examples/m.json")


# --- the marker ---------------------------------------------------------------

O, C = rt.MARK_OPEN, rt.MARK_CLOSE


def test_a_marked_block_is_exempt_and_the_rest_of_the_file_is_not():
    text = (f"{O}\nFrom a source checkout:\n\n```\npython run.py\n```\n{C}\n\n"
            "```\npython run.py\n```\n")
    assert _flagged(text) == [10]


def test_a_marked_phrase_inside_a_line_is_exempt():
    text = f"Press Try ({O}from a source checkout, `python tools/try_scene.py --scene x`{C}).\n"
    assert _flagged(text) == []
    # ... and only the phrase: the same command after it is still flagged.
    assert _flagged(text.replace(").", ") or `python tools/try_scene.py --list`.")) == [1]


def test_a_marker_must_say_source_where_the_reader_can_see_it():
    problems = rt.command_problems("docs/X.md", f"{O}\n```\npython run.py\n```\n{C}\n")
    assert len(problems) == 1 and "source" in problems[0][1], problems
    # A comment does not count as saying it.
    problems = rt.command_problems("docs/X.md",
                                   f"{O}<!-- source -->\n```\npython run.py\n```\n{C}\n")
    assert len(problems) == 1, problems


def test_an_unclosed_or_stray_marker_is_refused_rather_than_exempting_the_rest():
    unclosed = rt.command_problems("docs/X.md", f"text\n{O}\nsource\npython run.py\n")
    assert any("never closed" in m for _, m in unclosed), unclosed
    stray = rt.command_problems("docs/X.md", f"{C}\n")
    assert stray and "no" in stray[0][1], stray


def test_markers_nest():
    text = f"{O}\nsource\n{O}\npython run.py\n{C}\npython run.py\n{C}\n"
    assert rt.command_problems("docs/X.md", text) == []


def test_the_source_only_banner_exempts_a_whole_page_and_says_why():
    head, tail = rt.source_only_banner("docs/history/PLAN.md")
    wrapped = head + "```\npython tools/test_plan.py\n```\n" + tail
    assert rt.command_problems("docs/history/PLAN.md", wrapped) == []
    assert "(../GETTING_STARTED.md)" in head
    assert rt.dead_links({"docs/history/PLAN.md": wrapped},
                         {"docs/history/PLAN.md", "docs/GETTING_STARTED.md"}) == []


# --- links --------------------------------------------------------------------

def test_a_dead_link_is_named_with_its_file_and_line():
    files = {"docs/A.md": "# A\n\nSee [B](B.md) and [C](../docs/C.md#x).\n"}
    dead = rt.dead_links(files, {"docs/A.md", "docs/B.md"})
    assert dead == ["docs/A.md:3: ../docs/C.md#x -- no docs/C.md in the release"], dead


def test_links_that_are_not_relative_files_are_left_alone():
    text = ("[w](https://example.com/x.md) [a](#anchor) [m](mailto:a@b.c)\n"
            "`[not](a-link.md)`\n\n```\n[not](either.md)\n```\n")
    assert rt.relative_links("docs/A.md", text) == []


def test_a_folder_link_is_live_when_the_folder_ships_and_a_climb_out_is_dead():
    files = {"examples/README.md": "[o](openplc/) [up](../../x.md)\n"}
    present = {"examples/README.md", "examples/openplc/Sorting.st"}
    dead = rt.dead_links(files, present)
    assert dead == ["examples/README.md:1: ../../x.md -- climbs out of the release"], dead


def test_images_and_reference_definitions_are_links_too():
    text = '![demo](images/d.gif)\n<img src="images/e.png">\n\n[ref]: images/f.png\n'
    targets = [r for _, _, r in rt.relative_links("docs/A.md", text)]
    assert targets == ["docs/images/d.gif", "docs/images/e.png", "docs/images/f.png"], targets


# --- the checkout, the way the build will ship it -----------------------------

def _shipped() -> dict[str, str]:
    br = _build_release()
    return {rel: br.payload_bytes(rel, src).decode("utf-8")
            for rel, src in br.payload_files().items() if rt.is_text(rel)}


def test_the_release_ships_the_docs_its_pages_link_to():
    """IP-38: OPENPLC.md and GRADING.md, linked from shipped pages, were not
    in the zip."""
    files = _build_release().payload_files()
    for doc in ("docs/OPENPLC.md", "docs/GRADING.md", "docs/GETTING_STARTED.md"):
        assert doc in files, doc
    shipped = _shipped()
    assert rt.dead_links({r: t for r, t in shipped.items() if r.endswith(".md")},
                         set(files)) == []


def test_dropping_a_linked_doc_is_a_dead_link():
    """What the gate sees when a build leaves a linked page out."""
    files = _build_release().payload_files()
    shipped = _shipped()
    present = set(files) - {"docs/OPENPLC.md"}
    dead = rt.dead_links({r: t for r, t in shipped.items()
                          if r.endswith(".md") and r in present}, present)
    assert dead and all("docs/OPENPLC.md" in d for d in dead), dead


def test_every_command_the_release_shows_runs_from_the_release():
    """IP-36, on the checkout: the same check the gate makes on the zip."""
    problems = [f"{rel}:{line}: {message}"
                for rel, text in sorted(_shipped().items())
                for line, message in rt.command_problems(rel, text)]
    assert not problems, "\n".join(problems[:40])


def test_a_planted_python_command_in_a_shipped_doc_is_caught():
    shipped = _shipped()
    text = shipped["docs/GETTING_STARTED.md"] + "\nThen run:\n\n```\npython tools/grade.py --list\n```\n"
    assert rt.command_problems("docs/GETTING_STARTED.md", text)


def test_the_build_refuses_a_link_it_cannot_ship(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "A.md").write_text(
        "[code](../tools/grade.py) [gone](NOPE.md) [ok](B.md)\n", encoding="utf-8")
    (tmp_path / "docs" / "B.md").write_text("[back](A.md)\n", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "grade.py").write_text("", encoding="utf-8")
    added, problems = rt.doc_closure(tmp_path, {"docs/A.md"})
    assert added == {"docs/B.md"}
    assert len(problems) == 2 and any("not documentation" in p for p in problems) \
        and any("no docs/NOPE.md" in p for p in problems), problems
