# Godot export

Phase 1 exports baked component PNGs with a neutral Godot transform. The next export milestone will
target a selected directory containing `project.godot` and stage a built-in-node Godot 4.6 scene,
manifest, preview, validation report, and separate full-canvas layer textures below its `res://`
root.

Verification must use the configured Godot executable. It will import textures, load the generated
`format=3` `.tscn`, instantiate it, confirm each `Sprite2D`, texture size, z-index, visibility, tint,
and neutral transform, and then free the scene without errors. A generated scene is only `Godot
Verified` after these engine checks pass; skipped engine execution is `Exported — Unverified`.

