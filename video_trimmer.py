"""Video probing and browser-compatible trimming via imageio-ffmpeg."""

from dataclasses import dataclass
from fractions import Fraction
import math
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import cv2
import imageio_ffmpeg

from video_metadata import build_video_metadata


class VideoTrimmingError(RuntimeError):
    """Raised when a source cannot be probed or trimmed safely."""


@dataclass(frozen=True)
class VideoProbeResult:
    metadata: dict[str, object]
    validation_errors: tuple[str, ...]


_FRAME_INDEX_PATTERN = re.compile(r"\bn:\s*(\d+)\b")
_PTS_TIME_PATTERN = re.compile(r"\bpts_time:(N/A|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)")
_FRAME_DURATION_TIME_PATTERN = re.compile(
    r"\bduration_time:(N/A|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)
_PTS_PATTERN = re.compile(r"\bpts:\s*(-?\d+)\b")
_TIME_BASE_PATTERN = re.compile(r"\btime_base:\s*(\d+)/(\d+)\b")
_SHOWINFO_CONFIG_PATTERN = re.compile(
    r"config in time_base:\s*\d+/\d+,\s*frame_rate:\s*(\d+)/(\d+)"
)
_FRAME_SIZE_PATTERN = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
_PIXEL_FORMAT_PATTERN = re.compile(r"Video:\s*[^,\n]+,\s*([A-Za-z0-9_]+)(?:\([^)]*\))?")
_CODEC_PATTERN = re.compile(r"Video:\s*([^,\n]+)")
_DURATION_PATTERN = re.compile(r"\bDuration:\s*(\d+):(\d+):([\d.]+)")
_ROTATION_PATTERN = re.compile(r"rotation of\s+([-\d.]+)\s+degrees", re.IGNORECASE)
_STREAM_REPORTED_RATE_PATTERN = re.compile(r"\b([\d.]+)\s+fps\b")


def probe_video_metadata(path: Path, duration_field: str = "source_duration") -> VideoProbeResult:
    """Decode a video with bundled FFmpeg and collect stream and frame timing metadata."""
    if duration_field not in {"source_duration", "trimmed_duration"}:
        raise ValueError("Field durasi metadata tidak valid.")
    if not path.is_file() or path.stat().st_size == 0:
        raise VideoTrimmingError("File video tidak tersedia atau kosong.")
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "info",
        "-noautorotate",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-vf",
        "showinfo",
        "-an",
        "-sn",
        "-dn",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as error:
        raise VideoTrimmingError("Pemeriksaan video melewati batas waktu.") from error
    except OSError as error:
        raise VideoTrimmingError("FFmpeg tidak dapat dijalankan untuk memeriksa video.") from error

    diagnostic = completed.stderr
    frame_records: list[tuple[int, float | None, float | None]] = []
    pts_ticks: list[int] = []
    for line in diagnostic.splitlines():
        if "[Parsed_showinfo_" not in line:
            continue
        index_match = _FRAME_INDEX_PATTERN.search(line)
        if not index_match:
            continue
        pts_match = _PTS_TIME_PATTERN.search(line)
        duration_match = _FRAME_DURATION_TIME_PATTERN.search(line)
        pts = _parse_finite_float(pts_match.group(1)) if pts_match else None
        frame_duration = (
            _parse_finite_float(duration_match.group(1)) if duration_match else None
        )
        frame_records.append((int(index_match.group(1)), pts, frame_duration))
        pts_tick_match = _PTS_PATTERN.search(line)
        if pts_tick_match:
            pts_ticks.append(int(pts_tick_match.group(1)))
    frame_timestamps = [pts for _, pts, _ in frame_records if pts is not None]
    decoded_frame_count = len(frame_records)
    valid_pts_count = len(frame_timestamps)
    timestamps_complete = decoded_frame_count > 0 and valid_pts_count == decoded_frame_count
    if completed.returncode != 0 and not decoded_frame_count:
        raise VideoTrimmingError("Video tidak dapat didekode oleh FFmpeg.")

    video_line = next((line for line in diagnostic.splitlines() if "Video:" in line), "")
    size_match = _FRAME_SIZE_PATTERN.search(video_line)
    codec_match = _CODEC_PATTERN.search(video_line)
    pixel_match = _PIXEL_FORMAT_PATTERN.search(video_line)
    duration_match = _DURATION_PATTERN.search(diagnostic)
    rotation_match = _ROTATION_PATTERN.search(diagnostic)
    stream_rate_match = _SHOWINFO_CONFIG_PATTERN.search(diagnostic)
    time_base_match = _TIME_BASE_PATTERN.search(diagnostic)
    reported_rate_match = _STREAM_REPORTED_RATE_PATTERN.search(video_line)

    width = int(size_match.group(1)) if size_match else None
    height = int(size_match.group(2)) if size_match else None
    container_duration = (
        int(duration_match.group(1)) * 3600
        + int(duration_match.group(2)) * 60
        + float(duration_match.group(3))
        if duration_match
        else None
    )
    frame_intervals = [
        later - earlier
        for earlier, later in zip(frame_timestamps, frame_timestamps[1:])
    ]
    intervals = [interval for interval in frame_intervals if interval > 0]
    median_interval = _median(intervals) if timestamps_complete else None
    last_frame_duration = frame_records[-1][2] if timestamps_complete else None
    estimated_last_frame_duration = False
    if timestamps_complete and (last_frame_duration is None or last_frame_duration <= 0):
        last_frame_duration = median_interval
        estimated_last_frame_duration = last_frame_duration is not None
    timestamp_duration = (
        frame_timestamps[-1] - frame_timestamps[0] + last_frame_duration
        if len(frame_timestamps) >= 2
        and frame_timestamps[-1] > frame_timestamps[0]
        and last_frame_duration is not None
        else None
    )
    average_fps = (
        (valid_pts_count - 1) / (frame_timestamps[-1] - frame_timestamps[0])
        if timestamps_complete
        and valid_pts_count >= 2
        and frame_timestamps[-1] > frame_timestamps[0]
        else None
    )
    cfr = (
        _is_constant_interval(frame_intervals, intervals, median_interval)
        if timestamps_complete
        else None
    )
    stream_rate = (
        Fraction(int(stream_rate_match.group(1)), int(stream_rate_match.group(2)))
        if stream_rate_match and int(stream_rate_match.group(2)) > 0
        else None
    )
    reported_rate = (
        float(reported_rate_match.group(1))
        if reported_rate_match and _parse_finite_float(reported_rate_match.group(1)) is not None
        else None
    )
    nominal_rate_valid = stream_rate is not None
    if stream_rate is not None and reported_rate is not None and cfr is False:
        nominal_rate_valid = abs(float(stream_rate) - reported_rate) <= max(0.01, reported_rate * 0.02)
    if cfr is True and stream_rate is not None and median_interval is not None:
        nominal_rate_valid = (
            abs(float(1 / stream_rate) - median_interval)
            <= max(0.001, median_interval * 0.01)
        )
    strict_cadence_valid = True
    if stream_rate is not None and time_base_match and len(pts_ticks) == decoded_frame_count:
        strict_cadence_valid, _ = validate_rational_pts_cadence(
            pts_ticks,
            int(time_base_match.group(1)),
            int(time_base_match.group(2)),
            stream_rate.numerator,
            stream_rate.denominator,
        )
        if cfr is False:
            # VFR retains declared nominal metadata; strict cadence is intentionally
            # checked only after this stream has been normalized as output.
            strict_cadence_valid = True
    if cfr is True and nominal_rate_valid and stream_rate is not None and strict_cadence_valid:
        nominal_rate_numerator = stream_rate.numerator
        nominal_rate_denominator = stream_rate.denominator
        nominal_fps = float(stream_rate)
    else:
        nominal_rate_numerator = None
        nominal_rate_denominator = None
        nominal_fps = None
    inferred_cfr_fps = 1 / median_interval if cfr is True and median_interval else None
    actual_output_fps = (
        inferred_cfr_fps if duration_field == "trimmed_duration" else None
    )
    rotation = float(rotation_match.group(1)) if rotation_match else (0.0 if width and height else None)
    duration = container_duration if container_duration and container_duration > 0 else timestamp_duration

    values: dict[str, object] = {
        duration_field: duration,
        "nominal_frame_rate": nominal_fps,
        "nominal_frame_rate_numerator": nominal_rate_numerator,
        "nominal_frame_rate_denominator": nominal_rate_denominator,
        "average_frame_rate": average_fps,
        "inferred_cfr_frame_rate": inferred_cfr_fps,
        "actual_output_frame_rate": actual_output_fps,
        "frame_count": decoded_frame_count if decoded_frame_count > 0 else None,
        "valid_pts_count": valid_pts_count,
        "timestamp_duration": timestamp_duration,
        "width": width,
        "height": height,
        "rotation": rotation,
        "video_codec": codec_match.group(1).strip().split(" ")[0] if codec_match else None,
        "pixel_format": pixel_match.group(1) if pixel_match else None,
        "is_constant_frame_rate": cfr,
    }
    sources = {
        duration_field: {
            "kind": "measured" if container_duration and container_duration > 0 else "calculated",
            "method": "FFmpeg container duration" if container_duration and container_duration > 0 else "decoded PTS span plus median frame interval",
        },
        "nominal_frame_rate": {
            "kind": "measured" if cfr is True else "unavailable",
            "method": (
                "FFmpeg stream frame_rate rational, cross-validated against complete CFR PTS"
                if cfr is True
                else "Nominal FPS is not derived from a VFR source"
            ),
        },
        "nominal_frame_rate_numerator": {
            "kind": "measured" if cfr is True else "unavailable",
            "method": (
                "FFmpeg stream frame_rate numerator, cross-validated against complete CFR PTS"
                if cfr is True
                else "Nominal FPS is not derived from a VFR source"
            ),
        },
        "nominal_frame_rate_denominator": {
            "kind": "measured" if cfr is True else "unavailable",
            "method": (
                "FFmpeg stream frame_rate denominator, cross-validated against complete CFR PTS"
                if cfr is True
                else "Nominal FPS is not derived from a VFR source"
            ),
        },
        "average_frame_rate": {
            "kind": "calculated",
            "method": "valid PTS count minus one divided by first-to-last PTS span",
        },
        "inferred_cfr_frame_rate": {
            "kind": "calculated",
            "method": "reciprocal of median decoded PTS interval when CFR is validated",
        },
        "actual_output_frame_rate": {
            "kind": "calculated",
            "method": "output PTS interval inference; populated only for a trimmed CFR stream",
        },
        "frame_count": {"kind": "measured", "method": "FFmpeg showinfo decoded frame records, including records without PTS"},
        "valid_pts_count": {"kind": "measured", "method": "FFmpeg showinfo records with numeric PTS"},
        "timestamp_duration": {
            "kind": "calculated" if not estimated_last_frame_duration else "estimated",
            "method": (
                "first-to-last valid PTS plus final showinfo frame duration"
                if not estimated_last_frame_duration
                else "first-to-last valid PTS plus median interval estimate for missing final frame duration"
            ),
        },
        "width": {"kind": "measured", "method": "FFmpeg decoded input stream diagnostic"},
        "height": {"kind": "measured", "method": "FFmpeg decoded input stream diagnostic"},
        "rotation": {
            "kind": "measured",
            "method": "FFmpeg display matrix; zero when no display rotation is present",
        },
        "video_codec": {"kind": "measured", "method": "FFmpeg input stream diagnostic"},
        "pixel_format": {"kind": "measured", "method": "FFmpeg input stream diagnostic"},
        "is_constant_frame_rate": {
            "kind": "calculated",
            "method": "decoded frame PTS interval deviation from median",
        },
    }
    metadata = build_video_metadata(values, sources)
    errors = validate_video_metadata(metadata)
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            errors.append("OpenCV tidak dapat membuka video untuk pemeriksaan silang.")
        else:
            reported_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            if reported_frames <= 0:
                errors.append("OpenCV melaporkan frame count nol atau tidak valid.")
            elif abs(int(round(reported_frames)) - decoded_frame_count) > max(1, round(decoded_frame_count * 0.01)):
                errors.append("Frame count OpenCV tidak konsisten dengan frame FFmpeg yang didekode.")
    finally:
        capture.release()
    if not timestamps_complete:
        errors.append(
            f"Timestamp PTS tidak lengkap: {valid_pts_count} dari {decoded_frame_count} frame memiliki PTS valid."
        )
    if completed.returncode != 0 and decoded_frame_count:
        errors.append("FFmpeg melaporkan kegagalan setelah sebagian frame didekode.")
    metadata["timing_validation_status"] = "failed" if errors else "passed"
    metadata["metadata_source"]["timing_validation_status"] = {
        "kind": "calculated",
        "method": "video metadata validator",
    }
    return VideoProbeResult(metadata, tuple(errors))


def _parse_finite_float(value: str) -> float | None:
    if value == "N/A":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def resolve_target_frame_rate(metadata: dict[str, object]) -> dict[str, object]:
    """Resolve a CFR normalization target without guessing or float-based rounding."""
    status = "failed"
    input_rate: dict[str, object] | None = None
    target_rate: dict[str, int] | None = None
    reason: str | None = None

    frame_count = metadata.get("frame_count")
    valid_pts_count = metadata.get("valid_pts_count")
    if (
        not isinstance(frame_count, int)
        or isinstance(frame_count, bool)
        or frame_count <= 0
        or not isinstance(valid_pts_count, int)
        or isinstance(valid_pts_count, bool)
        or valid_pts_count != frame_count
    ):
        reason = "PTS tidak lengkap atau jumlah frame hasil decode tidak valid."
    else:
        is_cfr = metadata.get("is_constant_frame_rate")
        provenance = metadata.get("metadata_source", {})
        nominal_provenance = (
            provenance.get("nominal_frame_rate_numerator")
            if isinstance(provenance, dict)
            else None
        )
        denominator_provenance = (
            provenance.get("nominal_frame_rate_denominator")
            if isinstance(provenance, dict)
            else None
        )
        nominal_provenance_trusted = (
            isinstance(nominal_provenance, dict)
            and nominal_provenance.get("kind") in {"measured", "provided"}
            and isinstance(nominal_provenance.get("method"), str)
            and bool(nominal_provenance["method"].strip())
            and isinstance(denominator_provenance, dict)
            and denominator_provenance.get("kind") in {"measured", "provided"}
            and isinstance(denominator_provenance.get("method"), str)
            and bool(denominator_provenance["method"].strip())
        )
        numerator = metadata.get("nominal_frame_rate_numerator")
        denominator = metadata.get("nominal_frame_rate_denominator")
        if (
            is_cfr is True
            and not nominal_provenance_trusted
            and metadata.get("inferred_cfr_frame_rate") is not None
        ):
            reason = "CFR terdeteksi, tetapi rasio FPS sumber yang tervalidasi tidak tersedia."
        elif is_cfr is not True and is_cfr is not False:
            reason = "Status CFR/VFR tidak tersedia."
        elif is_cfr is False and not nominal_provenance_trusted:
            reason = "VFR tidak memiliki nominal FPS rational dengan provenance tepercaya."
        elif not nominal_provenance_trusted:
            reason = "Metadata nominal FPS rational tidak tersedia atau provenance tidak tepercaya."
        elif (
            not isinstance(numerator, int)
            or isinstance(numerator, bool)
            or numerator <= 0
            or not isinstance(denominator, int)
            or isinstance(denominator, bool)
            or denominator <= 0
        ):
            reason = "Nominal FPS rational tidak valid."
        else:
            rate = Fraction(numerator, denominator)
            input_rate = {
                "numerator": rate.numerator,
                "denominator": rate.denominator,
                "provenance": {
                    "numerator": nominal_provenance,
                    "denominator": denominator_provenance,
                },
            }
            nominal_float = metadata.get("nominal_frame_rate")
            nominal_float_conflicts = (
                nominal_float is not None
                and (
                    isinstance(nominal_float, bool)
                    or not isinstance(nominal_float, (int, float))
                    or not math.isfinite(nominal_float)
                    or abs(nominal_float - float(rate))
                    > max(1e-9, float(rate) * 1e-9)
                )
            )
            if nominal_float_conflicts:
                reason = "Nilai nominal FPS dan rasio numerator/denominator tidak konsisten."
            elif rate > 60:
                reason = "FPS sumber melebihi batas maksimum 60 FPS; konversi otomatis ditolak."
            else:
                target_rate = {
                    "numerator": rate.numerator,
                    "denominator": rate.denominator,
                }
                status = "ready"

    return {
        "status": status,
        "input_frame_rate": input_rate,
        "target_frame_rate": target_rate,
        "reason": reason,
    }


def validate_video_metadata(
    metadata: dict[str, object],
    timestamp_duration: float | None = None,
    expected_duration: float | None = None,
    expected_display_aspect: float | None = None,
) -> list[str]:
    """Return validation errors without treating unknown technical values as valid."""
    errors: list[str] = []
    for field in ("average_frame_rate",):
        value = metadata.get(field)
        if value is None or not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"{field} tidak tersedia atau tidak valid.")
    if (
        metadata.get("is_constant_frame_rate") is True
        and metadata.get("inferred_cfr_frame_rate") is None
    ):
        errors.append("inferred_cfr_frame_rate tidak tersedia atau tidak valid untuk video CFR.")
    for field in ("frame_count", "width", "height"):
        value = metadata.get(field)
        if value is None or not isinstance(value, int) or value <= 0:
            errors.append(f"{field} tidak tersedia atau tidak valid.")
    valid_pts_count = metadata.get("valid_pts_count")
    frame_count = metadata.get("frame_count")
    if valid_pts_count is not None and (
        not isinstance(valid_pts_count, int)
        or valid_pts_count < 0
        or not isinstance(frame_count, int)
        or valid_pts_count != frame_count
    ):
        errors.append("Jumlah timestamp PTS valid tidak konsisten dengan frame hasil decode.")
    for field in ("video_codec", "pixel_format"):
        value = metadata.get(field)
        if value is None:
            errors.append(f"{field} tidak tersedia atau tidak valid.")
    if any(metadata.get(field) is None for field in ("width", "height", "rotation")):
        errors.append("Orientasi tampilan tidak tersedia.")
    rotation = metadata.get("rotation")
    if isinstance(rotation, (int, float)) and abs(rotation / 90 - round(rotation / 90)) > 0.001:
        errors.append("Rotasi tampilan bukan kelipatan 90 derajat.")
    if metadata.get("source_duration") is None and metadata.get("trimmed_duration") is None:
        errors.append("Durasi tidak tersedia atau tidak valid.")
    for field in ("source_duration", "trimmed_duration"):
        value = metadata.get(field)
        if value is not None and (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            errors.append(f"{field} tidak tersedia atau tidak valid.")
    if metadata.get("is_constant_frame_rate") is None:
        errors.append("Status CFR/VFR tidak tersedia.")
    if (
        metadata.get("is_constant_frame_rate") is True
        and metadata.get("nominal_frame_rate_numerator") is None
    ):
        errors.append("Cadence PTS CFR tidak mengikuti nominal rational frame_rate.")
    duration = metadata.get("source_duration") or metadata.get("trimmed_duration")
    measured_timestamp_duration = (
        timestamp_duration
        if timestamp_duration is not None
        else metadata.get("timestamp_duration")
    )
    timestamp_duration_provenance = metadata.get("metadata_source", {}).get(
        "timestamp_duration", {}
    )
    timestamp_tolerance = 0.01
    if (
        isinstance(timestamp_duration_provenance, dict)
        and timestamp_duration_provenance.get("kind") == "estimated"
    ):
        timing_rate = metadata.get("average_frame_rate")
        if isinstance(timing_rate, (int, float)) and timing_rate > 0:
            timestamp_tolerance = max(0.01, 1 / timing_rate + 0.005)
    if (
        measured_timestamp_duration is not None
        and isinstance(duration, (int, float))
        and abs(duration - measured_timestamp_duration) > timestamp_tolerance
    ):
        errors.append("Durasi container tidak konsisten dengan timestamp frame.")
    rate = metadata.get("inferred_cfr_frame_rate")
    if not isinstance(rate, (int, float)) or rate <= 0:
        rate = metadata.get("average_frame_rate")
    expected_duration_tolerance = (
        max(0.005, 1 / rate + 0.005)
        if isinstance(rate, (int, float)) and rate > 0
        else 0.005
    )
    if (
        expected_duration is not None
        and isinstance(duration, (int, float))
        and abs(duration - expected_duration) > expected_duration_tolerance
    ):
        errors.append("Durasi hasil tidak konsisten dengan rentang yang diminta.")
    if expected_display_aspect is not None:
        width, height, rotation = (
            metadata.get("width"),
            metadata.get("height"),
            metadata.get("rotation"),
        )
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or not isinstance(rotation, (int, float))
        ):
            errors.append("Orientasi tampilan tidak tersedia.")
        else:
            display_width, display_height = _display_dimensions(width, height, rotation)
            display_aspect = display_width / display_height
            if abs(display_aspect - expected_display_aspect) > max(0.01, expected_display_aspect * 0.01):
                errors.append("Orientasi tampilan hasil tidak konsisten dengan sumber.")
    return errors


