from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from cat_layer_studio.models.animation import (
    AnimationHistory,
    AnimationTemplateSettings,
    GeneratedAnimation,
)
from cat_layer_studio.models.project import Project
from cat_layer_studio.services.animation_inspection_service import inspect_animation_frames
from cat_layer_studio.services.animation_service import (
    TEMPLATE_DEFINITIONS,
    default_animation_set,
    generate_animation_set,
    maximum_extent_times,
    reset_all_templates,
    reset_template,
    sample_track,
    update_compatibility,
)
from cat_layer_studio.services.attachment_treatment_service import (
    animation_treatments_are_current,
    prepare_animation_attachment_treatments,
    prepare_head_tilt_attachments,
)
from cat_layer_studio.services.composition_service import (
    composite_animation_frame,
    composite_assembly,
)
from cat_layer_studio.services.joint_placement_service import (
    prepare_movements_automatically,
    resolve_joint_placement,
)
from cat_layer_studio.widgets.composite_canvas import CompositeCanvas


class AutomaticAnimationsView(QWidget):
    project_changed = Signal()
    adjust_movement_point_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.project_directory: Path | None = None
        self.project: Project | None = None
        self.generated_animations: list[GeneratedAnimation] = []
        self.current_time = 0.0
        self.history = AnimationHistory()
        self.parameter_widgets: dict[str, QWidget] = {}
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

        heading = QLabel("Automatic Animations")
        heading.setStyleSheet("font-size: 20px; font-weight: 600")
        intro = QLabel(
            "Choose ready-made movements, adjust them in everyday language, and preview the "
            "result. Cat Layer Studio creates the tracks and keyframes for you."
        )
        intro.setWordWrap(True)
        self.rig_status = QLabel("1. Check the rig — open a project to begin.")
        self.rig_status.setWordWrap(True)

        self.templates = QListWidget()
        self.templates.currentRowChanged.connect(self._template_selected)
        self.template_enabled = QCheckBox("Include this animation")
        self.template_enabled.toggled.connect(self._enabled_changed)
        self.requirements = QLabel()
        self.requirements.setWordWrap(True)
        self.fix_movement = QPushButton("Fix movement automatically")
        self.fix_movement.clicked.connect(self._fix_selected_movement)
        self.adjust_movement_point = QPushButton("Review setup")
        self.adjust_movement_point.clicked.connect(self._adjust_movement_point)
        self.parameter_box = QGroupBox("3. Adjust movement")
        self.parameter_form = QFormLayout(self.parameter_box)
        reset_one = QPushButton("Reset this animation")
        reset_all = QPushButton("Reset all animations")
        reset_one.clicked.connect(self._reset_one)
        reset_all.clicked.connect(self._reset_all)
        reset_row = QHBoxLayout()
        reset_row.addWidget(reset_one)
        reset_row.addWidget(reset_all)

        choose_panel = QWidget()
        choose_layout = QVBoxLayout(choose_panel)
        choose_layout.addWidget(QLabel("2. Choose animations"))
        choose_layout.addWidget(self.templates)
        choose_layout.addWidget(self.template_enabled)
        choose_layout.addWidget(self.requirements)
        choose_layout.addWidget(self.fix_movement)
        choose_layout.addWidget(self.adjust_movement_point)
        choose_layout.addWidget(self.parameter_box)
        choose_layout.addLayout(reset_row)

        self.canvas = CompositeCanvas()
        self.animation_choice = QComboBox()
        self.animation_choice.currentIndexChanged.connect(self._animation_selected)
        play = QPushButton("Play")
        pause = QPushButton("Pause")
        restart = QPushButton("Restart")
        rest = QPushButton("Return to resting pose")
        self.preview_exhale = QPushButton("Preview exhale")
        self.preview_inhale = QPushButton("Preview inhale")
        self.preview_exhale.setVisible(False)
        self.preview_inhale.setVisible(False)
        play.clicked.connect(self.play)
        pause.clicked.connect(self.pause)
        restart.clicked.connect(self.restart)
        rest.clicked.connect(self.return_to_rest_pose)
        self.preview_exhale.clicked.connect(self.preview_idle_exhale)
        self.preview_inhale.clicked.connect(self.preview_idle_inhale)
        self.loop = QCheckBox("Loop preview")
        self.loop.setChecked(True)
        self.loop.toggled.connect(self._preview_options_changed)
        self.slow_motion = QCheckBox("Slow motion")
        self.slow_motion.toggled.connect(self._preview_options_changed)
        self.compare_rest = QCheckBox("Diagnostic: overlay resting pose")
        self.compare_rest.setChecked(False)
        self.compare_rest.toggled.connect(self._diagnostic_overlay_toggled)
        self.ghost_banner = QLabel(
            "Diagnostic comparison is on. The duplicated image is the resting pose overlay, "
            "not an animation seam."
        )
        self.ghost_banner.setWordWrap(True)
        self.ghost_banner.setVisible(False)
        self.emphasise_movement = QCheckBox("Emphasise movement for checking (preview only ×4)")
        self.emphasise_movement.toggled.connect(lambda _checked: self._render())
        self.transform_debug = QCheckBox("Developer: show transform diagnostics")
        self.transform_debug.toggled.connect(lambda _checked: self._render())
        self.show_joints = QCheckBox("Show movement joints")
        self.show_joints.toggled.connect(lambda _checked: self._render())
        self.show_extents = QCheckBox("Show maximum-extents frames")
        self.show_extents.toggled.connect(self._extent_toggled)
        controls = QHBoxLayout()
        for widget in (
            play,
            pause,
            restart,
            rest,
            self.loop,
            self.slow_motion,
            self.compare_rest,
        ):
            controls.addWidget(widget)
        breathing_extremes = QHBoxLayout()
        breathing_extremes.addWidget(self.preview_exhale)
        breathing_extremes.addWidget(self.preview_inhale)
        self.scrubber = QSlider()
        self.scrubber.setRange(0, 1000)
        self.scrubber.valueChanged.connect(self._scrubbed)
        self.time_label = QLabel("No animation generated yet")
        self.motion_label = QLabel("Current movement: resting pose")
        self.warning = QLabel("5. Check for gaps — generate a preview to run the checks.")
        self.warning.setWordWrap(True)
        prepare = QPushButton("Prepare movements automatically")
        prepare.clicked.connect(self.prepare_movements)
        generate = QPushButton("Prepare animations for export")
        generate.clicked.connect(self.prepare_for_export)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.addWidget(QLabel("4. Preview"))
        preview_layout.addWidget(self.animation_choice)
        preview_layout.addWidget(self.canvas, 1)
        preview_layout.addLayout(controls)
        preview_layout.addLayout(breathing_extremes)
        preview_layout.addWidget(self.scrubber)
        preview_layout.addWidget(self.time_label)
        preview_layout.addWidget(self.motion_label)
        preview_layout.addWidget(self.ghost_banner)
        preview_layout.addWidget(self.emphasise_movement)
        preview_layout.addWidget(self.transform_debug)
        preview_layout.addWidget(self.show_joints)
        preview_layout.addWidget(self.show_extents)
        preview_layout.addWidget(self.warning)
        preview_layout.addWidget(prepare)
        preview_layout.addWidget(generate)
        preview_layout.addWidget(
            QLabel(
                "7. Export to Godot — continue to Export after generation.\n"
                "8. Verify in Godot — use Export and verify in Godot."
            ),
        )

        splitter = QSplitter()
        splitter.addWidget(choose_panel)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(intro)
        layout.addWidget(self.rig_status)
        layout.addWidget(splitter, 1)

        self._shortcuts = [
            QShortcut(QKeySequence.StandardKey.Undo, self),
            QShortcut(QKeySequence.StandardKey.Redo, self),
        ]
        self._shortcuts[0].activated.connect(self.undo)
        self._shortcuts[1].activated.connect(self.redo)

    def set_project(self, directory: Path, project: Project) -> None:
        self.pause()
        self.project_directory = directory
        self.project = project
        if (
            project.animation_set is None
            or project.animation_set.rig_profile != project.rig_profile
        ):
            project.animation_set = default_animation_set(project.rig_profile)
        update_compatibility(project.animation_set, project)
        self.loop.blockSignals(True)
        self.loop.setChecked(project.animation_set.preview_loop)
        self.loop.blockSignals(False)
        self.slow_motion.blockSignals(True)
        self.slow_motion.setChecked(project.animation_set.preview_speed < 1.0)
        self.slow_motion.blockSignals(False)
        self.history.reset(project.animation_set)
        self.templates.clear()
        for definition in TEMPLATE_DEFINITIONS:
            status = project.animation_set.preview_status.get(
                definition.template_id, "Preview ready"
            )
            marker = status
            self.templates.addItem(f"{definition.label} — {marker}")
        self.rig_status.setText(
            f"1. Check the rig — {project.rig_profile} recognised. "
            "Stable movement joints are ready."
        )
        self.templates.setCurrentRow(0)
        self.generate()

    def _settings(self) -> AnimationTemplateSettings | None:
        if not self.project or not self.project.animation_set:
            return None
        row = self.templates.currentRow()
        if row < 0 or row >= len(self.project.animation_set.templates):
            return None
        return self.project.animation_set.templates[row]

    def select_template(self, template_id: str) -> None:
        if not self.project or not self.project.animation_set:
            return
        for row, settings in enumerate(self.project.animation_set.templates):
            if settings.template_id == template_id:
                self.templates.setCurrentRow(row)
                self.animation_choice.setCurrentText(
                    next(
                        (
                            item.name
                            for item in self.generated_animations
                            if item.template_id == template_id
                        ),
                        "",
                    )
                )
                break

    def _template_selected(self, _row: int) -> None:
        settings = self._settings()
        if not settings or not self.project or not self.project.animation_set:
            return
        status = self.project.animation_set.preview_status.get(
            settings.template_id, "Preview ready"
        )
        self.template_enabled.blockSignals(True)
        self.template_enabled.setChecked(settings.enabled)
        self.template_enabled.setEnabled(status in {"Preview ready", "Preview using suggestion"})
        self.template_enabled.blockSignals(False)
        definition = next(
            item for item in TEMPLATE_DEFINITIONS if item.template_id == settings.template_id
        )
        requirements = [*definition.required_joints, *definition.required_assets]
        export_readiness = self.project.animation_set.export_status.get(
            settings.template_id, "Needs automatic preparation"
        )
        self.requirements.setText(
            f"{definition.description}\nRequired: {', '.join(requirements) or 'current rig'}\n"
            f"Preview: {status}\nExport: "
            f"{export_readiness}"
        )
        movement_joint = self._movement_joint(settings.template_id)
        self.adjust_movement_point.setVisible(movement_joint is not None)
        self.fix_movement.setVisible(settings.template_id.startswith("head_tilt"))
        self._clear_parameter_form()
        for key, value in settings.parameters.items():
            widget = self._parameter_widget(key, value)
            self.parameter_widgets[key] = widget
            self.parameter_form.addRow(self._plain_label(key), widget)

    @staticmethod
    def _plain_label(key: str) -> str:
        labels = {
            "speed": "How fast should it move?",
            "breathing_strength": "Breathing strength",
            "breathing_speed": "Breathing speed",
            "keep_paws_grounded": "Keep paws grounded (Recommended)",
            "head_movement": "Head movement",
            "sway_amount": "Tail sway amount",
            "pause_between_sways": "Pause between sways",
            "movement_amount": "Movement amount",
            "twitch_speed": "Twitch speed",
            "return_speed": "Return speed",
            "move_ears_too": "Move ears too",
            "hold_closed_briefly": "Hold closed briefly",
        }
        return labels.get(key, key.replace("_", " ").capitalize())

    def _parameter_widget(self, key: str, value: object) -> QWidget:
        if isinstance(value, bool):
            widget = QCheckBox("On")
            widget.setChecked(value)
            widget.toggled.connect(lambda checked, name=key: self._parameter_changed(name, checked))
            return widget
        if isinstance(value, int):
            widget = QSpinBox()
            widget.setRange(1, 12)
            widget.setValue(value)
            widget.valueChanged.connect(
                lambda number, name=key: self._parameter_changed(name, number)
            )
            return widget
        if isinstance(value, float):
            widget = QDoubleSpinBox()
            widget.setRange(0.0, 20.0)
            widget.setDecimals(2)
            widget.setValue(value)
            widget.valueChanged.connect(
                lambda number, name=key: self._parameter_changed(name, number)
            )
            return widget
        widget = QComboBox()
        options = {
            "direction": ["Left first", "Right first"]
            if "tail" in (self._settings().template_id if self._settings() else "")
            else ["Left", "Right"],
            "breathing_strength": ["Very subtle", "Natural", "Noticeable"],
            "breathing_speed": ["Slow", "Natural", "Quick"],
            "sway_amount": ["Subtle", "Normal", "Expressive"],
            "movement_amount": ["Subtle", "Normal", "Expressive"],
            "tilt_amount": ["Subtle", "Normal", "Expressive"],
            "bounce_height": ["Subtle", "Normal", "Expressive"],
            "speed": ["Slow", "Normal", "Quick"],
            "twitch_speed": ["Slow", "Normal", "Quick"],
            "bounce_speed": ["Slow", "Normal", "Quick"],
            "return_speed": ["Slow", "Normal", "Quick"],
            "blink_speed": ["Slow", "Normal", "Quick"],
        }.get(key, [str(value)])
        widget.addItems(options)
        widget.setCurrentText(str(value))
        widget.currentTextChanged.connect(
            lambda text, name=key: self._parameter_changed(name, text)
        )
        return widget

    def _clear_parameter_form(self) -> None:
        while self.parameter_form.rowCount():
            self.parameter_form.removeRow(0)
        self.parameter_widgets.clear()

    @staticmethod
    def _movement_joint(template_id: str) -> str | None:
        if template_id.startswith("head_tilt"):
            return "Head"
        if template_id == "tail_sway":
            return "Tail"
        if template_id in {"idle_breathing", "happy_bounce"}:
            return "Body"
        return None

    def _adjust_movement_point(self) -> None:
        if (settings := self._settings()) and (joint := self._movement_joint(settings.template_id)):
            del joint
            self.adjust_movement_point_requested.emit(settings.template_id)

    def _fix_selected_movement(self) -> None:
        if not self.project or not self.project_directory:
            return
        results = prepare_head_tilt_attachments(self.project_directory, self.project)
        if self.project.animation_set:
            update_compatibility(self.project.animation_set, self.project)
        self.project_changed.emit()
        self.generate()
        self.warning.setText(
            "Head attachment preparation\n"
            + "\n".join(
                f"{name.replace('_', ' ').title()} — {status}" for name, status in results.items()
            )
        )

    def _commit(self) -> None:
        if self.project and self.project.animation_set:
            update_compatibility(self.project.animation_set, self.project)
            self.history.commit(self.project.animation_set)
            self.project.animation_verification_valid = False
            self.project.godot_export_status = "Needs regeneration"
            self.project.animation_set.last_successful_export = None
            self.project_changed.emit()
            self.generate()

    def _enabled_changed(self, enabled: bool) -> None:
        if settings := self._settings():
            settings.enabled = enabled
            self._commit()

    def _preview_options_changed(self, _checked: bool | None = None) -> None:
        if self.project and self.project.animation_set:
            self.project.animation_set.preview_loop = self.loop.isChecked()
            self.project.animation_set.preview_speed = 0.25 if self.slow_motion.isChecked() else 1.0
            self.project_changed.emit()

    def _diagnostic_overlay_toggled(self, checked: bool) -> None:
        self.ghost_banner.setVisible(checked)
        self._render()

    def prepare_movements(self) -> None:
        if not self.project or not self.project_directory or not self.project.animation_set:
            return
        result = prepare_movements_automatically(self.project_directory, self.project)
        update_compatibility(self.project.animation_set, self.project)
        self.project_changed.emit()
        self.generate()
        self.warning.setText(result.summary)

    def prepare_for_export(self) -> None:
        if not self.project or not self.project_directory or not self.project.animation_set:
            return
        preparation = prepare_movements_automatically(self.project_directory, self.project)
        preview_animations, _preview_warnings = generate_animation_set(
            self.project, purpose="preview", project_directory=self.project_directory
        )
        attachment_results = prepare_animation_attachment_treatments(
            self.project_directory, self.project, preview_animations
        )
        update_compatibility(self.project.animation_set, self.project)
        generated, warnings = generate_animation_set(
            self.project, purpose="export", project_directory=self.project_directory
        )
        passed = "\n".join(f"{item.name} — Passed" for item in generated)
        blocked = "\n".join(f"{name} — {reason}" for name, reason in warnings.items())
        heading = "Ready to export" if not warnings else "Export preparation needs attention"
        self.warning.setText(
            f"{heading}\n\n{passed}"
            + (f"\n{blocked}" if blocked else "")
            + f"\n\n{preparation.summary}"
            + "\n"
            + "\n".join(
                f"{name.replace('_', ' ').title()} — {status}"
                for name, status in attachment_results.items()
            )
        )
        self.project_changed.emit()

    def _parameter_changed(self, key: str, value: object) -> None:
        if not (settings := self._settings()):
            return
        settings.parameters[key] = value  # type: ignore[assignment]
        if "speed" in key and isinstance(value, str):
            definition = next(
                item for item in TEMPLATE_DEFINITIONS if item.template_id == settings.template_id
            )
            settings.duration = definition.default_duration * {
                "Slow": 1.3,
                "Normal": 1.0,
                "Quick": 0.72,
            }.get(value, 1.0)
        self._commit()

    def _reset_one(self) -> None:
        if self.project and self.project.animation_set and (settings := self._settings()):
            reset_template(self.project.animation_set, settings.template_id)
            self._commit()
            self._template_selected(self.templates.currentRow())

    def _reset_all(self) -> None:
        if self.project and self.project.animation_set:
            reset_all_templates(self.project.animation_set)
            self._commit()
            self._template_selected(self.templates.currentRow())

    def undo(self) -> None:
        if self.project and (state := self.history.undo()) is not None:
            self.project.animation_set = state
            self.project_changed.emit()
            self._template_selected(self.templates.currentRow())
            self.generate()

    def redo(self) -> None:
        if self.project and (state := self.history.redo()) is not None:
            self.project.animation_set = state
            self.project_changed.emit()
            self._template_selected(self.templates.currentRow())
            self.generate()

    def generate(self) -> None:
        if not self.project:
            return
        selected = self.animation_choice.currentText()
        self.generated_animations, warnings = generate_animation_set(
            self.project, purpose="preview", project_directory=self.project_directory
        )
        self.animation_choice.blockSignals(True)
        self.animation_choice.clear()
        self.animation_choice.addItems([item.name for item in self.generated_animations])
        if selected:
            self.animation_choice.setCurrentText(selected)
        self.animation_choice.blockSignals(False)
        self._animation_selected(self.animation_choice.currentIndex())
        messages = [message.replace("\n", " ") for message in warnings.values()]
        if (current := self.current_animation()) and self.project_directory:
            messages.extend(inspect_animation_frames(self.project_directory, self.project, current))
        self.warning.setText(
            "5. Check for gaps — "
            + (
                " ".join(messages)
                if messages
                else "No missing pivots or artwork states were found."
            )
        )
        self.current_time = 0.0
        self._render()

    def current_animation(self) -> GeneratedAnimation | None:
        index = self.animation_choice.currentIndex()
        return (
            self.generated_animations[index]
            if 0 <= index < len(self.generated_animations)
            else None
        )

    def _animation_selected(self, _index: int) -> None:
        animation = self.current_animation()
        if (
            animation
            and self.project
            and self.project_directory
            and not animation_treatments_are_current(
                self.project_directory, self.project, animation
            )
        ):
            self.pause()
            self.warning.setText(
                "Preparing seamless layer overlap automatically — source layers remain intact."
            )
            results = prepare_animation_attachment_treatments(
                self.project_directory, self.project, self.generated_animations
            )
            if self.project.animation_set:
                update_compatibility(self.project.animation_set, self.project)
            self.project_changed.emit()
            selected = animation.name
            self.generate()
            self.animation_choice.setCurrentText(selected)
            self.warning.setText(
                "Layer attachment preparation\n"
                + "\n".join(
                    f"{name.replace('_', ' ').title()} — {status}"
                    for name, status in results.items()
                )
            )
            return
        is_idle = bool(animation and animation.template_id == "idle_breathing")
        self.preview_exhale.setVisible(is_idle)
        self.preview_inhale.setVisible(is_idle)
        self.restart()

    def play(self) -> None:
        if self.current_animation():
            self.timer.start()

    def pause(self) -> None:
        self.timer.stop()

    def restart(self) -> None:
        self.current_time = 0.0
        self._render()

    def return_to_rest_pose(self) -> None:
        self.pause()
        self.current_time = 0.0
        self._render(rest=True)

    def preview_idle_exhale(self) -> None:
        animation = self.current_animation()
        if animation and animation.template_id == "idle_breathing":
            self.pause()
            self.current_time = 0.0
            self._render()

    def preview_idle_inhale(self) -> None:
        animation = self.current_animation()
        if animation and animation.template_id == "idle_breathing":
            self.pause()
            self.current_time = animation.duration / 2
            self._render()

    def _tick(self) -> None:
        animation = self.current_animation()
        if not animation:
            self.pause()
            return
        speed = 0.25 if self.slow_motion.isChecked() else 1.0
        self.current_time += self.timer.interval() / 1000 * speed
        if self.current_time > animation.duration:
            if self.loop.isChecked():
                self.current_time %= animation.duration
            else:
                self.current_time = animation.duration
                self.pause()
        self._render()

    def _scrubbed(self, value: int) -> None:
        if animation := self.current_animation():
            self.current_time = animation.duration * value / 1000
            self._render(update_slider=False)

    def _extent_toggled(self, checked: bool) -> None:
        if checked and (animation := self.current_animation()):
            self.current_time = maximum_extent_times(animation)[0]
        self._render()

    def _render(self, *, rest: bool = False, update_slider: bool = True) -> None:
        if not self.project or not self.project_directory:
            return
        animation = self.current_animation()
        try:
            image = (
                composite_assembly(self.project_directory, self.project)
                if rest or animation is None
                else composite_animation_frame(
                    self.project_directory,
                    self.project,
                    animation,
                    self.current_time,
                    movement_scale=4.0 if self.emphasise_movement.isChecked() else 1.0,
                    debug_overlay=self.transform_debug.isChecked(),
                )
            )
            if animation and self.compare_rest.isChecked() and not rest:
                resting = composite_assembly(self.project_directory, self.project)
                image = Image.blend(resting, image, 0.55)
            self.canvas.set_image(image)
            if animation:
                self.time_label.setText(
                    f"{animation.name} — {self.current_time:.2f} / {animation.duration:.2f} seconds"
                )
                displacement = 0.0
                for track in animation.tracks:
                    if track.property_name == "position":
                        current = sample_track(track, self.current_time)
                        resting_value = track.keys[0].value
                        if isinstance(current, tuple) and isinstance(resting_value, tuple):
                            displacement = max(
                                displacement,
                                (
                                    (current[0] - resting_value[0]) ** 2
                                    + (current[1] - resting_value[1]) ** 2
                                )
                                ** 0.5,
                            )
                shown = displacement * (4.0 if self.emphasise_movement.isChecked() else 1.0)
                self.motion_label.setText(f"Current joint displacement: {shown:.2f} px")
                if update_slider:
                    self.scrubber.blockSignals(True)
                    self.scrubber.setValue(round(self.current_time / animation.duration * 1000))
                    self.scrubber.blockSignals(False)
                if self.show_joints.isChecked() and animation.required_joints:
                    joint_name = animation.required_joints[0]
                    self.canvas.show_pivot(*resolve_joint_placement(self.project, joint_name))
                else:
                    self.canvas.show_pivot(None, None)
        except (OSError, ValueError) as error:
            self.warning.setText(f"Preview could not be drawn: {error}")
