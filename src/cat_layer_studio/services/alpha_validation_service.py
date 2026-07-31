from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True, slots=True)
class AlphaReport:
    has_alpha: bool
    has_visible_pixels: bool
    fully_opaque: bool
    semi_transparent_fraction: float
    touches_edge: bool
    messages: tuple[str, ...]


def validate_alpha(image: Image.Image, *, source_had_alpha: bool = True) -> AlphaReport:
    alpha = np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.uint8)
    visible = alpha > 0
    semi = (alpha > 0) & (alpha < 255)
    touches = bool(
        visible[0, :].any() or visible[-1, :].any() or visible[:, 0].any() or visible[:, -1].any()
    )
    fraction = float(semi.sum() / max(1, visible.sum()))
    messages: list[str] = []
    if not source_had_alpha:
        messages.append("The source has no alpha channel; transparent PNG is recommended.")
    if not visible.any():
        messages.append("No visible pixels were found.")
    if visible.all():
        messages.append("The entire image is opaque; check for an unremoved background.")
    if fraction > 0.5:
        messages.append("Much of the visible image is semi-transparent; inspect for haze.")
    if touches:
        messages.append(
            "Visible pixels touch a canvas edge; the part may be clipped or tightly cropped."
        )
    return AlphaReport(
        has_alpha=source_had_alpha,
        has_visible_pixels=bool(visible.any()),
        fully_opaque=bool(visible.all()),
        semi_transparent_fraction=fraction,
        touches_edge=touches,
        messages=tuple(messages),
    )


def clear_hidden_rgb(image: Image.Image) -> Image.Image:
    values = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    values[values[..., 3] == 0, :3] = 0
    return Image.fromarray(values, "RGBA")
