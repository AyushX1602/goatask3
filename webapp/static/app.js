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
const summaryText = document.getElementById("summaryText");
const candidateTable = document.getElementById("candidateTable");
const candidateBody = document.getElementById("candidateBody");

let currentRunId = null;

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

captureBtn.addEventListener("click", async () => {
  captureBtn.disabled = true;
  statusEl.textContent = "Capturing and running detect -> align -> embed -> liveness...";

  const blob = await captureFrame();
  const form = new FormData();
  form.append("frame", blob, "frame.jpg");

  try {
    const resp = await fetch("/api/scan", { method: "POST", body: form });
    const data = await resp.json();

    if (!data.face_found) {
      statusEl.textContent = data.message || "No face detected. Try again.";
      captureBtn.disabled = false;
      return;
    }

    currentRunId = data.run_id;
    cropPreview.src = "data:image/png;base64," + data.aligned_crop_png_b64;
    detScoreEl.textContent = data.det_score.toFixed(3);

    const label = data.liveness_label || "unknown";
    livenessBadge.innerHTML = `<span class="badge ${label}">${label.toUpperCase()} (${data.liveness_score.toFixed(3)})</span>`;

    scanResult.style.display = "block";
    searchBtn.disabled = false;
    statusEl.textContent = "Scan complete. Run id: " + data.run_id;
  } catch (err) {
    statusEl.textContent = "Scan failed: " + err.message;
  } finally {
    captureBtn.disabled = false;
  }
});

searchBtn.addEventListener("click", async () => {
  if (!currentRunId) return;
  searchBtn.disabled = true;
  searchStatus.style.display = "block";
  searchStatus.textContent = "Crawling Bluesky (first run only) and scoring candidates against your probe face...";
  resultSummary.style.display = "none";
  candidateTable.style.display = "none";

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
  candidateTable.style.display = "table";

  const isMatch = data.verdict === "MATCH";
  verdictBadge.textContent = data.verdict;
  verdictBadge.className = "badge " + (isMatch ? "match" : "no-match");
  summaryText.textContent =
    `Searched ${data.crawl_size} indexed faces from live Bluesky posts. ` +
    `Threshold ${data.threshold.toFixed(2)}, margin ${data.margin_required.toFixed(2)}.`;

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
