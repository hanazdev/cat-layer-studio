from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot

import numpy as np

from cat_layer_studio.models.transform import Transform


@dataclass(frozen=True, slots=True)
class LandmarkSuggestion:
    transform: Transform
    rms_error: float


def suggest_uniform_transform(
    candidate_points: list[tuple[float, float]],
    master_points: list[tuple[float, float]],
) -> LandmarkSuggestion:
    """Solve the least-squares similarity transform between matching landmarks."""
    if len(candidate_points) != len(master_points) or len(candidate_points) < 2:
        raise ValueError("Choose at least two matching points on both images.")
    source = np.asarray(candidate_points, dtype=float)
    target = np.asarray(master_points, dtype=float)
    source_centre = source.mean(axis=0)
    target_centre = target.mean(axis=0)
    source_zero = source - source_centre
    target_zero = target - target_centre
    matrix = source_zero.T @ target_zero
    u, singular, vt = np.linalg.svd(matrix)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:, -1] *= -1
        rotation = u @ vt
    denominator = float((source_zero**2).sum())
    scale = float(singular.sum() / denominator) if denominator else 1.0
    mapped = (source_zero @ rotation) * scale + target_centre
    error = float(np.sqrt(np.mean(np.sum((mapped - target) ** 2, axis=1))))
    angle = degrees(atan2(rotation[0, 1], rotation[0, 0]))
    translation = target_centre - source_centre
    return LandmarkSuggestion(
        Transform(
            x=float(translation[0]),
            y=float(translation[1]),
            scale_x=scale,
            scale_y=scale,
            rotation_degrees=angle,
        ),
        rms_error=error,
    )


def suggest_two_point_transform(
    candidate_points: list[tuple[float, float]], master_points: list[tuple[float, float]]
) -> LandmarkSuggestion:
    if len(candidate_points) != 2 or len(master_points) != 2:
        return suggest_uniform_transform(candidate_points, master_points)
    source_vector = np.subtract(candidate_points[1], candidate_points[0])
    target_vector = np.subtract(master_points[1], master_points[0])
    source_length = hypot(*source_vector)
    target_length = hypot(*target_vector)
    if source_length == 0:
        raise ValueError("Candidate landmarks must not overlap.")
    scale = target_length / source_length
    angle = degrees(
        atan2(target_vector[1], target_vector[0]) - atan2(source_vector[1], source_vector[0])
    )
    source_mid = np.mean(candidate_points, axis=0)
    target_mid = np.mean(master_points, axis=0)
    translation = target_mid - source_mid
    return LandmarkSuggestion(
        Transform(float(translation[0]), float(translation[1]), scale, scale, angle), 0.0
    )
