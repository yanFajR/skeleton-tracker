"""Atomic JSON persistence for baseline and retake metadata."""

import json
import math
import re
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_VIDEO_PATTERN = re.compile(r"^[0-9a-f]{32}\.(mp4|mov|webm)$")
_OUTPUT_PATTERN = re.compile(r"^[0-9a-f]{32}\.mp4$")
_MAX_NOTE_LENGTH = 2000


class ComparisonNotFoundError(LookupError):
    """Raised when a comparison ID does not identify a stored comparison."""


class ComparisonConflictError(ValueError):
    """Raised when an attempt cannot be added or processed in its current state."""


class SessionNotFoundError(LookupError):
    """Raised when a session ID does not identify a stored analysis session."""


class SessionConflictError(ValueError):
    """Raised when a session cannot be processed in its current state."""


class MetadataStoreError(RuntimeError):
    """Raised when persisted comparison metadata is unreadable or cannot be saved."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ComparisonNotFoundError("Comparison atau attempt tidak ditemukan.")
    return value


def validate_session_id(value: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise SessionNotFoundError("Sesi analisis tidak ditemukan.")
    return value


def validate_note(note: str) -> str:
    if len(note) > _MAX_NOTE_LENGTH:
        raise ValueError(f"Catatan tidak boleh lebih dari {_MAX_NOTE_LENGTH} karakter.")
    return note


class ComparisonStore:
    """Persist comparison records in one atomically replaced JSON file."""

    def __init__(self, path: Path, upload_directory: Path, output_directory: Path) -> None:
        self.path = path
        self.upload_directory = upload_directory
        self.output_directory = output_directory
        self._lock = threading.RLock()

    def create_baseline(self, source_filename: str, note: str = "") -> dict[str, Any]:
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        validate_note(note)
        timestamp = _now()
        attempt = self._new_attempt("baseline", source_filename, note, timestamp)
        comparison = {
            "comparison_id": uuid4().hex,
            "created_at": timestamp,
            "updated_at": timestamp,
            "baseline": attempt,
            "retake": None,
        }
        with self._lock:
            data = self._load()
            data["comparisons"].append(comparison)
            self._save(data)
        return deepcopy(comparison)

    def add_retake(
        self, comparison_id: str, source_filename: str, note: str = ""
    ) -> dict[str, Any]:
        validate_id(comparison_id)
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        validate_note(note)
        with self._lock:
            data = self._load()
            comparison = self._find(data, comparison_id)
            if comparison["retake"] is not None:
                raise ComparisonConflictError("Comparison ini sudah memiliki retake.")
            if comparison["baseline"]["source_filename"] == source_filename:
                raise ComparisonConflictError("Retake harus menggunakan file video yang berbeda.")
            timestamp = _now()
            comparison["retake"] = self._new_attempt(
                "retake", source_filename, note, timestamp
            )
            comparison["updated_at"] = timestamp
            self._save(data)
            return deepcopy(comparison)

    def find_by_source(self, source_filename: str) -> tuple[str, str] | None:
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        with self._lock:
            data = self._load()
            for comparison in data["comparisons"]:
                for role in ("baseline", "retake"):
                    attempt = comparison[role]
                    if attempt is not None and attempt["source_filename"] == source_filename:
                        return comparison["comparison_id"], role
        return None

    def begin_processing(self, source_filename: str) -> dict[str, Any]:
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        if not (self.upload_directory / source_filename).is_file():
            raise FileNotFoundError("Video tidak ditemukan.")
        with self._lock:
            data = self._load()
            located = self._find_attempt_by_source(data, source_filename)
            if located is None:
                timestamp = _now()
                comparison = {
                    "comparison_id": uuid4().hex,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "baseline": self._new_attempt("baseline", source_filename, "", timestamp),
                    "retake": None,
                }
                comparison["baseline"]["status"] = "processing"
                data["comparisons"].append(comparison)
                attempt = comparison["baseline"]
            else:
                comparison, attempt = located
                if attempt["status"] == "processing":
                    raise ComparisonConflictError("Video sedang diproses.")
                if attempt["status"] == "succeeded":
                    raise ComparisonConflictError("Video ini sudah berhasil diproses.")
                timestamp = _now()
                attempt.update(
                    status="processing",
                    result_filename=None,
                    frame_angles=[],
                    fps=None,
                    frames=None,
                    duration=None,
                    error=None,
                    updated_at=timestamp,
                )
                comparison["updated_at"] = timestamp
            self._save(data)
            return deepcopy(attempt)

    def complete_processing(
        self,
        source_filename: str,
        result_filename: str,
        processing_result: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        self._validate_file_reference(result_filename, self.output_directory, _OUTPUT_PATTERN)
        if not (self.output_directory / result_filename).is_file():
            raise FileNotFoundError("Video hasil tidak tersedia.")
        try:
            frame_angles = processing_result["frame_angles"]
            fps = processing_result["fps"]
            frames = processing_result["frames"]
            duration = processing_result["duration"]
        except KeyError as error:
            raise MetadataStoreError("Hasil pemrosesan video tidak lengkap.") from error
        if (
            not isinstance(frame_angles, list)
            or not isinstance(fps, (int, float))
            or not math.isfinite(fps)
            or fps <= 0
            or not isinstance(frames, int)
            or frames <= 0
            or len(frame_angles) != frames
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise MetadataStoreError("Hasil pemrosesan video tidak valid.")
        for item in frame_angles:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("time_seconds"), (int, float))
                or not math.isfinite(item["time_seconds"])
                or not {"left_elbow", "right_elbow"}.issubset(item)
                or any(
                    angle is not None
                    and (
                        not isinstance(angle, (int, float))
                        or not math.isfinite(angle)
                        or angle < 0
                        or angle > 180
                    )
                    for angle in (item.get("left_elbow"), item.get("right_elbow"))
                )
            ):
                raise MetadataStoreError("Hasil sudut siku tidak valid.")
        with self._lock:
            data = self._load()
            located = self._find_attempt_by_source(data, source_filename)
            if located is None:
                raise MetadataStoreError("Attempt untuk video tidak ditemukan.")
            comparison, attempt = located
            if attempt["status"] != "processing":
                raise ComparisonConflictError("Status attempt berubah saat pemrosesan.")
            timestamp = _now()
            attempt.update(
                status="succeeded",
                result_filename=result_filename,
                frame_angles=frame_angles,
                fps=fps,
                frames=frames,
                duration=duration,
                error=None,
                updated_at=timestamp,
            )
            comparison["updated_at"] = timestamp
            self._save(data)
            return deepcopy(attempt)

    def fail_processing(self, source_filename: str, error: str) -> None:
        with self._lock:
            data = self._load()
            located = self._find_attempt_by_source(data, source_filename)
            if located is None:
                return
            comparison, attempt = located
            timestamp = _now()
            attempt.update(
                status="failed",
                result_filename=None,
                frame_angles=[],
                fps=None,
                frames=None,
                duration=None,
                error=error,
                updated_at=timestamp,
            )
            comparison["updated_at"] = timestamp
            self._save(data)

    def update_note(self, comparison_id: str, attempt_id: str, note: str) -> dict[str, Any]:
        validate_id(comparison_id)
        validate_id(attempt_id)
        validate_note(note)
        with self._lock:
            data = self._load()
            comparison = self._find(data, comparison_id)
            attempt = next(
                (
                    comparison[role]
                    for role in ("baseline", "retake")
                    if comparison[role] is not None
                    and comparison[role]["attempt_id"] == attempt_id
                ),
                None,
            )
            if attempt is None:
                raise ComparisonNotFoundError("Attempt tidak ditemukan.")
            attempt["note"] = note
            attempt["updated_at"] = _now()
            comparison["updated_at"] = attempt["updated_at"]
            self._save(data)
            return deepcopy(comparison)

    def get(self, comparison_id: str) -> dict[str, Any]:
        validate_id(comparison_id)
        with self._lock:
            data = self._load()
            comparison = self._find(data, comparison_id)
            changed = self._reconcile(comparison)
            if changed:
                self._save(data)
            return deepcopy(comparison)

    def recover_interrupted(self) -> None:
        """Mark in-flight jobs interrupted by a server restart as failed."""
        with self._lock:
            data = self._load()
            changed = False
            for comparison in data["comparisons"]:
                changed = self._reconcile(comparison, startup=True) or changed
            if changed:
                self._save(data)

    @staticmethod
    def _new_attempt(role: str, source_filename: str, note: str, timestamp: str) -> dict[str, Any]:
        return {
            "attempt_id": uuid4().hex,
            "role": role,
            "source_filename": source_filename,
            "result_filename": None,
            "frame_angles": [],
            "fps": None,
            "frames": None,
            "duration": None,
            "note": note,
            "status": "uploaded",
            "error": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "comparisons": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MetadataStoreError(f"Metadata comparison tidak dapat dibaca: {error}") from error
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or not isinstance(data.get("comparisons"), list)
        ):
            raise MetadataStoreError("Format metadata comparison tidak valid.")
        comparison_ids: set[str] = set()
        attempt_ids: set[str] = set()
        source_files: set[str] = set()
        for comparison in data["comparisons"]:
            if not isinstance(comparison, dict):
                raise MetadataStoreError("Format metadata comparison tidak valid.")
            comparison_id = comparison.get("comparison_id")
            if (
                not isinstance(comparison_id, str)
                or not _ID_PATTERN.fullmatch(comparison_id)
                or comparison_id in comparison_ids
            ):
                raise MetadataStoreError("ID comparison pada metadata tidak valid.")
            comparison_ids.add(comparison_id)
            for role in ("baseline", "retake"):
                attempt = comparison.get(role)
                if attempt is None:
                    if role == "baseline":
                        raise MetadataStoreError("Metadata baseline tidak tersedia.")
                    continue
                if (
                    not isinstance(attempt, dict)
                    or attempt.get("role") != role
                    or not isinstance(attempt.get("attempt_id"), str)
                    or not _ID_PATTERN.fullmatch(attempt["attempt_id"])
                    or attempt["attempt_id"] in attempt_ids
                    or not isinstance(attempt.get("source_filename"), str)
                    or not _VIDEO_PATTERN.fullmatch(attempt["source_filename"])
                    or attempt["source_filename"] in source_files
                    or attempt.get("status") not in {"uploaded", "processing", "succeeded", "failed"}
                ):
                    raise MetadataStoreError("Metadata attempt tidak valid.")
                attempt_ids.add(attempt["attempt_id"])
                source_files.add(attempt["source_filename"])
                result_filename = attempt.get("result_filename")
                if result_filename is not None and (
                    not isinstance(result_filename, str)
                    or not _OUTPUT_PATTERN.fullmatch(result_filename)
                ):
                    raise MetadataStoreError("Referensi file hasil pada metadata tidak valid.")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="comparisons-",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as temp_file:
                temp_name = temp_file.name
                json.dump(data, temp_file, ensure_ascii=False, separators=(",", ":"))
                temp_file.flush()
            Path(temp_name).replace(self.path)
        except OSError as error:
            raise MetadataStoreError(f"Metadata comparison tidak dapat disimpan: {error}") from error
        finally:
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)

    @staticmethod
    def _find(data: dict[str, Any], comparison_id: str) -> dict[str, Any]:
        comparison = next(
            (item for item in data["comparisons"] if item.get("comparison_id") == comparison_id),
            None,
        )
        if comparison is None:
            raise ComparisonNotFoundError("Comparison tidak ditemukan.")
        return comparison

    @staticmethod
    def _find_attempt_by_source(
        data: dict[str, Any], source_filename: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for comparison in data["comparisons"]:
            for role in ("baseline", "retake"):
                attempt = comparison[role]
                if attempt is not None and attempt["source_filename"] == source_filename:
                    return comparison, attempt
        return None

    def _validate_file_reference(self, filename: str, directory: Path, pattern: re.Pattern[str]) -> None:
        if not pattern.fullmatch(filename):
            raise ComparisonNotFoundError("Referensi file tidak valid.")
        candidate = directory / filename
        try:
            candidate.resolve().relative_to(directory.resolve())
        except ValueError as error:
            raise ComparisonNotFoundError("Referensi file tidak valid.") from error

    def _reconcile(self, comparison: dict[str, Any], startup: bool = False) -> bool:
        changed = False
        for role in ("baseline", "retake"):
            attempt = comparison[role]
            if attempt is None:
                continue
            if startup and attempt["status"] == "processing":
                self._set_failed(attempt, "Pemrosesan terhenti saat aplikasi dimulai ulang.")
                comparison["updated_at"] = attempt["updated_at"]
                changed = True
            source = self.upload_directory / attempt["source_filename"]
            if not source.is_file() and attempt["status"] != "failed":
                self._set_failed(attempt, "File video sumber tidak ditemukan.")
                comparison["updated_at"] = attempt["updated_at"]
                changed = True
                continue
            result_filename = attempt["result_filename"]
            if result_filename is not None and not (self.output_directory / result_filename).is_file():
                attempt.update(
                    status="failed",
                    result_filename=None,
                    error="File hasil skeleton tidak ditemukan.",
                    updated_at=_now(),
                )
                comparison["updated_at"] = attempt["updated_at"]
                changed = True
        return changed

    @staticmethod
    def _set_failed(attempt: dict[str, Any], error: str) -> None:
        attempt.update(
            status="failed",
            result_filename=None,
            frame_angles=[],
            fps=None,
            frames=None,
            duration=None,
            error=error,
            updated_at=_now(),
        )


class SessionStore:
    """Persist single-session analysis metadata while keeping legacy comparison data intact."""

    def __init__(self, path: Path, upload_directory: Path, output_directory: Path) -> None:
        self.path = path
        self.upload_directory = upload_directory
        self.output_directory = output_directory
        self._lock = threading.RLock()

    def create_session(self, source_filename: str, note: str = "") -> dict[str, Any]:
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        validate_note(note)
        timestamp = _now()
        session = {
            "session_id": uuid4().hex,
            "created_at": timestamp,
            "updated_at": timestamp,
            "source_filename": source_filename,
            "status": "uploaded",
            "note": note,
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
            "analysis": {
                "summary": None,
            },
            "error": None,
        }
        with self._lock:
            data = self._load()
            if any(item.get("source_filename") == source_filename for item in data["sessions"]):
                raise SessionConflictError("Video ini sudah terdaftar pada sesi analisis lain.")
            data["sessions"].append(session)
            self._save(data)
        return deepcopy(session)

    def get(self, session_id: str) -> dict[str, Any]:
        validate_session_id(session_id)
        with self._lock:
            data = self._load()
            session = self._find(data, session_id)
            changed = self._reconcile(session)
            if changed:
                self._save(data)
            return deepcopy(session)

    def find_by_source(self, source_filename: str) -> str | None:
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        with self._lock:
            data = self._load()
            for session in data["sessions"]:
                trim = session.get("trim")
                if session.get("source_filename") == source_filename or (
                    isinstance(trim, dict)
                    and trim.get("status") == "succeeded"
                    and trim.get("trimmed_filename") == source_filename
                ):
                    return session["session_id"]
        return None

    def begin_trimming(self, session_id: str, start_time: float, end_time: float) -> dict[str, Any]:
        validate_session_id(session_id)
        if (
            not isinstance(start_time, (int, float))
            or not math.isfinite(start_time)
            or not isinstance(end_time, (int, float))
            or not math.isfinite(end_time)
            or start_time < 0
            or end_time <= start_time
        ):
            raise ValueError("Rentang waktu trimming tidak valid.")
        with self._lock:
            data = self._load()
            session = self._find(data, session_id)
            trim = session.get("trim") or {}
            if session["status"] == "processing":
                raise SessionConflictError("Sesi analisis sedang diproses.")
            if trim.get("status") == "processing":
                raise SessionConflictError("Trimming sesi sedang diproses.")
            if trim.get("status") == "succeeded":
                raise SessionConflictError("Sesi ini sudah memiliki hasil trimming.")
            if not (self.upload_directory / session["source_filename"]).is_file():
                raise FileNotFoundError("File video sumber tidak ditemukan.")
            timestamp = _now()
            trim_metadata = {
                key: value
                for key, value in (session.get("trim") or {}).items()
                if key not in {"enabled", "duration_seconds"}
            }
            session["trim"] = {
                **trim_metadata,
                "source_filename": session["source_filename"],
                "start_time": float(start_time),
                "end_time": float(end_time),
                "trimmed_filename": None,
                "source_duration": trim_metadata.get("source_duration"),
                "trimmed_duration": None,
                "created_at": timestamp,
                "status": "processing",
                "error": None,
            }
            session["updated_at"] = timestamp
            self._save(data)
            return deepcopy(session)

    def complete_trimming(
        self,
        session_id: str,
        trimmed_filename: str,
        source_duration: float,
        trimmed_duration: float,
        technical_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_session_id(session_id)
        self._validate_file_reference(trimmed_filename, self.upload_directory, _VIDEO_PATTERN)
        if not (self.upload_directory / trimmed_filename).is_file():
            raise FileNotFoundError("Video hasil trimming tidak tersedia.")
        if (
            not isinstance(source_duration, (int, float))
            or not math.isfinite(source_duration)
            or source_duration <= 0
            or not isinstance(trimmed_duration, (int, float))
            or not math.isfinite(trimmed_duration)
            or trimmed_duration <= 0
        ):
            raise ValueError("Durasi hasil trimming tidak valid.")
        if technical_metadata is not None and technical_metadata.get("timing_validation_status") != "passed":
            raise ValueError("Metadata teknis video belum lolos validasi.")
        with self._lock:
            data = self._load()
            session = self._find(data, session_id)
            trim = session.get("trim") or {}
            if trim.get("status") != "processing":
                raise SessionConflictError("Status trimming sesi berubah saat pemrosesan.")
            if trimmed_filename == session["source_filename"]:
                raise SessionConflictError("Video hasil trimming harus berupa file baru.")
            if any(
                other.get("source_filename") == trimmed_filename
                or (
                    isinstance(other.get("trim"), dict)
                    and other["trim"].get("trimmed_filename") == trimmed_filename
                )
                for other in data["sessions"]
            ):
                raise SessionConflictError("Video hasil trimming sudah dimiliki sesi lain.")
            timestamp = _now()
            session["trim"] = {
                **trim,
                "trimmed_filename": trimmed_filename,
                "source_duration": float(source_duration),
                "trimmed_duration": float(trimmed_duration),
                **(
                    {"technical_metadata": deepcopy(technical_metadata)}
                    if technical_metadata is not None
                    else {}
                ),
                "created_at": trim.get("created_at") or timestamp,
                "status": "succeeded",
                "error": None,
            }
            session.update(
                status="uploaded",
                result_filename=None,
                frame_angles=[],
                fps=None,
                frames=None,
                duration=None,
                error=None,
                updated_at=timestamp,
            )
            self._save(data)
            return deepcopy(session)

    def fail_trimming(
        self,
        session_id: str,
        error: str,
        source_duration: float | None = None,
        technical_metadata: dict[str, Any] | None = None,
    ) -> None:
        validate_session_id(session_id)
        with self._lock:
            data = self._load()
            session = self._find(data, session_id)
            trim = session.get("trim") or {}
            if trim.get("status") != "processing":
                raise SessionConflictError("Status trimming sesi berubah sebelum gagal disimpan.")
            timestamp = _now()
            session["trim"] = {
                **trim,
                "source_duration": (
                    float(source_duration)
                    if isinstance(source_duration, (int, float))
                    and math.isfinite(source_duration)
                    and source_duration > 0
                    else trim.get("source_duration")
                ),
                "created_at": trim.get("created_at") or timestamp,
                "status": "failed",
                "error": error,
                **(
                    {"technical_metadata": deepcopy(technical_metadata)}
                    if technical_metadata is not None
                    else {}
                ),
            }
            session["updated_at"] = timestamp
            self._save(data)

    def begin_processing(self, source_filename: str) -> dict[str, Any]:
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        if not (self.upload_directory / source_filename).is_file():
            raise FileNotFoundError("Video tidak ditemukan.")
        with self._lock:
            data = self._load()
            session = self._find_by_source(data, source_filename)
            if session is None:
                raise SessionNotFoundError("Sesi analisis untuk video ini tidak ditemukan.")
            if session["status"] == "processing":
                raise SessionConflictError("Sesi analisis sedang diproses.")
            if session["status"] == "succeeded":
                raise SessionConflictError("Sesi analisis ini sudah berhasil diproses.")
            timestamp = _now()
            session.update(
                status="processing",
                result_filename=None,
                frame_angles=[],
                fps=None,
                frames=None,
                duration=None,
                error=None,
                updated_at=timestamp,
            )
            self._save(data)
            return deepcopy(session)

    def complete_processing(
        self,
        source_filename: str,
        result_filename: str,
        processing_result: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_file_reference(source_filename, self.upload_directory, _VIDEO_PATTERN)
        self._validate_file_reference(result_filename, self.output_directory, _OUTPUT_PATTERN)
        if not (self.output_directory / result_filename).is_file():
            raise FileNotFoundError("Video hasil tidak tersedia.")
        try:
            frame_angles = processing_result["frame_angles"]
            fps = processing_result["fps"]
            frames = processing_result["frames"]
            duration = processing_result["duration"]
        except KeyError as error:
            raise MetadataStoreError("Hasil pemrosesan video tidak lengkap.") from error
        if (
            not isinstance(frame_angles, list)
            or not isinstance(fps, (int, float))
            or not math.isfinite(fps)
            or fps <= 0
            or not isinstance(frames, int)
            or frames <= 0
            or len(frame_angles) != frames
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise MetadataStoreError("Hasil pemrosesan video tidak valid.")
        for item in frame_angles:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("time_seconds"), (int, float))
                or not math.isfinite(item["time_seconds"])
                or not {"left_elbow", "right_elbow"}.issubset(item)
                or any(
                    angle is not None
                    and (
                        not isinstance(angle, (int, float))
                        or not math.isfinite(angle)
                        or angle < 0
                        or angle > 180
                    )
                    for angle in (item.get("left_elbow"), item.get("right_elbow"))
                )
            ):
                raise MetadataStoreError("Hasil sudut siku tidak valid.")
        with self._lock:
            data = self._load()
            session = self._find_by_source(data, source_filename)
            if session is None:
                raise SessionNotFoundError("Sesi analisis untuk video ini tidak ditemukan.")
            if session["status"] != "processing":
                raise SessionConflictError("Status sesi analisis berubah saat pemrosesan.")
            timestamp = _now()
            session.update(
                status="succeeded",
                result_filename=result_filename,
                frame_angles=frame_angles,
                fps=fps,
                frames=frames,
                duration=duration,
                error=None,
                updated_at=timestamp,
            )
            self._save(data)
            return deepcopy(session)

    def fail_processing(self, source_filename: str, error: str) -> None:
        with self._lock:
            data = self._load()
            session = self._find_by_source(data, source_filename)
            if session is None:
                return
            timestamp = _now()
            session.update(
                status="failed",
                result_filename=None,
                frame_angles=[],
                fps=None,
                frames=None,
                duration=None,
                error=error,
                updated_at=timestamp,
            )
            self._save(data)

    def update_note(self, session_id: str, note: str) -> dict[str, Any]:
        validate_session_id(session_id)
        validate_note(note)
        with self._lock:
            data = self._load()
            session = self._find(data, session_id)
            session["note"] = note
            session["updated_at"] = _now()
            self._save(data)
            return deepcopy(session)

    def recover_interrupted(self) -> None:
        with self._lock:
            data = self._load()
            changed = False
            for session in data["sessions"]:
                changed = self._reconcile(session) or changed
                if session["status"] == "processing":
                    session.update(
                        status="failed",
                        result_filename=None,
                        frame_angles=[],
                        fps=None,
                        frames=None,
                        duration=None,
                        error="Pemrosesan terhenti saat aplikasi dimulai ulang.",
                        updated_at=_now(),
                    )
                    changed = True
            if changed:
                self._save(data)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "sessions": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MetadataStoreError(f"Metadata sesi tidak dapat dibaca: {error}") from error
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("sessions"), list):
            raise MetadataStoreError("Format metadata sesi tidak valid.")
        seen_ids: set[str] = set()
        seen_sources: set[str] = set()
        for session in data["sessions"]:
            if not isinstance(session, dict):
                raise MetadataStoreError("Format metadata sesi tidak valid.")
            session_id = session.get("session_id")
            if not isinstance(session_id, str) or not _ID_PATTERN.fullmatch(session_id) or session_id in seen_ids:
                raise MetadataStoreError("ID sesi pada metadata tidak valid.")
            seen_ids.add(session_id)
            source_filename = session.get("source_filename")
            if not isinstance(source_filename, str) or not _VIDEO_PATTERN.fullmatch(source_filename) or source_filename in seen_sources:
                raise MetadataStoreError("Nama file sumber sesi tidak valid.")
            seen_sources.add(source_filename)
            trim = session.get("trim")
            if not isinstance(trim, dict):
                raise MetadataStoreError("Metadata trimming sesi tidak valid.")
            trimmed_filename = trim.get("trimmed_filename")
            if trimmed_filename is not None:
                if (
                    not isinstance(trimmed_filename, str)
                    or not _VIDEO_PATTERN.fullmatch(trimmed_filename)
                    or trimmed_filename in seen_sources
                    or trimmed_filename == source_filename
                ):
                    raise MetadataStoreError("Referensi file trimming sesi tidak valid.")
                seen_sources.add(trimmed_filename)
            trim_status = trim.get("status")
            if trim_status is not None and trim_status not in {
                "not_started",
                "processing",
                "succeeded",
                "failed",
            }:
                raise MetadataStoreError("Status trimming sesi tidak valid.")
            if trim_status == "succeeded" and trimmed_filename is None:
                raise MetadataStoreError("File trimming sesi yang berhasil tidak tersedia.")
            status = session.get("status")
            if status not in {"uploaded", "processing", "succeeded", "failed"}:
                raise MetadataStoreError("Status sesi tidak valid.")
            result_filename = session.get("result_filename")
            if result_filename is not None and (
                not isinstance(result_filename, str) or not _OUTPUT_PATTERN.fullmatch(result_filename)
            ):
                raise MetadataStoreError("Referensi file hasil pada metadata sesi tidak valid.")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="sessions-",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as temp_file:
                temp_name = temp_file.name
                json.dump(data, temp_file, ensure_ascii=False, separators=(",", ":"))
                temp_file.flush()
            Path(temp_name).replace(self.path)
        except OSError as error:
            raise MetadataStoreError(f"Metadata sesi tidak dapat disimpan: {error}") from error
        finally:
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)

    @staticmethod
    def _find(data: dict[str, Any], session_id: str) -> dict[str, Any]:
        session = next((item for item in data["sessions"] if item.get("session_id") == session_id), None)
        if session is None:
            raise SessionNotFoundError("Sesi analisis tidak ditemukan.")
        return session

    @staticmethod
    def _find_by_source(data: dict[str, Any], source_filename: str) -> dict[str, Any] | None:
        for session in data["sessions"]:
            trim = session.get("trim")
            if session.get("source_filename") == source_filename or (
                isinstance(trim, dict)
                and trim.get("status") == "succeeded"
                and trim.get("trimmed_filename") == source_filename
            ):
                return session
        return None

    def _validate_file_reference(self, filename: str, directory: Path, pattern: re.Pattern[str]) -> None:
        if not pattern.fullmatch(filename):
            raise SessionNotFoundError("Referensi file tidak valid.")
        candidate = directory / filename
        try:
            candidate.resolve().relative_to(directory.resolve())
        except ValueError as error:
            raise SessionNotFoundError("Referensi file tidak valid.") from error

    def _reconcile(self, session: dict[str, Any]) -> bool:
        changed = False
        trim = session.get("trim") or {}
        if trim.get("status") == "processing":
            session["trim"] = {
                **trim,
                "trimmed_filename": None,
                "trimmed_duration": None,
                "status": "failed",
                "error": "Proses trimming terhenti saat aplikasi dimulai ulang.",
            }
            changed = True
        elif (
            trim.get("status") == "succeeded"
            and trim.get("trimmed_filename")
            and not (self.upload_directory / trim["trimmed_filename"]).is_file()
        ):
            session["trim"] = {
                **trim,
                "trimmed_filename": None,
                "trimmed_duration": None,
                "status": "failed",
                "error": "File hasil trimming tidak ditemukan.",
            }
            changed = True
        if session["status"] == "processing":
            session.update(
                status="failed",
                result_filename=None,
                frame_angles=[],
                fps=None,
                frames=None,
                duration=None,
                error="Pemrosesan terhenti saat aplikasi dimulai ulang.",
                updated_at=_now(),
            )
            changed = True
        source = self.upload_directory / session["source_filename"]
        if not source.is_file() and session["status"] != "failed":
            session.update(
                status="failed",
                result_filename=None,
                frame_angles=[],
                fps=None,
                frames=None,
                duration=None,
                error="File video sumber tidak ditemukan.",
                updated_at=_now(),
            )
            changed = True
        result_filename = session["result_filename"]
        if result_filename is not None and not (self.output_directory / result_filename).is_file():
            session.update(
                status="failed",
                result_filename=None,
                frame_angles=[],
                fps=None,
                frames=None,
                duration=None,
                error="File hasil skeleton tidak ditemukan.",
                updated_at=_now(),
            )
            changed = True
        return changed
