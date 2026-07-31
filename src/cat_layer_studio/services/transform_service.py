from __future__ import annotations

from math import cos, isfinite, radians, sin

from PIL import Image

from cat_layer_studio.models.transform import Transform


def calculate_fit_inside_scale(
    source_size: tuple[int, int],
    canvas_size: tuple[int, int],
) -> float:
    """Return the exact uniform scale that keeps the whole source inside the canvas."""
    dimensions = (*source_size, *canvas_size)
    if any(dimension <= 0 for dimension in dimensions):
        raise ValueError("Source and canvas dimensions must be greater than zero.")
    scale = min(canvas_size[0] / source_size[0], canvas_size[1] / source_size[1])
    if not isfinite(scale) or scale <= 0:
        raise ValueError("The calculated fit scale must be a finite positive number.")
    return scale


def fit_inside_transform(
    source_size: tuple[int, int],
    canvas_size: tuple[int, int],
) -> Transform:
    """Create a centred, non-destructive whole-image fitting transform."""
    scale = calculate_fit_inside_scale(source_size, canvas_size)
    return Transform(x=0.0, y=0.0, scale_x=scale, scale_y=scale, rotation_degrees=0.0)


def rasterise_transform(
    source: Image.Image,
    transform: Transform,
    canvas_size: tuple[int, int],
) -> Image.Image:
    """Bake a fitting transform onto a transparent canonical canvas."""
    transform.validate()
    source = source.convert("RGBA")
    pivot_x = source.width / 2 if transform.pivot_x is None else transform.pivot_x
    pivot_y = source.height / 2 if transform.pivot_y is None else transform.pivot_y
    destination_x = canvas_size[0] / 2 + transform.x
    destination_y = canvas_size[1] / 2 + transform.y

    angle = radians(transform.rotation_degrees)
    cosine = cos(angle)
    sine = sin(angle)

    # Pillow expects the inverse mapping from destination pixel to source pixel.
    a = cosine / transform.scale_x
    b = sine / transform.scale_x
    d = -sine / transform.scale_y
    e = cosine / transform.scale_y
    c = pivot_x - (a * destination_x) - (b * destination_y)
    f = pivot_y - (d * destination_x) - (e * destination_y)

    return source.transform(
        canvas_size,
        Image.Transform.AFFINE,
        (a, b, c, d, e, f),
        resample=Image.Resampling.BICUBIC,
    )


def reset_translation(transform: Transform) -> Transform:
    return Transform(**(transform.to_dict() | {"x": 0.0, "y": 0.0}))


def reset_scale(transform: Transform) -> Transform:
    return Transform(**(transform.to_dict() | {"scale_x": 1.0, "scale_y": 1.0}))


def reset_rotation(transform: Transform) -> Transform:
    return Transform(**(transform.to_dict() | {"rotation_degrees": 0.0}))