def build_trim_video_metadata(
    source: VideoProbeResult,
    trimmed: VideoProbeResult,
    start_time: float,
    end_time: float,
    target_frame_rate_numerator: int | None = None,
    target_frame_rate_denominator: int | None = None,
) -> VideoProbeResult:
    """Combine source and output measurements and validate the trimmed stream."""
    source_metadata = source.metadata
    output_metadata = trimmed.metadata
    values: dict[str, object] = {
        "source_duration": source_metadata.get("source_duration"),
        "trimmed_duration": output_metadata.get("trimmed_duration"),
        "source_start_time": start_time,
        "source_end_time": end_time,
        "target_frame_rate_num": target_frame_rate_numerator,
        "target_frame_rate_den": target_frame_rate_denominator,
        "target_frame_rate_display": (
            f"{target_frame_rate_numerator}/{target_frame_rate_denominator}"
            if target_frame_rate_numerator and target_frame_rate_denominator
            else None
        ),
        "output_frame_count": output_metadata.get("frame_count"),
        "output_fps_mode": "cfr" if output_metadata.get("is_constant_frame_rate") is True else None,
        "output_validation_status": "passed",
        **{
            field: output_metadata.get(field)
            for field in (
                "nominal_frame_rate",
                "nominal_frame_rate_numerator",
                "nominal_frame_rate_denominator",
                "average_frame_rate",
                "inferred_cfr_frame_rate",
                "actual_output_frame_rate",
                "frame_count",
                "valid_pts_count",
                "timestamp_duration",
                "width",
                "height",
                "rotation",
                "video_codec",
                "pixel_format",
                "is_constant_frame_rate",
            )
        },
        "target_frame_rate": None,
        "timing_validation_status": "not_validated",
    }
    sources: dict[str, dict[str, str]] = {}
    source_fields = {"source_duration"}
    provided_fields = {"source_start_time", "source_end_time"}
    for field, value in values.items():
        if value is None:
            continue
        if field in source_fields:
            sources[field] = source_metadata["metadata_source"][field]
        elif field in provided_fields:
            sources[field] = {"kind": "provided", "method": "session trim request"}
        elif field == "timing_validation_status":
            sources[field] = {"kind": "calculated", "method": "video metadata validator"}
        elif field in {
            "target_frame_rate_num",
            "target_frame_rate_den",
            "target_frame_rate_display",
            "output_frame_count",
            "output_fps_mode",
            "output_validation_status",
        }:
            sources[field] = {"kind": "policy", "method": "B2.3.2 CFR normalization policy"}
        else:
            sources[field] = output_metadata["metadata_source"][field]

    errors = [*source.validation_errors, *trimmed.validation_errors]
    if target_frame_rate_numerator and target_frame_rate_denominator:
        output_rate_numerator = output_metadata.get("nominal_frame_rate_numerator")
        output_rate_denominator = output_metadata.get("nominal_frame_rate_denominator")
        if (
            output_rate_numerator != target_frame_rate_numerator
            or output_rate_denominator != target_frame_rate_denominator
        ):
            errors.append("FPS output tidak sama dengan target rational yang diselesaikan.")
        if output_metadata.get("is_constant_frame_rate") is not True:
            errors.append("Video output bukan CFR.")
    source_width = source_metadata.get("width")
    source_height = source_metadata.get("height")
    source_rotation = source_metadata.get("rotation")
    expected_display_aspect = None
    if (
        isinstance(source_width, int)
        and isinstance(source_height, int)
        and isinstance(source_rotation, (int, float))
        and source_height > 0
    ):
        display_width, display_height = _display_dimensions(
            source_width, source_height, source_rotation
        )
        expected_display_aspect = display_width / display_height
    errors.extend(
        validate_video_metadata(
            output_metadata,
            expected_duration=end_time - start_time,
            expected_display_aspect=expected_display_aspect,
        )
    )
    values["timing_validation_status"] = "failed" if errors else "passed"
    values["output_validation_status"] = "failed" if errors else "passed"
    sources["timing_validation_status"] = {
        "kind": "calculated",
        "method": "source, decoded output timestamps, duration and display orientation validation",
    }
    metadata = build_video_metadata(values, sources)
    return VideoProbeResult(metadata, tuple(dict.fromkeys(errors)))


