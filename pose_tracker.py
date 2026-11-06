"""MediaPipe Pose Landmarker setup and skeleton drawing helpers."""

from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

from angle_analysis import calculate_angle, calculate_trunk_angle


class PoseTracker:
    """Single-person Pose Landmarker running in timestamped video mode."""

    def __init__(self, model_path: Path) -> None:
        if not model_path.is_file() or model_path.stat().st_size == 0:
            raise FileNotFoundError("Model Pose Landmarker tidak ditemukan atau kosong.")
        options = mp.tasks.vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)

    def detect_and_draw_with_angles(
        self, bgr_frame: np.ndarray, timestamp_ms: int
    ) -> tuple[np.ndarray, dict[str, float | None]]:
        """Run one inference and return the annotated frame and elbow angles."""
        rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        angles: dict[str, float | None] = {
            "left_elbow": None,
            "right_elbow": None,
            "trunk_lean": None,
        }
        if not result.pose_landmarks:
            return bgr_frame, angles

        height, width = bgr_frame.shape[:2]
        landmarks = result.pose_landmarks[0]
        for side, shoulder_index, elbow_index, wrist_index in (
            (
                "left_elbow",
                mp.tasks.vision.PoseLandmark.LEFT_SHOULDER.value,
                mp.tasks.vision.PoseLandmark.LEFT_ELBOW.value,
                mp.tasks.vision.PoseLandmark.LEFT_WRIST.value,
            ),
            (
                "right_elbow",
                mp.tasks.vision.PoseLandmark.RIGHT_SHOULDER.value,
                mp.tasks.vision.PoseLandmark.RIGHT_ELBOW.value,
                mp.tasks.vision.PoseLandmark.RIGHT_WRIST.value,
            ),
        ):
            points = [
                (landmarks[index].x, landmarks[index].y) if _visible(landmarks[index]) else None
                for index in (shoulder_index, elbow_index, wrist_index)
            ]
            angles[side] = calculate_angle(*points)

        trunk_points = [
            (
                (landmarks[index].x, landmarks[index].y)
                if _visible(landmarks[index])
                else None
            )
            for index in (
                mp.tasks.vision.PoseLandmark.LEFT_SHOULDER.value,
                mp.tasks.vision.PoseLandmark.RIGHT_SHOULDER.value,
                mp.tasks.vision.PoseLandmark.LEFT_HIP.value,
                mp.tasks.vision.PoseLandmark.RIGHT_HIP.value,
            )
        ]
        angles["trunk_lean"] = calculate_trunk_angle(*trunk_points)

        for connection in mp.tasks.vision.PoseLandmarksConnections.POSE_LANDMARKS:
            start, end = landmarks[connection.start], landmarks[connection.end]
            if _visible(start) and _visible(end):
                cv2.line(bgr_frame, _point(start, width, height), _point(end, width, height), (0, 255, 255), 3, cv2.LINE_AA)
        for landmark in landmarks:
            if _visible(landmark):
                cv2.circle(bgr_frame, _point(landmark, width, height), 4, (255, 70, 0), -1, cv2.LINE_AA)
        return bgr_frame, angles

    def detect_and_draw(self, bgr_frame: np.ndarray, timestamp_ms: int) -> np.ndarray:
        """Run inference and return a BGR frame with a high-contrast skeleton."""
        annotated_frame, _ = self.detect_and_draw_with_angles(bgr_frame, timestamp_ms)
        return annotated_frame

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> "PoseTracker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _visible(landmark: object) -> bool:
    visibility = getattr(landmark, "visibility", None)
    presence = getattr(landmark, "presence", None)
    return (
        (visibility is None or visibility >= 0.5)
        and (presence is None or presence >= 0.5)
    )


def _point(landmark: object, width: int, height: int) -> tuple[int, int]:
    return (round(getattr(landmark, "x") * width), round(getattr(landmark, "y") * height))
