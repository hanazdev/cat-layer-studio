from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from cat_layer_studio.models.animation import (
    AnimationKey,
    AnimationSet,
    AnimationTemplateSettings,
    GeneratedAnimation,
    GeneratedTrack,
)
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import RigTemplate, get_rig_template
from cat_layer_studio.services.attachment_treatment_service import (
    enabled_attachment_treatment,
)
from cat_layer_studio.services.joint_placement_service import placement_for
from cat_layer_studio.services.rig_hierarchy_service import (
    configured_joint_pivots,
    local_rest_positions,
)


class AnimationCompatibilityError(ValueError):
    """A template cannot be generated for the current rig or artwork."""


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    template_id: str
    animation_name: str
    label: str
    description: str
    required_joints: tuple[str, ...]
    required_assets: tuple[str, ...]
    default_duration: float
    default_loop: bool
    default_parameters: dict[str, float | int | bool | str]
    default_enabled: bool = True


TEMPLATE_DEFINITIONS: tuple[TemplateDefinition, ...] = (
    TemplateDefinition(
        "idle_breathing",
        "idle",
        "Idle breathing",
        "A restrained, cosy breathing loop.",
        ("Body", "Head"),
        (),
        1.6,
        True,
        {
            "breathing_strength": "Natural",
            "breathing_speed": "Natural",
            "keep_paws_grounded": True,
            "head_movement": False,
        },
    ),
    TemplateDefinition(
        "tail_sway",
        "tail_sway",
        "Tail sway",
        "Moves any compatible tail around its approved root pivot.",
        ("Tail",),
        (),
        1.8,
        True,
        {
            "speed": "Normal",
            "sway_amount": "Normal",
            "pause_between_sways": False,
            "direction": "Left first",
        },
    ),
    TemplateDefinition(
        "ear_twitch_left",
        "ear_twitch_left",
        "Left ear twitch",
        "A quick twitch which returns to the resting pose.",
        ("EarScreenLeft",),
        (),
        0.45,
        False,
        {"movement_amount": "Normal", "twitch_speed": "Normal", "repeat_count": 1},
        False,
    ),
    TemplateDefinition(
        "ear_twitch_right",
        "ear_twitch_right",
        "Right ear twitch",
        "A quick twitch which returns to the resting pose.",
        ("EarScreenRight",),
        (),
        0.45,
        False,
        {"movement_amount": "Normal", "twitch_speed": "Normal", "repeat_count": 1},
        False,
    ),
    TemplateDefinition(
        "head_tilt_left",
        "head_tilt_left",
        "Head tilt left",
        "Tilts the head and returns exactly to rest.",
        ("Head",),
        (),
        1.0,
        False,
        {
            "direction": "Left",
            "tilt_amount": "Normal",
            "hold_duration": 0.25,
            "return_speed": "Normal",
        },
    ),
    TemplateDefinition(
        "head_tilt_right",
        "head_tilt_right",
        "Head tilt right",
        "Tilts the head and returns exactly to rest.",
        ("Head",),
        (),
        1.0,
        False,
        {
            "direction": "Right",
            "tilt_amount": "Normal",
            "hold_duration": 0.25,
            "return_speed": "Normal",
        },
    ),
    TemplateDefinition(
        "happy_bounce",
        "happy_bounce",
        "Happy bounce",
        "A short celebratory bounce which keeps the current seamless ear construction still.",
        ("Body", "Head"),
        (),
        1.0,
        False,
        {
            "bounce_height": "Normal",
            "bounce_speed": "Normal",
            "number_of_bounces": 2,
            "move_ears_too": False,
            "head_movement": False,
        },
    ),
    TemplateDefinition(
        "blink",
        "blink",
        "Blink",
        "Swaps compatible open and closed eye artwork without squashing it.",
        (),
        (
            "left_open_eye",
            "right_open_eye",
            "left_closed_eye",
            "right_closed_eye",
        ),
        0.22,
        False,
        {"blink_speed": "Normal", "hold_closed_briefly": True},
    ),
)

