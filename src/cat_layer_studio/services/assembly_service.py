from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.component_library_service import suggest_slot


def make_layer(project_directory: Path, texture: Path, z_index: int = 0) -> AssemblyLayer:
    relative = texture.resolve().relative_to(project_directory.resolve()).as_posix()
    slot = suggest_slot(texture.stem)
    template = get_rig_template("adult_front_sitting")
    joint = template.attachment_map.get(slot)
    suggested = next((j.suggested_pivot for j in template.joints if j.name == joint), None)
    return AssemblyLayer(
        id=uuid4().hex,
        display_name=texture.stem.replace("_", " ").replace("-", " ").title(),
        texture_path=relative,
        slot=slot,
        z_index=z_index,
        attachment_joint=joint,
        pivot_x=suggested[0] if suggested else None,
        pivot_y=suggested[1] if suggested else None,
    )


def normalise_z_order(layers: list[AssemblyLayer], step: int = 10) -> None:
    for index, layer in enumerate(sorted(layers, key=lambda item: (item.z_index, item.id)), 1):
        layer.z_index = index * step


def apply_recommended_order(project: Project) -> None:
    recommended = get_rig_template(project.rig_profile).recommended_z
    for layer in project.assembly_layers:
        if layer.slot in recommended:
            layer.z_index = recommended[layer.slot]


def move_layer(layers: list[AssemblyLayer], layer_id: str, direction: str) -> None:
    ordered = sorted(layers, key=lambda layer: (layer.z_index, layer.id))
    index = next(index for index, layer in enumerate(ordered) if layer.id == layer_id)
    if direction == "front":
        target = min(index + 1, len(ordered) - 1)
    elif direction == "back":
        target = max(index - 1, 0)
    elif direction == "top":
        target = len(ordered) - 1
    elif direction == "bottom":
        target = 0
    else:
        raise ValueError(f"Unknown layer movement: {direction}")
    layer = ordered.pop(index)
    ordered.insert(target, layer)
    for position, item in enumerate(ordered, 1):
        item.z_index = position * 10


@dataclass(slots=True)
class AssemblyHistory:
    states: list[list[AssemblyLayer]] = field(default_factory=list)
    index: int = -1

    def reset(self, layers: list[AssemblyLayer]) -> None:
        self.states = [deepcopy(layers)]
        self.index = 0

    def commit(self, layers: list[AssemblyLayer]) -> None:
        snapshot = deepcopy(layers)
        if self.index >= 0 and snapshot == self.states[self.index]:
            return
        self.states = self.states[: self.index + 1]
        self.states.append(snapshot)
        self.index += 1

    def undo(self) -> list[AssemblyLayer] | None:
        if self.index <= 0:
            return None
        self.index -= 1
        return deepcopy(self.states[self.index])

    def redo(self) -> list[AssemblyLayer] | None:
        if self.index + 1 >= len(self.states):
            return None
        self.index += 1
        return deepcopy(self.states[self.index])
