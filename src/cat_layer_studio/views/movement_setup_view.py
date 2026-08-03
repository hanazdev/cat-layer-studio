from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from cat_layer_studio.models.animation import AnimationKey, GeneratedAnimation, GeneratedTrack
from cat_layer_studio.models.joint_placement import (
    JointPlacementHistory,
    MovementCalibrationSession,
)
from cat_layer_studio.models.project import Project
from cat_layer_studio.models.rig_template import get_rig_template
from cat_layer_studio.services.attachment_treatment_service import (
    prepare_head_tilt_attachments,
)
from cat_layer_studio.services.composition_service import (
    composite_animation_frame,
    composite_assembly,
)
from cat_layer_studio.services.joint_placement_service import (
    accept_joint_placement,
    attachment_masks,
    ensure_joint_placements,
    find_safe_rotation_range,
    inspect_joint_movement,
    placement_for,
    reset_joint_to_suggestion,
    reset_joint_to_template,
    resolve_joint_placement,
    resolved_joint_placements,
    restore_last_approved_placement,
    set_joint_point,
    template_joint_placement,
    update_suggestion,
)
from cat_layer_studio.services.movement_calibration_service import (
    accept_calibration_session,
    begin_calibration_session,
    cancel_calibration_session,
    refresh_working_state,
)
from cat_layer_studio.services.rig_hierarchy_service import joint_paths, local_rest_positions
from cat_layer_studio.widgets.composite_canvas import CompositeCanvas


