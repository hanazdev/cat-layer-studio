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

`project.json` is UTF-8 JSON with `format_version: 1`. It stores the project name, canonical canvas,
relative master path, export directory, rig-profile identifier, and current candidate metadata.
Candidate metadata contains a relative preserved source path, explicit transform values, and an
optional mask path. Paths are resolved below the project root; escaping paths are rejected.

When a user accepts master normalisation, `source/master_original.<ext>` preserves the imported
file and `master/master_canvas.png` is the active, exact-canvas RGBA master. The metadata records
`master_original_path`, `master_working_path`, both sizes, the full-precision resize scale, and the
`fit_inside` mode. The legacy `master_path` remains the active reference. Version 1 projects that
only contain `master_path` continue to load; that path is treated as both the original and working
master until the user explicitly replaces or normalises it.

Saves use a temporary file followed by an atomic replacement. Before an existing project file is
replaced, the previous version is copied to `backups/project-<UTC timestamp>.json`.
