from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_upload_picker_uses_a_custom_accessible_control() -> None:
    template = (PROJECT_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

    assert 'class="visually-hidden-file"' in template
    assert 'id="session-upload-form" novalidate' in template
    assert 'class="picker-button"' in template
    assert ".file-picker input.visually-hidden-file { display: none; }" not in styles
    assert "clip-path: inset(50%)" in styles
    assert ".file-picker:focus-within { outline:" in styles


def test_review_controls_offer_required_playback_rates() -> None:
    template = (PROJECT_ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    for rate in ("0.1", "0.25", "0.5", "0.75", "1"):
        assert f'value="{rate}"' in template


def test_review_controls_uses_frame_metadata_for_synchronized_navigation() -> None:
    script = (PROJECT_ROOT / "static" / "js" / "session.js").read_text(encoding="utf-8")

    assert "stepSessionFrame(-1)" in script
    assert "stepSessionFrame(1)" in script
    assert "video.currentTime = nextIndex / fps" in script
    assert "updateSessionMeasurements(video)" in script
    assert "video.playbackRate = Number(sessionPlaybackRate.value)" in script
    assert "nearestFrameIndex(frames, video.currentTime, fps)" in script
    assert 'video.paused ? "Putar" : "Jeda"' in script
    assert 'video.addEventListener("loadeddata"' in script
    assert 'video.addEventListener("waiting"' in script
    assert 'video.addEventListener("playing"' in script
    assert "isCurrentResultVideo(video, generation)" in script
    assert "mountSessionResultVideo(resultUrl, sessionId)" in script
    assert "lifecycle !== sessionLifecycleGeneration" in script