class MovementSetupView(QWidget):
    project_changed = Signal()
    movement_point_accepted = Signal(str)
    calibration_cancelled = Signal(str)
    calibration_finished = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.project_directory: Path | None = None
        self.project: Project | None = None
        self.history = JointPlacementHistory()
        self.calibration_session: MovementCalibrationSession | None = None
        self.current_time = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

        self.heading = QLabel("Movement Setup")
        self.heading.setStyleSheet("font-size: 20px; font-weight: 600")
        self.intro = QLabel(
            "The app placed this movement point inside the part attachment. Preview the "
            "movement below. If it looks correct, choose Accept."
        )
        self.intro.setWordWrap(True)
        self.joint_choice = QComboBox()
        self.joint_choice.currentTextChanged.connect(self._joint_changed)
        self.connection = QLabel("Choose the moving part.")
        self.connection.setWordWrap(True)
        self.confidence = QLabel()
        self.confidence.setWordWrap(True)
        self.x_value = self._coordinate_spin()
        self.y_value = self._coordinate_spin()
        self.x_value.valueChanged.connect(
            lambda value: self._numeric_changed(value, self.y_value.value())
        )
        self.y_value.valueChanged.connect(
            lambda value: self._numeric_changed(self.x_value.value(), value)
        )
        form = QFormLayout()
        form.addRow("Movement point X", self.x_value)
        form.addRow("Movement point Y", self.y_value)

        nudge = QGridLayout()
        for column, amount in enumerate((0.25, 1.0, 5.0)):
            for row, (label, dx, dy) in enumerate(
                (("←", -amount, 0), ("→", amount, 0), ("↑", 0, -amount), ("↓", 0, amount))
            ):
                button = QPushButton(f"{label} {amount:g}px")
                button.clicked.connect(lambda _checked=False, x=dx, y=dy: self.nudge(x, y))
                nudge.addWidget(button, row, column)
        self.reset_suggestion_button = QPushButton("Reset to recommended point")
        reset_template = QPushButton("Reset to rig default")
        reset_all = QPushButton("Reset all joints")
        self.undo_button = QPushButton("Undo last change")
        self.redo_button = QPushButton("Redo")
        self.restore_accepted_button = QPushButton("Restore last accepted point")
        self.cancel_button = QPushButton("Cancel all changes")
        self.accept_button = QPushButton("Use this setup")
        self.reset_suggestion_button.clicked.connect(self.reset_to_suggestion)
        reset_template.clicked.connect(self.reset_to_template)
        reset_all.clicked.connect(self.reset_all)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.restore_accepted_button.clicked.connect(self.restore_last_accepted)
        self.cancel_button.clicked.connect(self.cancel_all_changes)
        self.accept_button.clicked.connect(self.accept)
        edit_actions = QGridLayout()
        for index, button in enumerate((reset_template, reset_all)):
            edit_actions.addWidget(button, index // 2, index % 2)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        self.moving_hierarchy = QLabel("Moving now: choose a part")
        self.moving_hierarchy.setWordWrap(True)
        self.session_status = QLabel("Saved — this movement setup will be used for export")
        self.session_status.setWordWrap(True)
        editor_layout.addWidget(self.moving_hierarchy)
        editor_layout.addWidget(self.session_status)
        editor_layout.addWidget(self.connection)
        editor_layout.addWidget(self.confidence)
        advanced = QGroupBox("Advanced: fine-tune movement point")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.addWidget(QLabel("Generic joint selector"))
        advanced_layout.addWidget(self.joint_choice)
        advanced_layout.addLayout(form)
        advanced_layout.addWidget(QLabel("Drag the orange point or nudge it"))
        advanced_layout.addLayout(nudge)
        advanced_layout.addLayout(edit_actions)
        editor_layout.addWidget(advanced)
        recovery = QGridLayout()
        for index, button in enumerate(
            (
                self.undo_button,
                self.redo_button,
                self.reset_suggestion_button,
                self.restore_accepted_button,
                self.cancel_button,
                self.accept_button,
            )
        ):
            recovery.addWidget(button, index // 2, index % 2)
        editor_layout.addWidget(QLabel("Recovery and save"))
        editor_layout.addLayout(recovery)
        editor_layout.addStretch()

        self.canvas = CompositeCanvas()
        self.canvas.movement_point_changed.connect(self._dragged)
        self.rotation = QDoubleSpinBox()
        self.rotation.setRange(0, 30)
        self.rotation.setValue(8)
        self.rotation.setSuffix("°")
        self.rotation.valueChanged.connect(lambda _value: self._render())
        self.x_test = QDoubleSpinBox()
        self.y_test = QDoubleSpinBox()
        for field in (self.x_test, self.y_test):
            field.setRange(-30, 30)
            field.setSuffix(" px")
            field.valueChanged.connect(lambda _value: self._render())
        test_form = QFormLayout()
        test_form.addRow("Rotation amount", self.rotation)
        test_form.addRow("X translation test", self.x_test)
        test_form.addRow("Y translation test", self.y_test)
        play = QPushButton("Play")
        pause = QPushButton("Pause")
        restart = QPushButton("Restart")
        rest = QPushButton("Return to rest")
        play.clicked.connect(self.play)
        pause.clicked.connect(self.pause)
        restart.clicked.connect(self.restart)
        rest.clicked.connect(self.return_to_rest)
        self.slow = QCheckBox("Slow motion")
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(True)
        self.ghost = QCheckBox("Diagnostic: overlay resting pose")
        self.ghost.setChecked(False)
        self.ghost.toggled.connect(self._ghost_toggled)
        self.ghost_banner = QLabel(
            "Diagnostic comparison is on. The duplicated image is the resting pose overlay, "
            "not an animation seam."
        )
        self.ghost_banner.setWordWrap(True)
        self.ghost_banner.setVisible(False)
        self.points = QCheckBox("Show movement points")
        self.points.setChecked(True)
        self.points.toggled.connect(lambda _checked: self._render())
        self.extents = QCheckBox("Show negative and positive maximum extents")
        self.extents.toggled.connect(lambda _checked: self._render())
        controls = QHBoxLayout()
        for item in (play, pause, restart, rest, self.slow, self.loop):
            controls.addWidget(item)
        self.timeline = QSlider()
        self.timeline.setRange(0, 1000)
        self.timeline.valueChanged.connect(self._scrubbed)
        find_safe = QPushButton("Find safe movement range")
        find_safe.clicked.connect(self.find_safe_range)
        self.fix_attachment = QPushButton("Fix head attachment automatically")
        self.fix_attachment.clicked.connect(self.fix_head_attachment)
        self.fix_attachment.setVisible(False)
        left_extreme = QPushButton("Preview left extreme")
        resting_pose = QPushButton("Preview resting pose")
        right_extreme = QPushButton("Preview right extreme")
        full_movement = QPushButton("Play full movement")
        left_extreme.clicked.connect(lambda: self._show_time(0.5))
        resting_pose.clicked.connect(lambda: self._show_time(0.0))
        right_extreme.clicked.connect(lambda: self._show_time(1.5))
        full_movement.clicked.connect(self.restart)
        quick_inspection = QHBoxLayout()
        for button in (left_extreme, resting_pose, right_extreme, full_movement):
            quick_inspection.addWidget(button)
        self.diagnostic = QLabel("4. Check the attachment — preview a movement.")
        self.diagnostic.setWordWrap(True)
        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.addWidget(QLabel("3. Preview the movement"))
        preview_layout.addWidget(self.canvas, 1)
        preview_layout.addLayout(controls)
        preview_layout.addWidget(self.timeline)
        preview_layout.addLayout(quick_inspection)
        preview_layout.addLayout(test_form)
        preview_layout.addWidget(self.ghost)
        preview_layout.addWidget(self.ghost_banner)
        preview_layout.addWidget(
            QLabel(
                "Orange dot — current movement point   Blue circle — suggested point\n"
                "Pink area — where the two parts overlap   Grey outline — resting pose comparison"
            )
        )
        preview_layout.addWidget(self.points)
        preview_layout.addWidget(self.extents)
        preview_layout.addWidget(find_safe)
        preview_layout.addWidget(self.fix_attachment)
        preview_layout.addWidget(self.diagnostic)

        splitter = QSplitter()
        splitter.addWidget(editor)
        splitter.addWidget(preview)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.intro)
        layout.addWidget(splitter, 1)
        self._install_shortcuts()

    @staticmethod
    def _coordinate_spin() -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(-8192, 8192)
        field.setDecimals(2)
        field.setSingleStep(0.25)
        return field

    def set_project(self, directory: Path, project: Project) -> None:
        self.pause()
        self.project_directory, self.project = directory, project
        self.calibration_session = None
        ensure_joint_placements(project)
        self.history.reset(project.joint_placements)
        current = self.joint_choice.currentText()
        self.joint_choice.blockSignals(True)
        self.joint_choice.clear()
        self.joint_choice.addItems(
            [joint.name for joint in get_rig_template(project.rig_profile).joints if joint.parent]
        )
        self.joint_choice.setCurrentText(current or "Head")
        self.joint_choice.blockSignals(False)
        self._joint_changed(self.joint_choice.currentText())

    def open_context(self, animation_template_id: str) -> None:
        if not self.project:
            return
        self.calibration_session = begin_calibration_session(self.project, animation_template_id)
        session = self.calibration_session
        self.history.reset(self.project.joint_placements)
        self.heading.setText(f"Fix {animation_template_id.replace('_', ' ').title()}")
        self.intro.setText(
            f"Moving part: {session.joint_name}\nAttached to: "
            f"{session.stationary_parent_joint}\nOnly the head, ears and eyes will turn. "
            "The body will remain still."
        )
        self.joint_choice.blockSignals(True)
        self.joint_choice.clear()
        self.joint_choice.addItem(session.joint_name)
        self.joint_choice.setCurrentText(session.joint_name)
        self.joint_choice.blockSignals(False)
        self.session_status.setText("Previewing — changes are temporary")
        self.fix_attachment.setVisible(session.joint_name == "Head")
        self._joint_changed(session.joint_name)

    def open_joint(self, joint_name: str) -> None:
        self.calibration_session = None
        self.heading.setText("Movement Setup")
        self.session_status.setText("Saved — this movement setup will be used for export")
        self.fix_attachment.setVisible(False)
        self.joint_choice.setCurrentText(joint_name)

    def selected_joint(self) -> str:
        return self.joint_choice.currentText()

    def _joint_changed(self, joint_name: str) -> None:
        if not self.project or not self.project_directory or not joint_name:
            return
        placement = placement_for(self.project, joint_name)
        if placement and placement.suggestion_x is None and not placement.approved:
            update_suggestion(self.project_directory, self.project, joint_name)
            self.history.commit(self.project.joint_placements)
            self.project_changed.emit()
        self.current_time = 0.0
        self.timeline.blockSignals(True)
        self.timeline.setValue(0)
        self.timeline.blockSignals(False)
        self._refresh_fields()
        self._render()

    def _refresh_fields(self) -> None:
        if not self.project or not (
            placement := placement_for(self.project, self.selected_joint())
        ):
            return
        for field, value in ((self.x_value, placement.x), (self.y_value, placement.y)):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)
        template = get_rig_template(self.project.rig_profile)
        joint = next(item for item in template.joints if item.name == placement.joint_name)
        self.connection.setText(
            f"{joint.name}\nAttached to: {joint.parent}\nTest: "
            + (
                "tilt left and right"
                if joint.name == "Head"
                else "move through both maximum extents"
            )
        )
        descendants = {
            "Head": "Head\n├── Ears\n└── Eyes",
            "Body": "Body\n├── Head, Ears and Eyes\n└── Tail",
            "Tail": "Tail",
        }.get(joint.name, joint.name)
        stationary = "Body and Tail" if joint.name == "Head" else (joint.parent or "none")
        self.moving_hierarchy.setText(
            f"Moving now:\n{descendants}\n\nStationary:\n{stationary}"
            + (
                "\n\nYou are now testing the whole body, not the head."
                if joint.name == "Body"
                else ""
            )
        )
        review = "Accepted" if placement.approved else "Review needed"
        self.restore_accepted_button.setEnabled(
            placement.last_approved_x is not None and placement.last_approved_y is not None
        )
        suggestion_x = placement.suggestion_x if placement.suggestion_x is not None else placement.x
        suggestion_y = placement.suggestion_y if placement.suggestion_y is not None else placement.y
        reason = placement.suggestion_reason or "Rig fallback reference."
        self.confidence.setText(
            f"Suggested movement point: ({suggestion_x:.2f}, {suggestion_y:.2f})\n"
            f"Confidence: {placement.confidence.title()} — {reason}\nStatus: {review}"
        )

    def _commit(self) -> None:
        if not self.project:
            return
        self.history.commit(self.project.joint_placements)
        if self.calibration_session:
            refresh_working_state(self.calibration_session, self.project)
            self.session_status.setText("Previewing — changes are temporary")
        else:
            self.project_changed.emit()
        self._refresh_fields()
        self._render()

    def _dragged(self, x: float, y: float) -> None:
        if self.project:
            set_joint_point(self.project, self.selected_joint(), x, y)
            self._commit()

    def _numeric_changed(self, x: float, y: float) -> None:
        if self.project:
            set_joint_point(self.project, self.selected_joint(), x, y)
            self._commit()

    def nudge(self, dx: float, dy: float) -> None:
        if self.project and (placement := placement_for(self.project, self.selected_joint())):
            set_joint_point(self.project, placement.joint_name, placement.x + dx, placement.y + dy)
            self._commit()

    def reset_to_suggestion(self) -> None:
        if self.project:
            reset_joint_to_suggestion(self.project, self.selected_joint())
            self._commit()

    def reset_to_template(self) -> None:
        if self.project:
            reset_joint_to_template(self.project, self.selected_joint())
            self._commit()

    def restore_last_accepted(self) -> None:
        if self.project:
            restore_last_approved_placement(self.project, self.selected_joint())
            self._commit()

    def cancel_all_changes(self) -> None:
        if not self.project or not self.calibration_session:
            return
        template_id = self.calibration_session.animation_template_id
        cancel_calibration_session(self.calibration_session, self.project)
        self.calibration_session = None
        self.project_changed.emit()
        self.calibration_cancelled.emit(template_id)

    def has_temporary_session(self) -> bool:
        return self.calibration_session is not None

    def reset_all(self) -> None:
        if self.project:
            for joint in get_rig_template(self.project.rig_profile).joints:
                reset_joint_to_template(self.project, joint.name)
            self._commit()

    def undo(self) -> None:
        if self.project and (state := self.history.undo()) is not None:
            self.project.joint_placements = state
            self.project_changed.emit()
            self._refresh_fields()
            self._render()

    def redo(self) -> None:
        if self.project and (state := self.history.redo()) is not None:
            self.project.joint_placements = state
            self.project_changed.emit()
            self._refresh_fields()
            self._render()

    def accept(self) -> None:
        if self.project:
            if self.calibration_session:
                template_id = self.calibration_session.animation_template_id
                accept_calibration_session(self.calibration_session, self.project)
                placement = placement_for(self.project, self.selected_joint())
                self.session_status.setText("Saved — this movement setup will be used for export")
                self.calibration_session = None
            else:
                template_id = ""
                placement = accept_joint_placement(self.project, self.selected_joint())
            assert placement is not None
            self.history.commit(self.project.joint_placements)
            self.project_changed.emit()
            self.movement_point_accepted.emit(placement.joint_name)
            if template_id:
                self.calibration_finished.emit(template_id)
            self._refresh_fields()

    def _test_animation(self) -> GeneratedAnimation | None:
        if not self.project:
            return None
        template = get_rig_template(self.project.rig_profile)
        joint_name = self.selected_joint()
        paths = joint_paths(template)
        rest = local_rest_positions(template, resolved_joint_placements(self.project))
        duration = 2.0
        keys = (0.0, 0.5, 1.0, 1.5, 2.0)
        tracks: list[GeneratedTrack] = []
        requested = (
            max(abs(value) for value in self.calibration_session.requested_range)
            if self.calibration_session and self.calibration_session.requested_range
            else self.rotation.value()
        )
        angle = math.radians(requested)
        if angle:
            tracks.append(
                GeneratedTrack(
                    paths[joint_name],
                    "rotation",
                    "linear",
                    tuple(
                        AnimationKey(time, value)
                        for time, value in zip(keys, (0.0, -angle, 0.0, angle, 0.0), strict=True)
                    ),
                )
            )
        x, y = rest[joint_name]
        if self.x_test.value() or self.y_test.value():
            dx, dy = self.x_test.value(), self.y_test.value()
            tracks.append(
                GeneratedTrack(
                    paths[joint_name],
                    "position",
                    "linear",
                    tuple(
                        AnimationKey(time, value)
                        for time, value in zip(
                            keys,
                            ((x, y), (x - dx, y - dy), (x, y), (x + dx, y + dy), (x, y)),
                            strict=True,
                        )
                    ),
                )
            )
        return GeneratedAnimation("movement_test", "movement_test", duration, True, tuple(tracks))

    def _render(self) -> None:
        if not self.project or not self.project_directory:
            return
        animation = self._test_animation()
        image = (
            composite_assembly(self.project_directory, self.project)
            if animation is None
            else composite_animation_frame(
                self.project_directory, self.project, animation, self.current_time
            )
        )
        if animation is not None and self.ghost.isChecked() and self.current_time:
            rest = composite_assembly(self.project_directory, self.project)
            image = Image.blend(rest, image, 0.78)
        if animation is not None and self.extents.isChecked():
            for extent_time in (0.5, 1.5):
                extent = composite_animation_frame(
                    self.project_directory, self.project, animation, extent_time
                )
                image = Image.blend(image, extent, 0.18)
        # Attachment regions: parent red, child blue, overlap magenta.
        try:
            parent, child, overlap = attachment_masks(
                self.project_directory, self.project, self.selected_joint()
            )
            colour = np.zeros(
                (self.project.canvas_height, self.project.canvas_width, 4), dtype=np.uint8
            )
            colour[parent] = (255, 90, 70, 38)
            colour[child] = (60, 150, 255, 38)
            colour[overlap] = (255, 0, 220, 105)
            image = Image.alpha_composite(image, Image.fromarray(colour, "RGBA"))
        except ValueError:
            pass
        self.canvas.set_image(image)
        placement = placement_for(self.project, self.selected_joint())
        if placement and self.points.isChecked():
            suggested = (
                None
                if placement.suggestion_x is None
                else (placement.suggestion_x, placement.suggestion_y)
            )
            self.canvas.show_movement_points(
                (placement.x, placement.y),
                suggested,
                template_joint_placement(self.project, placement.joint_name),
            )
            template = get_rig_template(self.project.rig_profile)
            joint = next(item for item in template.joints if item.name == placement.joint_name)
            parent = resolve_joint_placement(self.project, joint.parent) if joint.parent else None
            self.canvas.show_joint_connection(parent, (placement.x, placement.y))
        else:
            self.canvas.show_movement_points(None)
            self.canvas.show_joint_connection(None, None)
        angle = math.degrees(
            math.sin(self.current_time * math.pi) * math.radians(self.rotation.value())
        )
        diagnostic = inspect_joint_movement(
            self.project_directory, self.project, self.selected_joint(), angle
        )
        self.diagnostic.setText(
            f"4. Check the attachment — {diagnostic.message}\n"
            f"Overlap retained: {diagnostic.overlap_ratio:.0%}"
        )

    def find_safe_range(self) -> None:
        if self.project and self.project_directory:
            minimum, maximum = find_safe_rotation_range(
                self.project_directory, self.project, self.selected_joint()
            )
            self.history.commit(self.project.joint_placements)
            self.project_changed.emit()
            self.diagnostic.setText(
                f"Safe movement range: {minimum:+g}° to {maximum:+g}°. "
                "Review the extremes, then accept the movement point."
            )
            self._refresh_fields()

    def fix_head_attachment(self) -> None:
        if not self.project or not self.project_directory:
            return
        results = prepare_head_tilt_attachments(self.project_directory, self.project)
        self.diagnostic.setText(
            "Automatic attachment check\n"
            + "\n".join(
                f"{template_id.replace('_', ' ').title()} — {status}"
                for template_id, status in results.items()
            )
        )
        if self.calibration_session:
            self.calibration_session.dirty = True
        self._render()

    def _show_time(self, time: float) -> None:
        self.pause()
        self.current_time = time
        self.timeline.blockSignals(True)
        self.timeline.setValue(round(time / 2.0 * 1000))
        self.timeline.blockSignals(False)
        self._render()

    def play(self) -> None:
        self.timer.start()

    def _ghost_toggled(self, checked: bool) -> None:
        self.ghost_banner.setVisible(checked)
        self._render()

    def pause(self) -> None:
        self.timer.stop()

    def restart(self) -> None:
        self.current_time = 0.0
        self.timeline.setValue(0)
        self.play()

    def return_to_rest(self) -> None:
        self.pause()
        self.current_time = 0.0
        self.timeline.setValue(0)
        self._render()

    def _tick(self) -> None:
        speed = 0.25 if self.slow.isChecked() else 1.0
        self.current_time += 0.033 * speed
        if self.current_time > 2.0:
            if self.loop.isChecked():
                self.current_time %= 2.0
            else:
                self.return_to_rest()
                return
        self.timeline.blockSignals(True)
        self.timeline.setValue(round(self.current_time / 2.0 * 1000))
        self.timeline.blockSignals(False)
        self._render()

    def _scrubbed(self, value: int) -> None:
        self.current_time = value / 1000 * 2.0
        self._render()

    def _install_shortcuts(self) -> None:
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in (
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
        ):
            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            for sequence, amount in ((key, 1.0), (f"Shift+{key}", 5.0), (f"Alt+{key}", 0.25)):
                shortcut = QShortcut(QKeySequence(sequence), self.canvas)
                shortcut.activated.connect(
                    lambda x=dx, y=dy, scale=amount: self.nudge(x * scale, y * scale)
                )
                self._shortcuts.append(shortcut)
