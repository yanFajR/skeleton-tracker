from pathlib import Path
import os

import pytest
from fastapi.testclient import TestClient

import auth
from app import app
from video_metadata import build_video_metadata
from video_trimmer import VideoProbeResult, probe_video_metadata


@pytest.fixture
def client(tmp_path: Path):
    original_directory = app.state.upload_directory
    original_limit = app.state.max_upload_bytes
    original_output_directory = app.state.output_directory
    original_metadata_file = app.state.metadata_file
    original_session_file = app.state.session_file
    original_auth_secret_file = app.state.auth_secret_file
    app.state.upload_directory = tmp_path / "uploads"
    app.state.max_upload_bytes = 200 * 1024 * 1024
    app.state.output_directory = tmp_path / "outputs"
    app.state.metadata_file = tmp_path / "data" / "comparisons.json"
    app.state.session_file = tmp_path / "data" / "sessions.json"
    app.state.auth_secret_file = tmp_path / "data" / ".auth_secret"
    with TestClient(app) as test_client:
        login_response = test_client.post(
            "/login",
            data={
                "username": app.state.auth_username,
                "password": os.environ.get("MOTION_TRACKER_PASSWORD", auth.DEFAULT_PASSWORD),
            },
            follow_redirects=False,
        )
        assert login_response.status_code == 303
        yield test_client
    app.state.upload_directory = original_directory
    app.state.max_upload_bytes = original_limit
    app.state.output_directory = original_output_directory
    app.state.metadata_file = original_metadata_file
    app.state.session_file = original_session_file
    app.state.auth_secret_file = original_auth_secret_file


def webm_bytes() -> bytes:
    return b"\x1a\x45\xdf\xa3synthetic-webm-container"


def test_index_returns_indonesian_landing_page(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Motion Tracker" in response.text
    assert "Pilih rekaman smash" in response.text
    assert 'id="session-preview-video"' in response.text
    assert 'id="trim-start-range"' in response.text
    assert 'id="trim-end-range"' in response.text
    assert "Gunakan Video Ini" in response.text
    assert "<progress" in response.text
    assert "Tampilkan skeleton" not in response.text
    assert 'id="session-measurements"' in response.text
    assert "Alur perbandingan lama" not in response.text
    assert "Rekaman baseline" not in response.text
    assert "Unggah dan analisis baseline" not in response.text
    assert "js/app.js" not in response.text
    assert 'id="retake-select-file"' not in response.text
    assert "Pilih video pengulangan" not in response.text
    assert "fase gerakan belum diselaraskan" not in response.text.lower()
    assert "pemutar video sumber" not in response.text.lower()
    assert 'id="session-review-controls"' in response.text
    assert 'id="session-result-loading"' in response.text
    assert 'id="session-frame-prev"' in response.text
    assert 'id="session-frame-next"' in response.text
    assert 'id="session-playback-rate"' in response.text
    assert "0.25" in response.text


def test_session_upload_video_can_be_previewed(client: TestClient) -> None:
    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()

    preview = client.get(uploaded["video_url"])

    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("video/webm")
    assert preview.content == webm_bytes()


def _fake_trim_output(destination: Path, _source: Path, start_time: float, end_time: float) -> float:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"synthetic-trimmed-video")
    return end_time - start_time


def _fake_video_probe(
    duration_field: str = "source_duration", duration: float = 10.0
) -> VideoProbeResult:
    values = {
        duration_field: duration,
        "nominal_frame_rate": 30.0,
        "nominal_frame_rate_numerator": 30,
        "nominal_frame_rate_denominator": 1,
        "average_frame_rate": 30.0,
        "inferred_cfr_frame_rate": 30.0,
        "actual_output_frame_rate": 30.0 if duration_field == "trimmed_duration" else None,
        "frame_count": max(1, round(duration * 30)),
        "valid_pts_count": max(1, round(duration * 30)),
        "timestamp_duration": duration,
        "width": 64,
        "height": 64,
        "rotation": 0.0,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "is_constant_frame_rate": True,
    }
    sources = {
        field: {"kind": "measured", "method": "test fixture"}
        for field in values
        if field not in {
            "average_frame_rate",
            "inferred_cfr_frame_rate",
            "actual_output_frame_rate",
            "is_constant_frame_rate",
            "timestamp_duration",
        }
    }
    sources.update(
        {
            "average_frame_rate": {"kind": "calculated", "method": "test fixture"},
            "inferred_cfr_frame_rate": {"kind": "calculated", "method": "test fixture"},
            "actual_output_frame_rate": {"kind": "calculated", "method": "test fixture"},
            "timestamp_duration": {"kind": "calculated", "method": "test fixture"},
            "is_constant_frame_rate": {"kind": "calculated", "method": "test fixture"},
        }
    )
    return VideoProbeResult(build_video_metadata(values, sources), ())


