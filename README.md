# Cat Layer Studio

Cat Layer Studio is a local Windows desktop application for fitting and assembling modular cat-art
parts on one locked canvas. It preserves imported artwork, stores exact fitting and assembly values
in a self-contained project, and exports a reusable Godot 4.6 cutout rig.

This repository implements precise component fitting from issue #1 and modular preview / generic
Godot rig export from [issue #4](https://github.com/hanazdev/cat-layer-studio/issues/4). It does
**not** generate images, call an AI service, or author animation timelines.

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

## Workflow

1. Create a self-contained project and choose the locked master image.
2. Import a PNG, JPEG, or WebP candidate. Transparent RGBA PNG is strongly recommended.
3. Compare the part using overlay, flicker, difference, alpha, or edge views.
4. move, resize, and rotate it with explicit numeric values or keyboard nudges.
5. Optionally select matching landmarks and preview a suggested similarity transform.
6. Paint the area to keep when the source contains more than the intended component.
7. Export the approved result as a full-canvas RGBA PNG.
8. Add saved components to the Component Library assembly.
9. Order, position, show/hide, lock, name, and assign each layer to a stable part slot.
10. Place or fine-tune head, ear, and tail movement joints and preview a small turn.
11. Export the generic `adult_front_sitting` scene into a Godot project.
12. Select a Godot 4.6 executable to import, instantiate, inspect, and runtime-replace a slot.

Layers retain the full canvas because all parts share one coordinate system. The exported rig uses
stable `Skeleton2D` / `Bone2D` paths and calculates each sprite's local pivot offset automatically.
The rest pose therefore matches the Modular Preview while each texture remains replaceable through
`set_part(slot, texture)`.

## Tests and quality checks

```powershell
python -m pytest
python -m ruff check .
```

The deterministic tests cover fitting, project migration, assembly persistence, ordering,
subpixel compositing, validation, undo/redo, native scene/manifest writing, rollback, UI controls,
and a live Godot 4.6 import/instantiation/runtime-replacement fixture when Godot is installed.

## Godot verification

The app reports `Godot Verified` only after the selected Godot 4.6 executable imports the PNGs,
loads and instantiates the generated scene, checks its stable nodes, textures and z-indexes, and
replaces a texture through the slot API. A failed validation restores the previous exported rig.
See [docs/godot-export.md](docs/godot-export.md).
