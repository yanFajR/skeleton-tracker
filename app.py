"""FastAPI entry point for the Motion Tracker MVP."""

from contextlib import asynccontextmanager
import hmac
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import auth
from comparison_store import (
    ComparisonConflictError,
    ComparisonNotFoundError,
    ComparisonStore,
    MetadataStoreError,
    SessionConflictError,
    SessionNotFoundError,
    SessionStore,
)
from angle_analysis import calculate_angle_difference
from video_processor import VideoProcessingError, process_video
from video_trimmer import (
    VideoTrimmingError,
    build_trim_video_metadata,
    probe_video_metadata,
    resolve_target_frame_rate,
    trim_video,
)

BASE_DIR: Final = Path(__file__).resolve().parent
DEFAULT_MAX_UPLOAD_BYTES: Final = 200 * 1024 * 1024
ALLOWED_EXTENSIONS: Final = {".mp4", ".mov", ".webm"}
MEDIA_TYPES: Final = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
CHUNK_SIZE: Final = 1024 * 1024
MIN_TRIM_DURATION_SECONDS: Final = float(os.environ.get("MOTION_TRACKER_MIN_TRIM_DURATION_SECONDS", "0.5"))
SESSION_COOKIE_NAME: Final = "motion_tracker_session"
PUBLIC_PATHS: Final = frozenset({"/login", "/health"})
PUBLIC_PATH_PREFIXES: Final = ("/static/",)
NON_HTML_PATH_PREFIXES: Final = ("/api/", "/videos/", "/outputs/")
LOGIN_MAX_FAILURES: Final = 5
LOGIN_FAILURE_WINDOW_SECONDS: Final = 60.0

@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.comparison_store = ComparisonStore(
        application.state.metadata_file,
        application.state.upload_directory,
        application.state.output_directory,
    )
    application.state.session_store = SessionStore(
        application.state.session_file,
        application.state.upload_directory,
        application.state.output_directory,
    )
    application.state.comparison_store.recover_interrupted()
    application.state.session_store.recover_interrupted()
    application.state.auth_username = os.environ.get("MOTION_TRACKER_USERNAME", auth.DEFAULT_USERNAME)
    configured_hash = os.environ.get("MOTION_TRACKER_PASSWORD_HASH", "").strip()
    application.state.auth_password_hash = configured_hash or auth.hash_password(
        os.environ.get("MOTION_TRACKER_PASSWORD", auth.DEFAULT_PASSWORD)
    )
    application.state.auth_secret = auth.load_or_create_secret(
        application.state.auth_secret_file,
        environment_value=os.environ.get("MOTION_TRACKER_SECRET_KEY", "").strip() or None,
    )
    application.state.cookie_secure = os.environ.get("MOTION_TRACKER_COOKIE_SECURE", "0") == "1"
    application.state.login_failures = {}
    yield


