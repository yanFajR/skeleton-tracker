"""Small, dependency-free helpers for measuring 2D joint angles."""

import math
from collections.abc import Sequence

Point2D = Sequence[float] | None


def calculate_angle(first: Point2D, joint: Point2D, third: Point2D) -> float | None:
    """Return the angle at ``joint`` in degrees, or None for unusable points."""
    if first is None or joint is None or third is None:
        return None
    if any(len(point) != 2 for point in (first, joint, third)):
        return None

    try:
        first_x, first_y = float(first[0]), float(first[1])
        joint_x, joint_y = float(joint[0]), float(joint[1])
        third_x, third_y = float(third[0]), float(third[1])
    except (TypeError, ValueError, OverflowError):
        return None

    coordinates = (first_x, first_y, joint_x, joint_y, third_x, third_y)
    if not all(math.isfinite(value) for value in coordinates):
        return None

    first_vector = (first_x - joint_x, first_y - joint_y)
    third_vector = (third_x - joint_x, third_y - joint_y)
    first_length = math.hypot(*first_vector)
    third_length = math.hypot(*third_vector)
    if (
        first_length == 0
        or third_length == 0
        or not math.isfinite(first_length)
        or not math.isfinite(third_length)
    ):
        return None

    dot_product = first_vector[0] * third_vector[0] + first_vector[1] * third_vector[1]
    cosine = dot_product / (first_length * third_length)
    if not math.isfinite(cosine):
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def calculate_trunk_angle(
    left_shoulder: Point2D,
    right_shoulder: Point2D,
    left_hip: Point2D,
    right_hip: Point2D,
) -> float | None:
    """Return trunk lean from vertical at the hip center."""
    points = (left_shoulder, right_shoulder, left_hip, right_hip)
    if any(point is None or len(point) != 2 for point in points):
        return None

    try:
        shoulder_x = (float(left_shoulder[0]) + float(right_shoulder[0])) / 2
        shoulder_y = (float(left_shoulder[1]) + float(right_shoulder[1])) / 2
        hip_x = (float(left_hip[0]) + float(right_hip[0])) / 2
        hip_y = (float(left_hip[1]) + float(right_hip[1])) / 2
    except (TypeError, ValueError, OverflowError):
        return None

    coordinates = (shoulder_x, shoulder_y, hip_x, hip_y)
    if not all(math.isfinite(value) for value in coordinates):
        return None

    trunk_vertical = (hip_x, hip_y - 1)
    return calculate_angle(trunk_vertical, (hip_x, hip_y), (shoulder_x, shoulder_y))


def calculate_angle_difference(baseline: float | None, retake: float | None) -> float | None:
    """Return retake minus baseline when both elbow angles are usable."""
    if baseline is None or retake is None:
        return None
    try:
        baseline_value = float(baseline)
        retake_value = float(retake)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not math.isfinite(baseline_value)
        or not math.isfinite(retake_value)
        or not 0 <= baseline_value <= 180
        or not 0 <= retake_value <= 180
    ):
        return None
    return retake_value - baseline_value
