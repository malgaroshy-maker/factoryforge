"""What a release says, held to what a release has (IP-36, IP-38).

A release is an extracted folder with `FactoryForge[.exe]`,
`factoryforge-sidecar[.exe]`, `examples/` and `docs/` in it -- and no Python,
no Godot, no .NET SDK and no `tools/`. Two ways its text can lie about that:

* **It tells the reader to run something the folder cannot run**
  (`command_problems`): `python tools/grade.py`, `pip install ...`,
  `godot --path engine/`, `cd sidecar && ...`. Every one of those was in the
  zip that IP-09's first-hour walkthrough followed.
* **It links to a file the folder does not have** (`dead_links`):
  `docs/OPENPLC.md` and `docs/GRADING.md` were linked from shipped pages and
  not shipped.

`build_release.py` uses `doc_closure` to ship every document the shipped
documents link to, and `check_release.py` runs both checks against the built
archive.

The "from source" marker
------------------------
Some shipped text is for contributors, and that is fine as long as the reader
can tell. Wrap it in

    <!-- from-source -->
    (From a source checkout: `python -m factoryforge_sidecar` ...)
    <!-- /from-source -->

The two markers are HTML comments, so a Markdown renderer shows nothing of
them. They may sit on lines of their own (a block) or inside a line (a phrase),
and they nest. Everything between them is exempt from the command check -- and
must say so to a human: the region's visible text has to contain the word
"source", or the marker is refused. A marker nobody can see is not a label.
An unclosed marker, or a close with no open, is also refused, so a typo cannot
quietly exempt the rest of a file.

A document whose whole subject is the source tree (`SOURCE_ONLY_DOCS`) is not
edited for this: the build wraps its shipped copy in one region, under a
visible note saying its commands need a clone.

Program files (`.py`, `.sh`, `.c`, ...) are not scanned for commands: a
script's usage text describes the runtime the script needs, which is the
script's business, and the page that points to it says so.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

MARK_OPEN = "<!-- from-source -->"
MARK_CLOSE = "<!-- /from-source -->"

#: Text files whose content is read as instructions. Anything else in the
#: release -- binaries, images, archives -- is skipped.
TEXT_SUFFIXES = {".md", ".txt", ".st", ".scl", ".json", ".cfg", ".yml", ".yaml",
                 ".csv", ".ini", ".toml"}
TEXT_NAMES = {"LICENSE", "README"}
#: Programs. Their own usage lines are about the runtime they need.
PROGRAM_SUFFIXES = {".py", ".sh", ".c", ".h", ".bat", ".cmd", ".ps1", ".cs"}

#: What `doc_closure` may add to a release because a shipped page links to it.
#: A link to anything else outside the shipped trees -- a `.py`, a `.cs` -- is
#: source, not documentation, and the build refuses it rather than quietly
#: shipping part of the source tree.
DOC_SUFFIXES = {".md", ".txt", ".yml", ".yaml", ".png", ".gif", ".jpg", ".jpeg",
                ".svg", ".webp"}

#: Documents about the source tree itself. They ship because shipped pages
#: link to them (IP-38), and every command in them needs a clone. A path
#: ending in "/" covers everything under it.
SOURCE_ONLY_DOCS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/ROADMAP.md",
    "docs/IMPROVEMENT_PLAN.md",
    "docs/HARDENING_PLAN.md",
    "docs/PACKAGING.md",
    "docs/TEST_PLAN.md",
    "docs/PART_AUTHORING.md",
    "docs/DRIVER_AUTHORING.md",
    "docs/PRD.md",
    "docs/history/",
    ".github/",
)


def is_source_only(rel: str) -> bool:
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in SOURCE_ONLY_DOCS)


def is_text(rel: str) -> bool:
    p = PurePosixPath(rel)
    return p.suffix.lower() in TEXT_SUFFIXES or p.name in TEXT_NAMES


def is_program(rel: str) -> bool:
    return PurePosixPath(rel).suffix.lower() in PROGRAM_SUFFIXES


# --- the marker ---------------------------------------------------------------

_MARK = re.compile(re.escape(MARK_OPEN) + "|" + re.escape(MARK_CLOSE))


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def mask_from_source(text: str) -> tuple[str, list[tuple[int, str]]]:
    """*text* with every from-source region blanked (newlines kept, so line
    numbers still match), and the marker problems as `(line, message)`."""
    problems: list[tuple[int, str]] = []
    out = list(text)
    stack: list[int] = []
    for m in _MARK.finditer(text):
        if m.group(0) == MARK_OPEN:
            stack.append(m.start())
            continue
        if not stack:
            problems.append((_line_of(text, m.start()),
                             f"{MARK_CLOSE} with no {MARK_OPEN} before it"))
            continue
        start = stack.pop()
        if stack:
            continue            # nested: the outermost region blanks it all
        end = m.end()
        visible = _MARK.sub(" ", re.sub(r"<!--.*?-->", " ", text[start:end], flags=re.S))
        if not re.search(r"\bsource\b", visible, re.IGNORECASE):
            problems.append((_line_of(text, start),
                             "a from-source region must say so where the reader can see "
                             "it: its text never uses the word \"source\""))
        for i in range(start, end):
            if out[i] != "\n":
                out[i] = " "
    for start in stack:
        problems.append((_line_of(text, start), f"{MARK_OPEN} is never closed"))
    return "".join(out), problems


# --- commands -----------------------------------------------------------------

#: At the head of a command: the first word of a code line, of each `&&`/`;`/`|`
#: segment of one, or of an inline code span that has arguments.
_HEAD_RULES = [
    (re.compile(r"(?:\./)?tools/[\w./-]+"), "tools/ is not in the release"),
    (re.compile(r"(?:\./)?run\.py\b"), "run.py is not in the release"),
    (re.compile(r"python3?(?:\.exe)?\b|py\s+-\d|py\s+-m\b"),
     "the release has no Python"),
    (re.compile(r"pip3?(?:\.exe)?\b"), "the release has no Python to pip-install into"),
    (re.compile(r"pytest\b"), "the release has no test suite and no Python"),
    (re.compile(r"godot(?:\.exe)?\b|Godot_v\S*\.exe"),
     "the release has no Godot; FactoryForge[.exe] is the engine"),
    (re.compile(r"dotnet\b"), "the release has no .NET SDK"),
    (re.compile(r"cd\s+(?:\./)?(?:sidecar|engine|tools|harness)\b"),
     "the release has no sidecar/, engine/, tools/ or harness/ folder"),
]

#: Anywhere at all, prose included: nothing else looks like these.
_ANYWHERE_RULES = [
    (re.compile(r"(?<![\w./\\-])python3?(?:\.exe)?\s+(?:-[mcu]\b|[\w./\\-]+\.py\b)"),
     "the release has no Python"),
    (re.compile(r"(?<![\w-])pip3?\s+install\b"), "the release has no Python to pip-install into"),
    (re.compile(r"-m\s+factoryforge_sidecar\b"),
     "the release runs factoryforge-sidecar, not the Python package"),
]

_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE = re.compile(r"(`+)(.+?)\1")
_PROMPT = re.compile(r"^(?:\$|>|PS[^>\n]*>|[A-Za-z]:\\[^>\n]*>)\s*")
_COMMENT = re.compile(r"^(?:#+|//+|\(\*+|\*+\)?|;+|--|REM\b|::)\s*", re.IGNORECASE)
_SPLIT = re.compile(r"&&|\|\||;|\|")


def _heads(command: str) -> list[str]:
    """The first word of every command in one line of code."""
    line = command.strip()
    for _ in range(3):              # "# $ python ..." and the like
        line = _COMMENT.sub("", _PROMPT.sub("", line)).strip()
    return [seg.strip() for seg in _SPLIT.split(line) if seg.strip()]


def _check_head(segment: str) -> str | None:
    for pattern, why in _HEAD_RULES:
        if pattern.match(segment):
            return why
    return None


def command_problems(rel: str, text: str) -> list[tuple[int, str]]:
    """`(line, message)` for every command in *text* the release cannot run,
    outside from-source regions, plus any marker that is itself wrong."""
    if is_program(rel) or not is_text(rel):
        return []
    masked, problems = mask_from_source(text.replace("\r\n", "\n"))
    markdown = PurePosixPath(rel).suffix.lower() == ".md"
    in_fence = False
    prev_blank = True
    for number, line in enumerate(masked.split("\n"), 1):
        found: list[str] = []
        fence = bool(markdown and _FENCE.match(line))
        if fence:
            in_fence = not in_fence
        elif not markdown or in_fence or (line.startswith(("    ", "\t")) and prev_blank):
            # A line of code: every command head in it counts.
            found += [why for why in map(_check_head, _heads(line)) if why]
        if markdown and not in_fence and not fence:
            # Prose: only an inline code span with arguments is a command;
            # `tools/grade.py` alone is the name of a file.
            for span in _INLINE.finditer(line):
                body = span.group(2).strip()
                if re.search(r"\s\S", body):
                    found += [why for why in map(_check_head, _heads(body)) if why]
        for pattern, why in _ANYWHERE_RULES:
            if pattern.search(line):
                found.append(why)
        if found:
            snippet = line.strip()
            problems.append((number, f"{sorted(set(found))[0]}: {snippet[:120]}"))
        if markdown and not in_fence:
            prev_blank = not line.strip() or (prev_blank and line.startswith(("    ", "\t")))
    return sorted(problems)


# --- links --------------------------------------------------------------------

_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
_REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?", re.MULTILINE)
_HTML = re.compile(r"<(?:img|a|source|video)\b[^>]*?\b(?:src|href)\s*=\s*[\"']([^\"']+)[\"']",
                   re.IGNORECASE)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _without_code(text: str) -> str:
    """Markdown with code blocks and code spans blanked: `[x](y)` inside code
    is not a link."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append("")
        elif in_fence:
            out.append("")
        else:
            out.append(_INLINE.sub(lambda m: " " * len(m.group(0)), line))
    return "\n".join(out)


