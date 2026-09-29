# FactoryForge v1.2 — polish and reach

**Status:** V12-01 … V12-20, all open. Nothing started.
**Written:** 2026-09-29, against `b4c32bb`, the day v1.1.0 shipped. The v1.1
plan is closed and lives in
[`history/IMPROVEMENT_PLAN_v1.1.md`](history/IMPROVEMENT_PLAN_v1.1.md).
**Horizon:** the next release, v1.2.
**Focus (the user's choice):** make it look and feel like a finished product —
generated art through the Higgsfield API where art is what is missing — and put
it in front of more people: an Arabic interface, an installer, a website.

v1.1 made FactoryForge *correct*: a grader in the release, a no-licence first
hour proven on real controllers, every panel scene marked. v1.2 is about the
first thirty seconds — what someone sees when the window opens, and whether
they can install it and read it at all.

---

## What the review found

Each finding is what the repository shows on 2026-09-29, with how it was
measured.

1. **The 3D world is flat-shaded primitives.** `engine/src/Parts/` builds every
   part from `BoxMesh` (159 uses), `CylinderMesh` (97) and `SphereMesh` (36)
   with 271 hand-made `StandardMaterial3D`s. The engine folder holds **one
   image** — the 1.8 KB, 256 px `icon.png` — and no texture, model or font.
   It reads as a diagram. (The demo GIF, re-recorded on this day, is the
   honest picture.)
2. **The hall is a procedural sky and a grey box.** One `WorldEnvironment`,
   one `ProceduralSky`, two lights. Nothing says *factory* beyond the floor
   lines.
3. **The UI is Godot's default font and theme**, with about **231 hard-coded
   English strings** (`Text =`, `new Label`, `TooltipText` in `engine/src`)
   and no localization. Start-screen template cards are text only — 19
   templates, no pictures.
4. **The brand is a placeholder.** The icon is a 256 px drawing; the Windows
   exe takes it as-is; there is no logo, no `.ico`, no social preview image.
5. **Distribution is a zip.** ~100 MB per platform, unsigned (the release notes
   warn about SmartScreen), no installer, no Start-menu entry, no macOS, no
   website.
6. **Repo hygiene: 40 Godot `.uid` files were never committed** while 127 were.
   Godot 4.4+ expects every `.uid` in version control; without them a
   resource's id differs between machines.
7. **Higgsfield is reachable but empty.** The account is on the free plan with
   **0 credits**. A `gpt_image_2_5` image preflights at **0.25 credits**; 3D
   models (Meshy image→3D, Tripo, SAM 3D) must be preflighted per job.
   Nothing in this plan that generates art can start until credits exist.
8. **Left from v1.1:** a program written by a real student (IP-11 b) and a
   scene where a belt feeds the turntable (IP-33).

---

## Ground rules for generated assets

Every item that uses Higgsfield follows these, and a test enforces the
checkable ones.

- **Provenance.** `engine/assets/ASSETS.md` lists every generated file: the
  model, the full prompt, the date, the Higgsfield job id, and the licence
  terms it was generated under. A test fails if a file under `engine/assets/`
  is not listed.
- **Licence first.** The repository is MIT. Before the first asset is
  committed, confirm the plan's output terms allow redistribution in an open
  repository, and record them in `ASSETS.md`. If they do not, stop.
- **Visuals only.** Generated art changes how things look, never how they
  behave. Colliders, part geometry that physics touches, tags and timings stay
  as they are; every self-test, section H and the graded suite must still
  pass unchanged.
- **Look before committing.** Render the scene with and without the asset
  (`--film --camera`, same frame) and compare. A generated texture that reads
  worse than the flat colour is dropped. The GIF colour lesson applies: check
  the real render, not a thumbnail.
- **Budgets.** Textures 1024² at most, imported with VRAM compression; a GLB
  under ~5 MB and ~15 k triangles; the release zip under **130 MB** (it is
  ~100 MB now). V12-19 turns the budget into a gate.
- **Cost.** Every job is preflighted with `get_cost`; each item below states
  its estimate, and the running total is kept in `ASSETS.md`.
- **Students still read the machine.** Colour carries meaning here — a tall
  carton is orange, a short one blue, a lamp's colour is its state. Texturing
  keeps those signals.

### Which model for which job (tested 2026-09-29)

Generation goes through the user's Higgsfield **Cloud API** key, via the
`higgsfield-api` MCP server (a local wrapper around Higgsfield's official
`higgsfield-client` SDK; the key lives in the `HF_KEY` environment variable and
nowhere else). The API offers 16 image models and **no 3D models**. Six general
image models were run on the same four prompts and compared side by side;
textures were also tiled 2×2 to expose seams.

| Job | Use | Runner-up | Avoid, and why |
|---|---|---|---|
| Seamless textures | `marketing-studio/image/flare` — realistic speckled concrete, tiles cleaner than Grok, 48 s | `xai/grok-imagine-image-2.0`; `marketing-studio/image` (most seamless, but bland) | Ideogram (paints the repeats into the image), Recraft (reads as galvanised metal) |
| Logo / icon | `marketing-studio/image/flare` — the most polished gear-box-conveyor mark, two-tone | `marketing-studio/image`, Grok | `z-image/turbo` (wrote text into a "no text" icon) |
| Flat printed decals (labels on 3D cartons) | `recraft/v4.1/text-to-image` — every line right, flat print, 14 s | `marketing-studio/image/sunburst` (exact, richer design) | `z-image/turbo` (garbled barcode text, sideways arrows) |
| Hero / site / README art | `marketing-studio/image/flare` — detailed, believable, shows the gantry and chute, 33 s | `marketing-studio/image/sunburst`, Grok | — |

Ten models were compared on the same four prompts: Recraft V4.1 and V4.1 Pro,
Ideogram 4.0, Qwen Image 3, Z-Image Turbo, Grok Imagine 2.0, and Marketing
Studio Image in its base, Flare and Sunburst variants. Marketing Studio Flare
won three of the four and is among the fastest (33–48 s; the base variant took
94–196 s, Grok 70–96 s). The API does not report a job's price; read it from
the console's usage page. Qwen and Z-Image were intermittently "temporarily
unavailable" (not charged).

---

## Phase A — the look (Higgsfield)

**V12-01 — A material library, with real surfaces** · gate
*Files:* `engine/src/View/MaterialLibrary.cs` (new), `engine/src/Parts/*`,
`engine/assets/textures/`, `tools/make_pbr.py` (new).
*Done when:* parts take their materials from one library instead of building
their own: concrete floor, painted steel frame, brushed steel, belt rubber,
roller chrome, pallet wood, safety yellow/black, guard glass. Each is a
generated seamless albedo; normal and roughness maps are derived from it
locally by `tools/make_pbr.py`, so only one image per material is generated.
*Verify:* before/after renders of three templates; `--self-test=scene`,
`templates`, `layout`; section H 19/19.
*Size:* L. *Credits:* ~10 materials × up to 4 tries × 0.25 ≈ **10**.

**V12-02 — Cartons that look like cartons** · gate
*Files:* the carton/emitter code, `engine/assets/textures/cartons/`.
*Done when:* cartons are cardboard with tape and printed labels, 6–8 label
designs, and the tall/short colour survives as a coloured band or label so a
student can still tell them apart at a glance. The barcode scene's cartons
carry a visible barcode.
*Verify:* renders of sorting, pick-and-place and the scanner scene.
*Size:* M. *Credits:* ≈ **5**.

**V12-03 — A hall, not a box** · gate
*Files:* the environment setup in `engine/src/View/`, `engine/assets/`.
*Done when:* walls, roof trusses and floor read as a factory hall: a generated
2:1 industrial-hall panorama as the background/ambient, textured walls, and
lighting retuned to it. Headless and `--deterministic` runs are unaffected.
*Verify:* renders; frame time on the heaviest template before/after (V12-19).
*Size:* M. *Credits:* ≈ **3**.

**V12-04 — Set dressing from generated 3D models**
*Files:* `engine/assets/models/`, templates, a `Dressing` node type.
*Done when:* 4–6 props — an electrical cabinet, an operator desk with a
monitor, pallet racking, a forklift, safety fence panels, a person for scale —
are generated (image first, then Meshy image→3D with texture and PBR, 5–15 k
triangles), sized in metres, and placed around at least five templates as
non-interactive decoration that the property inspector can hide. Each model
is rendered and dropped if it reads badly.
*Verify:* renders; frame time; scene save/load round-trip keeps them.
*Size:* L. *Credits:* preflight each; recorded in `ASSETS.md`.
*Blocked on the API:* the Cloud API has no 3D models. This item needs the
separate Higgsfield app account (the OAuth `higgsfield` connection, which has
Meshy and Tripo image→3D) to hold credits, or it is dropped from v1.2.

**V12-05 — Better housings for static parts** (stretch)
Replace the static shells of a few parts — stack light, control panel,
photoelectric sensor head — with models, keeping every moving piece and every
collider as it is. Only if V12-04 shows the model quality holds up.
*Size:* L.

---

## Phase B — identity and interface

**V12-06 — A logo and a real icon** · gate
*Files:* `engine/icon.png`, `engine/icon.ico` (new), `engine/export_presets.cfg`,
`docs/images/`.
*Done when:* the user has picked one of 3–4 generated logo concepts; it is
cleaned into a 1024 px master, a multi-size Windows `.ico` wired into the
export preset, the Linux icon, a README banner and a 1280×640 social preview.
*Needs the user:* the choice of logo. Uploading the social preview is a
repository setting the user does in GitHub's UI.
*Size:* S. *Credits:* ≈ **3–5**.

**V12-07 — Template thumbnails from the real engine** · gate
*Files:* `tools/record_thumbnails.py` (new), the start screen, `engine/assets/thumbnails/`.
*Done when:* every start-screen card shows a picture of its scene, rendered by
the engine with the filming flags (`--film --camera`), not generated — a card
must show the scene the student will get. Re-rendering is one command.
*Verify:* `--self-test=templates` and `layout`; a check that every template has
a thumbnail.
*Size:* M. *Credits:* 0.

**V12-08 — A theme and a font** · gate
*Files:* `engine/assets/fonts/`, a Godot `Theme` resource, the UI code.
*Done when:* the UI uses an embedded OFL font covering Latin and Arabic (for
example Noto Sans with Noto Sans Arabic, or IBM Plex Sans Arabic), one theme
for colours and spacing, and toolbar/palette icons from one open icon set
(Lucide or Material Symbols, licence recorded) — not generated, because a
generated icon set does not stay consistent.
*Verify:* `--self-test=layout`; renders of the start screen and editor.
*Size:* M. *Credits:* 0.

**V12-09 — An Arabic interface** · gate
*Files:* every UI string in `engine/src`, `engine/assets/i18n/` (en/ar), the
start screen.
*Done when:* the ~231 UI strings go through Godot's `TranslationServer`, an
Arabic translation exists for each, a language switch on the start screen is
remembered, and panels lay out right-to-left in Arabic. Tag names, part ids
and code stay English: they are what a PLC program uses.
*Verify:* a self-test that every key has an Arabic string and that Arabic
renders shaped (the credit line already does — the check exists in spirit);
renders in both languages.
*Needs the user:* a native read of the Arabic, as for `README.ar.md`.
*Size:* L.

**V12-10 — A first-run welcome**
*Done when:* the first launch shows a three-step overlay — pick a template, F1
for Run mode, F5 to connect a PLC — dismissed for good with one click, in both
languages.
*Size:* S.

---

## Phase C — reach

**V12-11 — A Windows installer** · gate
*Files:* `tools/packaging/installer.iss` (new), `release.yml`, `docs/PACKAGING.md`.
*Done when:* the release carries a per-user Inno Setup installer (no admin
rights) beside the zip: Start-menu and optional desktop shortcut, a clean
uninstall, the sidecar and examples in place. The release gate runs its
smoke test against the installed copy too.
*Size:* M.

**V12-12 — Code signing** (decision)
Unsigned builds make SmartScreen warn on first run. Options: SignPath
Foundation (free for open source, by application), Azure Trusted Signing
(about $10 a month plus identity validation), or stay unsigned and keep the
warning documented. *Needs the user.*
*Size:* S once decided.

**V12-13 — macOS** (decision)
A Godot macOS export plus a PyInstaller build on a `macos-latest` runner.
Unsigned, it opens only through right-click → Open; notarizing needs an Apple
Developer account ($99 a year). PLCSIM is Windows-only, but OpenPLC and the
grader work. *Needs the user.*
*Size:* L.

**V12-14 — A website** · gate
*Files:* `site/` (new), a Pages workflow.
*Done when:* a GitHub Pages site in English and Arabic (right-to-left) shows
the demo video, what FactoryForge is, download buttons for the latest release,
and links into the first-hour guide; the hero art is generated (V12-06's
identity). It deploys from `master`.
*Size:* M. *Credits:* ≈ **3**.

**V12-15 — README, topics and release notes**
The V12-06 banner at the top of both READMEs; repository topics (a settings
change, done with the user's OK); release notes written in both languages.
*Size:* S.

**V12-16 — Re-record the demo** · gate
Run `tools/record_demo.py` once the new look is in, so the README shows it.
*Size:* S.

---

## Phase D — hygiene and leftovers

**V12-17 — Commit every `.uid`** · gate
Commit the 40 missing `.uid` files and add a test-plan check that fails when a
`.cs` or resource under `engine/` has no tracked `.uid`.
*Size:* S. Do this first: every later item adds files.

**V12-18 — A belt-fed turntable scene** (from IP-33)
A scene where a belt delivers onto the turntable's deck, its grader (the deck
drive is already modelled, `plant.deck_lane_velocity`), references, and a
section H trial.
*Size:* M.

**V12-19 — Size and speed as a gate** · gate
Measure frame time on the heaviest template and the release zip size before
and after the new assets; fail the release gate over 130 MB, and record the
frame times in `docs/PACKAGING.md`.
*Size:* S.

**V12-20 — A student's program** (from IP-11 b)
Grade a program an actual student wrote, commit the report. Needs a student;
does not gate v1.2.

---

## Decisions the user owns

1. **Higgsfield credits.** The account has none. Phase A and V12-06/14 need
   roughly **25–30 credits for images**, plus the 3D models in V12-04, whose
   cost is only known per job. Buy a plan or credits before Phase A.
2. **The licence of generated output** — confirm it allows redistribution in
   an MIT repository (see Ground rules).
3. **The logo** (V12-06), from the generated concepts.
4. **Code signing** (V12-12) and **macOS** (V12-13): yes, no, or later.
5. **A native read of the Arabic UI** (V12-09).

---

## Sequencing

### The v1.2 gate

V12-01, 02, 03, 06, 07, 08, 09, 11, 14, 16, 17, 19.

### Order

1. **V12-17** — before anything adds files.
2. **V12-06** — the identity decides the colours of everything after it.
3. **V12-01 → 02 → 03** — the look, once credits exist.
4. **V12-08**, then **V12-07** (thumbnails render the new look), then
   **V12-09** (the font from 08 carries the Arabic).
5. **V12-04** (and V12-05 if it earns it), **V12-10**.
6. **V12-11**, **V12-14**, **V12-15**.
7. **V12-19**, **V12-16**, then tag v1.2.0.

V12-12, 13, 18 and 20 run whenever their decision or person arrives.

### Deliberately not in v1.2

- **Generated textures on anything that carries meaning without a fallback**
  — lamps, the E-stop, tall/short colours stay readable first.
- **In-engine grading ("check my program")** — the bigger student-experience
  item; it belongs in a plan that focuses on students, not on polish.
- **New drivers** — none has had an outside user yet.

---

## Appendix A — work item index

| Item | Title | Phase | Size | Gate | Status |
|---|---|---|---|---|---|
| V12-01 | A material library, with real surfaces | A | L | ● | open |
| V12-02 | Cartons that look like cartons | A | M | ● | open |
| V12-03 | A hall, not a box | A | M | ● | open |
| V12-04 | Set dressing from generated 3D models | A | L |  | open |
| V12-05 | Better housings for static parts | A | L |  | open (stretch) |
| V12-06 | A logo and a real icon | B | S | ● | open (needs the user's pick) |
| V12-07 | Template thumbnails from the real engine | B | M | ● | open |
| V12-08 | A theme and a font | B | M | ● | open |
| V12-09 | An Arabic interface | B | L | ● | open |
| V12-10 | A first-run welcome | B | S |  | open |
| V12-11 | A Windows installer | C | M | ● | open |
| V12-12 | Code signing | C | S |  | open (needs the user) |
| V12-13 | macOS | C | L |  | open (needs the user) |
| V12-14 | A website | C | M | ● | open |
| V12-15 | README, topics and release notes | C | S |  | open |
| V12-16 | Re-record the demo | C | S | ● | open |
| V12-17 | Commit every `.uid` | D | S | ● | open |
| V12-18 | A belt-fed turntable scene | D | M |  | open |
| V12-19 | Size and speed as a gate | D | S | ● | open |
| V12-20 | A student's program | D | — |  | open (needs a student) |

---

*FactoryForge — developed by Mahamed Algaroshy (محمد الجروشي).*
