from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from cat_layer_studio.models.animation import GeneratedAnimation, GeneratedTrack
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.alpha_transform_service import normalise_rgba_for_transform
from cat_layer_studio.services.animation_inspection_service import (
    inspect_rendered_attachment,
    production_sample_times,
)
from cat_layer_studio.services.animation_service import (
    discover_eye_assets,
    generate_animation_set,
    maximum_extent_times,
    sample_track,
)
from cat_layer_studio.services.attachment_treatment_service import (
    discover_divergent_attachments,
    prepare_animation_attachment_treatments,
    with_attachment_visibility_tracks,
)
from cat_layer_studio.services.composition_service import (
    composite_animation_frame,
    composite_assembly,
    project_render_layers,
)
from cat_layer_studio.services.joint_placement_service import resolved_joint_placements
from cat_layer_studio.services.rig_hierarchy_service import evaluate_joint_matrices, joint_paths

SLOT_NODE_NAMES = {
    "tail": "TailVisual",
    "body": "BodyVisual",
    "head": "HeadVisual",
    "ear_screen_left": "EarScreenLeftVisual",
    "ear_screen_right": "EarScreenRightVisual",
    "eye_screen_left": "EyeScreenLeftVisual",
    "eye_screen_right": "EyeScreenRightVisual",
    "expression": "ExpressionVisual",
    "pattern": "PatternVisual",
    "white_marking": "WhiteMarkingVisual",
    "chest_fur": "ChestFurVisual",
    "accessory": "AccessoryVisual",
    "attachment_treatment": "AttachmentCoverageGuard",
}


@dataclass(frozen=True, slots=True)
class GodotExportResult:
    output_directory: Path
    scene_path: Path
    manifest_path: Path
    preview_path: Path
    res_scene_path: str
    animation_library_path: Path
    animation_manifest_path: Path
    rollback_directory: Path | None = None


def _safe_name(name: str) -> str:
    cleaned = "".join(character if character.isalnum() else "_" for character in name)
    return cleaned.strip("_") or "Custom"


def _node_name(layer: AssemblyLayer, used: set[str]) -> str:
    base = SLOT_NODE_NAMES.get(layer.slot, f"{_safe_name(layer.slot).title()}Visual")
    name = base
    suffix = 2
    while name in used:
        name = f"{base}{suffix}"
        suffix += 1
    used.add(name)
    return name