def relative_links(rel: str, text: str) -> list[tuple[int, str, str]]:
    """`(line, as written, resolved path relative to the release root)` for
    every relative link in a Markdown file *rel*. The resolved path is None
    when the link climbs out of the root. Anchors are not checked; files are."""
    if PurePosixPath(rel).suffix.lower() != ".md":
        return []
    body = _without_code(text.replace("\r\n", "\n"))
    found = []
    for pattern in (_LINK, _REFDEF, _HTML):
        for m in pattern.finditer(body):
            target = m.group(1)
            if _SCHEME.match(target) or target.startswith(("#", "//")):
                continue
            path = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not path:
                continue
            base = "" if path.startswith("/") else posixpath.dirname(rel)
            joined = posixpath.normpath(posixpath.join(base, path.lstrip("/")))
            resolved = None if joined == ".." or joined.startswith("../") else joined
            found.append((_line_of(body, m.start()), target, resolved))
    return sorted(found, key=lambda x: x[0])


def dead_links(files: dict[str, str], present: set[str]) -> list[str]:
    """`file:line: link` for every relative link in *files* (`{rel: text}`)
    that names nothing in *present* (every file path in the release). A link
    to a folder is live when anything is inside it."""
    folders = set()
    for p in present:
        parts = p.split("/")
        folders.update("/".join(parts[:i]) for i in range(1, len(parts)))
    problems = []
    for rel in sorted(files):
        for line, target, resolved in relative_links(rel, files[rel]):
            if resolved is None:
                problems.append(f"{rel}:{line}: {target} -- climbs out of the release")
            elif resolved.rstrip("/") not in present and resolved.rstrip("/") not in folders:
                problems.append(f"{rel}:{line}: {target} -- no {resolved} in the release")
    return problems


