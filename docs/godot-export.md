# Godot export

The Export tab writes a generic Godot 4.6 asset below a selected project's `res://` directory:

```text
cat_rig_adult_front_sitting.tscn
cat_adult_front_sitting_animations.tres
cat_animation_manifest.json
cat_rig_manifest.json
cat_part_catalog.json
preview.png
script.gd
verify_rig.gd
textures/*.png
```

The native scene root is `ModularCat2D`. It contains a `Skeleton2D`, stable `Bone2D` joints,
separate `Sprite2D` visuals carrying slot metadata, and an `AnimationPlayer` assigned to the
reusable library. The attached script exposes:

```gdscript
cat.set_part("tail", tail_texture)
cat.play_animation("idle")
cat.stop_animation()
cat.return_to_rest_pose()
cat.has_animation("tail_sway")
```

The default `adult_front_sitting` set contains `idle`, `tail_sway`, both ear twitches, both head
tilts, and `happy_bounce`. `blink` is added when open and closed artwork exists for both eyes. The
motion tracks target stable joint paths, so replacing a compatible texture does not duplicate or
stop the library.

All texture references are `res://` paths. The manifest records format version, profile, canvas,
scene, layer IDs, slots, node names, texture paths, z-indexes, offsets, visibility, opacity,
attachments, pivots, tint groups, scale-based breathing parameters, and generated attachment
treatment provenance. Each generated coverage guard is exported as an explicit parent-attached
Sprite2D immediately above its source parent and below its independently moving child and all
protected detail overlays. Discrete
visibility tracks activate it only while effective transforms diverge. Guards are never baked into
or substituted for source artwork.

Production export is strict for every divergent attachment, including future animation templates.
If final rendered coverage and seam checks have not passed, the enabled animation blocks export. A
user may export a valid subset only by explicitly disabling the failed template first.

Export is transactional. Existing output is retained until the new directory is complete. When
verification is requested, Godot first performs an import pass and then loads and instantiates the
scene in a headless fixture. It checks stable hierarchy, texture availability, z-index values, and
runtime replacement. It checks position, rotation, and scale samples, generated attachment metadata,
plays every generated animation through its duration, checks exact loop or
rest restoration, and replaces a part while `idle` is playing. When the selected engine has a real
rendering device it also compares an off-screen rest render against `preview.png`, allowing a mean
channel difference of 18/255 for sampler differences. Godot's Windows headless driver is a dummy
renderer, so that environment reports `Godot structurally verified — rendered parity still
required`; it verifies resources, tracks, transforms, generated-layer metadata, and rest
restoration but cannot claim visual parity. A rendering-capable run that passes the image comparison
reports `Godot visually verified — Rig and animations`. A failure restores the previous output.
