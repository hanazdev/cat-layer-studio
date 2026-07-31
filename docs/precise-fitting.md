# Precise fitting

The master is positional authority and remains locked. A candidate starts centred on the canonical
canvas and records five editable values: X, Y, width scale, height scale, and rotation. X/Y use
floating-point canvas pixels; scales are stored as factors; rotation is degrees.

Rasterisation uses an inverse affine mapping with bicubic resampling. The accepted transform is
baked once into a new RGBA image at the canonical dimensions. Source pixels are never rewritten.

Independent width and height are always visible. Divergence below 3% is allowed, 3–5% shows a
warning, above 5% shows a strong warning, and above 10% requires export confirmation.

Landmark fitting solves a least-squares similarity transform from two or more matching points. The
calculated move, uniform scale, rotation, and RMS error are shown before the user accepts it.