def _mock_trim_tools(
    monkeypatch: pytest.MonkeyPatch, app_module, source_duration: float = 10.0
) -> None:
    selected_duration = {"value": None}

    def fake_probe(_path: Path, duration_field: str = "source_duration") -> VideoProbeResult:
        duration = (
            source_duration
            if duration_field == "source_duration"
            else selected_duration["value"] or source_duration
        )
        return _fake_video_probe(duration_field, duration)

    def fake_trim(
        source: Path,
        destination: Path,
        start: float,
        end: float,
        target_numerator: int,
        target_denominator: int,
    ) -> float:
        assert target_numerator == 30
        assert target_denominator == 1
        selected_duration["value"] = end - start
        return _fake_trim_output(destination, source, start, end)

    monkeypatch.setattr(app_module, "probe_video_metadata", fake_probe)
    monkeypatch.setattr(app_module, "trim_video", fake_trim)


def test_real_vfr_source_is_normalized_to_cfr_and_source_is_preserved(
    client: TestClient,
    technical_video_fixtures: dict[str, Path],
) -> None:
    source = technical_video_fixtures["vfr"]
    original_bytes = source.read_bytes()
    uploaded = client.post(
        "/api/sessions",
        files={"video": ("vfr-source.mp4", original_bytes, "video/mp4")},
    )

    assert uploaded.status_code == 201
    session = uploaded.json()
    response = client.post(
        f"/api/sessions/{session['session_id']}/trim",
        json={"start_time": 0.1, "end_time": 1.1},
    )

    assert response.status_code == 200, response.text
    trim = response.json()["trim"]
    assert trim["trimmed_filename"].endswith(".mp4")
    output = app.state.upload_directory / trim["trimmed_filename"]
    output_probe = probe_video_metadata(output, duration_field="trimmed_duration")
    assert output_probe.metadata["nominal_frame_rate_numerator"] == 30
    assert output_probe.metadata["nominal_frame_rate_denominator"] == 1
    assert output_probe.metadata["is_constant_frame_rate"] is True
    assert source.read_bytes() == original_bytes
    uploaded_source = app.state.upload_directory / session["filename"]
    assert uploaded_source.read_bytes() != b""
    assert uploaded_source.read_bytes() == original_bytes


