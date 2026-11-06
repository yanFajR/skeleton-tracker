from pathlib import Path
import re
import subprocess

import cv2
import imageio_ffmpeg
import pytest

from comparison_store import SessionStore
from video_trimmer import (
    VideoTrimmingError,
    probe_video_metadata,
    resolve_target_frame_rate,
    validate_rational_pts_cadence,
)
from video_metadata import build_video_metadata


def test_video_metadata_contract_records_provenance_and_unknown_values() -> None:
    metadata = build_video_metadata(
        {
            "source_duration": 2.0,
            "source_start_time": 0.5,
            "source_end_time": 1.5,
            "frame_count": None,
            "rotation": -90,
        },
        {
            "source_duration": {
                "kind": "calculated",
                "method": "decoded_frame_count / measured_average_frame_rate",
            },
            "source_start_time": {"kind": "provided", "method": "trim request"},
            "source_end_time": {"kind": "provided", "method": "trim request"},
            "rotation": {"kind": "measured", "method": "MP4 display rotation metadata"},
        },
    )

    assert metadata["source_duration"] == 2.0
    assert metadata["schema_version"] == 1
    assert metadata["source_start_time"] == 0.5
    assert metadata["source_end_time"] == 1.5
    assert metadata["rotation"] == -90
    assert metadata["frame_count"] is None
    assert metadata["nominal_frame_rate"] is None
    assert metadata["is_constant_frame_rate"] is None
    provenance = metadata["metadata_source"]
    assert provenance["source_duration"] == {
        "kind": "calculated",
        "method": "decoded_frame_count / measured_average_frame_rate",
    }
    assert provenance["frame_count"] == {"kind": "unavailable", "method": None}
    assert metadata["timing_validation_status"] == "not_validated"


def test_video_metadata_rejects_values_without_provenance() -> None:
    with pytest.raises(ValueError, match="Sumber metadata"):
        build_video_metadata({"average_frame_rate": 30.0})


def test_legacy_session_without_technical_metadata_remains_readable(tmp_path) -> None:
    import json

    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    uploads.mkdir()
    source_filename = "a" * 32 + ".mp4"
    (uploads / source_filename).write_bytes(b"legacy-video")
    session_file = tmp_path / "sessions.json"
    legacy_session = {
        "schema_version": 1,
        "sessions": [
            {
                "session_id": "b" * 32,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "source_filename": source_filename,
                "status": "uploaded",
                "note": "",
                "result_filename": None,
                "frame_angles": [],
                "fps": None,
                "frames": None,
                "duration": None,
                "trim": {
                    "source_filename": source_filename,
                    "trimmed_filename": None,
                    "start_time": None,
                    "end_time": None,
                    "source_duration": None,
                    "trimmed_duration": None,
                    "created_at": None,
                    "status": "not_started",
                    "error": None,
                },
                "selected_phase_frames": {},
                "analysis": {"summary": None},
                "error": None,
            }
        ],
    }
    session_file.write_text(json.dumps(legacy_session), encoding="utf-8")

    session = SessionStore(session_file, uploads, outputs).get("b" * 32)

    assert session["session_id"] == "b" * 32
    assert "technical_metadata" not in session["trim"]


def test_technical_video_fixtures_are_created_and_decodable(
    technical_video_fixtures: dict[str, Path],
) -> None:
    expected_names = {
        "cfr_24",
        "cfr_30",
        "cfr_60",
        "vfr",
        "portrait",
        "landscape",
        "rotated",
        "nonstandard",
    }
    assert set(technical_video_fixtures) == expected_names

    for path in technical_video_fixtures.values():
        assert path.is_file()
        capture = cv2.VideoCapture(str(path))
        try:
            assert capture.isOpened()
            ok, frame = capture.read()
            assert ok
            assert frame.size > 0
        finally:
            capture.release()

    portrait = cv2.VideoCapture(str(technical_video_fixtures["portrait"]))
    landscape = cv2.VideoCapture(str(technical_video_fixtures["landscape"]))
    try:
        assert portrait.get(cv2.CAP_PROP_FRAME_WIDTH) < portrait.get(cv2.CAP_PROP_FRAME_HEIGHT)
        assert landscape.get(cv2.CAP_PROP_FRAME_WIDTH) > landscape.get(cv2.CAP_PROP_FRAME_HEIGHT)
    finally:
        portrait.release()
        landscape.release()

    for fixture_name, expected_fps in (
        ("cfr_24", 24.0),
        ("cfr_30", 30.0),
        ("cfr_60", 60.0),
        ("nonstandard", 24000 / 1001),
    ):
        capture = cv2.VideoCapture(str(technical_video_fixtures[fixture_name]))
        try:
            assert capture.get(cv2.CAP_PROP_FPS) == pytest.approx(expected_fps, abs=0.01)
            assert capture.get(cv2.CAP_PROP_FRAME_COUNT) > 0
        finally:
            capture.release()

    vfr_report = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-i",
            str(technical_video_fixtures["vfr"]),
            "-vf",
            "vfrdet",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert vfr_report.returncode == 0, vfr_report.stderr
    assert "VFR:" in vfr_report.stderr
    assert "VFR:0.000000" not in vfr_report.stderr

    rotation_report = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-i",
            str(technical_video_fixtures["rotated"]),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert rotation_report.returncode == 0, rotation_report.stderr
    assert "rotation of" in rotation_report.stderr.lower()