def _fmt(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _scene_text(
    project: Project,
    texture_res_paths: dict[str, str],
    script_res_path: str,
    animation_library_res_path: str,
) -> tuple[str, dict[str, str], dict[str, str]]:
    template = get_rig_template(project.rig_profile)
    closed_eye_ids = {
        layer_id
        for logical_name, layer_id in discover_eye_assets(project).items()
        if "closed" in logical_name
    }
    layers = project_render_layers(project)
    ext = [
        f'[ext_resource type="Script" path="{script_res_path}" id="1_script"]',
        '[ext_resource type="AnimationLibrary" '
        f'path="{animation_library_res_path}" id="2_animations"]',
    ]
    texture_ids: dict[str, str] = {}
    for index, layer in enumerate(layers, 3):
        resource_id = f"{index}_texture"
        texture_ids[layer.id] = resource_id
        ext.append(
            f'[ext_resource type="Texture2D" path="{texture_res_paths[layer.id]}" '
            f'id="{resource_id}"]'
        )
    lines = [f"[gd_scene load_steps={len(ext) + 1} format=3]", "", *ext, ""]
    lines.extend(
        [
            '[node name="ModularCat2D" type="Node2D"]',
            'script = ExtResource("1_script")',
            f"canvas_size = Vector2i({project.canvas_width}, {project.canvas_height})",
            "",
            '[node name="Skeleton2D" type="Skeleton2D" parent="."]',
            "",
        ]
    )
    joint_paths: dict[str, str] = {}
    joint_global = resolved_joint_placements(project)
    for joint in template.joints:
        if joint.parent is None:
            parent = "Skeleton2D"
            local = joint_global[joint.name]
            path = "Skeleton2D/Root"
        else:
            parent_path = joint_paths[joint.parent]
            parent = parent_path
            parent_pivot = joint_global[joint.parent]
            local = (
                joint_global[joint.name][0] - parent_pivot[0],
                joint_global[joint.name][1] - parent_pivot[1],
            )
            path = f"{parent_path}/{joint.name}"
        joint_paths[joint.name] = path
        lines.extend(
            [
                f'[node name="{joint.name}" type="Bone2D" parent="{parent}"]',
                f"position = Vector2({_fmt(local[0])}, {_fmt(local[1])})",
                f"rest = Transform2D(1, 0, 0, 1, {_fmt(local[0])}, {_fmt(local[1])})",
                "",
            ]
        )
    used_names: set[str] = set()
    exported_names: dict[str, str] = {}
    exported_paths: dict[str, str] = {}
    canvas_centre = (project.canvas_width / 2, project.canvas_height / 2)
    for layer in layers:
        joint_name = layer.attachment_joint or template.attachment_map.get(layer.slot, "Root")
        if joint_name not in joint_paths:
            joint_name = "Root"
        parent = joint_paths[joint_name]
        name = _node_name(layer, used_names)
        exported_names[layer.id] = name
        exported_paths[layer.id] = f"{parent}/{name}"
        asset_state = "closed" if layer.id in closed_eye_ids else (layer.asset_state or "default")
        treatment = next(
            (item for item in project.attachment_treatments if item.treatment_id == layer.id),
            None,
        )
        visible_at_rest = layer.visible and layer.id not in closed_eye_ids and treatment is None
        # Full-canvas textures retain their world rest pose while the stable joint moves.
        local_x = canvas_centre[0] + layer.offset_x - joint_global[joint_name][0]
        local_y = canvas_centre[1] + layer.offset_y - joint_global[joint_name][1]
        lines.extend(
            [
                f'[node name="{name}" type="Sprite2D" parent="{parent}" groups=["cat_part_slot"]]',
                f'texture = ExtResource("{texture_ids[layer.id]}")',
                f"position = Vector2({_fmt(local_x)}, {_fmt(local_y)})",
                f"z_index = {layer.z_index}",
                "texture_filter = 2",
                f"visible = {str(visible_at_rest).lower()}",
                f"modulate = Color(1, 1, 1, {_fmt(layer.opacity)})",
                f'metadata/slot_name = "{layer.slot}"',
                f'metadata/asset_state = "{asset_state}"',
                *(
                    [
                        "metadata/generated_attachment = true",
                        f'metadata/treatment_method = "{treatment.method}"',
                        f"metadata/provenance_version = {treatment.provenance_version}",
                    ]
                    if treatment
                    else []
                ),
                "",
            ]
        )
    lines.extend(
        [
            '[node name="AnimationPlayer" type="AnimationPlayer" parent="."]',
            'libraries = {&"": ExtResource("2_animations")}',
            "",
        ]
    )
    return "\n".join(lines), exported_names, exported_paths


def _script_text() -> str:
    return """extends Node2D
class_name ModularCat2D

@export var canvas_size := Vector2i(512, 512)

func _slot_nodes() -> Array[Node]:
    return get_tree().get_nodes_in_group("cat_part_slot")

func get_part(slot: StringName) -> Sprite2D:
    var fallback: Sprite2D = null
    for node in _slot_nodes():
        if self.is_ancestor_of(node):
            if node.get_meta("slot_name", "") == slot:
                if fallback == null:
                    fallback = node as Sprite2D
                if node.get_meta("asset_state", "default") != "closed":
                    return node as Sprite2D
    return fallback

func set_part(slot: StringName, texture: Texture2D) -> bool:
    var part := get_part(slot)
    if part == null:
        return false
    part.texture = texture
    return true

func play_animation(animation_name: StringName) -> void:
    if not has_animation(animation_name):
        push_warning("Unknown cat animation: " + String(animation_name))
        return
    $AnimationPlayer.play(animation_name)

func stop_animation() -> void:
    $AnimationPlayer.stop()

func return_to_rest_pose() -> void:
    $AnimationPlayer.play(&"RESET")
    $AnimationPlayer.advance(0.0)
    $AnimationPlayer.stop()

func has_animation(animation_name: StringName) -> bool:
    return $AnimationPlayer.has_animation(animation_name)
"""


def _godot_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, tuple):
        return f"Vector2({_fmt(float(value[0]))}, {_fmt(float(value[1]))})"
    if isinstance(value, (int, float)):
        return _fmt(float(value))
    return json.dumps(str(value))


