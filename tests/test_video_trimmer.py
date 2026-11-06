from pathlib import Path

import imageio_ffmpeg

from video_trimmer import probe_video, probe_video_metadata, trim_video


def _create_video(path: Path) -> None:
    writer = imageio_ffmpeg.write_frames(
        str(path),
        (64, 64),
        pix_fmt_in="bgr24",
        pix_fmt_out="yuv420p",
        fps=10,
        codec="libx264",
        quality=7,
        macro_block_size=1,
        ffmpeg_log_level="error",
        output_params=["-movflags", "+faststart"],
    )
    writer.send(None)
    for frame_index in range(20):
        frame = bytearray([frame_index * 10 % 255] * (64 * 64 * 3))
        writer.send(frame)
    writer.close()


def test_ffmpeg_trimming_creates_valid_new_video_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    destination = tmp_path / "trimmed.mp4"
    _create_video(source)
    original_bytes = source.read_bytes()

    source_duration, source_frames, _source_fps = probe_video(source)
    trimmed_duration = trim_video(source, destination, 0.4, 1.4, 10, 1)
    actual_trimmed_duration, trimmed_frames, trimmed_fps = probe_video(destination)

    assert source_duration >= 1.9
    assert source_frames >= 19
    assert destination.is_file()
    assert trimmed_duration > 0
    assert 0.8 <= actual_trimmed_duration <= 1.2
    assert trimmed_frames >= 8
    assert trimmed_fps > 0
    assert source.read_bytes() == original_bytes


def test_ffmpeg_trimming_preserves_rational_target_and_outputs_cfr(
    technical_video_fixtures: dict[str, Path],
    tmp_path: Path,
) -> None:
    source = technical_video_fixtures["nonstandard"]
    destination = tmp_path / "normalized.mp4"
    original_bytes = source.read_bytes()

    trim_video(source, destination, 0.0, 1.0, 24000, 1001)
    probe = probe_video_metadata(destination, duration_field="trimmed_duration")

    assert probe.validation_errors == ()
    assert probe.metadata["is_constant_frame_rate"] is True
    assert probe.metadata["nominal_frame_rate_numerator"] == 24000
    assert probe.metadata["nominal_frame_rate_denominator"] == 1001
    assert probe.metadata["valid_pts_count"] == probe.metadata["frame_count"]
    assert source.read_bytes() == original_bytes
