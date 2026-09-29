# Packaging a FactoryForge release

*Verified end to end on 2026-08-23 locally, on 2026-08-24 in CI, and again on
2026-09-02 after the nine new parts landed: both platforms build from scratch on
clean runners and pass every headless self-test in the release gate against the
exported binary — **25** of them on that last run. Windows 109 MB + 20 MB
sidecar; Linux 74 MB + 31 MB sidecar.*

*On 2026-09-23 (IP-08) a local Windows build passed the whole gate: every
self-test in `SELF_TESTS` against the exported binary, and the frozen sidecar
grading a scene to PASS and to FAIL with no Python on the machine's PATH. **The
Linux archive has not been built with the grader in it**; it is unverified
until `release.yml` next runs. The list in `check_release.py` is the count;
this paragraph deliberately does not repeat it.*

*On 2026-09-29 (IP-22) a local Windows build at d883a1a passed the whole gate
against the exported binary: all 32 `SELF_TESTS` (including `controlparts`,
`editorkeys` and `handlingparts`, which had never met a binary), the frozen
sidecar's `grade --list` offering all 18 graded scenes (mezzanine-lift among
them), `sorting-by-height` graded to PASS with `good` and to FAIL with `blind`,
`demo`, all 7 drivers usable, and 356 relative links in the shipped pages
resolving. No failures, so no fix was needed. **Linux is still unverified:**
`release.yml` accepts `workflow_dispatch` (a dry run, no release is created),
so a gate run on both platforms needs no tag.*

Running FactoryForge from a checkout means installing Godot, the .NET 8 SDK and
Python first — a real barrier for someone who wanted to learn ladder logic, not
to install a game engine and a compiler. A release exists so that stops being
true.

```bash
python tools/build_release.py
```

That is the whole recipe. It exports the engine, freezes the sidecar, copies the
payload, and writes `dist/FactoryForge-<platform>.zip`. The rest of this file is
what it does and why, for when it breaks.

On Windows, **`build_windows.bat`** does the same thing double-clickably, and
then runs the release gate — because a build that produced a binary is not the
same as a build that produced a working one:

```
build_windows.bat                 engine + frozen sidecar, zipped, checked
build_windows.bat --no-sidecar    engine only; much faster, no PLC drivers
build_windows.bat --skip-archive  leave dist\windows\ unzipped
```

Anything passed to it goes straight to `build_release.py`. It exits non-zero and
says not to ship the archive if either step fails. It pauses at the end so a
double-clicked window does not vanish before you can read the result; set
`FF_NO_PAUSE=1` in CI, where a runner with a real console would otherwise wait
for a key that is never coming.

---

## What you need

| | |
|---|---|
| **Godot 4.7.2 .NET (mono)** | the version the project is built and tested with; `project.godot` asks only for 4.7, so any 4.7.x works |
| **Export templates** | a ~1.2 GB download, once — see below |
| **.NET 8 SDK** | on the machine doing the export, not on the user's |
| **PyInstaller** | `pip install pyinstaller`, to freeze the sidecar |

### Installing the export templates

The editor's *Editor → Manage Export Templates → Download* does this, but it
needs a GUI. Headless, fetch the `.tpz` for your exact Godot version and unpack
it flat into Godot's template directory:

```bash
curl -L -o templates.tpz https://github.com/godotengine/godot/releases/download/4.7.2-stable/Godot_v4.7.2-stable_mono_export_templates.tpz
```

The archive holds a `templates/` folder with a `version.txt` in it — that file's
contents (`4.7.2.stable.mono`) is the directory name Godot looks for. Unpack the
files, without their `templates/` prefix, into:

* Windows — `%APPDATA%\Godot\export_templates\4.7.2.stable.mono\`
* Linux — `~/.local/share/godot/export_templates/4.7.2.stable.mono/`

`tools/build_release.py` does not do this for you: a 1.2 GB download is not
something a build script should start on its own.

---

## The one that will bite you: the solution file

**A .NET export needs `engine/FactoryForge.sln`.** Without it Godot prints

```
ERROR: Export .NET Project: This project contains C# files but no solution file
```

…and then **writes the binary anyway and exits 0**. You get a `FactoryForge.exe`
of the right size, with no `data_FactoryForge_*` folder beside it, whose every
C# script fails at runtime. It looks like a successful build.

`dotnet build` never creates a `.sln` — it only needs the `.csproj` — so a
checkout that has only ever been built from the command line will not have one.
Create it once:

```bash
dotnet new sln -n FactoryForge --format sln
```

`--format sln` matters on .NET 9+: without it you get a `.slnx`, the newer XML
format, which Godot 4.7 does not look for. `tools/build_release.py` refuses to
run if the `.sln` is missing rather than producing the silently-broken binary.

---

## What a release contains

```
FactoryForge-windows.zip
  windows/                             (linux/ in the Linux archive)
    FactoryForge.exe                   the engine, with the .pck embedded
    data_FactoryForge_windows_x86_64/  the .NET assemblies — required
    factoryforge-sidecar.exe           every PLC protocol and the grader, frozen
    examples/                          TIA, OpenPLC and Node-RED examples, mappings
    docs/                              GETTING_STARTED, OPENPLC, GRADING, tag-bus, ...
    README.md, LICENSE, ...            and every page those link to (below)
