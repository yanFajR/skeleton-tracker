# Motion Tracker

Motion Tracker is a local web application for reviewing sports video. It overlays MediaPipe pose landmarks and shows selected-frame elbow angles. It is an analysis aid, not an automated coach or a technique pass/fail system.

## Current status

**Phase B1 complete (single-session preview and trimming):** each session can upload one source video, preview it in the browser, choose a start/end range, create a new trimmed MP4, and run pose analysis against that trimmed file. Session pose analysis is rejected until trimming succeeds; the source video is never used as a fallback. Metadata ownership remains explicit: legacy uploads create comparison metadata only; session uploads and their trimmed files belong only to their session.

**Phase 4 pipeline remains active:** uploaded videos can still be processed frame by frame with MediaPipe Pose Landmarker and viewed as an annotated MP4. The app continues to measure left and right elbow angles from the same pose inference and update the values as the result video is scrubbed.

**Phase B2.2 implemented:** the B2.1 metadata contract is integrated with session trimming. FFmpeg decodes the source and trimmed output to collect per-frame PTS, stream properties, display rotation, and decoded frame count; session trim is marked successful only after technical validation passes. B2.3.2 subsequently adds validated CFR normalization to session trimming without changing the legacy comparison pipeline.

**Legacy comparison UI removed:** the baseline/retake workflow is no longer exposed by the web interface. Backend compatibility endpoints and historical comparison data remain unchanged so existing data is not deleted.

**UI styling and usability refinement remains in place:** the baseline/retake workflow still has clearer Indonesian instructions, refined video and angle cards, more readable statuses and notes, responsive spacing, and actionable upload/analysis errors. Internal comparison IDs remain in shareable URLs but are not shown in the page.

## Video result review

After a successful trim and pose analysis, the result video supports play/pause, frame navigation, and playback speeds 0.1×, 0.25×, 0.5×, 0.75×, and 1×. The elbow measurements are synchronized to the stored pose frame timestamp nearest the current playback position. Loading and buffering are shown while browser video data is being prepared, and delayed events from a previously selected session are ignored.

The frame review also shows left and right elbow angles and trunk lean. Trunk lean is the angle between the shoulder midpoint-to-hip midpoint line and the vertical axis; it is unavailable when shoulder or hip landmarks have low visibility/presence or invalid coordinates. Because it uses 2D landmarks, forward/backward direction requires camera orientation and is not inferred automatically.

Not yet implemented: manual phase selection, torso/knee angles, coordinate export, automatic smash detection, and technique scoring.

## Prerequisites

- Windows (initial target) or Linux
- Python 3.14.5 (the current project environment); MediaPipe 1.1.1 is installed in `.venv`.

## Environment setup

From the project folder, create the dedicated environment if it does not exist:

```powershell
python -m venv .venv
```

Use its interpreter directly (no activation is required):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start the application

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in a browser. The server binds only to the local machine.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

