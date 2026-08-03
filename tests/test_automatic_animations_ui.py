from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_layer_studio.models.assembly_layer import AssemblyLayer  # noqa: E402
from cat_layer_studio.models.attachment_treatment import AttachmentTreatment  # noqa: E402
from cat_layer_studio.models.project import Project  # noqa: E402
from cat_layer_studio.views import automatic_animations_view as animations_module  # noqa: E402
from cat_layer_studio.views.automatic_animations_view import (  # noqa: E402
    AutomaticAnimationsView,
)


def test_workspace_generates_previews_and_persists_parameter_history(
    tmp_path: Path, monkeypatch
) -> None:
    application = QApplication.instance() or QApplication([])
    components = tmp_path / "components"
    components.mkdir()
    Image.new("RGBA", (32, 32), (180, 90, 40, 255)).save(components / "body.png")
    project = Project("Animated UI", "master.png", 32, 32)
    project.assembly_layers = [
        AssemblyLayer(
            "body",
            "Body",
            "components/body.png",
            "body",
            attachment_joint="Body",
            pivot_x=16,
            pivot_y=20,
        )
    ]
    view = AutomaticAnimationsView()
    view.set_project(tmp_path, project)
    application.processEvents()

    assert view.animation_choice.count() == 5
    assert view.current_animation().name == "idle"
    assert project.animation_set.compatibility_status["blink"] == "Missing artwork"
    assert project.animation_set.preview_status["ear_twitch_left"] == "Not supported"
    assert project.animation_set.preview_status["head_tilt_left"] == "Preview using suggestion"
    assert project.animation_set.export_status["head_tilt_left"] == "Needs automatic preparation"
    assert view.compare_rest.isChecked() is False
    assert "preview only" in view.emphasise_movement.text()
    assert not view.preview_exhale.isHidden()
    assert not view.preview_inhale.isHidden()
    view.preview_idle_inhale()
    assert view.current_time == view.current_animation().duration / 2
    view.preview_idle_exhale()
    assert view.current_time == 0.0
    original = project.animation_set.templates[0].parameters["breathing_strength"]
    view._parameter_changed("breathing_strength", "Noticeable")
    assert project.animation_set.templates[0].parameters["breathing_strength"] == "Noticeable"
    view.undo()
    assert project.animation_set.templates[0].parameters["breathing_strength"] == original

    generated = tmp_path / "generated" / "attachments"
    generated.mkdir(parents=True)
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(generated / "head_neck_occlusion.png")

    def prepare_automatically(_directory: Path, target: Project, _animations) -> dict[str, str]:
        target.attachment_treatments = [
            AttachmentTreatment(
                treatment_id="generated_head_neck_occlusion",
                joint_name="Head",
                method="parent_underlay_coverage_guard",
                texture_path="generated/attachments/head_neck_occlusion.png",
                parent_joint="Body",
                z_index=1,
                source_layer_ids=("body",),
                template_ids=("head_tilt_left", "head_tilt_right"),
                algorithm_version=5,
                provenance_version=5,
            )
        ]
        return {
            "head_tilt_left": "Passed with generated attachment treatment",
            "head_tilt_right": "Passed with generated attachment treatment",
        }

    monkeypatch.setattr(
        animations_module, "prepare_animation_attachment_treatments", prepare_automatically
    )
    monkeypatch.setattr(
        animations_module,
        "animation_treatments_are_current",
        lambda _directory, target, _animation: bool(target.attachment_treatments),
    )
    head_tilt_index = next(
        index
        for index, animation in enumerate(view.generated_animations)
        if animation.template_id == "head_tilt_left"
    )
    view.animation_choice.setCurrentIndex(head_tilt_index)
    assert project.attachment_treatments
    assert project.attachment_treatments[0].parent_joint == "Body"
    view.deleteLater()
