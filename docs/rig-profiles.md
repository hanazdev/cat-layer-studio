# Rig profiles

`adult_front_sitting` is the first stable generic rig profile. It is deliberately not tied to a
named cat.

Its standard joints are `Root`, `Body`, `Head`, `EarScreenLeft`, `EarScreenRight`, and `Tail`.
Visual eyes attach to `Head`. The default proof slots are tail, body, head, both ears, and both eyes;
expression, pattern, white marking, chest fur, accessory, and custom layers can also be exported.

The recommended draw order places tail at 10, body at 20, head at 30, ears at 40/41, and eyes at
50/51. These are suggestions rather than constraints: ears may remain in front of the head and all
values are editable.

Every full-canvas visual is parented to its movement joint. Its local transform is calculated from
the approved canvas offset and pivot, so a rest pose needs no negative-coordinate calculation by
the user. Stable template bone paths can be targeted by a later reusable animation library.
