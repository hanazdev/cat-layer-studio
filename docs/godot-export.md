# Godot export

The Export tab writes a generic Godot 4.6 asset below a selected project's `res://` directory:

```text
cat_rig_adult_front_sitting.tscn
cat_rig_manifest.json
cat_part_catalog.json
preview.png
script.gd
verify_rig.gd
textures/*.png
```

The native scene root is `ModularCat2D`. It contains a `Skeleton2D`, stable `Bone2D` joints, and
separate `Sprite2D` visuals carrying slot metadata. The attached script exposes:

```gdscript
cat.set_part("tail", tail_texture)
```

All texture references are `res://` paths. The manifest records format version, profile, canvas,
scene, layer IDs, slots, node names, texture paths, z-indexes, offsets, visibility, opacity,
attachments, pivots, and tint groups.

Export is transactional. Existing output is retained until the new directory is complete. When
verification is requested, Godot first performs an import pass and then loads and instantiates the
scene in a headless fixture. It checks stable hierarchy, texture availability, z-index values, and
runtime replacement. It verifies every rest-pose transform exactly. When the selected engine has a
real rendering device it also compares an off-screen render against `preview.png`, allowing a mean
channel difference of 18/255 for sampler differences. Godot's Windows headless driver is a dummy
renderer, so that environment uses the exact-transform parity fallback and reports it in the engine
output. A failure restores the previous output; only the successful engine-backed path receives
`Godot Verified`.
