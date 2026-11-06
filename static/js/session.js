const sessionWorkflow = document.querySelector("#session-workflow");
const sessionUploadForm = document.querySelector("#session-upload-form");
const sessionFileInput = document.querySelector("#session-video-file");
const sessionSelectedFile = document.querySelector("#session-selected-file");
const sessionUploadButton = document.querySelector("#session-upload-button");
const sessionEditor = document.querySelector("#session-editor");
const sessionPreview = document.querySelector("#session-preview-video");
const sessionSourceDuration = document.querySelector("#session-source-duration");
const trimForm = document.querySelector("#session-trim-form");
const trimStartTime = document.querySelector("#trim-start-time");
const trimEndTime = document.querySelector("#trim-end-time");
const trimStartDisplay = document.querySelector("#trim-start-display");
const trimEndDisplay = document.querySelector("#trim-end-display");
const trimStartRange = document.querySelector("#trim-start-range");
const trimEndRange = document.querySelector("#trim-end-range");
const trimSelectedDuration = document.querySelector("#trim-selected-duration");
const trimRangeError = document.querySelector("#trim-range-error");
const trimSubmitButton = document.querySelector("#trim-submit-button");
const trimProcessing = document.querySelector("#trim-processing-status");
const trimProcessingMessage = document.querySelector("#trim-processing-message");
const sessionStatus = document.querySelector("#session-status");
const sessionResult = document.querySelector("#session-result");
const sessionResultLoading = document.querySelector("#session-result-loading");
const sessionReviewControls = document.querySelector("#session-review-controls");
const sessionFramePrev = document.querySelector("#session-frame-prev");
const sessionPlayToggle = document.querySelector("#session-play-toggle");
const sessionFrameNext = document.querySelector("#session-frame-next");
const sessionPlaybackRate = document.querySelector("#session-playback-rate");
const sessionFrameStatus = document.querySelector("#session-frame-status");
const sessionMeasurements = document.querySelector("#session-measurements");
const sessionAngleTime = document.querySelector("#session-angle-time");
const sessionLeftAngle = document.querySelector("#session-left-angle");
const sessionRightAngle = document.querySelector("#session-right-angle");
const sessionTrunkMeasurement = document.querySelector("#session-trunk-measurement");
const sessionTrunkAngle = document.querySelector("#session-trunk-angle");
const sessionRetryButton = document.querySelector("#session-retry-analysis");
const minimumTrimDuration = Number(sessionWorkflow.dataset.minTrimDuration);
const maximumTrimDuration = Number(sessionWorkflow.dataset.maxTrimDuration);
let activeSession = null;
let sourceDuration = null;
let resultVideoGeneration = 0;
let sessionLifecycleGeneration = 0;

function formatSessionAngle(angle) {
  return angle === null || angle === undefined ? "Tidak tersedia" : `${Number(angle).toFixed(1)}°`;
}

function updateSessionMeasurements(video) {
  const frames = activeSession?.frame_angles || [];
  const fps = activeSession?.fps;
  if (!frames.length || !Number.isFinite(Number(fps)) || Number(fps) <= 0) {
    sessionMeasurements.hidden = true;
    return;
  }
  sessionMeasurements.hidden = false;
  const frameIndex = nearestFrameIndex(frames, video.currentTime, fps);
  const measurement = frames[frameIndex];
  sessionAngleTime.textContent = `${measurement.time_seconds.toFixed(2)} dtk`;
  sessionLeftAngle.textContent = formatSessionAngle(measurement.left_elbow);
  sessionRightAngle.textContent = formatSessionAngle(measurement.right_elbow);
  sessionTrunkMeasurement.hidden = !Object.prototype.hasOwnProperty.call(measurement, "trunk_lean");
  sessionTrunkAngle.textContent = formatSessionAngle(measurement.trunk_lean);
  sessionFrameStatus.textContent = `Frame ${frameIndex + 1} / ${frames.length}`;
  sessionFramePrev.disabled = frameIndex <= 0;
  sessionFrameNext.disabled = frameIndex >= frames.length - 1;
}

