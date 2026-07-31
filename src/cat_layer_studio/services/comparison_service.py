from __future__ import annotations

import numpy as np
from PIL import Image, ImageChops, ImageEnhance, ImageFilter


def overlay(master: Image.Image, candidate: Image.Image, opacity: float = 0.5) -> Image.Image:
    opacity = min(1.0, max(0.0, opacity))
    layer = candidate.convert("RGBA").copy()
    alpha = layer.getchannel("A").point(lambda value: round(value * opacity))
    layer.putalpha(alpha)
    output = master.convert("RGBA").copy()
    output.alpha_composite(layer)
    return output


def difference(
    master: Image.Image,
    candidate: Image.Image,
    threshold: int = 0,
    highlighted: bool = False,
) -> Image.Image:
    diff = ImageChops.difference(master.convert("RGBA"), candidate.convert("RGBA"))
    values = np.asarray(diff, dtype=np.uint8)
    magnitude = values.max(axis=2)
    magnitude[magnitude < min(255, max(0, threshold))] = 0
    if highlighted:
        result = np.zeros((*magnitude.shape, 4), dtype=np.uint8)
        result[..., 0] = magnitude
        result[..., 3] = np.where(magnitude > 0, 255, 0)
        return Image.fromarray(result, "RGBA")
    return Image.fromarray(magnitude, "L").convert("RGBA")


def alpha_only(image: Image.Image) -> Image.Image:
    return image.convert("RGBA").getchannel("A").convert("RGBA")


def edge_view(image: Image.Image) -> Image.Image:
    edges = image.convert("RGBA").getchannel("A").filter(ImageFilter.FIND_EDGES)
    return ImageEnhance.Contrast(edges).enhance(2.0).convert("RGBA")
