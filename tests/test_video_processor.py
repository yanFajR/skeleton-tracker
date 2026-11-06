import subprocess
from pathlib import Path

import cv2
import numpy as np

from imageio_ffmpeg import get_ffmpeg_exe
from video_processor import process_video


class FakePoseTracker:
    def __init__(self, _model_path: Path) -> None:
        pass

    def __enter__(self) -> "FakePoseTracker":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def detect_and_draw_with_angles(
        self, frame: np.ndarray, _timestamp_ms: int
    ) -> tuple[np.ndarray, dict[str, float | None]]:
        return frame, {"left_elbow": 90.0, "right_elbow": None}


def test_processor_writes_decodable_h264_mp4(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    destination = tmp_path / "annotated.mp4"
    input_writer = cv2.VideoWriter(
        str(source), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (64, 48)
    )
    assert input_writer.isOpened()
    for frame_index in range(5):
        frame = np.full((48, 64, 3), frame_index * 30, dtype=np.uint8)
        input_writer.write(frame)
    input_writer.release()

    result = process_video(
        source,
        destination,
        tmp_path / "unused-model.task",
        tracker_factory=FakePoseTracker,
    )

    assert result["frames"] == 5
    assert result["frame_angles"][0]["left_elbow"] == 90.0
    inspection = subprocess.run(
        [get_ffmpeg_exe(), "-hide_banner", "-i", str(destination), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert inspection.returncode == 0, inspection.stderr
    assert "video: h264" in inspection.stderr.lower()
