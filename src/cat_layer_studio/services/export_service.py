from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.mask_service import apply_mask
from cat_layer_studio.services.transform_service import rasterise_transform


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: Path
    width: int
    height: int
    mode: str
    godot_position: tuple[int, int] = (0, 0)
    godot_scale: tuple[int, int] = (1, 1)
    godot_rotation: int = 0


def export_component(
    source: Image.Image,
    transform: Transform,
    canvas_size: tuple[int, int],
    destination: Path,
    *,
    mask: Image.Image | None = None,
    overwrite: bool = False,
) -> ExportResult:
    if destination.exists() and not overwrite:
        raise FileExistsError("The component already exists. Confirm replacement before exporting.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    output = rasterise_transform(source, transform, canvas_size)
    if mask is not None:
        output = apply_mask(output, mask)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".png.tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        output.save(temporary, format="PNG")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    result = ExportResult(destination, output.width, output.height, output.mode)
    metadata = destination.with_suffix(".json")
    metadata.write_text(
        json.dumps(asdict(result) | {"path": destination.name}, indent=2) + "\n",
        encoding="utf-8",
    )
    return result
