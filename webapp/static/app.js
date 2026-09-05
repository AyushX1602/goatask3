// Local demo UI logic. Talks only to the /api/* endpoints in server.py,
// which wrap pipeline.face and pipeline.search — no scoring or matching
// logic exists in this file (architecture.md 5a).

const video = document.getElementById("video");
const captureBtn = document.getElementById("captureBtn");
const searchBtn = document.getElementById("searchBtn");
const statusEl = document.getElementById("status");
const scanResult = document.getElementById("scanResult");
const cropPreview = document.getElementById("cropPreview");
const detScoreEl = document.getElementById("detScore");
const livenessBadge = document.getElementById("livenessBadge");
const searchStatus = document.getElementById("searchStatus");
const resultSummary = document.getElementById("resultSummary");
const verdictBadge = document.getElementById("verdictBadge");
const degradedBadge = document.getElementById("degradedBadge");
const summaryText = document.getElementById("summaryText");
const candidateTable = document.getElementById("candidateTable");
const candidateBody = document.getElementById("candidateBody");
const diagnostics = document.getElementById("diagnostics");
const diagnosticsSummary = document.getElementById("diagnosticsSummary");
const headlineNoMatch = document.getElementById("headlineNoMatch");

// Input mode tabs (D-19)
const tabWebcam = document.getElementById("tabWebcam");
const tabUpload = document.getElementById("tabUpload");
const panelWebcam = document.getElementById("panelWebcam");
const panelUpload = document.getElementById("panelUpload");
const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const uploadPreview = document.getElementById("uploadPreview");
const uploadBtn = document.getElementById("uploadBtn");

let currentRunId = null;
let pendingUploadFile = null;

// ---------------- input mode tabs ----------------

function activateTab(mode) {
  const webcamMode = mode === "webcam";
  tabWebcam.classList.toggle("active", webcamMode);
  tabUpload.classList.toggle("active", !webcamMode);
  panelWebcam.classList.toggle("active", webcamMode);
  panelUpload.classList.toggle("active", !webcamMode);
}
tabWebcam.addEventListener("click", () => activateTab("webcam"));
tabUpload.addEventListener("click", () => activateTab("upload"));

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  if (e.dataTransfer.files.length) setUploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) setUploadFile(fileInput.files[0]);
});

function setUploadFile(file) {
  pendingUploadFile = file;
  uploadPreview.src = URL.createObjectURL(file);
  uploadPreview.style.display = "block";
  uploadBtn.disabled = false;
}

// ---------------- webcam ----------------

async function initCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
    video.srcObject = stream;
  } catch (err) {
    statusEl.textContent = "Camera access denied or unavailable: " + err.message;
  }
}

function captureFrame() {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
}

// ---------------- shared scan handling ----------------

async function submitProbe(endpoint, blob, filename, triggerBtn, extraFields = {}) {
  triggerBtn.disabled = true;
  statusEl.textContent = "Running detect -> quality gate -> align -> embed...";

  const form = new FormData();
  form.append("frame", blob, filename);
  for (const [k, v] of Object.entries(extraFields)) form.append(k, v);

  try {
    const resp = await fetch(endpoint, { method: "POST", body: form });
    const data = await resp.json();

    if (!data.face_found) {
      statusEl.textContent = data.message || "No usable face found. Try again.";
      triggerBtn.disabled = false;
      return;
    }

    currentRunId = data.run_id;
    cropPreview.src = "data:image/png;base64," + data.aligned_crop_png_b64;
    detScoreEl.textContent = data.det_score.toFixed(3);

    // Three-state liveness (R-22, D-20): LIVE / SPOOF / not_applicable.
    // An upload must never render in the same style as a real LIVE pass.
    const label = data.liveness_label || "unknown";
    let liveText = label.toUpperCase();
    if (label === "not_applicable") {
      liveText = "N/A — provenance unverified";
    } else if (data.liveness_score !== null && data.liveness_score !== undefined) {
      liveText += ` (${data.liveness_score.toFixed(3)})`;
    }
    livenessBadge.innerHTML = `<span class="badge ${label}">${liveText}</span>`;

    scanResult.style.display = "block";
    searchBtn.disabled = false;
    statusEl.textContent = "Scan complete. Run id: " + data.run_id;
  } catch (err) {
    statusEl.textContent = "Scan failed: " + err.message;
  } finally {
    triggerBtn.disabled = false;
  }
}

