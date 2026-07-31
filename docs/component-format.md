# Component format

A Phase 1 component consists of a full-canvas RGBA PNG and a neighbouring JSON receipt. The PNG is
the runtime visual. The receipt records its dimensions, mode, and the neutral Godot transform
contract.

The exported PNG is produced in this order:

1. read the preserved candidate source;
2. rasterise the approved fitting transform onto the canonical canvas;
3. multiply the fitted alpha by the optional full-canvas keep-area selection;
4. write a new PNG atomically.

Existing files are not replaced unless the user explicitly confirms replacement.

