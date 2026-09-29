# Generated assets

Every file under `engine/assets/` that a generator made is listed here with its
model, full prompt, date and job id, so anyone can tell what was generated,
how, and under which terms. `tools/test_plan.py` check A8 fails when a file
under `engine/assets/` is not named in this document.

## Terms

Generated with the Higgsfield Cloud API (https://api.higgsfield.ai) on a paid
account, as read on 2026-09-29:

- The account holder owns the outputs. Commercial use and sublicensing are
  allowed, which is what distributing them in an MIT repository needs.
- Higgsfield's terms forbid using outputs to train AI models. That condition
  binds the account that generated them. It is noted here so nobody
  redistributing these files is surprised by it.
- Paid-plan outputs carry no watermark, and none of these do.

Colour carries meaning in the simulator (a tall carton is orange, a short one
blue, a lamp's colour is its state). Generated art keeps those signals and
never changes how a part behaves (the ground rules in
`docs/IMPROVEMENT_PLAN_v1.2.md`).

## Running cost

The API does not report a job's price. Costs are in the account's console at
console.higgsfield.ai.

| Batch | Date | Jobs | Kept |
|---|---|---|---|
| V12-06 logo concepts | 2026-09-29 | 4 | 1 |

## Files

### `branding/logo_1024.png` (V12-06)

- **Model:** `marketing-studio/image/flare`
- **Date:** 2026-09-29
- **Job id:** `57c0abb2-30ce-417c-81fd-e8da39a95aa2`
- **Arguments:** `aspect_ratio: "1:1"`
- **Prompt:** Logo mark for FactoryForge, a 3D factory simulator for learning
  PLC programming: a stylised conveyor belt carrying one cardboard box, the
  belt's end roller merged into a gear outline, no text, flat vector app icon,
  bold geometric shapes, orange and teal on a dark navy rounded-square
  background, clean edges, readable at 32 px, no gradients clutter, centered
- **Edits:** the 2048 px output sits on a white page, so
  `tools/make_branding.py --from-raw` re-masks the rounded square with
  transparent corners and scales it to 1024 px. Nothing inside the square was
  changed.
- **Picked from:** four concepts generated in the same batch (belt + gear,
  anvil, FF monogram, ladder-logic contact), chosen by the author for
  readability at 32 px.

Derived from it by `tools/make_branding.py` (outside this folder, listed so
their origin is on record):

- `engine/icon.png` (256 px, window and Linux icon)
- `engine/icon.ico` (16–256 px, Windows executable icon)
- `docs/images/banner.png` (README header)
- `docs/images/social_preview.png` (1280×640, GitHub social preview)

The banner and social preview set their text in Segoe UI.
