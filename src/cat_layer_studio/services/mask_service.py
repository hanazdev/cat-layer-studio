from __future__ import annotations

from PIL import Image, ImageChops


def blank_mask(canvas_size: tuple[int, int]) -> Image.Image:
    return Image.new("L", canvas_size, 0)


def apply_mask(image: Image.Image, mask: Image.Image) -> Image.Image:
    if image.size != mask.size:
        raise ValueError("The keep-area selection must use the project canvas size.")
    output = image.convert("RGBA").copy()
    output.putalpha(ImageChops.multiply(output.getchannel("A"), mask.convert("L")))
    return output
