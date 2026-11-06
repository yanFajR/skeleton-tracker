const uploadForm = document.querySelector("#upload-form");
const fileInput = document.querySelector("#video-file");
const selectedFile = document.querySelector("#selected-file");
const baselineNote = document.querySelector("#baseline-note");
const uploadButton = document.querySelector("#upload-button");
const uploadStatus = document.querySelector("#upload-status");
const processingStatus = document.querySelector("#processing-status");
const processingMessage = document.querySelector("#processing-message");
const landingGrid = document.querySelector(".landing-grid");
const comparisonSection = document.querySelector("#comparison-section");
const retakeUploadCard = document.querySelector("#retake-upload-card");
const retakeUploadForm = document.querySelector("#retake-upload-form");
const retakeFileInput = document.querySelector("#retake-file");
const retakeSelectFileButton = document.querySelector("#retake-select-file");
const retakeSelectedFile = document.querySelector("#retake-selected-file");
const retakeUploadNote = document.querySelector("#retake-upload-note");
const retakeUploadButton = document.querySelector("#retake-upload-button");
const retakeUploadStatus = document.querySelector("#retake-upload-status");
const retakeProcessingStatus = document.querySelector("#retake-processing-status");
const retakeProcessingMessage = document.querySelector("#retake-processing-message");
const baselineNoteForm = document.querySelector("#baseline-note-form");
const retakeNoteForm = document.querySelector("#retake-note-form");
const comparisonPlayers = { baseline: null, retake: null };
let comparison = null;
let comparisonId = null;
let differenceTimer = null;
let differenceRequestId = 0;
let processingAttemptId = null;
const noteAttemptIds = { baseline: null, retake: null };

function showStatus(element, message, kind = "") {
  element.textContent = message;
  element.className = `status ${kind}`;
}

function friendlyError(message, fallback) {
  message = typeof message === "string" ? message : "";
  if (message === "Comparison tidak ditemukan.") {
    return "Halaman perbandingan ini tidak ditemukan. Periksa tautan atau mulai dengan mengunggah baseline baru.";
  }
  if (message === "File video sumber tidak ditemukan.") {
    return "File video tidak ditemukan. Unggah video kembali untuk membuat analisis baru.";
  }
  if (message === "Video tidak dapat dibaca oleh OpenCV.") {
    return "Video tidak dapat dibaca. Pastikan file bisa diputar, lalu coba unggah ulang.";
  }
  if (message.startsWith("Format tidak didukung.")) {
    return "Format belum didukung. Pilih video MP4, MOV, atau WebM.";
  }
  if (message === "Ukuran video melebihi batas 200 MB.") {
    return "Ukuran video lebih dari 200 MB. Pilih video yang lebih kecil.";
  }
  if (message === "File video kosong.") {
    return "File yang dipilih kosong. Pilih video lain, lalu coba lagi.";
  }
  if (message.includes("Isi file bukan kontainer video")) {
    return "File ini tampaknya bukan video yang valid. Pilih video yang bisa diputar, lalu coba lagi.";
  }
  if (message.startsWith("Durasi video melebihi batas")) {
    return "Video terlalu panjang. Pilih video yang lebih singkat, lalu coba lagi.";
  }
  if (message === "Pemrosesan pose gagal.") {
    return "Analisis gerakan gagal. Coba proses ulang atau pilih video lain.";
  }
  if (message === "Comparison ini sudah memiliki retake.") {
    return "Retake untuk perbandingan ini sudah pernah diunggah.";
  }
  return message || fallback;
}

function setProcessing(role, active, message = "") {
  const isBaseline = role === "baseline";
  const status = isBaseline ? processingStatus : retakeProcessingStatus;
  const messageElement = isBaseline ? processingMessage : retakeProcessingMessage;
  const button = isBaseline ? uploadButton : retakeUploadButton;
  status.hidden = !active;
  messageElement.textContent = message;
  button.disabled = active;
}

