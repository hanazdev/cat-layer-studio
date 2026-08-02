from __future__ import annotations

import numpy as np
from PIL import Image


def normalise_rgba_for_transform(image: Image.Image, *, near_zero_alpha: int = 0) -> Image.Image:
    """Return a clean straight-alpha RGBA copy suitable for premultiplication.

    RGB hidden below fully transparent pixels is undefined in many PNGs.  Clearing it
    prevents geometric filters from pulling that colour into a visible edge.
    """
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    alpha = rgba[..., 3]
    rgba[alpha == 0, :3] = 0
    if near_zero_alpha > 0:
        rgba[alpha <= near_zero_alpha, :3] = 0
    return Image.fromarray(rgba, "RGBA")


def _premultiply(image: Image.Image) -> Image.Image:
    rgba = np.asarray(normalise_rgba_for_transform(image), dtype=np.float32)
    rgba[..., :3] *= rgba[..., 3:4] / 255.0
    return Image.fromarray(np.clip(np.rint(rgba), 0, 255).astype(np.uint8), "RGBA")


def _unpremultiply(image: Image.Image) -> Image.Image:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[..., 3:4]
    rgb = np.zeros_like(rgba[..., :3])
    np.divide(rgba[..., :3] * 255.0, alpha, out=rgb, where=alpha > 0)
    result = np.concatenate((np.clip(rgb, 0, 255), np.clip(alpha, 0, 255)), axis=2)
    return Image.fromarray(np.rint(result).astype(np.uint8), "RGBA")


def transform_premultiplied_rgba(
    image: Image.Image,
    affine: tuple[float, float, float, float, float, float],
    size: tuple[int, int],
    resampling: Image.Resampling = Image.Resampling.BICUBIC,
) -> Image.Image:
    """Apply Pillow's output-to-input affine in premultiplied-alpha space."""
    transformed = _premultiply(image).transform(
        size, Image.Transform.AFFINE, affine, resample=resampling
    )
    return _unpremultiply(transformed)