```

The folder inside the archive is named after the target, `windows/` or
`linux/` — `make_archive` stores paths relative to `dist/`. This listing
said `FactoryForge-windows/` until 2026-09-23, which no archive has ever
contained.

### Every page a shipped page links to ships too

`PAYLOAD` in `build_release.py` is only the seeds. `payload_files` follows
every relative link in every shipped Markdown page — `[x](y)`, `![x](y)`,
`<img src>`, `[ref]: y` — and ships the target, then follows the links in
that, until nothing new turns up (IP-38). Until it did, `docs/OPENPLC.md` and
`docs/GRADING.md` were linked from the first-hour guide and the starters and
were not in the zip. Following `README.md` is what brings in `AGENTS.md`, the
plans, `docs/history/` and the issue forms under `.github/`: the README links
to them, so a reader of the zip can follow those links too.

Two rules keep that from shipping the source tree by accident. The closure adds
only documentation (`.md`, `.txt`, `.yml`, and images); a shipped page linking
to a `.py` or `.cs` file, to a file that does not exist, or out of the
repository stops the build and names the link. And a file keeps its repository
path in the release, which is what lets the same relative link resolve in both.

### Commands a release cannot run are marked, or they fail the gate

The release has no Python, no Godot, no .NET SDK and no `tools/`. A page in
the zip that says `python tools/grade.py` hands someone a command that cannot
run, and until IP-36 every starter README did. `check_release.py` reads every
shipped text file (not `.py`, `.sh` or `.c`, whose usage lines describe the
runtime they need) and fails on `python …`, `pip install`, `pytest`, `dotnet`,
`godot`, `run.py`, `cd sidecar`, `-m factoryforge_sidecar`, or a `tools/…`
script run with arguments. A code line counts, and so does an inline code span
with arguments. A bare `` `tools/grade.py` `` names a file, and that is allowed.

Text for contributors is fine if the reader can tell. Wrap it:

```markdown
<!-- from-source -->
(From a source checkout: `python -m factoryforge_sidecar connect ...`.)
<!-- /from-source -->
```

The markers are HTML comments, so they do not render. They may sit on lines of
their own or inside a line (`… (<!-- from-source -->from a source checkout,
`python …`<!-- /from-source -->) …`), and they nest. The gate refuses a
region whose visible text never uses the word "source": a marker the reader
cannot see is not a label. It also refuses an unclosed marker or a stray close,
so a typo cannot exempt the rest of a file. On a line of its own, the marker
starts an HTML block, which can split a paragraph in two. Inside a line it
cannot, so use that form in the middle of a paragraph.

A page whose whole subject is the source tree (`SOURCE_ONLY_DOCS` in
`tools/packaging/release_text.py`: `AGENTS.md`, the plans, the authoring guides,
`TEST_PLAN`, this file, `docs/history/`, `.github/`) is not edited for this.
The build wraps its shipped copy in one region, under a visible note that its
commands need a clone and a link to Getting Started.

The commands a release *can* run are `.\factoryforge-sidecar …` on Windows and
`./factoryforge-sidecar …` on Linux, typed in the extracted folder. A bare
`factoryforge-sidecar` works in cmd.exe, but PowerShell and a Linux shell do
not look in the current folder, and `./` does not work in cmd, so `.\` is the
Windows form. The grader prints its connect line that way when frozen
(`grading/core.py`, `sidecar_launcher`), and `tools/gen_starters.py` writes
the starters that way. `tests/test_examples.py` checks every generated sidecar
command for the prefix. The gate does not flag a bare
`factoryforge-sidecar`, because prose names the program that way too.

The scene templates — listed in `engine/templates/manifest.json`, which is the
one place that knows how many there are — are `res://` resources and travel
**inside** the engine binary; they are deliberately not in that list. A second
copy travels inside `factoryforge-sidecar`, for the grader; see *How the grader
ships* below.

### One file, or one folder — one folder

`binary_format/embed_pck=true` on both presets, so the `.pck` is inside the
executable. That is as far as "one file" goes: a .NET export always needs its
assemblies folder beside the binary, so a single self-contained executable is
not available. Two items instead of three is the improvement that was actually
on the table, and it removes the `.pck` a user could lose.

