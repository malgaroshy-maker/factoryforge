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
| Start-screen background | 2026-09-29 | 1 | 1 |
| Trailer shots (Seedance 2.5, 720p, 3 x 6 s) | 2026-09-29 | 3 | 3 |

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

Derived from it by `tools/make_branding.py`:

- `branding/splash.png` (320 px, the boot splash on `project.godot`'s navy)

and, outside this folder (listed so their origin is on record):

- `engine/icon.png` (256 px, window and Linux icon)
- `engine/icon.ico` (16–256 px, Windows executable icon)
- `docs/images/banner.png` (README header)
- `docs/images/social_preview.png` (1280×640, GitHub social preview)

The banner and social preview set their text in Segoe UI.

### `menu/start_background.jpg`

- **Model:** `marketing-studio/image/flare`
- **Date:** 2026-09-29
- **Job id:** `2f8576e9-bb49-4293-bd00-c1b59972a4a0`
- **Arguments:** `aspect_ratio: "16:9"`
- **Prompt:** Soft stylised 3D render of a small factory floor seen from a
  high three-quarter angle, conveyors, a pusher, a chute and stacked cartons,
  simple clean shapes and matte materials like a toy model, gentle ambient
  light, the left third empty floor, dark navy base colour with orange and teal
  accents, calm, uncluttered, no people, no text, no logos, wide 16:9
  background for an application menu
- **Edits:** scaled from 2688×1520 to 1920×1080 (`tools/make_branding.py
  --menu-raw`). The start screen darkens it with a gradient at run time; the
  file itself is unaltered.
- **Picked from:** the only one generated. It was concept B of three proposed.
  The author chose it over a photoreal hall, which would have made the
  engine's own simple 3D look plainer by comparison, and over an abstract
  blueprint.

Not generated, and so not listed as generated: the template thumbnails in
`engine/templates/thumbnails/` are frames of the real engine, rendered by
`tools/make_thumbnails.py`.

## Outside the repository

### The trailer (`tools/make_trailer.py`)

The trailer is not committed; it is attached to releases and posts. Its three
generated shots were made on 2026-09-29 with Seedance 2.5, 720p, 6 s each,
with generated audio. The middle of the film is real engine footage.

| Shot | Model | Job id | Input |
|---|---|---|---|
| `1_intro` | `bytedance/seedance-2.5/image-to-video` | `9c62a258-a8d2-4975-b552-59529aad0263` | `menu/start_background.jpg` |
| `2_why` | `bytedance/seedance-2.5/text-to-video` | `afdade6b-72e1-4425-9493-50d44cf1ccd9` | text only, 16:9 |
| `3_outro` | `bytedance/seedance-2.5/image-to-video` | `20bdaea3-f836-4327-a120-75db28b3156c` | the logo on navy, as first and last frame |

Prompts, each ending ", cinematic, smooth slow camera, navy with orange and teal accents, no text, no logos":

- `1_intro`: Slow dolly-in over a stylised toy-like factory floor, cartons gliding along the conveyors, small orange warning lamps blinking, soft ambient hum of motors
- `2_why`: Close-up of a student's hands typing on a laptop at night, the screen softly out of focus glowing orange and teal, shallow depth of field, quiet keyboard clicks
- `3_outro`: The logo's conveyor belt starts moving, its rollers turn, the cardboard box slides a little and the gear rotates once, then everything settles still, a soft mechanical click