function nearestFrameIndex(frames, currentTime, fps) {
  if (!frames.length) return 0;
  const directIndex = Math.min(
    frames.length - 1,
    Math.max(0, Math.round(currentTime * Number(fps))),
  );
  let nearestIndex = directIndex;
  let nearestDistance = Math.abs(
    Number(frames[directIndex].time_seconds) - currentTime,
  );
  for (let index = Math.max(0, directIndex - 1); index <= Math.min(frames.length - 1, directIndex + 1); index += 1) {
    const distance = Math.abs(Number(frames[index].time_seconds) - currentTime);
    if (distance < nearestDistance) {
      nearestDistance = distance;
      nearestIndex = index;
    }
  }
  return nearestIndex;
}

function syncPlayToggle(video) {
  sessionPlayToggle.textContent = video.paused ? "Putar" : "Jeda";
  sessionPlayToggle.setAttribute("aria-pressed", String(!video.paused));
}

function configureSessionResultVideo(video) {
  const generation = ++resultVideoGeneration;
  video.controls = true;
  video.preload = "metadata";
  video.setAttribute("aria-label", "Video hasil analisis skeleton");
  syncPlayToggle(video);
  video.addEventListener("timeupdate", () => {
    if (isCurrentResultVideo(video, generation)) updateSessionMeasurements(video);
  });
  video.addEventListener("loadedmetadata", () => {
    if (isCurrentResultVideo(video, generation)) updateSessionMeasurements(video);
  });
  video.addEventListener("loadeddata", () => {
    if (!isCurrentResultVideo(video, generation)) return;
    sessionResultLoading.hidden = true;
    showSessionStatus("Video hasil siap ditinjau.", "success");
    syncPlayToggle(video);
    updateSessionMeasurements(video);
  });
  video.addEventListener("waiting", () => {
    if (isCurrentResultVideo(video, generation)) {
      sessionResultLoading.hidden = false;
      sessionResultLoading.textContent = "Memuat frame video hasil…";
    }
  });
  video.addEventListener("playing", () => {
    if (!isCurrentResultVideo(video, generation)) return;
    sessionResultLoading.hidden = true;
    syncPlayToggle(video);
  });
  video.addEventListener("play", () => {
    if (isCurrentResultVideo(video, generation)) syncPlayToggle(video);
  });
  video.addEventListener("pause", () => {
    if (isCurrentResultVideo(video, generation)) syncPlayToggle(video);
  });
  video.addEventListener("error", () => {
    if (!isCurrentResultVideo(video, generation)) return;
    sessionResultLoading.hidden = true;
    showSessionStatus("Video hasil tidak dapat diputar. Silakan buka kembali atau coba analisis lagi.", "error");
  });
  return video;
}

function mountSessionResultVideo(resultUrl, sessionId) {
  if (sessionId !== activeSession?.session_id) return null;
  const resultVideo = document.createElement("video");
  sessionResult.replaceChildren(resultVideo);
  configureSessionResultVideo(resultVideo);
  resultVideo.src = resultUrl;
  sessionResult.hidden = false;
  sessionResultLoading.hidden = false;
  sessionResultLoading.textContent = "Memuat video hasil…";
  sessionReviewControls.hidden = false;
  sessionPlaybackRate.value = "1";
  resultVideo.playbackRate = 1;
  return resultVideo;
}

function isCurrentResultVideo(video, generation) {
  return generation === resultVideoGeneration && sessionResult.querySelector("video") === video;
}

function sessionResultVideo() {
  return sessionResult.querySelector("video");
}

function stepSessionFrame(delta) {
  const video = sessionResultVideo();
  if (!video) return;
  const frames = activeSession?.frame_angles || [];
  const fps = Number(activeSession?.fps);
  if (!frames.length || !Number.isFinite(fps) || fps <= 0) return;
  video.pause();
  const currentIndex = nearestFrameIndex(frames, video.currentTime, fps);
  const nextIndex = Math.min(frames.length - 1, Math.max(0, currentIndex + delta));
  video.currentTime = nextIndex / fps;
  updateSessionMeasurements(video);
}

