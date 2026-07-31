from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image

from cat_layer_studio.services.transform_service import calculate_fit_inside_scale


def normalise_master_to_canvas(
    source: Image.Image, canvas_size: tuple[int, int]
) -> tuple[Image.Image, float]:
    """Return an exact RGBA canvas containing the complete, uniformly fitted master."""
    original_bytes = source.tobytes()
    scale = calculate_fit_inside_scale(source.size, canvas_size)
    fitted_size = (
        min(canvas_size[0], max(1, round(source.width * scale))),
        min(canvas_size[1], max(1, round(source.height * scale))),
    )
    fitted = source.convert("RGBA").resize(fitted_size, Image.Resampling.LANCZOS)
    result = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    result.alpha_composite(
        fitted,
        ((canvas_size[0] - fitted.width) // 2, (canvas_size[1] - fitted.height) // 2),
    )
    if source.tobytes() != original_bytes:
        raise RuntimeError("Master normalisation unexpectedly changed the source image.")
    return result, scale


def save_png_atomic(image: Image.Image, destination: Path) -> None:
    """Write a new PNG atomically, refusing to replace an unexpected existing file."""
    if destination.exists():
        raise FileExistsError(f"The destination already exists: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".png.tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        image.convert("RGBA").save(temporary, format="PNG")
        # Linking the completed same-directory temporary file is atomic and refuses overwrite.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
