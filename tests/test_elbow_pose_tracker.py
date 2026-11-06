from types import SimpleNamespace

import numpy as np

from pose_tracker import PoseTracker, _visible


def test_low_visibility_landmark_is_unavailable() -> None:
    landmark = SimpleNamespace(visibility=0.49, presence=0.9)

    assert not _visible(landmark)


def test_low_presence_landmark_is_unavailable() -> None:
    landmark = SimpleNamespace(visibility=0.9, presence=0.49)

    assert not _visible(landmark)


def test_valid_pose_tracker_landmarks_keep_left_and_right_separate() -> None:
    tracker = object.__new__(PoseTracker)
    points = [
        SimpleNamespace(x=0.0, y=0.0, visibility=0.9, presence=0.9)
        for _ in range(33)
    ]
    points[11].x, points[11].y = 0.0, 1.0
    points[13].x, points[13].y = 0.0, 0.0
    points[15].x, points[15].y = 1.0, 0.0
    points[12].x, points[12].y = 1.0, 0.0
    points[14].x, points[14].y = 0.0, 0.0
    points[16].x, points[16].y = 0.0, 1.0
    points[23].x, points[23].y = 0.0, 2.0
    points[24].x, points[24].y = 1.0, 2.0

    class Landmarker:
        def detect_for_video(self, _image, _timestamp_ms):
            return SimpleNamespace(pose_landmarks=[points])

    tracker._landmarker = Landmarker()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    _, angles = tracker.detect_and_draw_with_angles(frame, 0)

    assert angles["left_elbow"] == 90
    assert angles["right_elbow"] == 90
    assert 0 <= angles["trunk_lean"] <= 180