sessionFramePrev.addEventListener("click", () => stepSessionFrame(-1));
sessionFrameNext.addEventListener("click", () => stepSessionFrame(1));
sessionPlayToggle.addEventListener("click", async () => {
  const video = sessionResultVideo();
  if (!video) return;
  if (video.paused) {
    try {
      await video.play();
    } catch (_) {
      showSessionStatus("Pemutar video tidak dapat memulai playback.", "error");
    }
  } else {
    video.pause();
  }
});
sessionPlaybackRate.addEventListener("change", () => {
  const video = sessionResultVideo();
  if (!video) return;
  video.playbackRate = Number(sessionPlaybackRate.value);
});

function formatVideoTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00.000";
  const totalMilliseconds = Math.round(seconds * 1000);
  const minutes = Math.floor(totalMilliseconds / 60000);
  const remainingSeconds = Math.floor((totalMilliseconds % 60000) / 1000);
  const milliseconds = totalMilliseconds % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

function showSessionStatus(message, kind = "") {
  sessionStatus.textContent = message;
  sessionStatus.className = `status ${kind}`;
}

async function sessionApi(url, options = {}) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Permintaan tidak berhasil.");
  return result;
}

function setSessionUrl(sessionId) {
  const url = new URL(window.location.href);
  url.searchParams.delete("comparison_id");
  url.searchParams.set("session_id", sessionId);
  window.history.replaceState({}, "", url);
}

function selectedTimes() {
  return {
    start: Number(trimStartTime.value),
    end: Number(trimEndTime.value),
  };
}

function validateTrimRange() {
  const { start, end } = selectedTimes();
  const duration = end - start;
  trimStartDisplay.textContent = formatVideoTime(start);
  trimEndDisplay.textContent = formatVideoTime(end);
  let message = "";
  if (!Number.isFinite(start) || start < 0) {
    message = "Waktu mulai tidak boleh kurang dari 0.";
  } else if (!Number.isFinite(end) || end <= start) {
    message = "Waktu selesai harus lebih besar dari waktu mulai.";
  } else if (!Number.isFinite(sourceDuration) || end > sourceDuration) {
    message = "Waktu selesai tidak boleh melebihi durasi video.";
  } else if (duration < minimumTrimDuration) {
    message = `Durasi pilihan minimal ${minimumTrimDuration} detik.`;
  } else if (duration > maximumTrimDuration) {
    message = `Durasi pilihan maksimal ${maximumTrimDuration} detik.`;
  }
  trimRangeError.textContent = message;
  trimSelectedDuration.textContent = formatVideoTime(Math.max(0, duration));
  trimSubmitButton.disabled = Boolean(message) || !activeSession || trimProcessing.hidden === false;
  if (Number.isFinite(start) && start >= 0 && start <= sourceDuration) {
    trimStartRange.value = String(start);
  }
  if (Number.isFinite(end) && end >= 0 && end <= sourceDuration) {
    trimEndRange.value = String(end);
  }
  return !message;
}

function setPreview(url, label) {
  sessionPreview.pause();
  sessionPreview.src = url;
  sessionPreview.setAttribute("aria-label", label);
  sessionPreview.load();
  sessionPreview.onloadedmetadata = () => {
    if (!activeSession?.trim?.status || activeSession.trim.status !== "succeeded") {
      const duration = sessionPreview.duration;
      if (Number.isFinite(duration) && duration > 0) {
        sourceDuration = duration;
        sessionSourceDuration.textContent = formatVideoTime(duration);
        trimStartRange.max = String(duration);
        trimEndRange.max = String(duration);
          trimStartTime.max = String(duration);
          trimEndTime.max = String(duration);
        if (!trimEndTime.value || Number(trimEndTime.value) === 0) {
          trimEndTime.value = String(duration);
        }
        validateTrimRange();
      }
    }
  };
  sessionPreview.ontimeupdate = () => {
    if (sessionPreview.paused || trimSubmitButton.disabled) return;
    const { start, end } = selectedTimes();
    if (sessionPreview.currentTime < start || sessionPreview.currentTime >= end) {
      sessionPreview.currentTime = start;
    }
  };
}

function updateTrimInputFromRange(range, input) {
  input.value = range.value;
  validateTrimRange();
}

trimStartRange.addEventListener("input", () => updateTrimInputFromRange(trimStartRange, trimStartTime));
trimEndRange.addEventListener("input", () => updateTrimInputFromRange(trimEndRange, trimEndTime));
trimStartTime.addEventListener("input", validateTrimRange);
trimEndTime.addEventListener("input", validateTrimRange);
trimStartTime.addEventListener("change", validateTrimRange);
trimEndTime.addEventListener("change", validateTrimRange);