def _track_text(track: GeneratedTrack, index: int) -> list[str]:
    times = ", ".join(_fmt(key.time) for key in track.keys)
    transitions = ", ".join(_fmt(key.transition) for key in track.keys)
    values = ", ".join(_godot_value(key.value) for key in track.keys)
    interpolation = {"nearest": 0, "linear": 1, "cubic": 2}.get(track.interpolation, 1)
    update = 1 if track.interpolation == "nearest" else 0
    prefix = f"tracks/{index}"
    return [
        f'{prefix}/type = "value"',
        f"{prefix}/imported = false",
        f"{prefix}/enabled = true",
        f'{prefix}/path = NodePath("{track.target_path}:{track.property_name}")',
        f"{prefix}/interp = {interpolation}",
        f"{prefix}/loop_wrap = true",
        f'{prefix}/keys = {{"times": PackedFloat32Array({times}), '
        f'"transitions": PackedFloat32Array({transitions}), "update": {update}, '
        f'"values": [{values}]}}',
    ]


def _animation_library_text(animations: list[GeneratedAnimation]) -> str:
    reset_tracks: dict[tuple[str, str], GeneratedTrack] = {}
    for animation in animations:
        for track in animation.tracks:
            key = (track.target_path, track.property_name)
            reset_tracks.setdefault(
                key,
                GeneratedTrack(track.target_path, track.property_name, "nearest", (track.keys[0],)),
            )
    reset = GeneratedAnimation("RESET", "reset", 0.0, False, tuple(reset_tracks.values()))
    resources = [reset, *animations]
    lines = [f'[gd_resource type="AnimationLibrary" load_steps={len(resources) + 1} format=3]', ""]
    ids: list[tuple[str, str]] = []
    for index, animation in enumerate(resources, 1):
        resource_id = f"Animation_{index}_{_safe_name(animation.name)}"
        ids.append((animation.name, resource_id))
        lines.extend(
            [
                f'[sub_resource type="Animation" id="{resource_id}"]',
                f'resource_name = "{animation.name}"',
                f"length = {_fmt(animation.duration)}",
            ]
        )
        if animation.loop:
            lines.append("loop_mode = 1")
        for track_index, track in enumerate(animation.tracks):
            lines.extend(_track_text(track, track_index))
        lines.append("")
    entries = ", ".join(f'&"{name}": SubResource("{resource_id}")' for name, resource_id in ids)
    lines.extend(["[resource]", f"_data = {{{entries}}}", ""])
    return "\n".join(lines)


