from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cat_layer_studio.models.animation import GeneratedAnimation, GeneratedTrack
from cat_layer_studio.models.assembly_layer import AssemblyLayer
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.animation_service import discover_eye_assets, generate_animation_set
from cat_layer_studio.services.composition_service import composite_assembly, ordered_layers

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
    layers = ordered_layers(project.assembly_layers)
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
    joint_global = {joint.name: joint.suggested_pivot for joint in template.joints}
    for joint in template.joints:
        if joint.parent is None:
            parent = "Skeleton2D"
            local = joint.suggested_pivot
            path = "Skeleton2D/Root"
        else:
            parent_path = joint_paths[joint.parent]
            parent = parent_path
            parent_pivot = joint_global[joint.parent]
            local = (
                joint.suggested_pivot[0] - parent_pivot[0],
                joint.suggested_pivot[1] - parent_pivot[1],
            )
            path = f"{parent_path}/{joint.name}"
        joint_paths[joint.name] = path
        lines.extend(
            [
                f'[node name="{joint.name}" type="Bone2D" parent="{parent}"]',
                f"position = Vector2({_fmt(local[0])}, {_fmt(local[1])})",
                "rest = Transform2D(1, 0, 0, 1, 0, 0)",
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
        pivot = (
            layer.pivot_x if layer.pivot_x is not None else joint_global[joint_name][0],
            layer.pivot_y if layer.pivot_y is not None else joint_global[joint_name][1],
        )
        # When a user gives this layer a custom pivot, a private Bone2D keeps the stable template
        # intact while making the visible part rotate around exactly the approved point.
        parent = joint_paths[joint_name]
        if pivot != joint_global[joint_name]:
            pivot_bone = f"{_safe_name(layer.id)}Pivot"
            global_joint = joint_global[joint_name]
            lines.extend(
                [
                    f'[node name="{pivot_bone}" type="Bone2D" parent="{parent}"]',
                    "position = Vector2("
                    f"{_fmt(pivot[0] - global_joint[0])}, "
                    f"{_fmt(pivot[1] - global_joint[1])})",
                    "rest = Transform2D(1, 0, 0, 1, 0, 0)",
                    "",
                ]
            )
            parent = f"{parent}/{pivot_bone}"
        name = _node_name(layer, used_names)
        exported_names[layer.id] = name
        exported_paths[layer.id] = f"{parent}/{name}"
        asset_state = "closed" if layer.id in closed_eye_ids else (layer.asset_state or "default")
        local_x = canvas_centre[0] + layer.offset_x - pivot[0]
        local_y = canvas_centre[1] + layer.offset_y - pivot[1]
        lines.extend(
            [
                f'[node name="{name}" type="Sprite2D" parent="{parent}" groups=["cat_part_slot"]]',
                f'texture = ExtResource("{texture_ids[layer.id]}")',
                f"position = Vector2({_fmt(local_x)}, {_fmt(local_y)})",
                f"z_index = {layer.z_index}",
                f"visible = {str(layer.visible and layer.id not in closed_eye_ids).lower()}",
                f"modulate = Color(1, 1, 1, {_fmt(layer.opacity)})",
                f'metadata/slot_name = "{layer.slot}"',
                f'metadata/asset_state = "{asset_state}"',
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
) -> str:
    expected_json = json.dumps(expected, separators=(",", ":"))
    animations_json = json.dumps(
        [{"name": item.name, "duration": item.duration, "loop": item.loop} for item in animations],
        separators=(",", ":"),
    )
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
    viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
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
    var animations = JSON.parse_string('{animations_json}')
    for animation in animations:
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
    if RenderingServer.get_rendering_device() == null:
        print("PARITY_FALLBACK_DUMMY_RENDERER: exact rest transforms verified")
    else:
        var rendered := viewport.get_texture().get_image()
        var reference := reference_texture.get_image()
        if rendered.get_size() != reference.get_size():
            _fail("rendered preview has the wrong size")
            return
        var difference := 0.0
        for y in rendered.get_height():
            for x in rendered.get_width():
                var actual := rendered.get_pixel(x, y)
                var approved := reference.get_pixel(x, y)
                difference += abs(actual.r - approved.r) * 255.0
                difference += abs(actual.g - approved.g) * 255.0
                difference += abs(actual.b - approved.b) * 255.0
                difference += abs(actual.a - approved.a) * 255.0
        var denominator := rendered.get_width() * rendered.get_height() * 4.0
        var mean_difference := difference / denominator
        if mean_difference > 18.0:
            _fail("rest-pose parity exceeded tolerance: " + str(mean_difference))
            return
    print("CAT_LAYER_STUDIO_VERIFIED")
    quit(0)

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
        textures = staging / "textures"
        textures.mkdir()
        texture_res_paths: dict[str, str] = {}
        layers = ordered_layers(project.assembly_layers)
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
            shutil.copy2(project.resolve(project_directory, layer.texture_path), textures / name)
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
            project, asset_node_paths=eye_node_paths
        )
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
            }
            for layer in layers
        ]
        preview_res_path = f"{output_res_directory}/preview.png"
        (staging / "verify_rig.gd").write_text(
            _verification_script(scene_res_path, preview_res_path, expected, animations),
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
