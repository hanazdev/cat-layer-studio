from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cat_layer_studio.constants import STANDARD_SLOTS
from cat_layer_studio.services.image_loader import load_image


@dataclass(frozen=True, slots=True)
class ComponentInfo:
    path: Path
    relative_path: str
    display_name: str
    dimensions: tuple[int, int]
    has_alpha: bool
    suggested_slot: str
    in_assembly: bool


def suggest_slot(name: str) -> str:
    normalised = name.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "left_eye": "eye_screen_left",
        "right_eye": "eye_screen_right",
        "eye_left": "eye_screen_left",
        "eye_right": "eye_screen_right",
        "left_ear": "ear_screen_left",
        "right_ear": "ear_screen_right",
        "ear_left": "ear_screen_left",
        "ear_right": "ear_screen_right",
    }
    for alias, slot in aliases.items():
        if alias in normalised:
            return slot
    for slot in STANDARD_SLOTS:
        if slot in normalised:
            return slot
    for word in ("tail", "body", "head", "expression", "pattern", "accessory"):
        if word in normalised:
            return word
    return "custom"


def list_components(
    project_directory: Path, used_paths: set[str] | None = None
) -> list[ComponentInfo]:
    used = used_paths or set()
    result = []
    for path in sorted((project_directory / "components").glob("*.png")):
        loaded = load_image(path)
        relative = path.relative_to(project_directory).as_posix()
        result.append(
            ComponentInfo(
                path,
                relative,
                path.stem.replace("_", " ").replace("-", " ").title(),
                loaded.original_size,
                loaded.had_alpha,
                suggest_slot(path.stem),
                relative in used,
            )
        )
    return result


def import_component(project_directory: Path, source: Path) -> Path:
    loaded = load_image(source)
    if source.suffix.lower() != ".png" or not loaded.had_alpha:
        raise ValueError("Choose a transparent PNG. Other formats belong in Fit Component first.")
    destination_directory = project_directory / "components"
    destination_directory.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    destination = destination_directory / f"{stem}.png"
    counter = 2
    while destination.exists():
        if destination.read_bytes() == source.read_bytes():
            return destination
        destination = destination_directory / f"{stem}_{counter}.png"
        counter += 1
    shutil.copy2(source, destination)
    return destination