@pytest.mark.parametrize(
    ("fixture_name", "expected_fps", "expected_size", "expected_cfr"),
    [
        ("cfr_24", 24.0, (96, 64), True),
        ("cfr_30", 30.0, (96, 64), True),
        ("cfr_60", 60.0, (96, 64), True),
        ("portrait", 30.0, (64, 96), True),
        ("landscape", 30.0, (96, 64), True),
        ("nonstandard", 24000 / 1001, (96, 64), True),
    ],
)
def test_probe_reports_measured_cfr_video_values(
    technical_video_fixtures: dict[str, Path],
    fixture_name: str,
    expected_fps: float,
    expected_size: tuple[int, int],
    expected_cfr: bool,
) -> None:
    probe = probe_video_metadata(technical_video_fixtures[fixture_name])

    assert probe.validation_errors == ()
    assert probe.metadata["timing_validation_status"] == "passed"
    assert probe.metadata["frame_count"] > 0
    assert probe.metadata["valid_pts_count"] == probe.metadata["frame_count"]
    assert probe.metadata["width"] == expected_size[0]
    assert probe.metadata["height"] == expected_size[1]
    assert probe.metadata["nominal_frame_rate"] == pytest.approx(expected_fps, abs=0.01)
    assert probe.metadata["inferred_cfr_frame_rate"] == pytest.approx(expected_fps, abs=0.05)
    assert probe.metadata["average_frame_rate"] == pytest.approx(expected_fps, abs=0.05)
    assert probe.metadata["actual_output_frame_rate"] is None
    assert probe.metadata["is_constant_frame_rate"] is expected_cfr
    assert probe.metadata["video_codec"] == "h264"
    assert probe.metadata["pixel_format"] == "yuv420p"
    assert probe.metadata["rotation"] == 0.0
    assert probe.metadata["metadata_source"]["frame_count"]["method"] == (
        "FFmpeg showinfo decoded frame records, including records without PTS"
    )
    assert probe.metadata["metadata_source"]["nominal_frame_rate"]["kind"] == "measured"
    assert probe.metadata["metadata_source"]["inferred_cfr_frame_rate"]["kind"] == "calculated"


def test_probe_identifies_vfr_from_decoded_frame_timestamps(
    technical_video_fixtures: dict[str, Path],
) -> None:
    probe = probe_video_metadata(technical_video_fixtures["vfr"])

    assert probe.metadata["frame_count"] == 60
    assert probe.metadata["nominal_frame_rate"] is None
    assert probe.metadata["inferred_cfr_frame_rate"] is None
    assert probe.metadata["average_frame_rate"] is not None
    assert probe.metadata["is_constant_frame_rate"] is False
    assert probe.metadata["actual_output_frame_rate"] is None
    assert probe.metadata["timing_validation_status"] == "passed"
    assert probe.validation_errors == ()


def test_strict_rational_pts_cadence_accepts_ntsc_timeline() -> None:
    # In a 24000 stream timescale, 24000/1001 has a 1001-tick frame period.
    pts_ticks = [index * 1001 for index in range(37)]
    valid, error = validate_rational_pts_cadence(
        pts_ticks, 1, 24000, 24000, 1001
    )
    assert valid is True
    assert error is None


