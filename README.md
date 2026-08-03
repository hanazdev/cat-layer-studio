# Cat Layer Studio

Cat Layer Studio is a local Windows desktop application for fitting and assembling modular cat-art
parts on one locked canvas. It preserves imported artwork, stores exact fitting and assembly values
in a self-contained project, and exports a reusable Godot 4.6 cutout rig.

This repository implements precise component fitting from issue #1, modular preview / generic
Godot rig export from issue #4, and automatic reusable animation generation from
[issue #5](https://github.com/hanazdev/cat-layer-studio/issues/5). It does **not** generate images,
call an AI service, or provide a freeform animation timeline editor.

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
11. Open Automatic Animations, choose templates, adjust plain-language movement controls, and
    preview or inspect maximum motion extents.
12. Export the generic `adult_front_sitting` scene and reusable `AnimationLibrary` into Godot.
13. Select a Godot 4.6 executable to import, play every animation, and runtime-replace a slot.

Layers retain the full canvas because all parts share one coordinate system. The exported rig uses
stable `Skeleton2D` / `Bone2D` paths and calculates each sprite's local pivot offset automatically.
The rest pose therefore matches the Modular Preview while each texture remains replaceable through
`set_part(slot, texture)`. Animation tracks target those stable joints rather than texture
filenames, and the runtime API can play them with `play_animation("idle")`.

## Tests and quality checks

```powershell
python -m pytest
python -m ruff check .
```

The deterministic tests cover fitting, project migration, assembly persistence, ordering,
subpixel compositing, validation, animation defaults and keyframes, rest and loop boundaries,
undo/redo, native scene/library/manifest writing, rollback, UI controls, and a live Godot 4.6
playback/runtime-replacement fixture when Godot is installed.

## Godot verification

The app distinguishes structural verification from rendered visual parity. A dummy-renderer run
can verify resources, transforms, generated layers, exact rest/loop boundaries, and runtime texture
replacement, but reports that rendered parity is still required. Only a rendering-capable Godot
run that passes the image comparison reports `Godot visually verified — Rig and animations`. A
failed validation restores the previous exported rig.
See [docs/godot-export.md](docs/godot-export.md).