async function readJson(response) {
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Permintaan tidak berhasil.");
  return body;
}

async function api(url, options) {
  return readJson(await fetch(url, options));
}

function setComparisonUrl(id) {
  const url = new URL(window.location.href);
  url.searchParams.set("comparison_id", id);
  window.history.replaceState({}, "", url);
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  selectedFile.textContent = file
    ? `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`
    : "Belum ada file dipilih.";
});

retakeSelectFileButton.addEventListener("click", () => retakeFileInput.click());

retakeFileInput.addEventListener("change", () => {
  const file = retakeFileInput.files[0];
  retakeSelectedFile.textContent = file
    ? `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`
    : "Belum ada file dipilih.";
  retakeSelectFileButton.textContent = file
    ? "Ganti video pengulangan"
    : "Pilih video pengulangan";
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return showStatus(uploadStatus, "Pilih file video terlebih dahulu.", "error");
  if (!validateFile(file, uploadStatus)) return;
  uploadButton.disabled = true;
  setProcessing("baseline", true, "Menyiapkan video baseline…");
  showStatus(uploadStatus, "");
  try {
    const data = new FormData();
    data.append("video", file);
    data.append("note", baselineNote.value);
    const uploaded = await api("/api/videos", { method: "POST", body: data });
    comparisonId = uploaded.comparison_id;
    setComparisonUrl(comparisonId);
    await refreshComparison(true);
    await processAttempt(uploaded.filename, uploaded.role);
  } catch (error) {
    showStatus(uploadStatus, friendlyError(error.message, "Unggahan atau analisis gagal. Periksa video dan coba lagi."), "error");
  } finally {
    setProcessing("baseline", false);
  }
});

retakeUploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!comparisonId || retakeUploadButton.disabled) return;
  const file = retakeFileInput.files[0];
  if (!file) return showStatus(retakeUploadStatus, "Pilih video retake terlebih dahulu.", "error");
  if (!validateFile(file, retakeUploadStatus)) return;
  retakeUploadButton.disabled = true;
  setProcessing("retake", true, "Menyiapkan video retake…");
  showStatus(retakeUploadStatus, "");
  try {
    const data = new FormData();
    data.append("video", file);
    data.append("comparison_id", comparisonId);
    data.append("note", retakeUploadNote.value);
    const uploaded = await api("/api/videos", { method: "POST", body: data });
    await refreshComparison(true);
    await processAttempt(uploaded.filename, uploaded.role);
  } catch (error) {
    showStatus(retakeUploadStatus, friendlyError(error.message, "Unggahan atau analisis retake gagal. Periksa video dan coba lagi."), "error");
    if (comparisonId) {
      try {
        await refreshComparison();
      } catch (refreshError) {
        showStatus(retakeUploadStatus, `${friendlyError(error.message, "Unggahan retake gagal.")} ${friendlyError(refreshError.message, "Status perbandingan juga gagal dimuat.")}`, "error");
      }
    }
  } finally {
    setProcessing("retake", false);
  }
});

function validateFile(file, statusElement) {
  if (!file.size) {
    showStatus(statusElement, "File video kosong.", "error");
    return false;
  }
  if (file.size > 200 * 1024 * 1024) {
    showStatus(statusElement, "Ukuran video melebihi batas 200 MB.", "error");
    return false;
  }
  return true;
}

async function processAttempt(filename, role) {
  const attempt = comparison && comparison[role];
  if (attempt) {
    processingAttemptId = attempt.attempt_id;
    setProcessing(role, true, `Menganalisis gerakan ${role === "baseline" ? "baseline" : "retake"}…`);
  }
  try {
    await api(`/api/videos/${encodeURIComponent(filename)}/pose`, { method: "POST" });
    await refreshComparison();
    const statusElement = role === "baseline" ? uploadStatus : retakeUploadStatus;
    showStatus(statusElement, "Analisis skeleton selesai.", "success");
  } catch (error) {
    await refreshComparison();
    const statusElement = role === "baseline" ? uploadStatus : retakeUploadStatus;
    showStatus(statusElement, friendlyError(error.message, "Analisis gerakan gagal. Coba proses ulang video."), "error");
  } finally {
    if (attempt && processingAttemptId === attempt.attempt_id) processingAttemptId = null;
    setProcessing(role, false);
  }
}