On this Windows environment the default pytest temp root under `%LOCALAPPDATA%\Temp\pytest-of-it09` may fail with `WinError 5`. If that occurs, keep tests inside the workspace with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .\pytest-run-temp
```

B1 verification: the required workspace-local pytest command completed with 65 passed tests and one upstream Starlette/httpx TestClient deprecation warning. Tests cover session upload/preview, trim time validation, FFmpeg output validation, source preservation, failure metadata, ownership and restart persistence, mandatory trimming before session pose processing, trimmed-input selection, and legacy comparison behavior. An isolated browser end-to-end check uploaded and played a real MP4, trimmed a selected range with FFmpeg, processed the trimmed video with MediaPipe, and played the generated skeleton result without JavaScript errors.

B2.1 technical metadata fields are defined in `video_metadata.py`. Each value carries per-field provenance (`measured`, `calculated`, `estimated`, `provided`, `policy`, or `unavailable`); unavailable measurements remain `null`. Test media is generated at runtime with the project-local `imageio-ffmpeg` binary. The VFR fixture is made by varying frame presentation timestamps with FFmpeg's `setpts` and `-fps_mode vfr`; the rotation fixture is a stream-copy MP4 with a 90-degree display rotation set through FFmpeg's `-display_rotation` input option. No binary fixtures or production videos are added or changed.

B2.2 probing uses the bundled FFmpeg executable with `showinfo` and `-noautorotate`; `ffprobe` is not present in the `imageio-ffmpeg` binary directory or on PATH. `frame_count` counts every decoded `showinfo` frame record, while `valid_pts_count` counts only records with numeric PTS. Missing PTS therefore cannot silently reduce the decoded count or pass timing validation. CFR detection requires complete PTS and uses decoded PTS intervals (1 ms or 5% of median interval tolerance). `average_frame_rate` is calculated from valid PTS count and first-to-last PTS span. `nominal_frame_rate` remains null because this probe does not establish an exact stream-declared nominal rate; `inferred_cfr_frame_rate` is the separately labeled reciprocal-median-PTS estimate. For a trimmed CFR stream, `actual_output_frame_rate` records that same calculated output-timeline rate; it is null for a source probe or VFR stream.

The timestamp-derived duration uses the last frame's FFmpeg `duration_time` when available. If FFmpeg reports zero/unknown duration (as it does for the generated VFR fixture), the final frame duration is estimated from the median positive PTS interval and its provenance is marked `estimated`. A measured container duration is compared with timestamp duration using a 10 ms maximum tolerance for FFmpeg's centisecond-formatted duration. When the last frame duration is estimated, the comparison allows one average frame interval plus 5 ms, reflecting the unknown final VFR frame boundary; trim-range validation allows at most one frame interval plus 5 ms, rather than a fixed 100 ms floor or two-frame tolerance. These estimates are not equivalent to exact packet timing. OpenCV frame count remains a consistency cross-check, not the primary timing source.

B2.2 validation checks duration against frame timestamps, decoder frame-count agreement, FPS/rate availability, dimensions, display rotation, codec, pixel format, requested trim duration, and effective display orientation. Invalid trim output is recorded as failed with `timing_validation_status: "failed"` and is not retained as a successful trim. This validation does not normalize CFR or alter pose processing.

B2.2 remediation verification: the required workspace-local full test command completed with 81 passed tests and one upstream Starlette/httpx `TestClient` deprecation warning. Coverage includes decoded-frame/valid-PTS separation, incomplete PTS rejection, one-frame versus multi-frame duration discrepancies, and an actual session trim of the 90-degree rotation fixture.

Phase B2.3.1 adds a standalone `resolve_target_frame_rate` policy function. It accepts a target only when decoded frame count and valid PTS count agree, the input is classified CFR or VFR, and a provenance-trusted nominal FPS numerator/denominator is available. CFR rates are taken from FFmpeg `showinfo` stream `frame_rate` only after cross-checking against complete CFR PTS intervals. Trusted VFR nominal rates may be supplied with measured/provided provenance; average FPS and `inferred_cfr_frame_rate` are never fallbacks. Rates above 60 FPS and incomplete/unknown metadata fail with an explicit reason. The resolver returns integer rational numerator/denominator pairs. It is not connected to the active trim endpoint and performs no normalization.

The nonstandard-rate test fixture is now generated by FFmpeg's rational `24000/1001` lavfi source with a 24000 video track timescale. Tests verify the probed rational and PTS ticks (`0, 1001, 2002` in time base `1/24000`), not just the requested fixture setting.

B2.3.1 verification: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .\pytest-run-temp` completed with 93 passed tests and one upstream Starlette/httpx `TestClient` deprecation warning. The active trim endpoint and legacy comparison flow remain unchanged.

Phase B2.3.2 connects the validated target FPS resolver to session trimming. The source remains unchanged and unnormalized. The trimmed output is encoded with the resolved rational numerator/denominator through the bundled FFmpeg `fps` filter and CFR output mode. FFmpeg timestamps are reset and output is re-probed before the session is marked successful; output CFR status and rational FPS must match the resolved target. Rational values such as `24000/1001` are retained exactly rather than converted to approximate floats. If resolution or output validation fails, trimming remains failed, temporary output is removed, and the source remains available. Legacy comparisons do not use this normalization path.

B2.3.2 verification: the workspace-local command `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .\pytest-run-temp` completed with 95 passed tests and one upstream Starlette/httpx TestClient deprecation warning.

### B2.3.2 input policy final

B2.3.2 supports CFR and VFR source videos. Session trimming uses 30 FPS for every successful output. VFR sources are resampled from their original presentation timestamps so motion speed is preserved; no average-FPS substitution is used.

Every successful session trim targets `30/1` and must pass strict per-frame PTS cadence validation against the stream time base before its metadata is persisted. Source timestamps remain the basis for VFR resampling.