def _display_dimensions(width: int, height: int, rotation: float) -> tuple[int, int]:
    quarter_turns = round(rotation / 90)
    if abs(rotation - quarter_turns * 90) > 0.1:
        return width, height
    if quarter_turns % 2:
        return height, width
    return width, height


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _is_constant_interval(
    intervals: list[float],
    positive_intervals: list[float],
    median_interval: float | None,
) -> bool | None:
    if len(intervals) < 2 or median_interval is None or median_interval <= 0:
        return None
    if any(interval <= 0 for interval in intervals):
        return False
    tolerance = max(0.001, median_interval * 0.05)
    return all(abs(interval - median_interval) <= tolerance for interval in positive_intervals)


def validate_rational_pts_cadence(
    pts_ticks: list[int],
    time_base_numerator: int,
    time_base_denominator: int,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    start_index: int = 0,
) -> tuple[bool, str | None]:
    """Validate every PTS against an exact rational timeline.

    The first supplied PTS is the timeline origin. ``start_index`` records the
    absolute frame position when validating a partial timeline; cadence itself
    is based on the supplied sequence and its relative frame indices.
    """
    if (
        len(pts_ticks) < 2
        or time_base_numerator <= 0
        or time_base_denominator <= 0
        or frame_rate_numerator <= 0
        or frame_rate_denominator <= 0
        or start_index < 0
    ):
        return False, "PTS cadence membutuhkan sekurang-kurangnya dua frame dan rasio rational yang valid."

    first_pts = pts_ticks[0]
    for index, pts in enumerate(pts_ticks):
        ideal_ticks = (
            Fraction(first_pts, 1)
            + Fraction(index, 1)
            * Fraction(frame_rate_denominator * time_base_denominator, frame_rate_numerator * time_base_numerator)
        )
        distance_ticks = abs(Fraction(pts, 1) - ideal_ticks)
        # FFmpeg may quantize timestamps by at most one stream time-base tick.
        if distance_ticks > Fraction(1, 2):
            return False, (
                f"PTS frame {index} tidak mengikuti cadence "
                f"{frame_rate_numerator}/{frame_rate_denominator} pada time_base "
                f"{time_base_numerator}/{time_base_denominator}."
            )
    return True, None


