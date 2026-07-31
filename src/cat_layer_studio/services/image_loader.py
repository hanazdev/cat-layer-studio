from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(frozen=True, slots=True)
class LoadedImage:
    image: Image.Image
    original_mode: str
    original_size: tuple[int, int]
    format: str | None
    had_alpha: bool


def load_image(path: Path) -> LoadedImage:
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("Choose a PNG, JPEG, or WebP image.")
    with Image.open(path) as opened:
        opened.load()
        had_alpha = "A" in opened.getbands() or "transparency" in opened.info
        return LoadedImage(
            image=opened.convert("RGBA"),
            original_mode=opened.mode,
            original_size=opened.size,
            format=opened.format,
            had_alpha=had_alpha,
        )


def normalise_to_canvas(image: Image.Image, canvas_size: tuple[int, int]) -> Image.Image:
    """Centre an image on a canvas without stretching or cropping it."""
    output = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - image.width) // 2
    y = (canvas_size[1] - image.height) // 2
    output.alpha_composite(image.convert("RGBA"), (x, y))
    return output