async function refreshComparison(suppressResume = false) {
  if (!comparisonId) return;
  const loaded = await api(`/api/comparisons/${encodeURIComponent(comparisonId)}`);
  comparison = loaded;
  renderComparison(!suppressResume);
}

function renderComparison(resume = true) {
  if (!comparison) return;
  comparisonSection.hidden = false;
  landingGrid.hidden = true;
  renderAttempt("baseline", comparison.baseline);
  renderAttempt("retake", comparison.retake);

  const canUploadRetake =
    comparison.baseline.status === "succeeded" && comparison.retake === null;
  retakeUploadCard.hidden = !canUploadRetake;
  const retakeUsedButton = document.querySelector("#upload-another-retake");
  retakeUsedButton.hidden = comparison.baseline.status !== "succeeded";
  retakeUsedButton.textContent = canUploadRetake
    ? "Unggah video pengulangan"
    : "Lihat hasil pengulangan";
  retakeUploadButton.disabled = !canUploadRetake || retakeProcessingStatus.hidden === false;

  updateDifference();
  if (resume) resumePendingAttempt();
}

function renderAttempt(role, attempt) {
  const isBaseline = role === "baseline";
  const slot = document.querySelector(`#${role}-result`);
  const statusBadge = document.querySelector(`#${role}-status`);
  const noteForm = isBaseline ? baselineNoteForm : retakeNoteForm;
  const noteInput = document.querySelector(`#${role}-note-edit`);
  const noteStatus = document.querySelector(`#${role}-note-status`);
  const card = document.querySelector(`#${role}-card`);

  if (!attempt) {
    statusBadge.textContent = "Belum diunggah";
    statusBadge.className = "state-badge";
    slot.className = "video-slot empty-slot";
    slot.innerHTML = '<span class="empty-icon" aria-hidden="true">＋</span><p>Unggah rekaman kedua setelah mencoba perbaikan.</p>';
    noteForm.hidden = true;
    setMeasurement(role, null, null);
    card.dataset.attemptId = "";
    return;
  }

  card.dataset.attemptId = attempt.attempt_id;
  const statusNames = {
    uploaded: "Menunggu analisis",
    processing: "Sedang diproses",
    succeeded: "Analisis selesai",
    failed: "Analisis gagal",
  };
  statusBadge.textContent = statusNames[attempt.status] || "Status tidak diketahui";
  statusBadge.className = `state-badge state-${attempt.status}`;
  noteForm.hidden = false;
  if (noteAttemptIds[role] !== attempt.attempt_id) {
    noteInput.value = attempt.note || "";
    noteStatus.textContent = "";
    noteAttemptIds[role] = attempt.attempt_id;
  }

  if (attempt.status === "succeeded" && attempt.result_filename) {
    const resultUrl = `/outputs/${encodeURIComponent(attempt.result_filename)}`;
    if (slot.dataset.resultUrl !== resultUrl) {
      slot.className = "video-slot";
      slot.replaceChildren();
      const player = document.createElement("video");
      player.controls = true;
      player.preload = "metadata";
      player.setAttribute("aria-label", `Video skeleton ${role}`);
      player.src = resultUrl;
      player.addEventListener("timeupdate", () => updateAttemptMeasurements(role, attempt));
      player.addEventListener("seeked", () => updateAttemptMeasurements(role, attempt));
      slot.append(player);
      slot.dataset.resultUrl = resultUrl;
      comparisonPlayers[role] = player;
    }
    updateAttemptMeasurements(role, attempt);
    return;
  }

  if (slot.dataset.resultUrl) {
    slot.replaceChildren();
    delete slot.dataset.resultUrl;
    comparisonPlayers[role] = null;
  }
  slot.className = "video-slot empty-slot";
  slot.replaceChildren();
  const message = document.createElement("p");
  message.textContent = attempt.status === "failed"
    ? friendlyError(attempt.error, "Analisis gagal. Coba proses ulang video ini.")
    : attempt.status === "processing"
      ? "Video sedang dianalisis. Halaman akan memperbarui status secara otomatis."
      : "Video telah diunggah dan menunggu analisis.";
  slot.append(message);
  const canRetry =
    attempt.status === "uploaded" ||
    (attempt.status === "failed" &&
      attempt.error !== "File video sumber tidak ditemukan." &&
      attempt.error !== "Video tidak dapat dibaca oleh OpenCV." &&
      !(typeof attempt.error === "string" && attempt.error.startsWith("Durasi video melebihi batas")));
  if (canRetry) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button-secondary retry-button";
    retry.textContent = attempt.status === "failed" ? "Coba proses ulang" : "Mulai analisis";
    retry.addEventListener("click", () => processAttempt(attempt.source_filename, role));
    slot.append(retry);
  }
  setMeasurement(role, null, null);
}

