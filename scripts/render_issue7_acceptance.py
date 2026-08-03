from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cat_layer_studio.services.animation_inspection_service import (
    breathing_perceptual_metrics,
    inspect_rendered_attachment,
)
from cat_layer_studio.services.animation_service import generate_animation
from cat_layer_studio.services.attachment_treatment_service import (
    prepare_head_tilt_attachments,
    set_head_treatment_enabled,
)
from cat_layer_studio.services.composition_service import composite_animation_frame
from cat_layer_studio.services.godot_export_service import export_godot_rig
from cat_layer_studio.services.godot_verification_service import verify_godot_export
from cat_layer_studio.services.joint_placement_service import (
    placement_for,
    prepare_movements_automatically,
)
from cat_layer_studio.services.project_service import load_project


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--godot", type=Path)
    parser.add_argument("--visual-godot", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.fixture / "components", output / "components", dirs_exist_ok=True)
    shutil.copy2(args.fixture / "project.json", output / "project.json")
    directory, project = load_project(output / "project.json")
    head = placement_for(project, "Head")
    if head is None:
        raise RuntimeError("Head movement placement is missing.")
    head.validation_status = "valid"
    head.safe_rotation_min = -8
    head.safe_rotation_max = 8
    movement_preparation = prepare_movements_automatically(directory, project)
    source_paths = {
        label: args.fixture
        / next(layer.texture_path for layer in project.assembly_layers if layer.slot == slot)
        for label, slot in (("Head", "head"), ("Body", "body"))
    }
    source_hashes_before = {label: _sha256(path) for label, path in source_paths.items()}
    treatment_results = prepare_head_tilt_attachments(directory, project)
    previews: dict[str, str] = {}
    diagnostics: dict[str, dict[str, object]] = {}
    breathing_metrics: dict[str, int | bool] = {}
    requests = {
        "idle_exhale": ("idle_breathing", 0.0),
        "idle_inhale_midpoint": ("idle_breathing", None),
        "tail_sway_midpoint": ("tail_sway", None),
        "head_tilt_left_maximum": ("head_tilt_left", "extreme"),
        "head_tilt_right_maximum": ("head_tilt_right", "extreme"),
        "happy_bounce_midpoint": ("happy_bounce", None),
    }
    for label, (template_id, requested_time) in requests.items():
        settings = next(
            item for item in project.animation_set.templates if item.template_id == template_id
        )
        animation = generate_animation(
            settings, project, purpose="preview", project_directory=directory
        )
        if requested_time == "extreme":
            track = next(item for item in animation.tracks if item.property_name == "rotation")
            time = next(key.time for key in track.keys if key.value)
        elif requested_time is None:
            time = animation.duration / 2
        else:
            time = requested_time
        path = output / f"{label}.png"
        frame = composite_animation_frame(directory, project, animation, time)
        frame.save(path)
        if template_id.startswith("head_tilt"):
            frame.crop((100, 130, 412, 286)).resize((936, 468), Image.Resampling.NEAREST).save(
                output / f"{label}_neck_closeup.png"
            )
        previews[label] = path.name
        if template_id.startswith("head_tilt"):
            treated = inspect_rendered_attachment(directory, project, animation, "Head", time)
            set_head_treatment_enabled(project, False)
            untreated_frame = composite_animation_frame(directory, project, animation, time)
            untreated_frame.save(output / f"{label}_untreated.png")
            untreated_frame.crop((100, 130, 412, 286)).resize(
                (936, 468), Image.Resampling.NEAREST
            ).save(output / f"{label}_untreated_neck_closeup.png")
            untreated = inspect_rendered_attachment(directory, project, animation, "Head", time)
            set_head_treatment_enabled(project, True)
            diagnostics[template_id] = {
                "treated": asdict(treated),
                "untreated": asdict(untreated),
            }
        elif template_id == "idle_breathing":
            breathing = breathing_perceptual_metrics(directory, project, animation)
            breathing_metrics = {**asdict(breathing), "passed": breathing.passed}
    source_hashes_after = {label: _sha256(path) for label, path in source_paths.items()}
    report = {
        "source_hashes_before": source_hashes_before,
        "source_hashes_after": source_hashes_after,
        "source_assets_modified": source_hashes_before != source_hashes_after,
        "attachment_results": treatment_results,
        "movement_preparation": {
            "prepared": movement_preparation.prepared,
            "needs_review": movement_preparation.needs_review,
            "missing_artwork": movement_preparation.missing_artwork,
            "unsupported": movement_preparation.not_supported,
        },
        "attachment_diagnostics": diagnostics,
        "attachment_treatment": project.attachment_treatments[0].to_dict(),
        "breathing_perceptual_metrics": breathing_metrics,
        "previews": previews,
    }
    if args.godot:
        game = output / "godot_fixture"
        game.mkdir(exist_ok=True)
        (game / "project.godot").write_text(
            '[application]\nconfig/name="Issue7Acceptance"\n'
            '[rendering]\nrenderer/rendering_method="gl_compatibility"\n',
            encoding="utf-8",
        )
        exported = export_godot_rig(directory, project, game, "assets/cat")
        verification = verify_godot_export(
            args.godot,
            game,
            "res://assets/cat/verify_rig.gd",
            rendered=args.visual_godot,
        )
        exported_manifest = json.loads(exported.animation_manifest_path.read_text(encoding="utf-8"))
        report["godot_verification"] = {
            "passed": verification.passed,
            "message": verification.message,
            "output": verification.output,
            "visual_parity_verified": verification.visual_parity_verified,
            "scene": str(exported.scene_path),
            "animations": [item["name"] for item in exported_manifest["animations"]],
        }
    (output / "acceptance_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