_DEFINITIONS = {definition.template_id: definition for definition in TEMPLATE_DEFINITIONS}


def default_animation_set(rig_profile: str = "adult_front_sitting") -> AnimationSet:
    return AnimationSet(
        rig_profile=rig_profile,
        templates=[default_template_settings(item.template_id) for item in TEMPLATE_DEFINITIONS],
    )


def default_template_settings(template_id: str) -> AnimationTemplateSettings:
    definition = _definition(template_id)
    return AnimationTemplateSettings(
        template_id=template_id,
        enabled=definition.default_enabled,
        duration=definition.default_duration,
        loop=definition.default_loop,
        parameters=deepcopy(definition.default_parameters),
    )


def reset_template(animation_set: AnimationSet, template_id: str) -> None:
    replacement = default_template_settings(template_id)
    for index, current in enumerate(animation_set.templates):
        if current.template_id == template_id:
            replacement.enabled = current.enabled
            animation_set.templates[index] = replacement
            return
    animation_set.templates.append(replacement)


def reset_all_templates(animation_set: AnimationSet) -> None:
    animation_set.templates = [
        default_template_settings(definition.template_id) for definition in TEMPLATE_DEFINITIONS
    ]


def _definition(template_id: str) -> TemplateDefinition:
    try:
        return _DEFINITIONS[template_id]
    except KeyError as error:
        raise ValueError(f"Unknown animation template: {template_id}") from error


def _joint_paths(template: RigTemplate) -> dict[str, str]:
    paths: dict[str, str] = {}
    for joint in template.joints:
        paths[joint.name] = (
            f"Skeleton2D/{joint.name}"
            if joint.parent is None
            else f"{paths[joint.parent]}/{joint.name}"
        )
    return paths


def discover_eye_assets(project: Project) -> dict[str, str]:
    """Return logical eye states mapped to layer IDs, using explicit state data when present."""
    found: dict[str, str] = {}
    for layer in project.assembly_layers:
        if layer.slot not in {"eye_screen_left", "eye_screen_right"}:
            continue
        side = "left" if layer.slot.endswith("left") else "right"
        hint = (layer.asset_state or f"{layer.display_name} {layer.texture_path}").lower()
        state = "closed" if "closed" in hint else "open"
        found[f"{side}_{state}_eye"] = layer.id
    return found


def compatibility_message(
    settings: AnimationTemplateSettings,
    project: Project,
    *,
    available_joints: set[str] | None = None,
) -> str | None:
    definition = _definition(settings.template_id)
    if (
        settings.template_id in {"ear_twitch_left", "ear_twitch_right"}
        and project.rig_profile == "adult_front_sitting"
    ):
        return (
            "Ear twitch — Not supported by this rig artwork\n"
            "The ear attachment is designed for a seamless static overlap, but rotating it "
            "exposes the join."
        )
    template = get_rig_template(project.rig_profile)
    joints = (
        available_joints
        if available_joints is not None
        else {joint.name for joint in template.joints}
    )
    missing_joints = [joint for joint in definition.required_joints if joint not in joints]
    if missing_joints:
        return (
            "Missing joint"
            + ("s" if len(missing_joints) != 1 else "")
            + ": "
            + ", ".join(missing_joints)
            + "."
        )
    if settings.template_id == "blink":
        assets = discover_eye_assets(project)
        missing = [asset for asset in definition.required_assets if asset not in assets]
        if missing:
            labels = [asset.replace("_", " ") for asset in missing]
            return "Blink cannot be generated yet.\nMissing: " + " and ".join(labels) + "."
    return None