### B2.3.2 remediation

Session output validation now checks every decoded frame PTS against the exact resolved numerator/denominator timeline, using the FFmpeg stream time base to account for integer PTS quantization. A dropped frame or cadence interval outside that timeline rejects the output before trim success is persisted.

FFmpeg resamples source presentation timestamps with the `fps=30/1` filter for VFR and CFR outputs. Decoded timestamps are used rather than average FPS. If decoding or normalization fails, trimming returns an informative error and removes temporary output. Legacy comparison uploads and processing do not use this normalization path.

## Video upload and skeleton result

Upload MP4, MOV, or WebM video up to 200 MB. The server checks the extension, rejects empty files, applies the size limit while streaming, checks a basic container signature, and saves accepted files under a generated UUID filename in `uploads/`. It never uses the supplied filename as a path. The single-session workflow previews the uploaded source before trimming and processes the trimmed result with MediaPipe afterward. The legacy comparison upload remains available under **Alur perbandingan lama**.

The annotated result is played by the browser's native HTML5 player. Browser and operating-system codecs determine whether the generated H.264 video plays. Set `MOTION_TRACKER_MAX_UPLOAD_BYTES` before server startup to configure the upload limit in bytes.

Uploaded videos, trimmed results, pose outputs, and metadata remain local and are ignored by Git. Source and trimmed files are in `uploads/`, annotated MP4s in `outputs/`, legacy comparison records in `data/comparisons.json`, and session records in `data/sessions.json`. Removing a referenced file manually causes its metadata to be marked failed when reconciled; this does not automatically delete other files or metadata records.

## Pose tracking

The session page shows a native HTML video player, duration, and start/end controls. Select an interval from 0.5 seconds through the configured maximum (120 seconds by default), then choose **Gunakan Video Ini**. The server trims the source into a separate H.264 MP4 using the FFmpeg binary supplied by `imageio-ffmpeg`; it validates the encoded output before saving the session record and starts pose analysis using that trimmed file. Failed trimming is recorded in the session and leaves the original source untouched.

The legacy comparison form has been removed from the web interface. The session form previews first and starts analysis only after the user confirms a trim. Backend comparison endpoints and historical data remain available for compatibility, but the application UI now uses only sessions.

The upload form, loading indicator, result player, and angle measurements adapt to narrow mobile screens; the upload button expands to the available card width for easier tapping.

The default maximum duration for pose analysis and trimming is 120 seconds and can be changed with `MOTION_TRACKER_MAX_VIDEO_DURATION_SECONDS`. The default minimum trim duration is 0.5 seconds and can be changed with `MOTION_TRACKER_MIN_TRIM_DURATION_SECONDS`. Trimmed and annotated results are encoded as MP4 with H.264 video and `yuv420p` pixel format for compatibility with modern browsers. Encoding uses the FFmpeg binary bundled by `imageio-ffmpeg`, installed in the project virtual environment from `requirements.txt`; no separate FFmpeg installation is needed. The pretrained Lite model is `models/pose_landmarker_lite.task`, downloaded from Google's official MediaPipe model storage. MediaPipe is Apache-2.0; review the model's applicable terms before redistribution.

## Session upload and trimming API

- `POST /api/sessions` uploads one source video and creates only its session metadata.
- `GET /videos/{filename}` serves a validated server-generated source or trimmed video for browser preview.
- `POST /api/sessions/{session_id}/trim` accepts `{"start_time":1.25,"end_time":4.5}`. Times are rounded to milliseconds and checked against the source duration and configured limits. The client cannot supply a path or filename.
- A successful trim is saved as a new server-generated MP4 and recorded in the session's `trim` object, including source/trimmed filenames, selected times, source/trimmed durations, creation time, and status. Status and `trimmed_duration` are authoritative; redundant `enabled` and `duration_seconds` fields are not written.
- `POST /api/videos/{filename}/pose` remains the pose route. For a session source, it returns HTTP 409 until trimming succeeds and then uses only the trimmed file; legacy comparison files continue to use their original upload.
- `GET /api/sessions/{session_id}` reads the persisted session and trim status.

Trimming currently runs synchronously in a FastAPI worker with a five-minute FFmpeg timeout. A completed trim is immutable in this stage; to choose a different range, upload another session. Slow motion and manual phase selection are not part of B1.

