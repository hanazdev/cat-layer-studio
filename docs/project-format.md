# Project format

A project is a self-contained directory:

```text
project.json
source/
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

Saves use a temporary file followed by an atomic replacement. Before an existing project file is
replaced, the previous version is copied to `backups/project-<UTC timestamp>.json`.