def required_movement_joints(settings: AnimationTemplateSettings) -> tuple[str, ...]:
    if settings.template_id.startswith("head_tilt"):
        return ("Head",)
    if settings.template_id == "tail_sway":
        return ("Tail",)
    if settings.template_id in {"idle_breathing", "happy_bounce"}:
        return ("Head",) if bool(settings.parameters.get("head_movement", False)) else ()
    return ()


def preview_status(settings: AnimationTemplateSettings, project: Project) -> str:
    message = compatibility_message(settings, project)
    if message:
        return (
            "Not supported" if settings.template_id.startswith("ear_twitch") else "Missing artwork"
        )
    for joint_name in required_movement_joints(settings):
        placement = placement_for(project, joint_name)
        if placement is None or not placement.approved:
            return "Preview using suggestion"
    return "Preview ready"


def export_status(settings: AnimationTemplateSettings, project: Project) -> str:
    preview = preview_status(settings, project)
    if preview in {"Missing artwork", "Not supported", "Generation error"}:
        return preview
    for joint_name in required_movement_joints(settings):
        placement = placement_for(project, joint_name)
        if placement is not None and placement.approved and placement.validation_status == "valid":
            continue
        if placement is None or placement.suggestion_x is None:
            return "Needs automatic preparation"
        return "Needs user review"
    if settings.template_id.startswith("head_tilt"):
        treatment = enabled_attachment_treatment(project, "Head")
        if treatment is None:
            return "Needs automatic fix"
        if treatment.verification_status == "Passed with generated attachment treatment":
            return treatment.verification_status
        if treatment.verification_status == "Not supported by this artwork":
            return treatment.verification_status
        return "Needs automatic fix"
    return "Ready for export"


def readiness_status(settings: AnimationTemplateSettings, project: Project) -> str:
    """Compatibility alias for callers that only display preview readiness."""
    return preview_status(settings, project)


def update_compatibility(animation_set: AnimationSet, project: Project) -> dict[str, str]:
    animation_set.preview_status = {
        settings.template_id: preview_status(settings, project)
        for settings in animation_set.templates
    }
    animation_set.export_status = {
        settings.template_id: export_status(settings, project)
        for settings in animation_set.templates
    }
    animation_set.compatibility_status = dict(animation_set.preview_status)
    return animation_set.compatibility_status