def test_strict_rational_pts_cadence_rejects_dropped_frame() -> None:
    pts_ticks = [0, 1001, 2002, 4004]
    valid, error = validate_rational_pts_cadence(
        pts_ticks, 1, 24000, 24000, 1001
    )
    assert valid is False
    assert error is not None


def test_strict_rational_pts_cadence_rejects_duplicate_pts() -> None:
    pts_ticks = [0, 1001, 1001, 3003]
    valid, error = validate_rational_pts_cadence(
        pts_ticks, 1, 24000, 24000, 1001
    )
    assert valid is False
    assert error is not None


def test_strict_rational_pts_cadence_rejects_regressive_pts() -> None:
    pts_ticks = [0, 1001, 500, 3003]
    valid, error = validate_rational_pts_cadence(
        pts_ticks, 1, 24000, 24000, 1001
    )
    assert valid is False
    assert error is not None


def test_strict_rational_pts_cadence_accepts_other_rational_rate() -> None:
    pts_ticks = [0, 40, 80, 120]
    valid, error = validate_rational_pts_cadence(
        pts_ticks, 1, 1000, 25, 1
    )
    assert valid is True
    assert error is None


def test_strict_rational_pts_cadence_rejects_timestamp_above_quantization_tolerance() -> None:
    pts_ticks = [0, 1002, 2004]
    valid, error = validate_rational_pts_cadence(
        pts_ticks, 1, 24000, 24000, 1001
    )
    assert valid is False
    assert error is not None


def test_strict_rational_pts_cadence_supports_documented_start_index() -> None:
    pts_ticks = [1001, 2002, 5005]
    valid, error = validate_rational_pts_cadence(
        pts_ticks, 1, 24000, 24000, 1001, start_index=2
    )
    assert valid is False

    pts_ticks = [3003, 4004, 5005]
    valid, error = validate_rational_pts_cadence(
        pts_ticks, 1, 24000, 24000, 1001, start_index=2
    )
    assert valid is True
    assert error is None


def _trusted_rate_metadata(
    numerator: int, denominator: int, *, is_cfr: bool
) -> dict[str, object]:
    rate = numerator / denominator
    fields = {
        "frame_count": 60,
        "valid_pts_count": 60,
        "is_constant_frame_rate": is_cfr,
        "nominal_frame_rate": rate,
        "nominal_frame_rate_numerator": numerator,
        "nominal_frame_rate_denominator": denominator,
    }
    method = "container nominal frame-rate rational"
    sources = {
        field: {"kind": "measured", "method": method}
        for field in (
            "nominal_frame_rate",
            "nominal_frame_rate_numerator",
            "nominal_frame_rate_denominator",
        )
    }
    sources.update(
        {
            "frame_count": {"kind": "measured", "method": "decoded frame count"},
            "valid_pts_count": {"kind": "measured", "method": "valid PTS count"},
            "is_constant_frame_rate": {"kind": "calculated", "method": "PTS intervals"},
        }
    )
    return build_video_metadata(fields, sources)


@pytest.mark.parametrize(
    ("fixture_name", "expected_ratio"),
    [
        ("cfr_24", (24, 1)),
        ("cfr_30", (30, 1)),
        ("cfr_60", (60, 1)),
        ("nonstandard", (24000, 1001)),
    ],
)
def test_target_rate_resolver_preserves_probed_cfr_rational(
    technical_video_fixtures: dict[str, Path],
    fixture_name: str,
    expected_ratio: tuple[int, int],
) -> None:
    probe = probe_video_metadata(technical_video_fixtures[fixture_name])

    assert probe.metadata["is_constant_frame_rate"] is True
    assert probe.metadata["frame_count"] == probe.metadata["valid_pts_count"]
    assert (
        probe.metadata["nominal_frame_rate_numerator"],
        probe.metadata["nominal_frame_rate_denominator"],
    ) == expected_ratio
    decision = resolve_target_frame_rate(probe.metadata)

    assert decision["status"] == "ready"
    assert decision["target_frame_rate"] == {
        "numerator": expected_ratio[0],
        "denominator": expected_ratio[1],
    }
    assert decision["input_frame_rate"]["numerator"] == expected_ratio[0]
    assert decision["input_frame_rate"]["denominator"] == expected_ratio[1]
    assert isinstance(decision["target_frame_rate"]["numerator"], int)
    assert isinstance(decision["target_frame_rate"]["denominator"], int)
    assert decision["reason"] is None


