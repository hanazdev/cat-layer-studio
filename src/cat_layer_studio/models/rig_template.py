from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RigJoint:
    name: str
    parent: str | None
    suggested_pivot: tuple[float, float]


@dataclass(frozen=True, slots=True)
class RigTemplate:
    id: str
    joints: tuple[RigJoint, ...]
    attachment_map: dict[str, str]
    recommended_z: dict[str, int]


ADULT_FRONT_SITTING = RigTemplate(
    id="adult_front_sitting",
    joints=(
        RigJoint("Root", None, (256.0, 256.0)),
        RigJoint("Body", "Root", (256.0, 360.0)),
        RigJoint("Head", "Body", (256.0, 270.0)),
        RigJoint("EarScreenLeft", "Head", (190.0, 150.0)),
        RigJoint("EarScreenRight", "Head", (322.0, 150.0)),
        RigJoint("Tail", "Body", (350.0, 370.0)),
    ),
    attachment_map={
        "body": "Body",
        "tail": "Tail",
        "head": "Head",
        "ear_screen_left": "EarScreenLeft",
        "ear_screen_right": "EarScreenRight",
        "eye_screen_left": "Head",
        "eye_screen_right": "Head",
    },
    recommended_z={
        "tail": 10,
        "body": 20,
        "head": 30,
        "ear_screen_left": 40,
        "ear_screen_right": 41,
        "eye_screen_left": 50,
        "eye_screen_right": 51,
    },
)


def get_rig_template(profile: str) -> RigTemplate:
    if profile != ADULT_FRONT_SITTING.id:
        raise ValueError(f"Unsupported rig profile: {profile}")
    return ADULT_FRONT_SITTING