app = FastAPI(title="Motion Tracker", version="0.3.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
app.state.upload_directory = BASE_DIR / "uploads"
app.state.max_upload_bytes = int(os.environ.get("MOTION_TRACKER_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES))
app.state.output_directory = BASE_DIR / "outputs"
app.state.metadata_file = BASE_DIR / "data" / "comparisons.json"
app.state.session_file = BASE_DIR / "data" / "sessions.json"
app.state.auth_secret_file = BASE_DIR / "data" / ".auth_secret"
app.state.model_path = BASE_DIR / "models" / "pose_landmarker_lite.task"
app.state.max_video_duration_seconds = int(os.environ.get("MOTION_TRACKER_MAX_VIDEO_DURATION_SECONDS", "120"))


def _safe_next_url(value: str | None) -> str:
    """Allow only same-site absolute paths as post-login redirect targets."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if any(character in value for character in ("\r", "\n", "\\")):
        return "/"
    return value


def _authenticated_username(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    secret = getattr(app.state, "auth_secret", None)
    expected_username = getattr(app.state, "auth_username", "")
    if not token or not secret or not expected_username:
        return None
    username = auth.verify_session_token(token, secret)
    if username is None or not hmac.compare_digest(
        username.encode("utf-8"), expected_username.encode("utf-8")
    ):
        return None
    return username


def _login_locked(client_host: str) -> bool:
    now = time.monotonic()
    attempts = [
        moment
        for moment in app.state.login_failures.get(client_host, [])
        if now - moment < LOGIN_FAILURE_WINDOW_SECONDS
    ]
    app.state.login_failures[client_host] = attempts
    return len(attempts) >= LOGIN_MAX_FAILURES


def _record_login_failure(client_host: str) -> None:
    attempts = app.state.login_failures.setdefault(client_host, [])
    attempts.append(time.monotonic())


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PATH_PREFIXES):
        return await call_next(request)
    if _authenticated_username(request) is not None:
        return await call_next(request)
    if path.startswith(NON_HTML_PATH_PREFIXES):
        return JSONResponse({"detail": "Autentikasi diperlukan."}, status_code=401)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


class NoteUpdate(BaseModel):
    note: str


class TrimRequest(BaseModel):
    start_time: float = Field(allow_inf_nan=False)
    end_time: float = Field(allow_inf_nan=False)


def _is_supported_video_header(header: bytes, extension: str) -> bool:
    """Check lightweight container signatures; this does not validate codecs."""
    if extension in {".mp4", ".mov"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    return extension == ".webm" and header.startswith(b"\x1a\x45\xdf\xa3")


def _safe_video_path(filename: str) -> Path:
    """Resolve a server-generated filename without permitting path traversal."""
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Video tidak ditemukan.")
    extension = Path(filename).suffix.lower()
    stem = Path(filename).stem
    if extension not in ALLOWED_EXTENSIONS or len(stem) != 32 or any(c not in "0123456789abcdef" for c in stem):
        raise HTTPException(status_code=404, detail="Video tidak ditemukan.")
    candidate = app.state.upload_directory / filename
    try:
        candidate.resolve().relative_to(app.state.upload_directory.resolve())
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Video tidak ditemukan.") from error
    return candidate


def _safe_output_path(filename: str) -> Path:
    if (
        Path(filename).name != filename
        or not filename.endswith(".mp4")
        or len(Path(filename).stem) != 32
        or any(character not in "0123456789abcdef" for character in Path(filename).stem)
    ):
        raise HTTPException(status_code=404, detail="Hasil tidak ditemukan.")
    candidate = app.state.output_directory / filename
    try:
        candidate.resolve().relative_to(app.state.output_directory.resolve())
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Hasil tidak ditemukan.") from error
    return candidate


def _comparison_store() -> ComparisonStore:
    return app.state.comparison_store


def _session_store() -> SessionStore:
    return app.state.session_store


def _comparison_http_error(error: Exception) -> HTTPException:
    if isinstance(error, ComparisonNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ComparisonConflictError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=500, detail="Metadata comparison gagal diproses.")


def _resolve_metadata_owner(filename: str) -> tuple[str, str | None, str | None]:
    comparison_match = _comparison_store().find_by_source(filename)
    session_match = _session_store().find_by_source(filename)
    if comparison_match is not None and session_match is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Konflik metadata: file video terdaftar di comparison dan session. "
                f"comparison_id={comparison_match[0]}, session_id={session_match}."
            ),
        )
    if comparison_match is not None:
        return "comparison", comparison_match[0], None
    if session_match is not None:
        return "session", None, session_match
    raise HTTPException(status_code=404, detail="Video tidak ditemukan.")


def _session_http_error(error: Exception) -> HTTPException:
    if isinstance(error, SessionNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, SessionConflictError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=500, detail="Metadata sesi analisis gagal diproses.")


def _login_page(request: Request, next_url: str, username: str = "", error: str = "", status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next": next_url, "username": username, "error": error},
        status_code=status_code,
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = Query("/", max_length=2048)) -> Response:
    next_url = _safe_next_url(next)
    if _authenticated_username(request) is not None:
        return RedirectResponse(next_url, status_code=303)
    return _login_page(request, next_url)


@app.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: str = Form("", max_length=64),
    password: str = Form("", max_length=256),
    next: str = Form("/"),
) -> Response:
    next_url = _safe_next_url(next)
    client_host = request.client.host if request.client else "unknown"
    if _login_locked(client_host):
        return _login_page(
            request,
            next_url,
            error="Terlalu banyak percobaan masuk. Tunggu sebentar lalu coba lagi.",
            status_code=429,
        )
    username_matches = hmac.compare_digest(
        username.encode("utf-8"), app.state.auth_username.encode("utf-8")
    )
    password_matches = auth.verify_password(password, app.state.auth_password_hash)
    if not (username_matches and password_matches):
        _record_login_failure(client_host)
        return _login_page(
            request,
            next_url,
            error="Nama pengguna atau kata sandi salah.",
            status_code=401,
        )
    app.state.login_failures.pop(client_host, None)
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        auth.create_session_token(app.state.auth_username, app.state.auth_secret),
        max_age=auth.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=app.state.cookie_secure,
        path="/",
    )
    return response


@app.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "min_trim_duration_seconds": MIN_TRIM_DURATION_SECONDS,
            "max_trim_duration_seconds": app.state.max_video_duration_seconds,
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/videos", status_code=status.HTTP_201_CREATED)
async def upload_video(
    video: UploadFile = File(...),
    comparison_id: str | None = Form(None),
    note: str = Form(""),
) -> dict[str, str | None]:
    """Legacy comparison upload endpoint. It creates or updates comparison metadata only."""
    extension = Path(video.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        await video.close()
        raise HTTPException(status_code=415, detail="Format tidak didukung. Gunakan MP4, MOV, atau WebM.")

    upload_directory: Path = app.state.upload_directory
    upload_directory.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    bytes_written = 0
    header = b""
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix="upload-", suffix=".tmp", dir=upload_directory, delete=False) as destination:
            temp_name = destination.name
            while chunk := await video.read(CHUNK_SIZE):
                bytes_written += len(chunk)
                if bytes_written > app.state.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Ukuran video melebihi batas 200 MB.")
                if len(header) < 32:
                    header += chunk[:32 - len(header)]
                destination.write(chunk)
        if bytes_written == 0:
            raise HTTPException(status_code=422, detail="File video kosong.")
        if not _is_supported_video_header(header, extension):
            raise HTTPException(status_code=422, detail="Isi file bukan kontainer video MP4, MOV, atau WebM yang valid.")
        server_filename = f"{uuid4().hex}{extension}"
        Path(temp_name).replace(upload_directory / server_filename)
        temp_name = None
    finally:
        await video.close()
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)

    try:
        if comparison_id is None:
            comparison = _comparison_store().create_baseline(server_filename, note)
            role = "baseline"
        else:
            comparison = _comparison_store().add_retake(comparison_id, server_filename, note)
            role = "retake"
    except (ComparisonNotFoundError, ComparisonConflictError, ValueError) as error:
        (upload_directory / server_filename).unlink(missing_ok=True)
        raise _comparison_http_error(error) from error
    except MetadataStoreError as error:
        (upload_directory / server_filename).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(error)) from error

    attempt = comparison[role]
    return {
        "message": "Video berhasil diunggah dan disimpan sebagai " + role + ".",
        "video_url": f"/videos/{server_filename}",
        "filename": server_filename,
        "comparison_id": comparison["comparison_id"],
        "attempt_id": attempt["attempt_id"],
        "role": role,
    }


@app.post("/api/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(video: UploadFile = File(...), note: str = Form("")) -> dict[str, str]:
    """Create a single analysis session from a source video without legacy comparison metadata."""
    extension = Path(video.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        await video.close()
        raise HTTPException(status_code=415, detail="Format tidak didukung. Gunakan MP4, MOV, atau WebM.")

    upload_directory: Path = app.state.upload_directory
    upload_directory.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    bytes_written = 0
    header = b""
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix="upload-", suffix=".tmp", dir=upload_directory, delete=False) as destination:
            temp_name = destination.name
            while chunk := await video.read(CHUNK_SIZE):
                bytes_written += len(chunk)
                if bytes_written > app.state.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Ukuran video melebihi batas 200 MB.")
                if len(header) < 32:
                    header += chunk[:32 - len(header)]
                destination.write(chunk)
        if bytes_written == 0:
            raise HTTPException(status_code=422, detail="File video kosong.")
        if not _is_supported_video_header(header, extension):
            raise HTTPException(status_code=422, detail="Isi file bukan kontainer video MP4, MOV, atau WebM yang valid.")
        server_filename = f"{uuid4().hex}{extension}"
        Path(temp_name).replace(upload_directory / server_filename)
        temp_name = None
    finally:
        await video.close()
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)

    try:
        comparison_conflict = _comparison_store().find_by_source(server_filename)
        session_conflict = _session_store().find_by_source(server_filename)
        if comparison_conflict is not None or session_conflict is not None:
            (upload_directory / server_filename).unlink(missing_ok=True)
            raise HTTPException(
                status_code=409,
                detail=(
                    "File video sudah terdaftar pada metadata lain; "
                    f"comparison={comparison_conflict[0] if comparison_conflict else 'none'}, "
                    f"session={session_conflict if session_conflict else 'none'}."
                ),
            )
        session = _session_store().create_session(server_filename, note)
    except (SessionNotFoundError, SessionConflictError, ValueError) as error:
        (upload_directory / server_filename).unlink(missing_ok=True)
        raise _session_http_error(error) from error
    except MetadataStoreError as error:
        (upload_directory / server_filename).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(error)) from error

    return {
        "message": "Sesi analisis berhasil dibuat.",
        "video_url": f"/videos/{server_filename}",
        "filename": server_filename,
        "session_id": session["session_id"],
        "status": session["status"],
    }


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    try:
        return _session_store().get(session_id)
    except (SessionNotFoundError, ValueError) as error:
        raise _session_http_error(error) from error
    except MetadataStoreError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.put("/api/sessions/{session_id}/note")
def update_session_note(session_id: str, update: NoteUpdate) -> dict[str, Any]:
    try:
        return _session_store().update_note(session_id, update.note)
    except (SessionNotFoundError, ValueError) as error:
        raise _session_http_error(error) from error
    except MetadataStoreError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.post("/api/sessions/{session_id}/trim")
def trim_session_video(session_id: str, request: TrimRequest) -> dict[str, Any]:
    start_time = round(request.start_time, 3)
    end_time = round(request.end_time, 3)
    source_duration: float | None = None
    try:
        session = _session_store().get(session_id)
        owner, _comparison_id, owner_session_id = _resolve_metadata_owner(session["source_filename"])
        if owner != "session" or owner_session_id != session_id:
            raise HTTPException(status_code=409, detail="Video sumber tidak dimiliki sesi ini.")
        source = _safe_video_path(session["source_filename"])
        if start_time < 0 or end_time <= start_time:
            raise HTTPException(status_code=422, detail="Waktu mulai dan selesai tidak valid.")
        requested_duration = end_time - start_time
        if requested_duration < MIN_TRIM_DURATION_SECONDS:
            raise HTTPException(
                status_code=422,
                detail=f"Durasi potongan minimal {MIN_TRIM_DURATION_SECONDS:g} detik.",
            )
        if requested_duration > app.state.max_video_duration_seconds:
            raise HTTPException(
                status_code=422,
                detail=f"Durasi potongan maksimal {app.state.max_video_duration_seconds} detik.",
            )
        _session_store().begin_trimming(session_id, start_time, end_time)
    except HTTPException:
        raise
    except (SessionNotFoundError, SessionConflictError, ValueError) as error:
        raise _session_http_error(error) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MetadataStoreError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    trimmed_filename = f"{uuid4().hex}.mp4"
    for _attempt in range(5):
        if (
            not (app.state.upload_directory / trimmed_filename).exists()
            and _comparison_store().find_by_source(trimmed_filename) is None
            and _session_store().find_by_source(trimmed_filename) is None
        ):
            break
        trimmed_filename = f"{uuid4().hex}.mp4"
    else:
        _session_store().fail_trimming(session_id, "Nama file hasil trimming tidak dapat dibuat.")
        raise HTTPException(status_code=500, detail="Nama file hasil trimming tidak dapat dibuat.")
    destination = _safe_video_path(trimmed_filename)
    technical_metadata: dict[str, object] | None = None
    try:
        source_probe = probe_video_metadata(source)
        if source_probe.validation_errors:
            raise VideoTrimmingError(
                "Metadata teknis video sumber tidak valid: "
                + "; ".join(source_probe.validation_errors)
            )
        source_duration = source_probe.metadata.get("source_duration")
        if not isinstance(source_duration, (int, float)):
            raise VideoTrimmingError("Durasi video sumber tidak tersedia.")
        if end_time > source_duration:
            raise VideoTrimmingError("Waktu selesai melebihi durasi video.")
        target_numerator = 30
        target_denominator = 1
        trimmed_duration = trim_video(
            source,
            destination,
            start_time,
            end_time,
            target_numerator,
            target_denominator,
        )
        if (
            trimmed_duration < MIN_TRIM_DURATION_SECONDS
            or trimmed_duration > app.state.max_video_duration_seconds
        ):
            raise VideoTrimmingError("Durasi hasil trimming tidak memenuhi batas aplikasi.")
        trimmed_probe = probe_video_metadata(destination, duration_field="trimmed_duration")
        combined_probe = build_trim_video_metadata(
            source_probe,
            trimmed_probe,
            start_time,
            end_time,
            target_numerator,
            target_denominator,
        )
        technical_metadata = combined_probe.metadata
        probed_trimmed_duration = technical_metadata.get("trimmed_duration")
        if not isinstance(probed_trimmed_duration, (int, float)):
            technical_metadata["timing_validation_status"] = "failed"
            raise VideoTrimmingError("Durasi hasil trimming tidak dapat divalidasi.")
        if (
            probed_trimmed_duration < MIN_TRIM_DURATION_SECONDS
            or probed_trimmed_duration > app.state.max_video_duration_seconds
        ):
            technical_metadata["timing_validation_status"] = "failed"
            raise VideoTrimmingError("Durasi hasil trimming tidak memenuhi batas aplikasi.")
        if combined_probe.validation_errors:
            technical_metadata["timing_validation_status"] = "failed"
            raise VideoTrimmingError(
                "Validasi teknis video hasil trimming gagal: "
                + "; ".join(combined_probe.validation_errors)
            )
        completed = _session_store().complete_trimming(
            session_id,
            trimmed_filename,
            source_duration,
            probed_trimmed_duration,
            technical_metadata,
        )
    except VideoTrimmingError as error:
        destination.unlink(missing_ok=True)
        _session_store().fail_trimming(
            session_id, str(error), source_duration, technical_metadata
        )
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (SessionNotFoundError, SessionConflictError, ValueError, FileNotFoundError, MetadataStoreError) as error:
        destination.unlink(missing_ok=True)
        _session_store().fail_trimming(
            session_id, str(error), source_duration, technical_metadata
        )
        if isinstance(error, (SessionNotFoundError, SessionConflictError, ValueError)):
            raise _session_http_error(error) from error
        raise HTTPException(status_code=500, detail=str(error)) from error
    except Exception as error:
        destination.unlink(missing_ok=True)
        _session_store().fail_trimming(
            session_id, "Proses trimming gagal.", source_duration, technical_metadata
        )
        raise HTTPException(status_code=500, detail="Proses trimming gagal.") from error

    return {
        "message": "Video berhasil dipotong.",
        "session_id": session_id,
        "trim": completed["trim"],
        "video_url": f"/videos/{trimmed_filename}",
    }


@app.get("/videos/{filename}")
async def get_video(filename: str) -> FileResponse:
    """Serve only validated, server-generated uploaded videos."""
    video_path = _safe_video_path(filename)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video tidak ditemukan.")
    return FileResponse(video_path, media_type=MEDIA_TYPES[video_path.suffix.lower()])


@app.post("/api/videos/{filename}/pose")
def process_pose(filename: str) -> dict[str, object]:
    """Synchronously render a safe uploaded video with its pose skeleton."""
    requested_source = _safe_video_path(filename)

    try:
        owner, comparison_id, session_id = _resolve_metadata_owner(filename)
    except HTTPException:
        raise

    if owner == "comparison":
        storage = _comparison_store()
        source = requested_source
        processing_filename = filename
        if not source.is_file():
            raise HTTPException(status_code=404, detail="Video tidak ditemukan.")
        try:
            entry = storage.begin_processing(filename)
        except (ComparisonNotFoundError, ComparisonConflictError, ValueError) as error:
            raise _comparison_http_error(error) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Video tidak ditemukan.") from error
        except MetadataStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
    else:
        storage = _session_store()
        try:
            session = storage.get(session_id)
            trim = session.get("trim") or {}
            if trim.get("status") != "succeeded" or not trim.get("trimmed_filename"):
                raise HTTPException(
                    status_code=409,
                    detail="Video sesi harus berhasil dipotong sebelum analisis pose.",
                )
            processing_filename = trim["trimmed_filename"]
            if filename not in {session["source_filename"], processing_filename}:
                raise HTTPException(status_code=409, detail="Video tidak dimiliki sesi ini.")
            resolved_owner, _comparison_match, resolved_session = _resolve_metadata_owner(processing_filename)
            if resolved_owner != "session" or resolved_session != session_id:
                raise HTTPException(status_code=409, detail="File hasil trimming tidak dimiliki sesi ini.")
            source = _safe_video_path(processing_filename)
            if not source.is_file():
                raise FileNotFoundError("Video tidak ditemukan.")
            entry = storage.begin_processing(processing_filename)
        except (SessionNotFoundError, SessionConflictError, ValueError) as error:
            raise _session_http_error(error) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Video tidak ditemukan.") from error
        except MetadataStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    output_name = f"{uuid4().hex}.mp4"
    destination = app.state.output_directory / output_name
    try:
        processing_result = process_video(
            source, destination, app.state.model_path, app.state.max_video_duration_seconds
        )
    except FileNotFoundError as error:
        storage.fail_processing(processing_filename if owner == "session" else filename, str(error))
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(error)) from error
    except VideoProcessingError as error:
        storage.fail_processing(processing_filename if owner == "session" else filename, str(error))
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        storage.fail_processing(processing_filename if owner == "session" else filename, "Pemrosesan pose gagal.")
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Pemrosesan pose gagal.") from error
    try:
        completed = storage.complete_processing(
            processing_filename if owner == "session" else filename,
            output_name,
            processing_result,
        )
    except (ComparisonConflictError, ComparisonNotFoundError, ValueError, SessionConflictError, SessionNotFoundError) as error:
        destination.unlink(missing_ok=True)
        storage.fail_processing(processing_filename if owner == "session" else filename, "Pemrosesan pose gagal disimpan.")
        if owner == "comparison":
            raise _comparison_http_error(error) from error
        raise _session_http_error(error) from error
    except (FileNotFoundError, MetadataStoreError) as error:
        destination.unlink(missing_ok=True)
        storage.fail_processing(processing_filename if owner == "session" else filename, "File hasil skeleton tidak tersedia.")
        raise HTTPException(status_code=500, detail=str(error)) from error

    payload: dict[str, object] = {
        "message": "Skeleton dan sudut siku berhasil dianalisis.",
        "result_url": f"/outputs/{output_name}",
        **processing_result,
    }
    if owner == "comparison":
        payload["comparison_id"] = comparison_id
        payload["attempt_id"] = entry["attempt_id"]
        payload["role"] = entry["role"]
    else:
        payload["session_id"] = session_id
        payload["status"] = completed["status"]
        payload["source_filename"] = completed["source_filename"]
        payload["processed_filename"] = processing_filename
    return payload


@app.get("/api/comparisons/{comparison_id}")
def get_comparison(comparison_id: str) -> dict[str, Any]:
    try:
        return _comparison_store().get(comparison_id)
    except (ComparisonNotFoundError, ValueError) as error:
        raise _comparison_http_error(error) from error
    except MetadataStoreError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.put("/api/comparisons/{comparison_id}/attempts/{attempt_id}/note")
def update_attempt_note(
    comparison_id: str, attempt_id: str, update: NoteUpdate
) -> dict[str, Any]:
    try:
        return _comparison_store().update_note(comparison_id, attempt_id, update.note)
    except (ComparisonNotFoundError, ComparisonConflictError, ValueError) as error:
        raise _comparison_http_error(error) from error
    except MetadataStoreError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/comparisons/{comparison_id}/angle-difference")
def get_angle_difference(
    comparison_id: str,
    baseline_frame: int = Query(ge=0),
    retake_frame: int = Query(ge=0),
) -> dict[str, Any]:
    try:
        comparison = _comparison_store().get(comparison_id)
    except (ComparisonNotFoundError, ValueError) as error:
        raise _comparison_http_error(error) from error
    except MetadataStoreError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    baseline = comparison["baseline"]
    retake = comparison["retake"]
    if retake is None:
        raise HTTPException(status_code=409, detail="Retake belum tersedia.")
    if baseline["status"] != "succeeded" or retake["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="Kedua hasil analisis belum tersedia.")

    selected_angles: dict[str, dict[str, float | None]] = {}
    for side in ("left_elbow", "right_elbow"):
        baseline_frames = baseline["frame_angles"]
        retake_frames = retake["frame_angles"]
        if baseline_frame >= len(baseline_frames) or retake_frame >= len(retake_frames):
            raise HTTPException(status_code=422, detail="Frame yang dipilih tidak tersedia.")
        baseline_angle = baseline_frames[baseline_frame][side]
        retake_angle = retake_frames[retake_frame][side]
        selected_angles[side] = {
            "baseline": baseline_angle,
            "retake": retake_angle,
            "difference": calculate_angle_difference(baseline_angle, retake_angle),
        }

    return {
        "baseline_frame": baseline_frame,
        "retake_frame": retake_frame,
        "angles": selected_angles,
    }


@app.get("/outputs/{filename}")
async def get_output(filename: str) -> FileResponse:
    output = _safe_output_path(filename)
    if not output.is_file():
        raise HTTPException(status_code=404, detail="Hasil tidak ditemukan.")
    return FileResponse(output, media_type="video/mp4")