captureBtn.addEventListener("click", async () => {
  const blob = await captureFrame();
  await submitProbe("/api/scan", blob, "frame.jpg", captureBtn);
});

const publicUrlInput = document.getElementById("publicUrlInput");

uploadBtn.addEventListener("click", async () => {
  if (!pendingUploadFile) return;
  const extra = publicUrlInput.value.trim() ? { public_image_url: publicUrlInput.value.trim() } : {};
  await submitProbe("/api/upload", pendingUploadFile, pendingUploadFile.name, uploadBtn, extra);
});

// ---------------- search ----------------

searchBtn.addEventListener("click", async () => {
  if (!currentRunId) return;
  searchBtn.disabled = true;
  searchStatus.style.display = "block";
  searchStatus.textContent = "Searching (web detection primary, Bluesky keyless fallback) and re-verifying every candidate against your probe face...";
  resultSummary.style.display = "none";
  headlineNoMatch.style.display = "none";
  diagnostics.style.display = "none";
  diagnostics.open = false;

  try {
    const resp = await fetch(`/api/search/${currentRunId}`, { method: "POST" });
    const data = await resp.json();
    renderResults(data);
  } catch (err) {
    searchStatus.textContent = "Search failed: " + err.message;
  } finally {
    searchBtn.disabled = false;
  }
});

function renderResults(data) {
  searchStatus.style.display = "none";
  resultSummary.style.display = "block";

  const isMatch = data.verdict === "MATCH";
  verdictBadge.textContent = data.verdict;
  verdictBadge.className = "badge " + (isMatch ? "match" : "no-match");
  degradedBadge.style.display = data.degraded_closed_corpus ? "inline-block" : "none";

  const corpusNote = data.degraded_closed_corpus
    ? `No web-detection key configured — searched ${data.crawl_size} indexed faces from a locally-built Bluesky corpus, not the open web.`
    : `Searched the open web via GCV/SerpApi web detection.`;
  summaryText.textContent =
    `${corpusNote} Threshold ${data.threshold.toFixed(2)}, margin ${data.margin_required.toFixed(2)}.`;

  // R-21: on NO_MATCH the verdict is the headline. Rejected candidates go
  // behind a "show diagnostics" toggle, never presented as ranked suggestions.
  if (!isMatch) {
    headlineNoMatch.style.display = "block";
    headlineNoMatch.innerHTML =
      `<strong>No match found.</strong> None of ${data.candidates.length} candidate(s) scored ` +
      `above the threshold with sufficient margin. This is a correct, honest outcome — ` +
      `not every face has a matching public post.`;
  } else {
    headlineNoMatch.style.display = "none";
  }

  diagnostics.style.display = "block";
  diagnosticsSummary.textContent = isMatch
    ? `show diagnostics (${data.candidates.length} candidate(s) examined, including rejects)`
    : `show diagnostics (${data.candidates.length} rejected candidate(s) — noise, not suggestions)`;
  candidateTable.style.display = "table";

  candidateBody.innerHTML = "";
  if (data.candidates.length === 0) {
    candidateBody.innerHTML = '<tr><td colspan="6" class="empty">No candidates returned.</td></tr>';
    return;
  }

  for (const c of data.candidates) {
    const tr = document.createElement("tr");
    if (c.decision === "ACCEPT") tr.classList.add("accept");

    const scorePct = c.score !== null ? Math.max(0, Math.min(100, c.score * 100)) : 0;
    const scoreCell = c.score !== null
      ? `<span class="score-bar"><span class="score-fill ${c.decision === "ACCEPT" ? "accept" : ""}" style="width:${scorePct}%"></span></span>${c.score.toFixed(4)}`
      : "—";

    const postCell = c.page_url
      ? `<a href="${c.page_url}" target="_blank" rel="noopener">${c.page_url.replace("https://", "")}</a>`
      : "—";

    tr.innerHTML = `
      <td>${c.rank + 1}</td>
      <td>${scoreCell}</td>
      <td>${c.source}</td>
      <td>${postCell}</td>
      <td>${c.decision}</td>
      <td style="color:#8b949e;">${c.reason}</td>
    `;
    candidateBody.appendChild(tr);
  }
}

initCamera();
