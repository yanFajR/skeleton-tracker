import math

import pytest

from angle_analysis import (
    calculate_angle,
    calculate_angle_difference,
    calculate_trunk_angle,
)


def test_calculates_straight_arm_angle() -> None:
    assert calculate_angle((0, 0), (1, 0), (2, 0)) == pytest.approx(180)


def test_calculates_bent_arm_angle() -> None:
    assert calculate_angle((0, 1), (0, 0), (1, 0)) == pytest.approx(90)


def test_calculates_forty_five_degree_angle() -> None:
    assert calculate_angle((1, 0), (0, 0), (1, 1)) == pytest.approx(45)


def test_calculates_zero_degree_edge_case() -> None:
    assert calculate_angle((1, 0), (0, 0), (2, 0)) == pytest.approx(0)


@pytest.mark.parametrize(
    ("first", "joint", "third"),
    [
        (None, (0, 0), (1, 0)),
        ((0, 1), None, (1, 0)),
        ((0, 1), (0, 0), None),
        ((0, 1), (0, 0), (0, 0)),
        ((0, 1), (0, 0), (math.nan, 1)),
        ((0, 1), (0, 0), (math.inf, 1)),
        ((1e308, 0), (-1e308, 0), (0, 1e308)),
        ((0, 1, 2), (0, 0), (1, 0)),
    ],
)
def test_returns_none_for_missing_or_invalid_points(first, joint, third) -> None:
    assert calculate_angle(first, joint, third) is None


@pytest.mark.parametrize(
    ("baseline", "retake", "expected"),
    [
        (90, 110, 20),
        (110, 90, -20),
        (90, 90, 0),
        (None, 90, None),
        (90, None, None),
        (math.nan, 90, None),
        (90, math.inf, None),
        (-1, 90, None),
        (90, 181, None),
    ],
)
def test_calculate_angle_difference(baseline, retake, expected) -> None:
    assert calculate_angle_difference(baseline, retake) == expected


def test_calculates_upright_trunk_angle() -> None:
    assert calculate_trunk_angle((0, 1), (0.2, 1), (0, 2), (0.2, 2)) == pytest.approx(0)


def test_calculates_leaning_trunk_angle() -> None:
    angle = calculate_trunk_angle((1, 1), (1.2, 1), (0, 2), (0.2, 2))
    assert 40 < angle < 50


@pytest.mark.parametrize(
    ("left_shoulder", "right_shoulder", "left_hip", "right_hip"),
    [
        (None, (0, 1), (0, 2), (0.2, 2)),
        ((0, 1), (0.2, 1), (0, math.nan), (0.2, 2)),
        ((0, 1), (0.2, 1), (0, 2), (0, 0, 2)),
    ],
)
def test_invalid_trunk_landmarks_are_unavailable(
    left_shoulder, right_shoulder, left_hip, right_hip
) -> None:
    assert calculate_trunk_angle(left_shoulder, right_shoulder, left_hip, right_hip) is None