function updateAttemptMeasurements(role, attempt) {
  const player = comparisonPlayers[role];
  const frames = attempt.frame_angles || [];
  if (!player || !attempt.fps || !frames.length) {
    setMeasurement(role, null, null);
    return;
  }
  const frameIndex = Math.min(
    frames.length - 1,
    Math.max(0, Math.round(player.currentTime * attempt.fps)),
  );
  const measurement = frames[frameIndex];
  setMeasurement(role, measurement, measurement.time_seconds);
  scheduleDifferenceUpdate();
}

function setMeasurement(role, measurement, time) {
  document.querySelector(`#${role}-time`).textContent =
    time === null || time === undefined ? "Sudut frame terpilih" : `${time.toFixed(2)} dtk`;
  document.querySelector(`#${role}-left-angle`).textContent = formatAngle(measurement?.left_elbow);
  document.querySelector(`#${role}-right-angle`).textContent = formatAngle(measurement?.right_elbow);
}

function formatAngle(angle) {
  return angle === null || angle === undefined ? "Tidak tersedia" : `${angle.toFixed(1)}°`;
}

function selectedFrameIndex(role) {
  const attempt = comparison && comparison[role];
  const player = comparisonPlayers[role];
  if (!attempt || !player || !attempt.frame_angles?.length || !attempt.fps) return null;
  return Math.min(
    attempt.frame_angles.length - 1,
    Math.max(0, Math.round(player.currentTime * attempt.fps)),
  );
}

function scheduleDifferenceUpdate() {
  window.clearTimeout(differenceTimer);
  differenceTimer = window.setTimeout(updateDifference, 100);
}

async function updateDifference() {
  const message = document.querySelector("#difference-message");
  const values = document.querySelector("#difference-values");
  if (!comparison || !comparison.retake) {
    message.textContent = "Unggah retake untuk melihat selisih pengukuran.";
    values.hidden = true;
    return;
  }
  if (comparison.baseline.status !== "succeeded" || comparison.retake.status !== "succeeded") {
    message.textContent = "Selisih tersedia setelah kedua video berhasil dianalisis.";
    values.hidden = true;
    return;
  }
  const baselineFrame = selectedFrameIndex("baseline");
  const retakeFrame = selectedFrameIndex("retake");
  if (baselineFrame === null || retakeFrame === null) {
    message.textContent = "Data sudut pada frame terpilih tidak tersedia.";
    values.hidden = true;
    return;
  }

  const requestId = ++differenceRequestId;
  const query = new URLSearchParams({
    baseline_frame: String(baselineFrame),
    retake_frame: String(retakeFrame),
  });
  try {
    const result = await api(
      `/api/comparisons/${encodeURIComponent(comparisonId)}/angle-difference?${query}`,
    );
    if (requestId !== differenceRequestId) return;
    values.hidden = false;
    const left = result.angles.left_elbow;
    const right = result.angles.right_elbow;
    document.querySelector("#left-difference").textContent = formatDifference(left.difference);
    document.querySelector("#right-difference").textContent = formatDifference(right.difference);
    const availableCount = [left, right].filter((angle) => angle.difference !== null).length;
    message.textContent = availableCount
      ? `Retake − baseline · frame ${baselineFrame + 1} dan frame ${retakeFrame + 1} yang dipilih`
      : "Sudut pada frame terpilih tidak tersedia untuk dihitung selisihnya.";
  } catch (error) {
    if (requestId !== differenceRequestId) return;
    message.textContent = error.message || "Selisih sudut tidak dapat dimuat.";
    values.hidden = true;
  }
}