Pose processing runs in FastAPI's synchronous worker thread. Keep the page open and wait for processing to finish; the server can still respond to other requests while it processes the video.

## Baseline, retake, and comparison

The first upload is stored as a baseline, analyzed automatically, and displayed with its skeleton video and selected-frame elbow angles. The page link includes its `comparison_id`; reopening that URL reloads metadata from the local JSON store. Add or edit the baseline note under its result. Once baseline analysis succeeds, the retake upload card appears directly below the comparison heading so the next step is easy to find without scrolling past the result cards. Use the visible **Pilih video pengulangan** button to choose the next smash, then submit it for analysis and optionally record the change being tried. The retake is attached using the current comparison ID and is analyzed automatically. The retake upload control is disabled during submission, and the API allows only one retake per comparison. At the bottom of the comparison page, **Unggah video pengulangan** jumps back to that upload form, while **Mulai baseline baru** returns to the home page to create a separate comparison.

Baseline and retake skeleton videos are presented side by side on wide screens and stacked on narrow screens. The comparison page uses the available screen width on desktop and allows taller video frames; each video has independent playback controls, so users can choose a frame in each result. The angle difference is calculated as retake minus baseline for those selected frames; increases and decreases are described as numeric changes, not as improvements or regressions. If either measurement is unavailable, the difference for that side is unavailable too. The UI explains that movement phases are not aligned, so equal timestamps do not imply equivalent smash phases.

The page can be opened again with `?comparison_id=<id>` to read its metadata and results. Uploaded or in-progress attempts are resumed/polled; failed attempts show the stored error and can be retried if the source remains available. A missing video or result is reported from the comparison metadata.

Every successful `POST /api/videos` upload creates a comparison with a baseline attempt and returns `comparison_id`, `attempt_id`, and `role`. The optional multipart form field `note` stores an evaluation note (up to 2,000 characters). To attach a retake, include the comparison's `comparison_id` in the multipart form.

- `GET /api/comparisons/{comparison_id}` reads the stored baseline and optional retake metadata.
- `PUT /api/comparisons/{comparison_id}/attempts/{attempt_id}/note` accepts `{"note":"..."}` to update an evaluation note.
- `GET /api/comparisons/{comparison_id}/angle-difference?baseline_frame=<index>&retake_frame=<index>` returns each selected elbow angle and its difference. The frame indices refer independently to the frame arrays for the baseline and retake.

Comparison and attempt IDs must be 32-character lowercase UUID hex strings. File references are limited to server-generated names within the upload/output directories. JSON updates use an atomic file replacement. If the application restarts during processing, an in-progress attempt is marked failed on startup; processing can be retried by calling its pose endpoint again. The local JSON store is intended for the current single-process local application, not concurrent multi-worker deployments.


## Elbow angles

The result page shows left and right elbow angles for the selected result-video frame. Measurements come from the same per-frame pose detection used to draw the skeleton, so the video is not processed a second time. If a pose or one of the required shoulder, elbow, or wrist landmarks is not visible enough, that side is shown as not detected.

Angles are calculated in 2D from the camera image. Camera angle, perspective, and body orientation can affect the measurement; it is not a definitive judgment of sports technique.

The result player updates left and right measurements from the processed frame metadata while the video moves. Both values are calculated separately in the same MediaPipe inference that draws the skeleton. Shoulder, elbow, and wrist landmarks must have finite coordinates and confidence; landmarks with visibility or presence below 0.5, or zero-length arm vectors, return an unavailable measurement instead of a misleading numeric angle.

The session result includes play/pause, frame backward/forward controls, and playback speeds 0.1×, 0.25×, 0.5×, 0.75×, and 1×. Frame navigation pauses playback and seeks directly to the corresponding stored analysis frame.

Install all dependencies in the existing environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Troubleshooting

- If `python` is not found, install Python and reopen PowerShell.
- If the browser cannot connect, ensure Uvicorn is still running and use the exact local URL above.
- On Windows, an asyncio `ConnectionResetError` with `WinError 10054` means the browser or another client closed its connection. By itself, it does not indicate a pose-detection failure. Keep the page open while processing; if the page reports an error, check the preceding server log for the actual processing error.
- If a result video still cannot play, check that the browser is up to date and that its H.264 playback support is enabled.
- Keep dependencies inside `.venv`; do not install project packages system-wide.

Further development should wait for approval before starting another stage.
#   s k e l e t o n - t r a c k e r  
 