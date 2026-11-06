"""Frame-by-frame annotated video rendering."""

from pathlib import Path
from typing import Callable

import cv2
import imageio_ffmpeg

from pose_tracker import PoseTracker


class VideoProcessingError(RuntimeError):
    """Raised when a video cannot be safely processed."""


def process_video(
    source: Path,
    destination: Path,
    model_path: Path,
    max_duration_seconds: int = 120,
    tracker_factory: Callable[[Path], PoseTracker] = PoseTracker,
) -> dict[str, float | int | list[dict[str, float | None]]]:
    """Render an H.264 MP4 overlay one frame at a time and release resources."""
    capture = cv2.VideoCapture(str(source))
    writer = None
    processing_succeeded = False
    try:
        if not capture.isOpened():
            raise VideoProcessingError("Video tidak dapat dibaca oleh OpenCV.")
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or width <= 0 or height <= 0:
            raise VideoProcessingError("Metadata video tidak valid.")
        duration = frames / fps if frames else 0
        if duration > max_duration_seconds:
            raise VideoProcessingError(f"Durasi video melebihi batas {max_duration_seconds} detik.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        writer = imageio_ffmpeg.write_frames(
            str(destination),
            (width, height),
            pix_fmt_in="bgr24",
            pix_fmt_out="yuv420p",
            fps=fps,
            codec="libx264",
            quality=7,
            macro_block_size=1,
            ffmpeg_log_level="error",
            output_params=[
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-movflags",
                "+faststart",
            ],
        )
        writer.send(None)
        frame_index = 0
        frame_angles: list[dict[str, float | None]] = []
        with tracker_factory(model_path) as tracker:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                timestamp_ms = round(frame_index * 1000 / fps)
                annotated_frame, angles = tracker.detect_and_draw_with_angles(frame, timestamp_ms)
                writer.send(annotated_frame)
                frame_angles.append({"time_seconds": frame_index / fps, **angles})
                frame_index += 1
        if frame_index == 0:
            raise VideoProcessingError("Video tidak memiliki frame yang dapat diproses.")
        writer.close()
        writer = None
        if not destination.is_file() or destination.stat().st_size == 0:
            raise VideoProcessingError("Video hasil H.264 tidak berhasil dibuat.")
        processing_succeeded = True
        return {
            "frames": frame_index,
            "fps": fps,
            "duration": frame_index / fps,
            "frame_angles": frame_angles,
        }
    finally:
        capture.release()
        try:
            if writer is not None:
                writer.close()
        finally:
            if not processing_succeeded:
                destination.unlink(missing_ok=True)
