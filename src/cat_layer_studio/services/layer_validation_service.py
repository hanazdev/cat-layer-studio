from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from cat_layer_studio.models.project import Project
from cat_layer_studio.services.composition_service import finite_layer_coordinates


@dataclass(frozen=True, slots=True)
class ValidationResult:
    name: str
    status: str
    detail: str = ""


def validate_assembly(project_directory: Path, project: Project) -> list[ValidationResult]:
    layers = project.assembly_layers
    results: list[ValidationResult] = []
    results.append(
        ValidationResult(
            "Layers found", "Passed" if layers else "Needs attention", f"{len(layers)} layer(s)"
        )
    )
    paths_ok = True
    textures_ok = True
    sizes_ok = True
    alpha_ok = True
    for layer in layers:
        try:
            path = project.resolve(project_directory, layer.texture_path)
            paths_ok = paths_ok and path.exists()
            with Image.open(path) as image:
                image.load()
                sizes_ok = sizes_ok and image.size == project.canvas_size
                alpha_ok = (
                    alpha_ok
                    and image.mode == "RGBA"
                    and ("A" in image.getbands() or "transparency" in image.info)
                )
        except (ValueError, OSError, UnidentifiedImageError):
            paths_ok = False
            textures_ok = False
    results.extend(
        [
            ValidationResult("Texture paths", "Passed" if paths_ok else "Failed"),
            ValidationResult("Textures load", "Passed" if textures_ok else "Failed"),
            ValidationResult("Canvas sizes", "Passed" if sizes_ok else "Failed"),
            ValidationResult("Transparency", "Passed" if alpha_ok else "Failed"),
        ]
    )
    ids = [layer.id for layer in layers]
    results.append(
        ValidationResult("Unique layer IDs", "Passed" if len(ids) == len(set(ids)) else "Failed")
    )
    slots = [
        layer.slot
        for layer in layers
        if layer.slot not in {"custom", "accessory", "pattern", "white_marking"}
    ]
    results.append(
        ValidationResult(
            "Slot assignments",
            "Passed" if len(slots) == len(set(slots)) else "Needs attention",
            "Only accessory, pattern, white marking, and custom slots may repeat.",
        )
    )
    z_values = [layer.z_index for layer in layers]
    results.append(
        ValidationResult(
            "Draw order",
            "Passed" if len(z_values) == len(set(z_values)) else "Needs attention",
            "Duplicate draw-order values are resolved by layer ID but should be made unique.",
        )
    )
    results.append(
        ValidationResult(
            "Layer coordinates",
            "Passed" if all(finite_layer_coordinates(layer) for layer in layers) else "Failed",
        )
    )
    results.append(
        ValidationResult(
            "Visible result", "Passed" if any(layer.visible for layer in layers) else "Failed"
        )
    )
    bounds_ok = all(
        abs(layer.offset_x) < project.canvas_width and abs(layer.offset_y) < project.canvas_height
        for layer in layers
    )
    results.append(
        ValidationResult(
            "Composite bounds",
            "Passed" if bounds_ok else "Needs attention",
            "A large offset may move a layer completely beyond the canvas."
            if not bounds_ok
            else "",
        )
    )
    required = {
        "tail",
        "body",
        "head",
        "ear_screen_left",
        "ear_screen_right",
        "eye_screen_left",
        "eye_screen_right",
    }
    missing = sorted(required - {layer.slot for layer in layers})
    results.append(
        ValidationResult(
            "Required slots",
            "Passed" if not missing else "Needs attention",
            "Missing: " + ", ".join(missing) if missing else "All proof slots assigned.",
        )
    )
    blocking = any(result.status == "Failed" for result in results)
    results.append(ValidationResult("Godot export", "Not ready" if blocking else "Ready"))
    return results