sessionFileInput.addEventListener("change", () => {
  const file = sessionFileInput.files[0];
  sessionSelectedFile.textContent = file
    ? `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`
    : "Belum ada file dipilih.";
});

sessionUploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = sessionFileInput.files[0];
  if (!file) return showSessionStatus("Pilih file video terlebih dahulu.", "error");
  if (!file.size) return showSessionStatus("File video kosong.", "error");
  if (file.size > 200 * 1024 * 1024) {
    return showSessionStatus("Ukuran video melebihi batas 200 MB.", "error");
  }
  sessionUploadButton.disabled = true;
  showSessionStatus("Mengunggah video…");
  try {
    const formData = new FormData();
    formData.append("video", file);
    const uploaded = await sessionApi("/api/sessions", { method: "POST", body: formData });
    sessionLifecycleGeneration += 1;
    resultVideoGeneration += 1;
    sessionResult.replaceChildren();
    sessionResultLoading.hidden = true;
    sessionResult.hidden = true;
    activeSession = {
      session_id: uploaded.session_id,
      source_filename: uploaded.filename,
      status: uploaded.status,
      trim: null,
    };
    setSessionUrl(activeSession.session_id);
    sessionEditor.hidden = false;
    sessionUploadForm.hidden = true;
    sourceDuration = null;
    trimStartTime.value = "0";
    trimEndTime.value = "0";
    trimStartRange.value = "0";
    trimEndRange.value = "0";
    setPreview(uploaded.video_url, "Pratinjau video sumber");
    showSessionStatus("Video siap ditinjau. Pilih bagian yang ingin dianalisis.");
  } catch (error) {
    showSessionStatus(error.message || "Video gagal diunggah.", "error");
  } finally {
    sessionUploadButton.disabled = false;
  }
});

trimForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeSession || !validateTrimRange()) return;
  const { start, end } = selectedTimes();
  trimSubmitButton.disabled = true;
  trimProcessing.hidden = false;
  trimProcessingMessage.textContent = "Memotong bagian video…";
  showSessionStatus("");
  try {
    const trimmed = await sessionApi(
      `/api/sessions/${encodeURIComponent(activeSession.session_id)}/trim`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_time: start, end_time: end }),
      },
    );
    activeSession.trim = trimmed.trim;
    sourceDuration = trimmed.trim.source_duration;
    sessionSourceDuration.textContent = formatVideoTime(sourceDuration);
    trimSelectedDuration.textContent = formatVideoTime(trimmed.trim.trimmed_duration);
    setPreview(trimmed.video_url, "Pratinjau bagian video yang dipilih");
    trimStartTime.value = String(trimmed.trim.start_time);
    trimEndTime.value = String(trimmed.trim.end_time);
    trimStartTime.disabled = true;
    trimEndTime.disabled = true;
    trimStartRange.disabled = true;
    trimEndRange.disabled = true;
    trimRangeError.textContent = "";
    await analyzeTrimmedSession();
  } catch (error) {
    showSessionStatus(error.message || "Trimming atau analisis gagal.", "error");
  } finally {
    trimProcessing.hidden = true;
    trimSubmitButton.disabled = activeSession?.trim?.status === "succeeded";
    if (activeSession?.trim?.status !== "succeeded") validateTrimRange();
  }
});

async function analyzeTrimmedSession() {
  if (!activeSession?.trim || activeSession.trim.status !== "succeeded") return;
  const sessionId = activeSession.session_id;
  const lifecycle = sessionLifecycleGeneration;
  trimProcessing.hidden = false;
  trimProcessingMessage.textContent = "Menganalisis gerakan pada bagian pilihan…";
  sessionRetryButton.disabled = true;
  try {
    const analysis = await sessionApi(
      `/api/videos/${encodeURIComponent(activeSession.source_filename)}/pose`,
      { method: "POST" },
    );
    if (lifecycle !== sessionLifecycleGeneration || sessionId !== activeSession?.session_id) return;
    activeSession.status = analysis.status;
    activeSession.result_filename = analysis.result_url.split("/").pop();
    activeSession.frames = analysis.frames;
    activeSession.fps = analysis.fps;
    activeSession.frame_angles = analysis.frame_angles;
    mountSessionResultVideo(analysis.result_url, sessionId);
    sessionRetryButton.hidden = true;
    showSessionStatus("Potongan video dan analisis berhasil disimpan.", "success");
  } catch (error) {
    sessionRetryButton.hidden = false;
    sessionRetryButton.textContent = "Coba analisis lagi";
    showSessionStatus(error.message || "Analisis pose gagal.", "error");
  } finally {
    trimProcessing.hidden = true;
    sessionRetryButton.disabled = false;
  }
}