def test_session_trimming_saves_metadata_and_preserves_original(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    original_path = app.state.upload_directory / uploaded["filename"]
    original_bytes = original_path.read_bytes()
    _mock_trim_tools(monkeypatch, app_module)

    response = client.post(
        f"/api/sessions/{uploaded['session_id']}/trim",
        json={"start_time": 1.125, "end_time": 3.625},
    )

    assert response.status_code == 200
    trimmed = response.json()["trim"]
    assert trimmed["source_filename"] == uploaded["filename"]
    assert trimmed["trimmed_filename"].endswith(".mp4")
    assert trimmed["start_time"] == 1.125
    assert trimmed["end_time"] == 3.625
    assert trimmed["source_duration"] == 10.0
    assert trimmed["trimmed_duration"] == 2.5
    assert trimmed["created_at"]
    assert trimmed["status"] == "succeeded"
    technical = trimmed["technical_metadata"]
    assert technical["timing_validation_status"] == "passed"
    assert technical["source_duration"] == 10.0
    assert technical["trimmed_duration"] == 2.5
    assert technical["source_start_time"] == 1.125
    assert technical["source_end_time"] == 3.625
    assert technical["frame_count"] > 0
    assert technical["is_constant_frame_rate"] is True
    assert technical["video_codec"] == "h264"
    assert technical["target_frame_rate_num"] == 30
    assert technical["target_frame_rate_den"] == 1
    assert technical["target_frame_rate_display"] == "30/1"
    assert technical["output_frame_count"] == technical["frame_count"]
    assert technical["output_fps_mode"] == "cfr"
    assert technical["output_validation_status"] == "passed"
    assert "enabled" not in trimmed
    assert "duration_seconds" not in trimmed
    assert original_path.read_bytes() == original_bytes
    assert client.get(response.json()["video_url"]).content == b"synthetic-trimmed-video"
    stored = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    assert stored["trim"] == trimmed


def test_rotated_video_trim_preserves_display_orientation_and_source_bytes(
    client: TestClient, technical_video_fixtures: dict[str, Path]
) -> None:
    import hashlib

    from video_trimmer import probe_video_metadata

    fixture_path = technical_video_fixtures["rotated"]
    source_bytes = fixture_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    source_probe = probe_video_metadata(fixture_path)
    assert source_probe.validation_errors == ()
    assert (source_probe.metadata["width"], source_probe.metadata["height"]) == (96, 64)
    assert abs(source_probe.metadata["rotation"]) == 90

    uploaded_response = client.post(
        "/api/sessions",
        files={"video": ("rotated.mp4", source_bytes, "video/mp4")},
    )
    assert uploaded_response.status_code == 201
    uploaded = uploaded_response.json()
    source_path = app.state.upload_directory / uploaded["filename"]
    assert source_path.read_bytes() == source_bytes

    trim_response = client.post(
        f"/api/sessions/{uploaded['session_id']}/trim",
        json={"start_time": 0.2, "end_time": 1.6},
    )

    assert trim_response.status_code == 200, trim_response.text
    trim = trim_response.json()["trim"]
    trimmed_path = app.state.upload_directory / trim["trimmed_filename"]
    output_probe = probe_video_metadata(trimmed_path, duration_field="trimmed_duration")
    assert output_probe.validation_errors == ()

    def display_dimensions(metadata: dict[str, object]) -> tuple[int, int]:
        width, height, rotation = metadata["width"], metadata["height"], metadata["rotation"]
        assert isinstance(width, int) and isinstance(height, int)
        assert isinstance(rotation, (int, float))
        return (height, width) if abs(round(rotation / 90)) % 2 else (width, height)

    assert display_dimensions(source_probe.metadata) == (64, 96)
    assert display_dimensions(output_probe.metadata) == (64, 96)
    assert (output_probe.metadata["width"], output_probe.metadata["height"]) == (64, 96)
    assert output_probe.metadata["rotation"] == 0.0

    session = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    technical = session["trim"]["technical_metadata"]
    assert technical["source_duration"] == source_probe.metadata["source_duration"]
    assert technical["trimmed_duration"] == output_probe.metadata["trimmed_duration"]
    assert technical["width"] == output_probe.metadata["width"]
    assert technical["height"] == output_probe.metadata["height"]
    assert technical["rotation"] == output_probe.metadata["rotation"]
    assert technical["timing_validation_status"] == "passed"
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash


@pytest.mark.parametrize(
    ("start_time", "end_time"),
    [(-0.1, 1.0), (1.0, 1.0), (2.0, 1.0), (0.0, 0.25), (0.0, 121.0)],
)
def test_session_trimming_rejects_invalid_time_ranges(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    start_time: float,
    end_time: float,
) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    _mock_trim_tools(monkeypatch, app_module, source_duration=200)

    response = client.post(
        f"/api/sessions/{uploaded['session_id']}/trim",
        json={"start_time": start_time, "end_time": end_time},
    )

    assert response.status_code == 422
    stored = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    assert stored["trim"].get("status") != "succeeded"


def test_session_trimming_rejects_end_after_source_duration_and_records_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    _mock_trim_tools(monkeypatch, app_module, source_duration=5)

    response = client.post(
        f"/api/sessions/{uploaded['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 6.0},
    )

    assert response.status_code == 422
    stored = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    assert stored["trim"]["status"] == "failed"
    assert "melebihi durasi" in stored["trim"]["error"]
    assert (app.state.upload_directory / uploaded["filename"]).is_file()


def test_session_trimming_failure_is_persisted_and_keeps_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    _mock_trim_tools(monkeypatch, app_module)

    def fail_trim(*_args, **_kwargs):
        raise app_module.VideoTrimmingError("FFmpeg gagal memotong video.")

    monkeypatch.setattr(app_module, "trim_video", fail_trim)
    response = client.post(
        f"/api/sessions/{uploaded['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 3.0},
    )

    assert response.status_code == 422
    stored = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    assert stored["trim"]["status"] == "failed"
    assert stored["trim"]["error"] == "FFmpeg gagal memotong video."
    assert stored["trim"]["trimmed_filename"] is None
    assert stored["trim"]["source_duration"] == 10.0
    assert "enabled" not in stored["trim"]
    assert "duration_seconds" not in stored["trim"]
    assert stored["trim"]["status"] == "failed"
    assert (app.state.upload_directory / uploaded["filename"]).is_file()


def test_invalid_trim_probe_does_not_mark_session_video_valid(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    _mock_trim_tools(monkeypatch, app_module)
    original_probe = app_module.probe_video_metadata

    def invalid_output_probe(path: Path, duration_field: str = "source_duration") -> VideoProbeResult:
        result = original_probe(path, duration_field)
        if duration_field == "trimmed_duration":
            result.metadata["frame_count"] = None
            return VideoProbeResult(
                result.metadata, ("frame_count tidak tersedia atau tidak valid.",)
            )
        return result

    monkeypatch.setattr(app_module, "probe_video_metadata", invalid_output_probe)

    response = client.post(
        f"/api/sessions/{uploaded['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 3.0},
    )

    assert response.status_code == 422
    session = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    assert session["trim"]["status"] == "failed"
    assert session["trim"]["technical_metadata"]["timing_validation_status"] == "failed"
    assert session["trim"]["technical_metadata"]["frame_count"] is None
    assert session["trim"]["trimmed_filename"] is None
    assert not list(app.state.upload_directory.glob("*.mp4"))


def test_trimming_is_bound_to_session_id_and_persists_after_store_reload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module
    from comparison_store import SessionStore

    first = client.post(
        "/api/sessions", files={"video": ("first.webm", webm_bytes(), "video/webm")}
    ).json()
    second = client.post(
        "/api/sessions", files={"video": ("second.webm", webm_bytes(), "video/webm")}
    ).json()
    _mock_trim_tools(monkeypatch, app_module)

    response = client.post(
        f"/api/sessions/{first['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 3.0},
    )

    assert response.status_code == 200
    assert response.json()["trim"]["source_filename"] == first["filename"]
    second_session = client.get(f"/api/sessions/{second['session_id']}").json()
    assert second_session["trim"].get("trimmed_filename") is None
    assert "technical_metadata" not in second_session["trim"]
    restarted_store = SessionStore(
        app.state.session_file, app.state.upload_directory, app.state.output_directory
    )
    restored = restarted_store.get(first["session_id"])
    assert restored["trim"]["trimmed_filename"] == response.json()["trim"]["trimmed_filename"]
    assert restored["trim"]["technical_metadata"]["timing_validation_status"] == "passed"


def test_trimming_rejects_invalid_session_id(client: TestClient) -> None:
    response = client.post(
        f"/api/sessions/{'not-a-session'}/trim",
        json={"start_time": 0.0, "end_time": 1.0},
    )

    assert response.status_code == 404


def test_pose_processing_uses_successful_trimmed_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    _mock_trim_tools(monkeypatch, app_module)
    trim_response = client.post(
        f"/api/sessions/{uploaded['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 3.0},
    )
    trimmed_filename = trim_response.json()["trim"]["trimmed_filename"]
    processed_sources: list[Path] = []

    def fake_process(source: Path, destination: Path, *_args, **_kwargs) -> dict:
        processed_sources.append(source)
        return _write_fake_result(
            destination,
            {
                "frames": 1,
                "fps": 25.0,
                "duration": 0.04,
                "frame_angles": [{"time_seconds": 0.0, "left_elbow": 90.0, "right_elbow": 90.0}],
            },
        )

    monkeypatch.setattr(app_module, "process_video", fake_process)
    response = client.post(f"/api/videos/{uploaded['filename']}/pose")

    assert response.status_code == 200
    assert processed_sources == [app.state.upload_directory / trimmed_filename]
    stored = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    assert stored["status"] == "succeeded"


def test_pose_processing_rejects_session_without_successful_trim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    processed_sources: list[Path] = []
    monkeypatch.setattr(
        app_module,
        "process_video",
        lambda source, *_args, **_kwargs: processed_sources.append(source),
    )

    response = client.post(f"/api/videos/{uploaded['filename']}/pose")

    assert response.status_code == 409
    assert "harus berhasil dipotong" in response.json()["detail"]
    assert processed_sources == []
    session = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    assert session["status"] == "uploaded"
    assert session["trim"]["status"] == "not_started"


def test_trimming_failure_is_recorded_without_creating_output(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()

    def fake_probe(_path: Path, duration_field: str = "source_duration") -> VideoProbeResult:
        metadata = _fake_video_probe(duration_field).metadata
        metadata["nominal_frame_rate_numerator"] = None
        metadata["nominal_frame_rate_denominator"] = None
        metadata["metadata_source"]["nominal_frame_rate_numerator"] = {
            "kind": "unavailable",
            "method": None,
        }
        metadata["metadata_source"]["nominal_frame_rate_denominator"] = {
            "kind": "unavailable",
            "method": None,
        }
        return VideoProbeResult(metadata, ())

    monkeypatch.setattr(app_module, "probe_video_metadata", fake_probe)

    def fake_trim(*_args, **_kwargs):
        raise app_module.VideoTrimmingError("FFmpeg gagal memotong video.")

    monkeypatch.setattr(app_module, "trim_video", fake_trim)
    response = client.post(
        f"/api/sessions/{uploaded['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 3.0},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "FFmpeg gagal memotong video."
    session = client.get(f"/api/sessions/{uploaded['session_id']}").json()
    assert session["trim"]["status"] == "failed"
    assert session["trim"]["trimmed_filename"] is None
    assert not list(app.state.upload_directory.glob("*.mp4"))


def test_health_check(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_uploads_supported_video_with_server_generated_name(client: TestClient) -> None:
    response = client.post("/api/videos", files={"video": ("smash.webm", webm_bytes(), "video/webm")})
    assert response.status_code == 201
    payload = response.json()
    assert payload["filename"].endswith(".webm")
    assert "smash" not in payload["filename"]
    assert payload["role"] == "baseline"
    assert len(payload["comparison_id"]) == 32
    assert len(payload["attempt_id"]) == 32
    assert "session_id" not in payload
    playback = client.get(payload["video_url"])
    assert playback.status_code == 200
    assert playback.headers["content-type"].startswith("video/webm")
    assert playback.content == webm_bytes()
    comparison = client.get(f"/api/comparisons/{payload['comparison_id']}").json()
    assert comparison["baseline"]["attempt_id"] == payload["attempt_id"]
    assert comparison["baseline"]["source_filename"] == payload["filename"]
    assert comparison["retake"] is None


def test_legacy_upload_does_not_create_session_record(client: TestClient) -> None:
    response = client.post("/api/videos", files={"video": ("legacy.webm", webm_bytes(), "video/webm")})
    assert response.status_code == 201
    assert "session_id" not in response.json()
    assert not app.state.session_store.path.exists()


def test_session_upload_creates_one_session_without_comparison_record(client: TestClient) -> None:
    response = client.post("/api/sessions", files={"video": ("session.webm", webm_bytes(), "video/webm")})
    assert response.status_code == 201
    payload = response.json()
    assert len(payload["session_id"]) == 32
    assert payload["filename"].endswith(".webm")
    assert "comparison_id" not in payload
    session = client.get(f"/api/sessions/{payload['session_id']}").json()
    assert session["source_filename"] == payload["filename"]
    assert session["status"] == "uploaded"
    assert not app.state.comparison_store.path.exists()


def test_session_processing_updates_session_record_and_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app as app_module

    upload = client.post("/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}).json()
    _mock_trim_tools(monkeypatch, app_module)
    trim_response = client.post(
        f"/api/sessions/{upload['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 3.0},
    )
    trimmed_filename = trim_response.json()["trim"]["trimmed_filename"]
    monkeypatch.setattr(
        app_module,
        "process_video",
        lambda *args, **kwargs: _write_fake_result(
            args[1],
            {"frames": 1, "fps": 25.0, "duration": 0.04, "frame_angles": [{"time_seconds": 0.0, "left_elbow": 90.0, "right_elbow": 90.0}]},
        ),
    )

    response = client.post(f"/api/videos/{upload['filename']}/pose")
    assert response.status_code == 200
    assert response.json()["processed_filename"] == trimmed_filename
    stored = client.get(f"/api/sessions/{upload['session_id']}").json()
    assert stored["status"] == "succeeded"
    assert stored["result_filename"] == Path(response.json()["result_url"]).name


def test_session_processing_failure_persists_failed_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app as app_module

    upload = client.post("/api/sessions", files={"video": ("smash.webm", webm_bytes(), "video/webm")}).json()
    _mock_trim_tools(monkeypatch, app_module)
    client.post(
        f"/api/sessions/{upload['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 3.0},
    )
    monkeypatch.setattr(app_module, "process_video", lambda *args, **kwargs: _raise_processing_error(app_module))

    response = client.post(f"/api/videos/{upload['filename']}/pose")
    assert response.status_code == 422
    stored = client.get(f"/api/sessions/{upload['session_id']}").json()
    assert stored["status"] == "failed"
    assert stored["result_filename"] is None


def test_pose_processing_changes_only_the_trimmed_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app as app_module

    first = client.post(
        "/api/sessions", files={"video": ("first.webm", webm_bytes(), "video/webm")}
    ).json()
    second = client.post(
        "/api/sessions", files={"video": ("second.webm", webm_bytes(), "video/webm")}
    ).json()
    _mock_trim_tools(monkeypatch, app_module)
    trim = client.post(
        f"/api/sessions/{first['session_id']}/trim",
        json={"start_time": 1.0, "end_time": 3.0},
    ).json()["trim"]
    monkeypatch.setattr(
        app_module,
        "process_video",
        lambda _source, destination, *_args, **_kwargs: _write_fake_result(
            destination,
            {
                "frames": 1,
                "fps": 25.0,
                "duration": 0.04,
                "frame_angles": [
                    {"time_seconds": 0.0, "left_elbow": 90.0, "right_elbow": 90.0}
                ],
            },
        ),
    )

    response = client.post(f"/api/videos/{first['filename']}/pose")

    assert response.status_code == 200
    first_session = client.get(f"/api/sessions/{first['session_id']}").json()
    second_session = client.get(f"/api/sessions/{second['session_id']}").json()
    assert first_session["status"] == "succeeded"
    assert first_session["trim"] == trim
    assert second_session["status"] == "uploaded"
    assert second_session["result_filename"] is None
    assert second_session["trim"]["status"] == "not_started"


def test_duplicate_filename_in_both_metadata_stores_is_rejected(client: TestClient) -> None:
    import app as app_module

    duplicate_name = "d" * 32 + ".webm"
    app.state.upload_directory.mkdir(parents=True, exist_ok=True)
    (app.state.upload_directory / duplicate_name).write_bytes(webm_bytes())
    app.state.comparison_store.create_baseline(duplicate_name)
    app.state.session_store.create_session(duplicate_name)

    response = client.post(f"/api/videos/{duplicate_name}/pose")
    assert response.status_code == 409
    assert "Konflik metadata" in response.json()["detail"]


def test_missing_filename_returns_not_found(client: TestClient) -> None:
    response = client.post("/api/videos/" + ("e" * 32) + ".webm/pose")
    assert response.status_code == 404
    assert response.json()["detail"] == "Video tidak ditemukan."


def test_retake_is_attached_to_the_requested_comparison(client: TestClient) -> None:
    baseline = client.post(
        "/api/videos",
        files={"video": ("baseline.webm", webm_bytes(), "video/webm")},
        data={"note": "Baseline note"},
    ).json()
    other_baseline = client.post(
        "/api/videos", files={"video": ("other.webm", webm_bytes(), "video/webm")}
    ).json()

    retake_response = client.post(
        "/api/videos",
        files={"video": ("retake.webm", webm_bytes(), "video/webm")},
        data={"comparison_id": baseline["comparison_id"], "note": "Retake note"},
    )

    assert retake_response.status_code == 201
    retake = retake_response.json()
    assert retake["comparison_id"] == baseline["comparison_id"]
    assert retake["attempt_id"] != baseline["attempt_id"]
    assert retake["role"] == "retake"
    first = client.get(f"/api/comparisons/{baseline['comparison_id']}").json()
    second = client.get(f"/api/comparisons/{other_baseline['comparison_id']}").json()
    assert first["baseline"]["note"] == "Baseline note"
    assert first["retake"]["attempt_id"] == retake["attempt_id"]
    assert first["retake"]["source_filename"] == retake["filename"]
    assert first["retake"]["note"] == "Retake note"
    assert second["retake"] is None


def test_comparison_accepts_only_one_retake(client: TestClient) -> None:
    baseline = client.post(
        "/api/videos", files={"video": ("baseline.webm", webm_bytes(), "video/webm")}
    ).json()
    first_retake = client.post(
        "/api/videos",
        files={"video": ("retake.webm", webm_bytes(), "video/webm")},
        data={"comparison_id": baseline["comparison_id"]},
    )
    second_retake = client.post(
        "/api/videos",
        files={"video": ("another-retake.webm", webm_bytes(), "video/webm")},
        data={"comparison_id": baseline["comparison_id"]},
    )

    assert first_retake.status_code == 201
    assert second_retake.status_code == 409
    comparison = client.get(f"/api/comparisons/{baseline['comparison_id']}").json()
    assert comparison["retake"]["source_filename"] == first_retake.json()["filename"]
    assert len(list(app.state.upload_directory.iterdir())) == 2


def test_invalid_comparison_and_file_references_are_rejected(client: TestClient) -> None:
    from comparison_store import ComparisonNotFoundError

    assert client.get("/api/comparisons/../secret").status_code == 404
    assert client.get("/outputs/../secret.mp4").status_code == 404
    with pytest.raises(ComparisonNotFoundError):
        app.state.comparison_store.create_baseline("../secret.webm")
    response = client.post(
        "/api/videos",
        files={"video": ("retake.webm", webm_bytes(), "video/webm")},
        data={"comparison_id": "../secret"},
    )
    assert response.status_code == 404
    assert list(app.state.upload_directory.iterdir()) == []


def test_metadata_can_be_read_by_a_fresh_store_after_restart(client: TestClient) -> None:
    import app as app_module
    from comparison_store import ComparisonStore

    uploaded = client.post(
        "/api/videos", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    restarted_store = ComparisonStore(
        app.state.metadata_file, app.state.upload_directory, app.state.output_directory
    )

    comparison = restarted_store.get(uploaded["comparison_id"])

    assert comparison["baseline"]["attempt_id"] == uploaded["attempt_id"]
    assert app_module.app.state.metadata_file.is_file()


def test_note_can_be_updated_using_comparison_and_attempt_ids(client: TestClient) -> None:
    uploaded = client.post(
        "/api/videos", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()

    response = client.put(
        f"/api/comparisons/{uploaded['comparison_id']}/attempts/{uploaded['attempt_id']}/note",
        json={"note": "Fokus pada timing ayunan"},
    )

    assert response.status_code == 200
    assert response.json()["baseline"]["note"] == "Fokus pada timing ayunan"


def test_comparison_and_attempt_ids_are_validated_for_note_updates(client: TestClient) -> None:
    uploaded = client.post(
        "/api/videos", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()

    invalid_comparison = client.put(
        f"/api/comparisons/{'x' * 32}/attempts/{uploaded['attempt_id']}/note",
        json={"note": "Catatan"},
    )
    invalid_attempt = client.put(
        f"/api/comparisons/{uploaded['comparison_id']}/attempts/{'y' * 32}/note",
        json={"note": "Catatan"},
    )

    assert invalid_comparison.status_code == 404
    assert invalid_attempt.status_code == 404


def test_rejects_invalid_extension(client: TestClient) -> None:
    response = client.post("/api/videos", files={"video": ("smash.exe", webm_bytes(), "application/octet-stream")})
    assert response.status_code == 415
    assert "Format tidak didukung" in response.json()["detail"]


def test_rejects_empty_file(client: TestClient) -> None:
    response = client.post("/api/videos", files={"video": ("empty.webm", b"", "video/webm")})
    assert response.status_code == 422
    assert response.json()["detail"] == "File video kosong."


def test_rejects_oversized_file(client: TestClient) -> None:
    app.state.max_upload_bytes = 8
    response = client.post("/api/videos", files={"video": ("large.webm", webm_bytes(), "video/webm")})
    assert response.status_code == 413
    assert "melebihi batas" in response.json()["detail"]
    assert not list(app.state.upload_directory.glob("*"))


def test_rejects_unsafe_filename_when_serving_video(client: TestClient) -> None:
    assert client.get("/videos/..%2Fsecret.webm").status_code == 404


def test_pose_rejects_invalid_video_reference(client: TestClient) -> None:
    assert client.post("/api/videos/not-a-video/pose").status_code == 404


def test_pose_processing_error_removes_output(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app as app_module

    upload = app.state.upload_directory
    upload.mkdir(parents=True)
    name = "a" * 32 + ".webm"
    (upload / name).write_bytes(webm_bytes())
    app.state.output_directory = upload / "outputs"

    def fails(*args, **kwargs):
        args[1].parent.mkdir(parents=True, exist_ok=True)
        args[1].write_bytes(b"partial")
        args[1].unlink()
        raise app_module.VideoProcessingError("gagal sintetis")

    app.state.comparison_store.create_baseline(name)
    monkeypatch.setattr(app_module, "process_video", fails)
    response = client.post(f"/api/videos/{name}/pose")
    assert response.status_code == 422
    assert not list(app.state.output_directory.glob("*.mp4"))
    located = app.state.comparison_store.find_by_source(name)
    assert located is not None
    comparison = client.get(f"/api/comparisons/{located[0]}").json()
    assert comparison["baseline"]["status"] == "failed"
    assert comparison["baseline"]["result_filename"] is None


def test_pose_response_includes_frame_specific_elbow_angles(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    upload = app.state.upload_directory
    upload.mkdir(parents=True)
    name = "b" * 32 + ".webm"
    (upload / name).write_bytes(webm_bytes())
    frame_angles = [
        {"time_seconds": 0.0, "left_elbow": 180.0, "right_elbow": None},
        {"time_seconds": 0.04, "left_elbow": 90.0, "right_elbow": 120.0},
    ]
    app.state.comparison_store.create_baseline(name)
    monkeypatch.setattr(
        app_module,
        "process_video",
        lambda *args, **kwargs: _write_fake_result(
            args[1],
            {
                "frames": 2,
                "fps": 25.0,
                "duration": 0.08,
                "frame_angles": frame_angles,
            },
        ),
    )

    response = client.post(f"/api/videos/{name}/pose")

    assert response.status_code == 200
    assert response.json()["frame_angles"] == frame_angles
    assert response.json()["fps"] == 25.0
    assert response.json()["comparison_id"]
    assert response.json()["attempt_id"]
    comparison = client.get(f"/api/comparisons/{response.json()['comparison_id']}").json()
    assert comparison["baseline"]["status"] == "succeeded"
    assert comparison["baseline"]["result_filename"] == Path(response.json()["result_url"]).name
    assert comparison["baseline"]["frame_angles"] == frame_angles


def test_angle_difference_uses_independently_selected_frames_and_handles_missing_angles(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    results = [
        {
            "frames": 2,
            "fps": 25.0,
            "duration": 0.08,
            "frame_angles": [
                {"time_seconds": 0.0, "left_elbow": 100.0, "right_elbow": None},
                {"time_seconds": 0.04, "left_elbow": 80.0, "right_elbow": 75.0},
            ],
        },
        {
            "frames": 1,
            "fps": 30.0,
            "duration": 1 / 30,
            "frame_angles": [
                {"time_seconds": 0.0, "left_elbow": 110.0, "right_elbow": 90.0},
            ],
        },
    ]

    def fake_process(source: Path, destination: Path, *_args, **_kwargs) -> dict:
        return _write_fake_result(destination, results.pop(0))

    monkeypatch.setattr(app_module, "process_video", fake_process)
    baseline = client.post(
        "/api/videos", files={"video": ("baseline.webm", webm_bytes(), "video/webm")}
    ).json()
    assert client.post(f"/api/videos/{baseline['filename']}/pose").status_code == 200
    retake = client.post(
        "/api/videos",
        files={"video": ("retake.webm", webm_bytes(), "video/webm")},
        data={"comparison_id": baseline["comparison_id"]},
    ).json()
    assert client.post(f"/api/videos/{retake['filename']}/pose").status_code == 200

    selected = client.get(
        f"/api/comparisons/{baseline['comparison_id']}/angle-difference",
        params={"baseline_frame": 1, "retake_frame": 0},
    )
    unavailable = client.get(
        f"/api/comparisons/{baseline['comparison_id']}/angle-difference",
        params={"baseline_frame": 0, "retake_frame": 0},
    )

    assert selected.status_code == 200
    assert selected.json()["baseline_frame"] == 1
    assert selected.json()["retake_frame"] == 0
    assert selected.json()["angles"]["left_elbow"] == {
        "baseline": 80.0,
        "retake": 110.0,
        "difference": 30.0,
    }
    assert selected.json()["angles"]["right_elbow"]["difference"] == 15.0
    assert unavailable.json()["angles"]["right_elbow"] == {
        "baseline": None,
        "retake": 90.0,
        "difference": None,
    }


def test_angle_difference_rejects_missing_retake_or_frame(client: TestClient) -> None:
    baseline = client.post(
        "/api/videos", files={"video": ("baseline.webm", webm_bytes(), "video/webm")}
    ).json()

    no_retake = client.get(
        f"/api/comparisons/{baseline['comparison_id']}/angle-difference",
        params={"baseline_frame": 0, "retake_frame": 0},
    )

    assert no_retake.status_code == 409
    assert client.get(
        f"/api/comparisons/{baseline['comparison_id']}/angle-difference",
        params={"baseline_frame": -1, "retake_frame": 0},
    ).status_code == 422


def test_angle_difference_waits_until_both_attempts_are_processed(client: TestClient) -> None:
    baseline = client.post(
        "/api/videos", files={"video": ("baseline.webm", webm_bytes(), "video/webm")}
    ).json()
    retake = client.post(
        "/api/videos",
        files={"video": ("retake.webm", webm_bytes(), "video/webm")},
        data={"comparison_id": baseline["comparison_id"]},
    ).json()

    response = client.get(
        f"/api/comparisons/{baseline['comparison_id']}/angle-difference",
        params={"baseline_frame": 0, "retake_frame": 0},
    )
    stored = client.get(f"/api/comparisons/{baseline['comparison_id']}").json()

    assert retake["role"] == "retake"
    assert response.status_code == 409
    assert stored["baseline"]["status"] == "uploaded"
    assert stored["retake"]["status"] == "uploaded"


def _write_fake_result(destination: Path, result: dict) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"synthetic-result")
    return result


def test_processing_failure_is_persisted_without_a_result_reference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    upload_response = client.post(
        "/api/videos", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    monkeypatch.setattr(
        app_module,
        "process_video",
        lambda *args, **kwargs: _raise_processing_error(app_module),
    )

    response = client.post(f"/api/videos/{upload_response['filename']}/pose")
    comparison = client.get(f"/api/comparisons/{upload_response['comparison_id']}").json()

    assert response.status_code == 422
    assert comparison["baseline"]["status"] == "failed"
    assert comparison["baseline"]["result_filename"] is None
    assert comparison["baseline"]["frame_angles"] == []


def _raise_processing_error(app_module):
    raise app_module.VideoProcessingError("video gagal")


def test_missing_result_file_changes_success_to_failed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app as app_module

    uploaded = client.post(
        "/api/videos", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    monkeypatch.setattr(
        app_module,
        "process_video",
        lambda *args, **kwargs: _write_fake_result(
            args[1],
            {
                "frames": 1,
                "fps": 25.0,
                "duration": 0.04,
                "frame_angles": [{"time_seconds": 0.0, "left_elbow": 90.0, "right_elbow": None}],
            },
        ),
    )
    processed = client.post(f"/api/videos/{uploaded['filename']}/pose").json()
    result_path = app.state.output_directory / Path(processed["result_url"]).name
    result_path.unlink()

    comparison = client.get(f"/api/comparisons/{uploaded['comparison_id']}").json()

    assert comparison["baseline"]["status"] == "failed"
    assert comparison["baseline"]["result_filename"] is None
    assert comparison["baseline"]["error"] == "File hasil skeleton tidak ditemukan."


def test_restart_marks_interrupted_processing_as_failed(client: TestClient) -> None:
    from comparison_store import ComparisonStore

    uploaded = client.post(
        "/api/videos", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    store = app.state.comparison_store
    store.begin_processing(uploaded["filename"])
    restarted_store = ComparisonStore(
        app.state.metadata_file, app.state.upload_directory, app.state.output_directory
    )

    restarted_store.recover_interrupted()

    attempt = restarted_store.get(uploaded["comparison_id"])["baseline"]
    assert attempt["status"] == "failed"
    assert attempt["result_filename"] is None
    assert "dimulai ulang" in attempt["error"]


def test_missing_source_file_changes_attempt_to_failed(client: TestClient) -> None:
    uploaded = client.post(
        "/api/videos", files={"video": ("smash.webm", webm_bytes(), "video/webm")}
    ).json()
    (app.state.upload_directory / uploaded["filename"]).unlink()

    comparison = client.get(f"/api/comparisons/{uploaded['comparison_id']}").json()

    assert comparison["baseline"]["status"] == "failed"
    assert comparison["baseline"]["result_filename"] is None
    assert comparison["baseline"]["error"] == "File video sumber tidak ditemukan."