def _verification_script(
    scene_res_path: str,
    preview_res_path: str,
    expected: list[dict[str, object]],
    animations: list[GeneratedAnimation],
    expected_motion: list[dict[str, object]],
) -> str:
    expected_json = json.dumps(expected, separators=(",", ":"))
    animations_json = json.dumps(
        [{"name": item.name, "duration": item.duration, "loop": item.loop} for item in animations],
        separators=(",", ":"),
    )
    motion_json = json.dumps(expected_motion, separators=(",", ":"))
    return f'''extends SceneTree

func _init() -> void:
    call_deferred("_run")

func _run() -> void:
    var packed := load("{scene_res_path}") as PackedScene
    if packed == null:
        _fail("scene did not load")
        return
    var rig := packed.instantiate()
    var viewport := SubViewport.new()
    viewport.size = rig.canvas_size
    viewport.transparent_bg = true
    viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
    root.add_child(viewport)
    viewport.add_child(rig)
    if rig.name != "ModularCat2D" or rig.get_node_or_null("Skeleton2D/Root") == null:
        _fail("stable hierarchy is missing")
        return
    var player := rig.get_node_or_null("AnimationPlayer") as AnimationPlayer
    if player == null:
        _fail("AnimationPlayer is missing")
        return
    var rest_pose := _snapshot_pose(rig)
    var motion_checks = JSON.parse_string('{motion_json}')
    for check in motion_checks:
        rig.return_to_rest_pose()
        rig.play_animation(check.animation)
        player.seek(float(check.time), true)
        var animation_resource := player.get_animation(check.animation)
        for expected_track in check.tracks:
            var track_path := NodePath(expected_track.path + ":" + expected_track.property)
            var track_index := animation_resource.find_track(track_path, Animation.TYPE_VALUE)
            if track_index < 0:
                _fail("missing stable animation target: " + str(track_path))
                return
            var sampled = animation_resource.value_track_interpolate(
                track_index, float(check.time)
            )
            if expected_track.property == "position" or expected_track.property == "scale":
                var expected_value := Vector2(float(expected_track.x), float(expected_track.y))
                if not sampled is Vector2 or sampled.distance_to(expected_value) > 0.001:
                    _fail("Godot track sample mismatch: " + str(track_path))
                    return
            elif absf(float(sampled) - float(expected_track.value)) > 0.0001:
                _fail("Godot track sample mismatch: " + str(track_path))
                return
        for expected_joint in check.joints:
            var bone := rig.get_node_or_null(NodePath(expected_joint.path)) as Bone2D
            if bone == null:
                _fail("missing stable animation target: " + expected_joint.path)
                return
            var expected_position := Vector2(float(expected_joint.x), float(expected_joint.y))
            if bone.global_position.distance_to(expected_position) > 0.001:
                _fail("maximum-extent position mismatch: " + expected_joint.path)
                return
        if DisplayServer.get_name().to_lower() != "headless" and check.reference != "":
            await process_frame
            await process_frame
            var rendered_motion := viewport.get_texture().get_image()
            var reference_motion_texture := load(check.reference) as Texture2D
            if reference_motion_texture == null:
                _fail("motion reference did not load: " + check.reference)
                return
            var motion_difference := _mean_image_difference(
                rendered_motion, reference_motion_texture.get_image()
            )
            if motion_difference > 18.0:
                _fail(
                    "rendered motion parity exceeded tolerance for "
                    + check.animation + ": " + str(motion_difference)
                )
                return
    var animations = JSON.parse_string('{animations_json}')
    for animation in animations:
        rig.return_to_rest_pose()
        if not rig.has_animation(animation.name):
            _fail("missing animation: " + animation.name)
            return
        rig.play_animation(animation.name)
        player.advance(float(animation.duration))
        if not bool(animation.loop) and player.is_playing():
            _fail("non-looping animation did not finish: " + animation.name)
            return
        if not _pose_matches(rig, rest_pose):
            _fail("animation did not return exactly to rest: " + animation.name)
            return
        rig.return_to_rest_pose()
    var expected = JSON.parse_string('{expected_json}')
    for item in expected:
        var part = rig.find_child(item.node_name, true, false) as Sprite2D
        if part == null or part.texture == null:
            _fail("missing slot or texture: " + item.slot)
            return
        if part.z_index != int(item.z_index):
            _fail("wrong z-index: " + item.slot)
            return
        var expected_position := Vector2(float(item.x), float(item.y))
        if part.global_position.distance_to(expected_position) > 0.001:
            _fail("wrong rest position: " + item.slot)
            return
        if rig.find_child(item.attachment_joint, true, false) == null:
            _fail("missing attachment joint: " + item.attachment_joint)
            return
        if item.slot == "attachment_treatment":
            if not bool(part.get_meta("generated_attachment", false)):
                _fail("generated attachment coverage metadata is missing")
                return
            if part.get_meta("treatment_method", "") != item.treatment_method:
                _fail("generated attachment coverage method does not match the manifest")
                return
    if expected.size() > 0:
        var first = rig.get_part(expected[0].slot)
        if rig.has_animation(&"idle"):
            rig.play_animation(&"idle")
            player.advance(0.1)
        if not rig.set_part(expected[0].slot, first.texture):
            _fail("runtime replacement failed")
            return
        if rig.has_animation(&"idle") and not player.is_playing():
            _fail("part replacement stopped idle")
            return
    rig.return_to_rest_pose()
    await process_frame
    await process_frame
    var reference_texture := load("{preview_res_path}") as Texture2D
    if reference_texture == null:
        _fail("preview reference did not load")
        return
    if DisplayServer.get_name().to_lower() == "headless":
        print("PARITY_FALLBACK_DUMMY_RENDERER: exact transforms and generated layers verified")
    else:
        var rendered := viewport.get_texture().get_image()
        var reference := reference_texture.get_image()
        if rendered.get_size() != reference.get_size():
            _fail("rendered preview has the wrong size")
            return
        var mean_difference := _mean_image_difference(rendered, reference)
        if mean_difference > 18.0:
            _fail("rest-pose parity exceeded tolerance: " + str(mean_difference))
            return
    print("CAT_LAYER_STUDIO_VERIFIED")
    quit(0)

func _mean_image_difference(rendered: Image, reference: Image) -> float:
    if rendered.get_size() != reference.get_size():
        return INF
    var difference := 0.0
    for y in rendered.get_height():
        for x in rendered.get_width():
            var actual := rendered.get_pixel(x, y)
            var approved := reference.get_pixel(x, y)
            difference += abs(actual.r - approved.r) * 255.0
            difference += abs(actual.g - approved.g) * 255.0
            difference += abs(actual.b - approved.b) * 255.0
            difference += abs(actual.a - approved.a) * 255.0
    return difference / (rendered.get_width() * rendered.get_height() * 4.0)

func _snapshot_pose(rig: Node) -> Dictionary:
    var result := {{}}
    for node in rig.find_children("*", "Bone2D", true, false):
        result[str(rig.get_path_to(node))] = {{
            "kind": "bone",
            "position": node.position,
            "rotation": node.rotation,
            "scale": node.scale,
        }}
    for node in rig.find_children("*", "Sprite2D", true, false):
        result[str(rig.get_path_to(node))] = {{"kind": "sprite", "visible": node.visible}}
    return result

func _pose_matches(rig: Node, expected: Dictionary) -> bool:
    for path in expected:
        var node := rig.get_node_or_null(NodePath(path))
        if node == null:
            return false
        var state: Dictionary = expected[path]
        if state.kind == "bone":
            if node.position.distance_to(state.position) > 0.0001:
                return false
            if not is_equal_approx(node.rotation, state.rotation):
                return false
            if node.scale.distance_to(state.scale) > 0.0001:
                return false
        elif node.visible != state.visible:
            return false
    return true

func _fail(message: String) -> void:
    push_error(message)
    quit(1)
'''


