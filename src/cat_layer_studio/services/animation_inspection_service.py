from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

from cat_layer_studio.models.animation import GeneratedAnimation
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.animation_service import inspect_animation, maximum_extent_times
from cat_layer_studio.services.composition_service import (
    composite_animation_frame,
    composite_assembly,
)


def _alpha_area(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[9:])


def _edge_has_pixels(image: Image.Image) -> bool:
    alpha = image.getchannel("A")
    width, height = alpha.size
    return any(
        alpha.getpixel(point) > 8
        for point in (
            *((x, 0) for x in range(width)),
            *((x, height - 1) for x in range(width)),
            *((0, y) for y in range(height)),
            *((width - 1, y) for y in range(height)),
        )
    )


def inspect_animation_frames(
    project_directory: Path,
    project: Project,
    animation: GeneratedAnimation,
) -> list[str]:
    """Run conservative clipping/overlap checks at keys and maximum motion extents."""
    warnings = inspect_animation(project, animation)
    rest = composite_assembly(project_directory, project)
    rest_area = max(1, _alpha_area(rest))
    rest_touches_edge = _edge_has_pixels(rest)
    key_times = {key.time for track in animation.tracks for key in track.keys}
    sample_times = sorted({0.0, animation.duration, *key_times, *maximum_extent_times(animation)})
    measurable_change = False
    for time in sample_times:
        frame = composite_animation_frame(project_directory, project, animation, time)
        if ImageChops.difference(rest, frame).getbbox() is not None:
            measurable_change = True
        if _edge_has_pixels(frame) and not rest_touches_edge:
            warnings.append(
                f"{animation.name} needs attention. Visible pixels reach the canvas edge at "
                f"{time:.2f} seconds; inspect this extent for clipping."
            )
            break
        area_change = abs(_alpha_area(frame) - rest_area) / rest_area
        if area_change > 0.2:
            warnings.append(
                f"{animation.name} needs attention. Overlap changes sharply at {time:.2f} "
                "seconds; inspect the attachment seams or reduce the movement amount."
            )
            break
    if animation.template_id == "idle_breathing" and not measurable_change:
        warnings.append(
            "Idle breathing — Preview did not show measurable movement. "
            "Try Emphasise movement for checking. Export is blocked until the preview "
            "confirms movement."
        )
    return warnings