### How Python ships

**Frozen with PyInstaller, one executable per platform.** The engine speaks the
tag bus and nothing else — every PLC protocol lives in the Python sidecar — so a
release without one can render a factory and talk to nothing.

The alternative was documenting `pip install -e sidecar`, which pushes an
install step onto exactly the audience least likely to enjoy it. Freezing costs
~20 MB and a few minutes of build time, and is the only option where a person
downloads one archive and connects to a PLC without reading anything.

Two things about the freeze are worth knowing:

* The entry point is `tools/packaging/sidecar_entry.py`, **not**
  `factoryforge_sidecar/__main__.py`. Freezing the module directly strips its
  package context, and its relative imports then raise
  `ImportError: attempted relative import with no known parent package` — but
  only once a command does real work. `--help` still prints, which makes the
  break easy to ship.
* **The frozen binary carries whichever optional drivers were importable when
  it was built**, and this is easy to get wrong quietly. Each protocol driver
  guards its own third-party import and registers *regardless*, so it can
  explain itself at connect time rather than vanishing from the CLI. A release
  built without an extra therefore ships a driver that is present, listed in
  `--help`, and dead.

  The first CI run did exactly this: the workflow installed `sidecar[opcua]`,
  so both archives had a working OPC UA path and no S7 or PLCSIM — two of the
  three Siemens routes the README headlines. Build with
  `pip install -e "sidecar[opcua,siemens,plcsim,mqtt]"`, which is what the workflow
  now does.

  `factoryforge-sidecar drivers` reports what a build can genuinely run, and
  `check_release.py` asks it before letting a release out. Note that neither
  `connect --help` nor the driver registry answers this — the first is a
  hardcoded string, the second lists registered drivers whether or not their
  dependency arrived.

### How the grader ships

`factoryforge-sidecar grade` is the grader (`factoryforge_sidecar.grading`,
the same `main` that `python tools/grade.py` runs), so someone with only the
zip can mark a program: same flags, same output, same JSON, same exit codes
(0 PASS, 1 FAIL, 2 ERROR, 3 DISQUALIFIED). It was not in v1.0.0 (IP-08).

The grader is harder to freeze than the drivers, for two reasons, and each one
produces a binary that starts, prints `--help`, and marks nothing:

* **Its scenes are found, not imported.** `grading/registry.py` lists the
  modules in `grading/scenes/` and `grading/reference/` at runtime, and
  nothing imports them by name, so a freezer that only follows imports leaves
  every one of them out. `--collect-submodules factoryforge_sidecar` is what
  brings them in — and it is evaluated by *importing the package in the Python
  that runs PyInstaller*, before `--paths` applies. If that Python cannot
  import the package, it collects nothing and says nothing; if it imports a
  different copy (an editable install of another checkout), it collects that
  copy's module list. `build_release.py` therefore runs PyInstaller with this
  checkout's `sidecar/` first on `PYTHONPATH`, and before freezing asks
  `collect_submodules` itself and refuses unless every `.py` under
  `factoryforge_sidecar/` is in the answer. Local builds before this had
  exactly that gap — the package was not installed in the Python that froze
  it — and the drivers survived only because `drivers/__init__.py` imports
  each of them by name.
* **It reads the templates from disk.** Each plant model takes its belt
  speeds, pump ratings and deck lengths from `engine/templates/*.json`
  (`grading/templates.py`, IP-19). The exported engine keeps those inside its
  `.pck`, where Python cannot read them, so the freeze bundles the directory
  with `--add-data <repo>/engine/templates<sep>engine/templates`. The
  destination is fixed: `templates.py` looks for
  `sys._MEIPASS/engine/templates` in a frozen build. `<sep>` is
  `os.pathsep` — `;` on Windows, `:` on Linux — which is how the same line
  builds on both runners without `release.yml` knowing about it.

`templates.py` argues against shipping a copy of the templates, because a copy
drifts. This one cannot: it is taken from the same checkout, in the same build,
as the engine export whose `.pck` holds the other copy.

Missing either one, `grade` says which on the grader's own ERROR line and
exits 2 — "no modules found in factoryforge_sidecar.grading.scenes; in a frozen
build, they were not collected", or the directories it searched for the
templates — rather than a traceback. `FACTORYFORGE_TEMPLATES` still overrides
the bundled copy, for an instructor marking against a template they changed.

`demo` works in a release for the first time with this change. It used to
import its scene from the checkout's `harness/` directory, found from its own
file's location, and a sidecar frozen from v1.0.0 fails on its first line with
`ModuleNotFoundError: No module named 'engine_stub'`. It imports from the
package now.

### How the engine finds it