sessionRetryButton.addEventListener("click", analyzeTrimmedSession);

async function restoreSession(sessionId) {
  const lifecycle = ++sessionLifecycleGeneration;
  resultVideoGeneration += 1;
  sessionResult.replaceChildren();
  sessionResultLoading.hidden = true;
  sessionResult.hidden = true;
  try {
    const session = await sessionApi(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (lifecycle !== sessionLifecycleGeneration) return;
    activeSession = session;
    sessionEditor.hidden = false;
    sessionUploadForm.hidden = true;
    setSessionUrl(sessionId);
    const trim = session.trim || {};
    sourceDuration = trim.source_duration || null;
    trimStartTime.value = String(trim.start_time ?? 0);
    trimEndTime.value = String(trim.end_time ?? 0);
    if (sourceDuration) {
      sessionSourceDuration.textContent = formatVideoTime(sourceDuration);
      trimStartRange.max = String(sourceDuration);
      trimEndRange.max = String(sourceDuration);
      trimStartTime.max = String(sourceDuration);
      trimEndTime.max = String(sourceDuration);
      trimStartRange.value = trimStartTime.value;
      trimEndRange.value = trimEndTime.value;
    }
    const hasTrim = trim.status === "succeeded" && trim.trimmed_filename;
    const filename = hasTrim ? trim.trimmed_filename : session.source_filename;
    setPreview(`/videos/${encodeURIComponent(filename)}`, hasTrim ? "Pratinjau bagian video yang dipilih" : "Pratinjau video sumber");
    if (hasTrim) {
      trimStartTime.disabled = true;
      trimEndTime.disabled = true;
      trimStartRange.disabled = true;
      trimEndRange.disabled = true;
      trimSubmitButton.disabled = true;
    } else {
      sessionPreview.onloadedmetadata = () => {
        const duration = sessionPreview.duration;
        if (Number.isFinite(duration) && duration > 0) {
          sourceDuration = duration;
          sessionSourceDuration.textContent = formatVideoTime(duration);
          trimStartRange.max = String(duration);
          trimEndRange.max = String(duration);
          trimEndTime.max = String(duration);
          if (Number(trimStartTime.value) > duration) trimStartTime.value = "0";
          if (Number(trimEndTime.value) > duration || Number(trimEndTime.value) === 0) {
            trimEndTime.value = String(duration);
          }
          trimStartRange.value = trimStartTime.value;
          trimEndRange.value = trimEndTime.value;
          validateTrimRange();
        }
      };
    }
    if (session.result_filename && session.status === "succeeded") {
      mountSessionResultVideo(`/outputs/${encodeURIComponent(session.result_filename)}`, sessionId);
      sessionRetryButton.hidden = true;
      showSessionStatus("Analisis sesi ini sudah selesai.", "success");
    } else if (trim.status === "failed") {
      sessionRetryButton.hidden = true;
      showSessionStatus(trim.error || "Trimming gagal. Pilih rentang lain lalu coba lagi.", "error");
    } else if (hasTrim) {
      sessionRetryButton.hidden = false;
      sessionRetryButton.textContent = session.status === "failed" ? "Coba analisis lagi" : "Analisis pose";
      showSessionStatus(session.error || "Video potongan siap dianalisis.");
    } else {
      sessionRetryButton.hidden = true;
      showSessionStatus("Sesi video berhasil dibuka.");
    }
  } catch (error) {
    showSessionStatus(error.message || "Sesi video tidak dapat dibuka.", "error");
  }
}

const initialSessionId = new URLSearchParams(window.location.search).get("session_id");
if (initialSessionId) restoreSession(initialSessionId);