def test_nonstandard_fixture_has_exact_rational_pts_timing(
    technical_video_fixtures: dict[str, Path],
) -> None:
    report = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(technical_video_fixtures["nonstandard"]),
            "-map",
            "0:v:0",
            "-vf",
            "showinfo",
            "-frames:v",
            "3",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert report.returncode == 0, report.stderr
    config = re.search(
        r"config in time_base:\s*(\d+)/(\d+),\s*frame_rate:\s*(\d+)/(\d+)",
        report.stderr,
    )
    pts = re.findall(r"\bn:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:", report.stderr)
    assert config is not None
    assert tuple(map(int, config.groups())) == (1, 24000, 24000, 1001)
    assert list(map(int, pts)) == [0, 1001, 2002]


def test_target_rate_resolver_accepts_trusted_vfr_nominal_rate() -> None:
    decision = resolve_target_frame_rate(
        _trusted_rate_metadata(24000, 1001, is_cfr=False)
    )

    assert decision["status"] == "ready"
    assert decision["input_frame_rate"]["numerator"] == 24000
    assert decision["input_frame_rate"]["denominator"] == 1001
    assert decision["target_frame_rate"] == {"numerator": 24000, "denominator": 1001}


def test_target_rate_resolver_rejects_vfr_without_nominal_rate_even_with_estimates() -> None:
    metadata = build_video_metadata(
        {
            "frame_count": 60,
            "valid_pts_count": 60,
            "average_frame_rate": 29.5,
            "inferred_cfr_frame_rate": 30.0,
            "is_constant_frame_rate": False,
        },
        {
            "frame_count": {"kind": "measured", "method": "decoded frame count"},
            "valid_pts_count": {"kind": "measured", "method": "valid PTS count"},
            "average_frame_rate": {"kind": "calculated", "method": "PTS span"},
            "inferred_cfr_frame_rate": {"kind": "calculated", "method": "median interval"},
            "is_constant_frame_rate": {"kind": "calculated", "method": "PTS intervals"},
        },
    )

    decision = resolve_target_frame_rate(metadata)

    assert decision["status"] == "failed"
    assert decision["target_frame_rate"] is None
    assert "VFR" in decision["reason"]


def test_target_rate_resolver_rejects_untrusted_provenance_and_missing_metadata() -> None:
    metadata = _trusted_rate_metadata(30, 1, is_cfr=False)
    metadata["metadata_source"]["nominal_frame_rate_numerator"] = {
        "kind": "calculated",
        "method": "median PTS interval",
    }

    untrusted_decision = resolve_target_frame_rate(metadata)
    missing_decision = resolve_target_frame_rate(build_video_metadata())

    assert untrusted_decision["status"] == "failed"
    assert untrusted_decision["target_frame_rate"] is None
    assert missing_decision["status"] == "failed"
    assert missing_decision["input_frame_rate"] is None
    assert missing_decision["target_frame_rate"] is None


def test_target_rate_resolver_rejects_fps_above_60() -> None:
    decision = resolve_target_frame_rate(
        _trusted_rate_metadata(120, 1, is_cfr=True)
    )

    assert decision["status"] == "failed"
    assert decision["input_frame_rate"]["numerator"] == 120
    assert decision["target_frame_rate"] is None
    assert "melebihi batas maksimum 60 FPS" in decision["reason"]


def test_target_rate_resolver_rejects_conflicting_float_and_rational_metadata() -> None:
    metadata = _trusted_rate_metadata(24000, 1001, is_cfr=True)
    metadata["nominal_frame_rate"] = 24.0

    decision = resolve_target_frame_rate(metadata)

    assert decision["status"] == "failed"
    assert decision["target_frame_rate"] is None
    assert "tidak konsisten" in decision["reason"]


def test_target_rate_resolver_rejects_incomplete_pts() -> None:
    metadata = _trusted_rate_metadata(30, 1, is_cfr=True)
    metadata["valid_pts_count"] = 59

    decision = resolve_target_frame_rate(metadata)

    assert decision["status"] == "failed"
    assert decision["input_frame_rate"] is None
    assert decision["target_frame_rate"] is None
    assert "PTS tidak lengkap" in decision["reason"]


