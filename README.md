# Cat Layer Studio

Cat Layer Studio is a local Windows desktop application for fitting slightly mismatched cat-art
parts to one locked master canvas. It preserves the imported artwork, stores fitting values in a
self-contained project, and exports aligned transparent layers at the exact canonical size.

This repository currently implements the first milestone from
[issue #1](https://github.com/hanazdev/cat-layer-studio/issues/1): precise component fitting.
It does **not** generate images, call an AI service, author animation timelines, or export named-cat
runtime scenes. Godot-native layered scene export and reusable rig compatibility follow only after
the fitting workflow passes its acceptance tests.

## Install and run

Python 3.12 or newer is required.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cat-layer-studio
```

The app opens with guided project actions. Create a project, choose the canonical master, import a
transparent part, and use the comparison and exact-fit controls. Arrow keys move by one pixel,
Shift+Arrow moves by five, and Alt+Arrow moves by a quarter pixel.

## Phase 1 workflow

1. Create a self-contained project and choose the locked master image.
2. Import a PNG, JPEG, or WebP candidate. Transparent RGBA PNG is strongly recommended.
3. Compare the part using overlay, flicker, difference, alpha, or edge views.
4. move, resize, and rotate it with explicit numeric values or keyboard nudges.
5. Optionally select matching landmarks and preview a suggested similarity transform.
6. Paint the area to keep when the source contains more than the intended component.
7. Export the approved result as a full-canvas RGBA PNG.

Layers retain the full canvas because all parts then share one coordinate system. The fitting
transform is baked into the pixels, so Godot can place every layer at position `(0, 0)`, scale
`(1, 1)`, and rotation `0`. Later, a generic Godot assembler will combine these reusable physical
parts and recolour tint groups. Animation will be reused through stable rig profiles rather than
being recreated for every generated cat.

## Tests and quality checks

```powershell
python -m pytest
python -m ruff check .
```

The deterministic tests use synthetic images and cover canvas normalisation, translation,
subpixel rasterisation, uniform and non-uniform scaling, rotation, alpha preservation, masking,
landmark fitting, project round-trips, safe paths, and full-canvas export/reimport.

## Godot verification

Godot verification is not presented as complete in this Phase 1 milestone. The planned verifier
will require an explicitly selected Godot 4.6 executable, run the project headlessly, load and
instantiate the generated layered `.tscn`, and report `Godot Verified` only after engine checks
pass. See [docs/godot-export.md](docs/godot-export.md).