`SidecarLocator` looks in order: the `FACTORYFORGE_SIDECAR` environment
variable, then beside the binary, then beside `engine/` (a checkout), then the
executable's directory. A frozen `factoryforge-sidecar[.exe]` wins over a source
`sidecar/` package in the same directory, because the one that needs no Python
is the one to run.

`--self-test=sidecar` reports which it found and how it would start it. Run it
against a release and it should say `Bundled`; against a checkout, `Source`.

---

## Verifying a release

Every engine self-test runs against the exported binary — that is the point of
them being headless:

```bash
./dist/windows/FactoryForge.exe --headless -- --self-test=scenes
```

All of them passed against the packaged Windows build on the last run,
including the two that read
checked-in fixtures. Those two used to fail in an export for two separate
reasons, both now fixed: the fixtures lived outside `res://` at a path that does
not exist beside a binary, and even once moved in, `System.IO.File` cannot read
inside a `.pck` — only Godot's `FileAccess` can (see `engine/src/Sim/FixtureFile.cs`).

### The frozen sidecar is run, not just listed

`check_release.py` also runs `factoryforge-sidecar` the way someone with only
the zip would: from a scratch directory, with `FACTORYFORGE_TEMPLATES`,
`PYTHONPATH` and `PYTHONHOME` removed, so the only modules and templates it can
find are its own.

* `drivers` — every expected protocol driver usable.
* `grade --list` — exactly the scenes the checkout's `grading/scenes/` defines,
  read from their `SCENE = "..."` lines.
* `grade --scene sorting-by-height --reference good --lockstep --seed 1` must
  exit 0 with a JSON report saying PASS, and `--reference blind`, a program
  on a stopwatch, must exit 1 with FAIL on the `sort.*` checks and nothing
  failed under `controller.*` or `integrity.*`. Both references, because a
  grader that can only PASS or can only FAIL has not been tested.
* `demo --driver mock --duration 3` — the scene runs, shuts down, exit 0, no
  traceback.

Each check was watched failing before it was trusted (AGENTS.md gotcha 24):
built without the templates `--add-data`, every `grade` check failed with the
grader's "cannot find them" message; built with a Python that could not import
the package, every `grade` check failed with "they were not collected"; and
the sidecar frozen from v1.0.0 failed every check but `drivers` (`grade` does not exist there,
and `demo` dies on `No module named 'engine_stub'`). In the first two builds
the `drivers` check still passed, which is why listing drivers was never
going to catch either.

To iterate on the freeze alone, `check_release.py --target windows
--sidecar-only` runs only these checks. It prints that it is not a release
verdict, and it is not one.

### The shipped text is read, from the archive

The gate reads `dist/FactoryForge-<target>.zip`, which is what a user
downloads, or the staged folder if the build skipped the archive, and it says
which one it read. It checks the two things described under *What a release
contains*: no command the release cannot run outside a from-source region, and
no relative link to a file the archive does not hold. It names every offending
`file:line`. `check_release.py --target windows --text-only` runs only these
two checks, and like `--sidecar-only` it is not a release verdict.
`tests/test_release_text.py` runs the same reader over the file set
`payload_files` computes from the checkout, so a doc edit that would fail the
gate fails in pytest first, with no Godot needed.

Both checks were watched failing on 2026-09-24 against a real Windows archive
(gotcha 24). With `python tools/grade.py --list` planted in the zip's
`docs/GETTING_STARTED.md`, the gate failed on exactly that line. With
`docs/OPENPLC.md` removed from the zip, it failed with the 20 pages that link
to it. The unmodified archive passed both, and then passed the whole gate.

---

## Code signing — not signed, and the download page says so

Releases are **not** signed. Windows SmartScreen will warn on first run, and the
user has to click *More info → Run anyway*.

That is a deliberate choice, not an oversight. An OV code-signing certificate
runs a few hundred dollars a year and requires an identity that an
individual-authored open project may not want to maintain; an EV one needs
hardware. The honest alternative to paying for it is saying plainly that the
warning is expected and why — which the README does — rather than leaving a
first-time user to guess whether the download is safe.

Revisit if the project ever distributes through a channel where the warning
blocks installation outright rather than just alarming.

---

## Known gaps

- ~~The Linux binary is built but not run here.~~ **Checked on 2026-08-24**:
  the release workflow builds it on `ubuntu-latest` and runs 24 self-tests
  against the exported binary. Both platforms passed on the first run.
- **macOS is not packaged.** No preset exists and it cannot be tested from here.
  `TerminalLauncher` already handles macOS terminals when someone picks it up.
- **The frozen sidecar is per-platform.** A Windows release cannot ship the
  Linux one; each platform's archive has to be built on that platform, which is
  what the CI matrix is for.

---

*FactoryForge — developed by Mahamed Algaroshy (محمد الجروشي).*
