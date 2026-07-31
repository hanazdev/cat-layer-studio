import pytest

from cat_layer_studio.services.landmark_fit_service import suggest_uniform_transform


def test_landmarks_recover_translation_scale_and_rotation() -> None:
    candidate = [(0, 0), (10, 0), (0, 10)]
    master = [(5, -2), (5, 18), (-15, -2)]
    suggestion = suggest_uniform_transform(candidate, master)
    assert suggestion.transform.scale_x == pytest.approx(2)
    assert suggestion.transform.scale_y == pytest.approx(2)
    assert abs(suggestion.transform.rotation_degrees) == pytest.approx(90)
    assert suggestion.rms_error == pytest.approx(0)


def test_landmarks_require_matching_pairs() -> None:
    with pytest.raises(ValueError, match="at least two"):
        suggest_uniform_transform([(0, 0)], [(1, 1)])
