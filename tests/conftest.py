from pathlib import Path
import subprocess

import imageio_ffmpeg
import pytest


def _write_cfr_video(path: Path, size: tuple[int, int], fps: float, frame_count: int = 60) -> None:
    writer = imageio_ffmpeg.write_frames(
        str(path),
        size,
        pix_fmt_in="bgr24",
        pix_fmt_out="yuv420p",
        fps=fps,
        codec="libx264",
        quality=7,
        macro_block_size=1,
        ffmpeg_log_level="error",
        output_params=["-movflags", "+faststart"],
    )
    writer.send(None)
    width, height = size
    for index in range(frame_count):
        intensity = index % 255
        writer.send(bytearray([intensity] * (width * height * 3)))
    writer.close()


def _write_rational_cfr_video(
    path: Path, size: tuple[int, int], fps_numerator: int, fps_denominator: int, frame_count: int
) -> None:
    width, height = size
    process = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r={fps_numerator}/{fps_denominator}",
            "-frames:v",
            str(frame_count),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-video_track_timescale",
            str(fps_numerator),
            "-movflags",
            "+faststart",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if process.returncode != 0:
        raise RuntimeError(f"Gagal membuat fixture CFR rational: {process.stderr}")


@pytest.fixture(scope="session")
def technical_video_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    fixture_directory = tmp_path_factory.mktemp("technical-videos")
    cfr_24 = fixture_directory / "cfr-24.mp4"
    cfr_30 = fixture_directory / "cfr-30.mp4"
    cfr_60 = fixture_directory / "cfr-60.mp4"
    _write_cfr_video(cfr_24, (96, 64), 24, 48)
    _write_cfr_video(cfr_30, (96, 64), 30, 60)
    _write_cfr_video(cfr_60, (96, 64), 60, 120)

    vfr = fixture_directory / "vfr.mp4"
    vfr_process = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(cfr_30),
            "-vf",
            "setpts=PTS+gte(N\\,30)*0.05/TB",
            "-fps_mode",
            "vfr",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(vfr),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if vfr_process.returncode != 0:
        raise RuntimeError(f"Gagal membuat fixture VFR: {vfr_process.stderr}")

    portrait = fixture_directory / "portrait.mp4"
    landscape = fixture_directory / "landscape.mp4"
    _write_cfr_video(portrait, (64, 96), 30, 60)
    _write_cfr_video(landscape, (96, 64), 30, 60)

    rotated = fixture_directory / "rotated-metadata.mp4"
    rotation_process = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-display_rotation:v:0",
            "90",
            "-i",
            str(landscape),
            "-c",
            "copy",
            str(rotated),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if rotation_process.returncode != 0:
        raise RuntimeError(f"Gagal membuat fixture metadata rotasi: {rotation_process.stderr}")

    nonstandard = fixture_directory / "nonstandard-rate-duration.mp4"
    _write_rational_cfr_video(nonstandard, (96, 64), 24000, 1001, 37)
    return {
        "cfr_24": cfr_24,
        "cfr_30": cfr_30,
        "cfr_60": cfr_60,
        "vfr": vfr,
        "portrait": portrait,
        "landscape": landscape,
        "rotated": rotated,
        "nonstandard": nonstandard,
    }