def _amount(value: object, values: dict[str, float], default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return values.get(str(value).lower(), default)


def _track(
    path: str,
    property_name: str,
    values: list[tuple[float, object]],
    interpolation: str = "linear",
) -> GeneratedTrack:
    return GeneratedTrack(
        path,
        property_name,
        interpolation,
        tuple(AnimationKey(round(time, 6), value) for time, value in values),
    )


def body_grounding_anchor(
    project: Project, project_directory: Path | None = None
) -> tuple[float, float]:
    """Return the centre of the lowest visible Body paw band in canvas coordinates."""
    body = next((item for item in project.assembly_layers if item.slot == "body"), None)
    if body is not None and project_directory is not None:
        try:
            with Image.open(project.resolve(project_directory, body.texture_path)) as opened:
                alpha = np.asarray(opened.convert("RGBA").getchannel("A"))
            ys, xs = np.where(alpha >= 16)
            if len(xs):
                lowest = int(ys.max())
                band = ys >= lowest - 3
                return (
                    float(np.median(xs[band])) + body.offset_x,
                    float(lowest) + body.offset_y,
                )
        except OSError:
            pass
    pivots = configured_joint_pivots(project)
    body_pivot = pivots.get("Body", (project.canvas_width / 2, project.canvas_height * 0.7))
    return (body_pivot[0], float(project.canvas_height - 1))


def generate_animation(
    settings: AnimationTemplateSettings,
    project: Project,
    *,
    asset_node_paths: dict[str, str] | None = None,
    available_joints: set[str] | None = None,
    purpose: str = "export",
    project_directory: Path | None = None,
) -> GeneratedAnimation:
    message = compatibility_message(settings, project, available_joints=available_joints)
    if message:
        raise AnimationCompatibilityError(message)
    if purpose not in {"preview", "export"}:
        raise ValueError("purpose must be 'preview' or 'export'")
    status = (
        preview_status(settings, project)
        if purpose == "preview"
        else export_status(settings, project)
    )
    blocking = {"Missing artwork", "Not supported", "Generation error"}
    if purpose == "export":
        blocking |= {
            "Needs automatic preparation",
            "Needs automatic fix",
            "Needs user review",
            "Not supported by this artwork",
            "Verification failed",
        }
    if status in blocking:
        raise AnimationCompatibilityError(status)
    definition = _definition(settings.template_id)
    template = get_rig_template(project.rig_profile)
    paths = _joint_paths(template)
    rest = local_rest_positions(template, configured_joint_pivots(project, template))
    p = deepcopy(settings.parameters)
    duration = max(0.01, float(p.get("duration", settings.duration)))
    tracks: list[GeneratedTrack] = []

    if settings.template_id == "idle_breathing":
        strength = str(p.get("breathing_strength", "Natural")).lower()
        scale_x, scale_y = {
            "very subtle": (1.008, 1.006),
            "natural": (1.016, 1.012),
            "noticeable": (1.024, 1.018),
        }.get(strength, (1.016, 1.012))
        x, y = rest["Body"]
        anchor_x, anchor_y = body_grounding_anchor(project, project_directory)
        body_pivot = configured_joint_pivots(project, template)["Body"]
        grounded = bool(p.get("keep_paws_grounded", True))
        compensation = (
            (1.0 - scale_x) * (anchor_x - body_pivot[0]) if grounded else 0.0,
            (1.0 - scale_y) * (anchor_y - body_pivot[1]) if grounded else 0.0,
        )
        p.update(
            breathing_scale_x=scale_x,
            breathing_scale_y=scale_y,
            grounding_anchor_x=anchor_x,
            grounding_anchor_y=anchor_y,
            grounding_tolerance_pixels=0.75,
            grounding_method="lowest visible Body paw band",
        )
        tracks.append(
            _track(
                paths["Body"],
                "scale",
                [(0, (1.0, 1.0)), (duration / 2, (scale_x, scale_y)), (duration, (1.0, 1.0))],
                "cubic",
            )
        )
        if grounded:
            tracks.append(
                _track(
                    paths["Body"],
                    "position",
                    [
                        (0, (x, y)),
                        (duration / 2, (x + compensation[0], y + compensation[1])),
                        (duration, (x, y)),
                    ],
                    "cubic",
                )
            )
    elif settings.template_id == "tail_sway":
        requested = _amount(p.get("sway_amount"), {"subtle": 4, "normal": 8, "expressive": 14}, 8)
        placement = placement_for(project, "Tail")
        safe = requested
        if (
            placement
            and placement.safe_rotation_min is not None
            and placement.safe_rotation_max is not None
        ):
            safe = min(requested, abs(placement.safe_rotation_min), placement.safe_rotation_max)
        p["requested_rotation_degrees"] = requested
        p["generated_rotation_degrees"] = safe
        p["clamped_to_safe_range"] = safe < requested
        angle = math.radians(safe)
        if str(p.get("direction", "Left first")).lower().startswith("right"):
            angle = -angle
        pause = 0.08 * duration if bool(p.get("pause_between_sways", False)) else 0.0
        tracks.append(
            _track(
                paths["Tail"],
                "rotation",
                [
                    (0, 0.0),
                    (duration * 0.25 - pause / 2, angle),
                    (duration * 0.25 + pause / 2, angle),
                    (duration * 0.5, 0.0),
                    (duration * 0.75 - pause / 2, -angle),
                    (duration * 0.75 + pause / 2, -angle),
                    (duration, 0.0),
                ],
            )
        )
    elif settings.template_id.startswith("ear_twitch_"):
        joint = "EarScreenLeft" if settings.template_id.endswith("left") else "EarScreenRight"
        sign = -1 if joint.endswith("Left") else 1
        angle = sign * math.radians(
            _amount(p.get("movement_amount"), {"subtle": 5, "normal": 9, "expressive": 14}, 9)
        )
        repeats = max(1, int(p.get("repeat_count", 1)))
        values: list[tuple[float, object]] = []
        for repeat in range(repeats):
            start = duration * repeat / repeats
            span = duration / repeats
            values.extend(
                [
                    (start, 0.0),
                    (start + span * 0.3, angle),
                    (start + span * 0.55, -angle * 0.35),
                    (start + span, 0.0),
                ]
            )
        tracks.append(_track(paths[joint], "rotation", values))
    elif settings.template_id.startswith("head_tilt_"):
        sign = -1 if settings.template_id.endswith("left") else 1
        requested = sign * _amount(
            p.get("tilt_amount"), {"subtle": 4, "normal": 8, "expressive": 13}, 8
        )
        generated = requested
        placement = placement_for(project, "Head")
        if (
            placement
            and placement.safe_rotation_min is not None
            and placement.safe_rotation_max is not None
        ):
            generated = max(
                placement.safe_rotation_min, min(placement.safe_rotation_max, requested)
            )
        p["requested_rotation_degrees"] = requested
        p["generated_rotation_degrees"] = generated
        p["clamped_to_safe_range"] = generated != requested
        angle = math.radians(generated)
        hold = min(duration * 0.7, max(0.0, float(p.get("hold_duration", 0.25))))
        attack = max(0.05, (duration - hold) * 0.45)
        tracks.append(
            _track(
                paths["Head"],
                "rotation",
                [(0, 0.0), (attack, angle), (attack + hold, angle), (duration, 0.0)],
            )
        )
    elif settings.template_id == "happy_bounce":
        height = _amount(p.get("bounce_height"), {"subtle": 3, "normal": 6, "expressive": 10}, 6)
        count = max(1, int(p.get("number_of_bounces", 2)))
        x, y = rest["Body"]
        values = [(0.0, (x, y))]
        for bounce in range(count):
            start = duration * bounce / count
            span = duration / count
            values.extend(
                [
                    (start + span * 0.35, (x, y - height)),
                    (start + span * 0.7, (x, y + height * 0.18)),
                    (start + span, (x, y)),
                ]
            )
        tracks.append(_track(paths["Body"], "position", values))
        if bool(p.get("head_movement", False)):
            tracks.append(
                _track(
                    paths["Head"],
                    "rotation",
                    [(0, 0.0), (duration * 0.45, math.radians(2.0)), (duration, 0.0)],
                )
            )
        if bool(p.get("move_ears_too", False)) and project.rig_profile != "adult_front_sitting":
            for joint, sign in (("EarScreenLeft", -1), ("EarScreenRight", 1)):
                tracks.append(
                    _track(
                        paths[joint],
                        "rotation",
                        [(0, 0.0), (duration * 0.45, sign * math.radians(3.0)), (duration, 0.0)],
                    )
                )
    elif settings.template_id == "blink":
        assets = discover_eye_assets(project)
        node_paths = asset_node_paths or {
            key: f"Visuals/{layer_id}" for key, layer_id in assets.items()
        }
        close_at = duration * 0.32
        open_at = duration * (0.78 if bool(p.get("hold_closed_briefly", True)) else 0.58)
        for side in ("left", "right"):
            open_path = node_paths[f"{side}_open_eye"]
            closed_path = node_paths[f"{side}_closed_eye"]
            tracks.append(
                _track(
                    open_path,
                    "visible",
                    [(0, True), (close_at, False), (open_at, True), (duration, True)],
                    "nearest",
                )
            )
            tracks.append(
                _track(
                    closed_path,
                    "visible",
                    [(0, False), (close_at, True), (open_at, False), (duration, False)],
                    "nearest",
                )
            )
    else:  # pragma: no cover - definitions and branches are intentionally exhaustive.
        raise ValueError(f"No generator for template: {settings.template_id}")

    return GeneratedAnimation(
        definition.animation_name,
        definition.template_id,
        duration,
        settings.loop,
        tuple(tracks),
        p,
        definition.required_joints,
        definition.required_assets,
    )


def generate_animation_set(
    project: Project,
    *,
    asset_node_paths: dict[str, str] | None = None,
    purpose: str = "export",
    project_directory: Path | None = None,
) -> tuple[list[GeneratedAnimation], dict[str, str]]:
    animation_set = project.animation_set or default_animation_set(project.rig_profile)
    project.animation_set = animation_set
    update_compatibility(animation_set, project)
    blocking = {"Missing artwork", "Not supported", "Generation error"}
    status_map = (
        animation_set.preview_status if purpose == "preview" else animation_set.export_status
    )
    if purpose == "export":
        blocking |= {
            "Needs automatic preparation",
            "Needs automatic fix",
            "Needs user review",
            "Not supported by this artwork",
            "Verification failed",
        }
    warnings = {
        template_id: status for template_id, status in status_map.items() if status in blocking
    }
    warnings = {
        template_id: ("Not supported by this artwork" if status == "Not supported" else status)
        for template_id, status in warnings.items()
    }
    generated: list[GeneratedAnimation] = []
    for settings in animation_set.templates:
        if not settings.enabled:
            continue
        if settings.template_id in warnings:
            continue
        try:
            generated.append(
                generate_animation(
                    settings,
                    project,
                    asset_node_paths=asset_node_paths,
                    purpose=purpose,
                    project_directory=project_directory,
                )
            )
        except AnimationCompatibilityError as error:
            warnings[settings.template_id] = str(error)
    return generated, warnings


def sample_track(track: GeneratedTrack, time: float) -> object:
    keys = track.keys
    if time <= keys[0].time:
        return keys[0].value
    if time >= keys[-1].time:
        return keys[-1].value
    left = keys[0]
    for right in keys[1:]:
        if time <= right.time:
            if track.interpolation == "nearest" or isinstance(left.value, (bool, str)):
                return left.value
            ratio = (time - left.time) / (right.time - left.time)
            if track.interpolation == "cubic":
                ratio = ratio * ratio * (3.0 - 2.0 * ratio)
            if isinstance(left.value, tuple) and isinstance(right.value, tuple):
                return tuple(
                    a + (b - a) * ratio for a, b in zip(left.value, right.value, strict=True)
                )
            return float(left.value) + (float(right.value) - float(left.value)) * ratio
        left = right
    return keys[-1].value


def maximum_extent_times(animation: GeneratedAnimation) -> list[float]:
    """Return key times whose values are furthest from their track's rest value."""
    times: set[float] = set()
    for track in animation.tracks:
        if track.property_name == "visible":
            continue
        rest = track.keys[0].value

        def distance(value: object, rest_value: object = rest) -> float:
            if isinstance(rest_value, tuple) and isinstance(value, tuple):
                return math.hypot(value[0] - rest_value[0], value[1] - rest_value[1])
            if isinstance(rest_value, (int, float)) and isinstance(value, (int, float)):
                return abs(float(value) - float(rest_value))
            return 0.0

        greatest = max(distance(key.value) for key in track.keys)
        times.update(key.time for key in track.keys if distance(key.value) == greatest and greatest)
    return sorted(times) or [animation.duration / 2]


def inspect_animation(project: Project, animation: GeneratedAnimation) -> list[str]:
    warnings: list[str] = []
    joint_names = set(animation.required_joints)
    for joint_name in joint_names:
        placement = placement_for(project, joint_name)
        if placement is None or not placement.approved:
            warnings.append(
                f"{animation.name} needs attention. The {joint_name} movement point has not "
                "been reviewed."
            )
    return warnings