function formatDifference(difference) {
  if (difference === null || difference === undefined) return "Tidak tersedia";
  if (difference === 0) return "Tidak berubah (0.0°)";
  const direction = difference > 0 ? "naik" : "turun";
  return `${direction} ${Math.abs(difference).toFixed(1)}°`;
}

async function saveNote(role) {
  const attempt = comparison && comparison[role];
  if (!attempt) return;
  const input = document.querySelector(`#${role}-note-edit`);
  const status = document.querySelector(`#${role}-note-status`);
  const button = (role === "baseline" ? baselineNoteForm : retakeNoteForm).querySelector("button");
  button.disabled = true;
  status.className = "inline-status";
  status.textContent = "Menyimpan catatan…";
  try {
    comparison = await api(
      `/api/comparisons/${encodeURIComponent(comparisonId)}/attempts/${encodeURIComponent(attempt.attempt_id)}/note`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: input.value }),
      },
    );
    status.className = "inline-status note-success";
    status.textContent = "Catatan tersimpan.";
  } catch (error) {
    status.className = "inline-status note-error";
    status.textContent = friendlyError(error.message, "Catatan gagal disimpan. Coba lagi.");
  } finally {
    button.disabled = false;
  }
}

baselineNoteForm.addEventListener("submit", (event) => {
  event.preventDefault();
  saveNote("baseline");
});
retakeNoteForm.addEventListener("submit", (event) => {
  event.preventDefault();
  saveNote("retake");
});

async function resumePendingAttempt() {
  if (!comparison || processingAttemptId) return;
  for (const role of ["baseline", "retake"]) {
    const attempt = comparison[role];
    if (attempt && attempt.status === "uploaded") {
      await processAttempt(attempt.source_filename, role);
      return;
    }
    if (attempt && attempt.status === "processing") {
      window.setTimeout(async () => {
        try {
          await refreshComparison();
        } catch (error) {
          showStatus(uploadStatus, error.message || "Status comparison tidak dapat diperbarui.", "error");
        }
      }, 1500);
    }
  }
}

document.querySelector("#back-to-baseline").addEventListener("click", () => {
  window.location.assign("/");
});

document.querySelector("#upload-another-retake").addEventListener("click", () => {
  const target = comparison?.retake
    ? document.querySelector("#retake-card")
    : retakeUploadCard;
  target?.scrollIntoView({ behavior: "smooth", block: "start" });
});

document.querySelector("#copy-link-button").addEventListener("click", async (event) => {
  try {
    await navigator.clipboard.writeText(window.location.href);
    event.currentTarget.textContent = "Tautan tersalin";
  } catch {
    event.currentTarget.textContent = "Salin tautan gagal";
  }
});

const initialComparisonId = new URLSearchParams(window.location.search).get("comparison_id");
if (initialComparisonId) {
  comparisonId = initialComparisonId;
  refreshComparison().catch((error) => {
    showStatus(uploadStatus, friendlyError(error.message, "Perbandingan tidak dapat dibuka. Periksa tautan atau mulai dari baseline baru."), "error");
    landingGrid.hidden = false;
    document.querySelector("#upload-section").hidden = false;
  });
}
