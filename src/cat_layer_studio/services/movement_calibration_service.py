from __future__ import annotations

from copy import deepcopy
from dataclasses import fields

from cat_layer_studio.models.joint_placement import MovementCalibrationSession
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.animation_service import required_movement_joints
from cat_layer_studio.services.joint_placement_service import (
    accept_joint_placement,
    placement_for,
)


def _settings(project: Project, template_id: str):
    if project.animation_set is None:
        raise ValueError("This project has no automatic animation setup.")
    return next(
        (item for item in project.animation_set.templates if item.template_id == template_id),
        None,
    )


def begin_calibration_session(
    project: Project, animation_template_id: str
) -> MovementCalibrationSession:
    settings = _settings(project, animation_template_id)
    if settings is None:
        raise ValueError(f"Unknown animation template: {animation_template_id}")
    required = required_movement_joints(settings)
    if len(required) != 1:
        raise ValueError("This animation does not have one calibratable moving hierarchy.")
    joint_name = required[0]
    placement = placement_for(project, joint_name)
    if placement is None:
        raise ValueError(f"No movement point exists for {joint_name}.")
    template = get_rig_template(project.rig_profile)
    joint = next(item for item in template.joints if item.name == joint_name)
    requested = None
    if animation_template_id.startswith("head_tilt"):
        amount = {"subtle": 4.0, "normal": 8.0, "expressive": 13.0}.get(
            str(settings.parameters.get("tilt_amount", "Natural")).lower(), 8.0
        )
        requested = (-amount, amount)
    return MovementCalibrationSession(
        animation_template_id=animation_template_id,
        joint_name=joint_name,
        stationary_parent_joint=joint.parent or "Root",
        original_joint_placement=deepcopy(placement),
        working_joint_placement=deepcopy(placement),
        original_animation_parameters=deepcopy(settings.parameters),
        requested_range=requested,
        project_state_at_start=deepcopy(project.to_dict()),
    )


def refresh_working_state(session: MovementCalibrationSession, project: Project) -> None:
    placement = placement_for(project, session.joint_name)
    if placement is None:
        raise ValueError(f"No movement point exists for {session.joint_name}.")
    session.working_joint_placement = deepcopy(placement)
    session.dirty = placement != session.original_joint_placement


def restore_project_snapshot(project: Project, snapshot: dict) -> None:
    restored = Project.from_dict(deepcopy(snapshot))
    for item in fields(Project):
        setattr(project, item.name, deepcopy(getattr(restored, item.name)))


def cancel_calibration_session(session: MovementCalibrationSession, project: Project) -> None:
    restore_project_snapshot(project, session.project_state_at_start)
    session.working_joint_placement = deepcopy(session.original_joint_placement)
    session.dirty = False
    session.saved = False


def accept_calibration_session(session: MovementCalibrationSession, project: Project) -> None:
    placement = accept_joint_placement(project, session.joint_name)
    session.working_joint_placement = deepcopy(placement)
    session.original_joint_placement = deepcopy(placement)
    session.dirty = False
    session.saved = True
    project.animation_verification_valid = False
    project.godot_export_status = "Needs regeneration"
    if project.animation_set:
        project.animation_set.last_successful_export = None