def probe_video(path: Path) -> tuple[float, int, float]:
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise VideoTrimmingError("Video tidak dapat dibaca untuk menentukan durasi.")
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if fps <= 0 or frames <= 0:
            raise VideoTrimmingError("Durasi atau frame video tidak valid.")
        duration = frames / fps
        if duration <= 0:
            raise VideoTrimmingError("Durasi video tidak valid.")
        return duration, int(frames), fps
    finally:
        capture.release()


def trim_video(
    source: Path,
    destination: Path,
    start_time: float,
    end_time: float,
    target_frame_rate_numerator: int,
    target_frame_rate_denominator: int,
) -> float:
    """Encode the selected interval to a new H.264 MP4 and validate its duration."""
    if (
        isinstance(target_frame_rate_numerator, bool)
        or not isinstance(target_frame_rate_numerator, int)
        or target_frame_rate_numerator <= 0
        or isinstance(target_frame_rate_denominator, bool)
        or not isinstance(target_frame_rate_denominator, int)
        or target_frame_rate_denominator <= 0
    ):
        raise VideoTrimmingError("Target FPS rational tidak valid.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"trim-{uuid4().hex}.mp4")
    duration = end_time - start_time
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_time:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        f"setpts=PTS-STARTPTS,fps={target_frame_rate_numerator}/{target_frame_rate_denominator}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-fps_mode:v",
        "cfr",
        "-r",
        f"{target_frame_rate_numerator}/{target_frame_rate_denominator}",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(temporary),
    ]
    try:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired as error:
            raise VideoTrimmingError("Proses trimming melewati batas waktu.") from error
        except OSError as error:
            raise VideoTrimmingError("FFmpeg tidak dapat dijalankan untuk trimming.") from error
        if completed.returncode != 0:
            raise VideoTrimmingError("FFmpeg gagal memotong video.")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise VideoTrimmingError("Video hasil trimming tidak berhasil dibuat.")
        actual_duration, frames, fps = probe_video(temporary)
        if frames <= 0 or actual_duration <= 0:
            raise VideoTrimmingError("Video hasil trimming tidak valid.")
        duration_tolerance = max(0.25, 2 / fps)
        if abs(actual_duration - duration) > duration_tolerance:
            raise VideoTrimmingError("Durasi video hasil trimming tidak sesuai pilihan.")
        temporary.replace(destination)
        return actual_duration
    finally:
        temporary.unlink(missing_ok=True)
