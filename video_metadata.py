"""Contract helpers for technical video metadata."""

from collections.abc import Mapping
import math
from typing import Final

_FIELD_NAMES: Final = (
    "source_duration",
    "trimmed_duration",
    "source_start_time",
    "source_end_time",
    "nominal_frame_rate",
    "nominal_frame_rate_numerator",
    "nominal_frame_rate_denominator",
    "average_frame_rate",
    "inferred_cfr_frame_rate",
    "target_frame_rate",
    "target_frame_rate_num",
    "target_frame_rate_den",
    "target_frame_rate_display",
    "actual_output_frame_rate",
    "output_frame_count",
    "frame_count",
    "valid_pts_count",
    "timestamp_duration",
    "width",
    "height",
    "rotation",
    "video_codec",
    "pixel_format",
    "is_constant_frame_rate",
    "timing_validation_status",
    "output_fps_mode",
    "output_validation_status",
)
_PROVENANCE_KINDS: Final = {
    "measured",
    "calculated",
    "estimated",
    "provided",
    "policy",
}
_TIMING_STATUSES: Final = {
    "not_validated",
    "passed",
    "warning",
    "failed",
}
_POSITIVE_NUMERIC_FIELDS: Final = {
    "source_duration",
    "trimmed_duration",
    "source_end_time",
    "nominal_frame_rate",
    "average_frame_rate",
    "inferred_cfr_frame_rate",
    "target_frame_rate",
    "actual_output_frame_rate",
    "timestamp_duration",
}
_NON_NEGATIVE_NUMERIC_FIELDS: Final = {"source_start_time"}
_POSITIVE_INTEGER_FIELDS: Final = {
    "frame_count",
    "output_frame_count",
    "width",
    "height",
    "nominal_frame_rate_numerator",
    "nominal_frame_rate_denominator",
    "target_frame_rate_num",
    "target_frame_rate_den",
}
_NON_NEGATIVE_INTEGER_FIELDS: Final = {"valid_pts_count"}
_TEXT_FIELDS: Final = {"video_codec", "pixel_format", "target_frame_rate_display", "output_fps_mode", "output_validation_status"}


def build_video_metadata(
    values: Mapping[str, object] | None = None,
    sources: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, object]:
    """Build the B2 technical metadata contract without inventing unknown values."""
    metadata_values = dict(values or {})
    metadata_sources = dict(sources or {})
    unknown_values = metadata_values.keys() - set(_FIELD_NAMES)
    unknown_sources = metadata_sources.keys() - set(_FIELD_NAMES)
    if unknown_values or unknown_sources:
        raise ValueError("Field metadata video tidak dikenal.")

    fields: dict[str, object] = {field: None for field in _FIELD_NAMES}
    provenance: dict[str, dict[str, str | None]] = {
        field: {"kind": "unavailable", "method": None} for field in _FIELD_NAMES
    }
    fields["timing_validation_status"] = "not_validated"
    provenance["timing_validation_status"] = {
        "kind": "policy",
        "method": "initial_contract_state",
    }

    for field, value in metadata_values.items():
        if value is None:
            fields[field] = None
            continue
        source = metadata_sources.get(field)
        if source is None:
            raise ValueError(f"Sumber metadata untuk {field} wajib dicantumkan.")
        kind = source.get("kind")
        method = source.get("method")
        if kind not in _PROVENANCE_KINDS or not isinstance(method, str) or not method.strip():
            raise ValueError(f"Provenance metadata untuk {field} tidak valid.")
        if field in _POSITIVE_NUMERIC_FIELDS | _NON_NEGATIVE_NUMERIC_FIELDS:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (field in _POSITIVE_NUMERIC_FIELDS and value <= 0)
                or (field in _NON_NEGATIVE_NUMERIC_FIELDS and value < 0)
            ):
                raise ValueError(f"Nilai metadata untuk {field} tidak valid.")
        elif field in _POSITIVE_INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Nilai metadata untuk {field} tidak valid.")
        elif field in _NON_NEGATIVE_INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Nilai metadata untuk {field} tidak valid.")
        elif field in _TEXT_FIELDS:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Nilai metadata untuk {field} tidak valid.")
        elif field == "rotation":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not -360 <= value <= 360
            ):
                raise ValueError(f"Nilai metadata untuk {field} tidak valid.")
        elif field == "is_constant_frame_rate" and not isinstance(value, bool):
            raise ValueError(f"Nilai metadata untuk {field} tidak valid.")
        if field == "timing_validation_status" and value not in _TIMING_STATUSES:
            raise ValueError("Status validasi timing tidak dikenal.")
        fields[field] = value
        provenance[field] = {"kind": kind, "method": method}

    start_time = fields["source_start_time"]
    end_time = fields["source_end_time"]
    if start_time is not None and end_time is not None and end_time <= start_time:
        raise ValueError("Rentang waktu sumber metadata tidak valid.")

    return {
        "schema_version": 1,
        **fields,
        "metadata_source": provenance,
    }