def test_probe_counts_decoded_frames_separately_from_valid_pts(
    technical_video_fixtures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import video_trimmer

    original_run = subprocess.run

    def run_with_one_missing_pts(*args, **kwargs):
        result = original_run(*args, **kwargs)
        if "-vf" not in args[0] or "showinfo" not in args[0]:
            return result
        lines = result.stderr.splitlines()
        frame_line = next(
            index
            for index, line in enumerate(lines)
            if re.search(r"\bn:\s*10\b", line) and "pts_time:" in line
        )
        lines[frame_line] = re.sub(
            r"(pts_time:)[^\s]+", r"\1N/A", lines[frame_line], count=1
        )
        return subprocess.CompletedProcess(
            args[0], result.returncode, result.stdout, "\n".join(lines)
        )

    monkeypatch.setattr(video_trimmer.subprocess, "run", run_with_one_missing_pts)
    probe = probe_video_metadata(technical_video_fixtures["cfr_30"])

    assert probe.metadata["frame_count"] == 60
    assert probe.metadata["valid_pts_count"] == 59
    assert probe.metadata["average_frame_rate"] is None
    assert probe.metadata["is_constant_frame_rate"] is None
    assert probe.metadata["timing_validation_status"] == "failed"
    assert any("59 dari 60" in error for error in probe.validation_errors)


def test_probe_reads_rotation_metadata_and_display_dimensions(
    technical_video_fixtures: dict[str, Path],
) -> None:
    probe = probe_video_metadata(technical_video_fixtures["rotated"])

    assert probe.metadata["width"] == 96
    assert probe.metadata["height"] == 64
    assert abs(probe.metadata["rotation"]) == 90
    assert probe.metadata["timing_validation_status"] == "passed"
    assert probe.validation_errors == ()


def test_probe_marks_missing_or_corrupted_metadata_invalid(tmp_path: Path) -> None:
    from video_trimmer import validate_video_metadata

    invalid_metadata = build_video_metadata()
    errors = validate_video_metadata(invalid_metadata)
    assert "average_frame_rate tidak tersedia atau tidak valid." in errors
    assert "frame_count tidak tersedia atau tidak valid." in errors
    assert "Orientasi tampilan tidak tersedia." in errors
    assert "Status CFR/VFR tidak tersedia." in errors

    corrupted = tmp_path / "corrupted.mp4"
    corrupted.write_bytes(b"not a video")
    with pytest.raises(VideoTrimmingError, match="tidak dapat didekode"):
        probe_video_metadata(corrupted)


def test_validator_detects_inconsistent_duration_and_orientation() -> None:
    from video_trimmer import validate_video_metadata

    metadata = build_video_metadata(
        {
            "source_duration": 2.0,
            "average_frame_rate": 30.0,
            "frame_count": 60,
            "width": 96,
            "height": 64,
            "rotation": 0.0,
            "video_codec": "h264",
            "pixel_format": "yuv420p",
            "is_constant_frame_rate": True,
            "inferred_cfr_frame_rate": 30.0,
        },
        {
            "source_duration": {"kind": "measured", "method": "test"},
            "average_frame_rate": {"kind": "calculated", "method": "test"},
            "frame_count": {"kind": "measured", "method": "test"},
            "width": {"kind": "measured", "method": "test"},
            "height": {"kind": "measured", "method": "test"},
            "rotation": {"kind": "measured", "method": "test"},
            "video_codec": {"kind": "measured", "method": "test"},
            "pixel_format": {"kind": "measured", "method": "test"},
            "is_constant_frame_rate": {"kind": "calculated", "method": "test"},
            "inferred_cfr_frame_rate": {"kind": "calculated", "method": "test"},
        },
    )

    duration_errors = validate_video_metadata(metadata, timestamp_duration=1.0)
    assert "Durasi container tidak konsisten dengan timestamp frame." in duration_errors

    orientation_errors = validate_video_metadata(
        metadata, expected_display_aspect=64 / 96
    )
    assert "Orientasi tampilan hasil tidak konsisten dengan sumber." in orientation_errors

    one_frame_boundary_difference = validate_video_metadata(
        metadata, expected_duration=2.0 - 1 / 30
    )
    assert "Durasi hasil tidak konsisten dengan rentang yang diminta." not in one_frame_boundary_difference

    two_frame_difference = validate_video_metadata(
        metadata, expected_duration=2.0 - 2 / 30
    )
    assert "Durasi hasil tidak konsisten dengan rentang yang diminta." in two_frame_difference