def export_godot_rig(
    project_directory: Path,
    project: Project,
    godot_project_directory: Path,
    output_relative: str,
) -> GodotExportResult:
    godot_project = godot_project_directory.resolve()
    if not (godot_project / "project.godot").is_file():
        raise ValueError("The selected directory does not contain project.godot.")
    output = (godot_project / output_relative).resolve()
    if output == godot_project or godot_project not in output.parents:
        raise ValueError("The output directory must stay inside the selected Godot project.")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-staging-", dir=output.parent))
    rollback: Path | None = None
    try:
        preview_animations, _preview_warnings = generate_animation_set(
            project, purpose="preview", project_directory=project_directory
        )
        prepare_animation_attachment_treatments(project_directory, project, preview_animations)
        textures = staging / "textures"
        textures.mkdir()
        texture_res_paths: dict[str, str] = {}
        layers = project_render_layers(project)
        used_names: set[str] = set()
        texture_names: dict[str, str] = {}
        for layer in layers:
            base = f"{_safe_name(Path(layer.texture_path).stem)}.png"
            name = base
            counter = 2
            while name.lower() in used_names:
                name = f"{Path(base).stem}_{counter}.png"
                counter += 1
            used_names.add(name.lower())
            texture_names[layer.id] = name
            with Image.open(project.resolve(project_directory, layer.texture_path)) as source:
                normalise_rgba_for_transform(source).save(textures / name)
            texture_res_paths[layer.id] = (
                f"res://{output_relative.replace(os.sep, '/').strip('/')}/textures/{name}"
            )
        output_res_directory = f"res://{output_relative.replace(os.sep, '/').strip('/')}"
        animation_library_name = f"cat_{project.rig_profile}_animations.tres"
        animation_library_res_path = f"{output_res_directory}/{animation_library_name}"
        scene_text, node_names, node_paths = _scene_text(
            project,
            texture_res_paths,
            f"{output_res_directory}/script.gd",
            animation_library_res_path,
        )
        eye_assets = discover_eye_assets(project)
        eye_node_paths = {
            logical_name: node_paths[layer_id]
            for logical_name, layer_id in eye_assets.items()
            if layer_id in node_paths
        }
        animations, animation_warnings = generate_animation_set(
            project,
            asset_node_paths=eye_node_paths,
            project_directory=project_directory,
        )
        animations = with_attachment_visibility_tracks(project, animations, node_paths)
        unresolved_attachments = {
            template_id: reason
            for template_id, reason in animation_warnings.items()
            if next(
                (
                    item.enabled
                    for item in project.animation_set.templates
                    if item.template_id == template_id
                ),
                False,
            )
            and reason
            in {
                "Needs automatic preparation",
                "Needs automatic fix",
                "Needs user review",
                "Not supported by this artwork",
                "Verification failed",
            }
        }
        if unresolved_attachments:
            detail = "; ".join(
                f"{name.replace('_', ' ').title()}: {reason}"
                for name, reason in unresolved_attachments.items()
            )
            raise ValueError(
                "Production export blocked because a visible layer attachment has not passed. "
                f"Run automatic preparation or explicitly disable it. {detail}"
            )
        template = get_rig_template(project.rig_profile)
        attachment_map = {
            layer.attachment_joint or template.attachment_map.get(layer.slot, "Root")
            for layer in layers
            if layer.visible
        }
        verified_animations: list[GeneratedAnimation] = []
        for animation in animations:
            failure: str | None = None
            discovered_joints = {
                relation.joint_name
                for relation in discover_divergent_attachments(project, [animation])
            }
            for joint_name in set(animation.required_joints) | discovered_joints:
                joint = next(item for item in template.joints if item.name == joint_name)
                if joint_name not in attachment_map or joint.parent not in attachment_map:
                    continue
                for time in production_sample_times(animation):
                    diagnostic = inspect_rendered_attachment(
                        project_directory, project, animation, joint_name, time
                    )
                    if diagnostic.status in {"gap", "fringe", "boundary", "unknown"}:
                        failure = diagnostic.message
                        break
                if failure:
                    break
            if failure:
                animation_warnings[animation.template_id] = f"Verification failed: {failure}"
            else:
                verified_animations.append(animation)
        animations = verified_animations
        failed_enabled = {
            animation.template_id: animation_warnings[animation.template_id]
            for animation in preview_animations
            if animation.template_id in animation_warnings
            and next(
                (
                    item.enabled
                    for item in project.animation_set.templates
                    if item.template_id == animation.template_id
                ),
                False,
            )
        }
        if failed_enabled:
            detail = "; ".join(f"{name}: {reason}" for name, reason in failed_enabled.items())
            raise ValueError(f"Production export blocked by attachment verification. {detail}")
        (staging / animation_library_name).write_text(
            _animation_library_text(animations), encoding="utf-8", newline="\n"
        )
        scene_name = f"cat_rig_{project.rig_profile}.tscn"
        scene_res_path = f"{output_res_directory}/{scene_name}"
        (staging / scene_name).write_text(scene_text, encoding="utf-8", newline="\n")
        (staging / "script.gd").write_text(_script_text(), encoding="utf-8", newline="\n")
        manifest_layers = []
        for layer in layers:
            entry = layer.to_dict() | {
                "texture_path": f"textures/{texture_names[layer.id]}",
                "node_name": node_names[layer.id],
            }
            manifest_layers.append(entry)
        manifest = {
            "format_version": 1,
            "rig_profile": project.rig_profile,
            "canvas_size": [project.canvas_width, project.canvas_height],
            "scene_path": scene_res_path,
            "layers": manifest_layers,
            "joint_placements": [item.to_dict() for item in project.joint_placements],
            "attachment_treatments": [item.to_dict() for item in project.attachment_treatments],
        }
        (staging / "cat_rig_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        catalog: dict[str, dict[str, str]] = {}
        closed_layer_ids = {
            layer_id for logical_name, layer_id in eye_assets.items() if "closed" in logical_name
        }
        for layer in layers:
            if layer.slot in catalog and layer.id in closed_layer_ids:
                continue
            catalog[layer.slot] = {
                "node": node_names[layer.id],
                "texture": f"textures/{texture_names[layer.id]}",
            }
        (staging / "cat_part_catalog.json").write_text(
            json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        animation_manifest = {
            "format_version": 1,
            "rig_profile": project.rig_profile,
            "animation_library": animation_library_res_path,
            "generation_timestamp": datetime.now(UTC).isoformat(),
            "animations": [
                {
                    "name": animation.name,
                    "template": animation.template_id,
                    "duration": animation.duration,
                    "loop": animation.loop,
                    "parameters": animation.parameters,
                    "required_joints": list(animation.required_joints),
                    "required_assets": list(animation.required_assets),
                    "compatibility_warnings": [],
                }
                for animation in animations
            ],
            "compatibility_warnings": animation_warnings,
            "joint_placements": [item.to_dict() for item in project.joint_placements],
            "attachment_treatments": [item.to_dict() for item in project.attachment_treatments],
        }
        (staging / "cat_animation_manifest.json").write_text(
            json.dumps(animation_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        composite_assembly(project_directory, project).save(staging / "preview.png")
        expected = [
            {
                "slot": layer.slot,
                "node_name": node_names[layer.id],
                "z_index": layer.z_index,
                "x": project.canvas_width / 2 + layer.offset_x,
                "y": project.canvas_height / 2 + layer.offset_y,
                "attachment_joint": layer.attachment_joint or "Root",
                "treatment_method": next(
                    (
                        item.method
                        for item in project.attachment_treatments
                        if item.treatment_id == layer.id
                    ),
                    "",
                ),
            }
            for layer in layers
        ]
        template = get_rig_template(project.rig_profile)
        paths = joint_paths(template)
        expected_motion: list[dict[str, object]] = []
        verification_frames = staging / "verification_frames"
        verification_frames.mkdir()
        for animation in animations:
            for time in maximum_extent_times(animation):
                _, matrices = evaluate_joint_matrices(project, animation, time)
                active_tracks = [
                    track
                    for track in animation.tracks
                    if track.property_name in {"position", "rotation", "scale"}
                ]
                active_joints = {
                    joint.name
                    for joint in template.joints
                    if any(track.target_path == paths[joint.name] for track in active_tracks)
                }
                frame_name = f"{animation.template_id}_{round(time * 1000):06d}.png"
                composite_animation_frame(project_directory, project, animation, time).save(
                    verification_frames / frame_name
                )
                expected_motion.append(
                    {
                        "animation": animation.name,
                        "time": time,
                        "reference": (
                            f"{output_res_directory}/verification_frames/{frame_name}"
                            if animation.template_id
                            in {"idle_breathing", "head_tilt_left", "head_tilt_right"}
                            else ""
                        ),
                        "tracks": [
                            (
                                {
                                    "path": track.target_path,
                                    "property": track.property_name,
                                    "x": sample_track(track, time)[0],
                                    "y": sample_track(track, time)[1],
                                }
                                if track.property_name in {"position", "scale"}
                                else {
                                    "path": track.target_path,
                                    "property": track.property_name,
                                    "value": sample_track(track, time),
                                }
                            )
                            for track in active_tracks
                        ],
                        "joints": [
                            {
                                "path": paths[joint.name],
                                "x": matrices[joint.name].tx,
                                "y": matrices[joint.name].ty,
                            }
                            for joint in template.joints
                            if joint.name in active_joints
                        ],
                    }
                )
        preview_res_path = f"{output_res_directory}/preview.png"
        (staging / "verify_rig.gd").write_text(
            _verification_script(
                scene_res_path, preview_res_path, expected, animations, expected_motion
            ),
            encoding="utf-8",
            newline="\n",
        )
        if output.exists():
            rollback = Path(tempfile.mkdtemp(prefix=f".{output.name}-previous-", dir=output.parent))
            rollback.rmdir()
            os.replace(output, rollback)
        os.replace(staging, output)
        staging = output  # prevents cleanup from targeting a no-longer-existing source path
        return GodotExportResult(
            output,
            output / scene_name,
            output / "cat_rig_manifest.json",
            output / "preview.png",
            scene_res_path,
            output / animation_library_name,
            output / "cat_animation_manifest.json",
            rollback,
        )
    except Exception:
        if rollback and rollback.exists() and not output.exists():
            os.replace(rollback, output)
        raise
    finally:
        if staging.exists() and staging != output:
            shutil.rmtree(staging)


def accept_export(result: GodotExportResult) -> None:
    if result.rollback_directory and result.rollback_directory.exists():
        shutil.rmtree(result.rollback_directory)


def rollback_export(result: GodotExportResult) -> None:
    if not result.rollback_directory or not result.rollback_directory.exists():
        return
    failed = result.output_directory.with_name(f".{result.output_directory.name}-failed")
    if failed.exists():
        shutil.rmtree(failed)
    os.replace(result.output_directory, failed)
    os.replace(result.rollback_directory, result.output_directory)
    shutil.rmtree(failed)
