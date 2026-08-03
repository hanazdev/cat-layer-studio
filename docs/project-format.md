# Project format

A project is a self-contained directory:

```text
project.json
source/
master/
masks/
components/
previews/
exports/
backups/
```

`project.json` is UTF-8 JSON with `format_version: 2`. It stores the project name, canonical canvas,
relative master path, export directory, rig-profile identifier, current candidate metadata, and a
separately versioned `assembly_layers` list (`assembly_format_version: 1`).
It also stores an optional versioned `animation_set`, including selected templates, beginner and
advanced parameter values, preview speed and loop state, compatibility messages, and the last
successful animation-library export. Older projects load without this field and receive safe
defaults when Automatic Animations is opened.

Animation format 4 uses the strengthened, perceptually gated Idle scale presets while preserving
the selected `breathing_strength` and `breathing_speed` labels. Animation format 3 replaced legacy
Idle vertical-bob settings with `breathing_strength`,
`breathing_speed`, and recommended `keep_paws_grounded` settings. It supports position, rotation,
and scale tracks. Loading an older animation format preserves compatible timing and accepted joint
placements, restores missing last-accepted coordinates, disables obsolete ear/head motion, and
invalidates stale Cat Layer Studio and Godot verification.

Attachment treatment format 5 stores generic parent-underlay coverage guards discovered from divergent
rig transforms rather than animation names. It records parent/child joints and layer IDs, transform
owner, source hashes, protected and mask bounds, sampled animations, coverage policy, z-order,
regeneration provenance, background validation, and final-render verification. Versions 1–4 are
stale on load. Complete native layers remain immutable base passes; guards add native parent pixels
immediately above the parent and below every native child layer, only while parent and child
effective transforms differ, and are absent at exact rest.
Source component paths are never replaced by generated treatment paths.
Candidate metadata contains a relative preserved source path, explicit transform values, and an
optional mask path. Paths are resolved below the project root; escaping paths are rejected.

Each assembly layer persists its stable ID, display name, relative component path, slot, visibility,
lock, draw order, X/Y offsets, opacity, attachment joint, pivot, tint group, rig profile, and an
optional semantic artwork state such as `open` or `closed`.
Projects created before assemblies existed load with an empty layer list.

When a user accepts master normalisation, `source/master_original.<ext>` preserves the imported
file and `master/master_canvas.png` is the active, exact-canvas RGBA master. The metadata records
`master_original_path`, `master_working_path`, both sizes, the full-precision resize scale, and the
`fit_inside` mode. The legacy `master_path` remains the active reference. Version 1 projects that
only contain `master_path` continue to load; that path is treated as both the original and working
master until the user explicitly replaces or normalises it.

Saves use a temporary file followed by an atomic replacement. Before an existing project file is
replaced, the previous version is copied to `backups/project-<UTC timestamp>.json`.