# --- what ships ---------------------------------------------------------------

def doc_closure(root: Path, shipped: set[str]) -> tuple[set[str], list[str]]:
    """Every file outside *shipped* (paths relative to *root*) that a shipped
    Markdown page links to, directly or through another page added here. The
    second value is the links the build must refuse: to nothing, out of the
    tree, or to something that is not documentation."""
    added: set[str] = set()
    problems: list[str] = []
    queue = sorted(p for p in shipped if p.endswith(".md"))
    seen = set(queue)
    while queue:
        rel = queue.pop()
        text = (root / rel).read_text(encoding="utf-8")
        for line, target, resolved in relative_links(rel, text):
            where = f"{rel}:{line}: {target}"
            if resolved is None:
                problems.append(f"{where} -- climbs out of the repository")
                continue
            path = root / resolved
            if resolved in shipped or resolved in added:
                continue
            if path.is_dir():
                members = [p.relative_to(root).as_posix() for p in sorted(path.rglob("*"))
                           if p.is_file() and "__pycache__" not in p.parts]
            elif path.is_file():
                members = [resolved]
            else:
                problems.append(f"{where} -- no {resolved} in the repository")
                continue
            for member in members:
                if member in shipped or member in added:
                    continue
                if PurePosixPath(member).suffix.lower() not in DOC_SUFFIXES:
                    problems.append(f"{where} -- {member} is not documentation; link it "
                                    f"on GitHub instead, or ship it on purpose in PAYLOAD")
                    continue
                added.add(member)
                if member.endswith(".md") and member not in seen:
                    seen.add(member)
                    queue.append(member)
    return added, problems


def source_only_banner(rel: str) -> tuple[str, str] | None:
    """The head and tail `build_release.py` wraps a SOURCE_ONLY_DOCS file in,
    or None for a file of a kind it cannot annotate."""
    suffix = PurePosixPath(rel).suffix.lower()
    if suffix == ".md":
        guide = posixpath.relpath("docs/GETTING_STARTED.md", posixpath.dirname(rel) or ".")
        head = (f"{MARK_OPEN}\n"
                "> **This page is about FactoryForge's source code.** It is in the download\n"
                "> because other pages link to it. Its commands need a clone of the source\n"
                "> repository, and some need Python, Godot or the .NET SDK; none of them runs\n"
                f"> from this folder. To use the download, start at [Getting Started]({guide}).\n\n")
        return head, f"\n{MARK_CLOSE}\n"
    if suffix in (".yml", ".yaml"):
        head = (f"# {MARK_OPEN}\n"
                "# Part of FactoryForge's source repository, shipped because a page in the\n"
                "# download links to it. Its commands need a clone of the source; none of\n"
                "# them runs from this folder.\n")
        return head, f"\n# {MARK_CLOSE}\n"
    return None
