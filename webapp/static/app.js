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

// Maps a (decision, full reason) pair to a short, table-friendly label.
// The full reason is NEVER discarded (R-24) — it moves to the `title`
// tooltip on the cell, not deleted. A row with a decision but no visible
// reason (a bare "—") is exactly the ambiguity R-21 rule 4 forbids: the
// viewer should never have to guess whether a dash means no-face,
// too-small, or fetch-failed.
function shortenReason(decision, reason) {
  const platformMatch = /^([A-Za-z ]+?) serves media only/.exec(reason || "");
  if (decision === "reject-platform-blocked" && platformMatch) {
    return `platform-blocked (${platformMatch[1]})`;
  }
  if (decision === "reject-fetch-failed") return "fetch-failed";
  if (decision === "reject-not-an-image") return "not-an-image";
  if (decision === "reject-no-face") return "no-face";
  if (decision === "reject-face-too-small") return "face-too-small";
  if (decision === "reject-no-image") return "no-image-url";
  if (decision === "reject-domain") return reason || decision;
  if (decision === "reject-below-threshold") return reason || decision;
  return reason || decision;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

function renderResults(data) {
  searchStatus.style.display = "none";
  resultSummary.style.display = "block";

  // Four possible verdicts now (matcher.py): MATCH, NO_MATCH, NO_CANDIDATES,
  // MATCH_NON_SOCIAL. Each gets its OWN headline copy — never a generic
  // "isMatch ? X : Y" — because a generic NO_MATCH-shaped sentence next to
  // a MATCH_NON_SOCIAL caption is exactly the contradiction found live 5
  // Sep 2026: "No match found ... honest outcome" rendered simultaneously
  // with "1 high-scoring non-social source also matched this face".
  const isMatch = data.verdict === "MATCH";
  verdictBadge.textContent = data.verdict;
  verdictBadge.className = "badge " + (isMatch ? "match" : "no-match");
  degradedBadge.style.display = data.degraded_closed_corpus ? "inline-block" : "none";

  const corpusNote = data.degraded_closed_corpus
    ? `No web-detection key configured — searched ${data.crawl_size} indexed faces from a locally-built Bluesky corpus, not the open web.`
    : `Searched the open web via GCV/SerpApi web detection.`;
  summaryText.textContent =
    `${corpusNote} Threshold ${data.threshold.toFixed(2)}, margin ${data.margin_required.toFixed(2)}.`;

  const corroborating = data.candidates.filter((c) => c.decision === "corroborating").length;
  const unverifiable = data.unverifiable_platform_hits || [];

  // R-21: the verdict is the headline for every non-MATCH outcome.
  // Rejected candidates go behind a "show diagnostics" toggle.
  headlineNoMatch.style.display = "block";
  if (data.verdict === "NO_CANDIDATES") {
    headlineNoMatch.innerHTML =
      `<strong>Search did not run.</strong> Every provider failed or returned no ` +
      `candidates to examine — this is NOT the same claim as "no match found"; ` +
      `nothing was actually searched. Check provider availability / API keys.`;
  } else if (data.verdict === "MATCH_NON_SOCIAL") {
    const nonSocial = data.candidates.filter(
      (c) => c.decision === "reject-domain" && c.score !== null && c.score >= data.threshold
    );
    const domains = nonSocial
      .slice(0, 3)
      .map((c) => { try { return new URL(c.page_url).hostname.replace("www.", ""); } catch { return c.source; } })
      .join(", ");
    headlineNoMatch.innerHTML =
      `<strong>Match found, but not on a social platform.</strong> ` +
      `${nonSocial.length} high-scoring source(s) (e.g. ${domains}) matched this face ` +
      `convincingly, but the brief asks for a social media post specifically, so this is ` +
      `reported as its own outcome rather than counted as the match.`;
  } else if (data.verdict === "NO_MATCH") {
    headlineNoMatch.innerHTML =
      `<strong>No match found.</strong> None of ${data.candidates.length} candidate(s) scored ` +
      `above the threshold with sufficient margin. This is a correct, honest outcome — ` +
      `not every face has a matching public post.` +
      (unverifiable.length > 0
        ? ` <strong>${unverifiable.length} platform-blocked hit(s)</strong> ` +
          `(${[...new Set(unverifiable.map((h) => h.source))].join(", ")}) — the search ` +
          `engine reported this image on those platforms, but they serve media only to ` +
          `their own crawler, so the face could not be independently re-verified. ` +
          `Recorded, not accepted.`
        : "");
  } else {
    headlineNoMatch.style.display = "none";
  }

  if (isMatch) {
    if (corroborating > 0) {
      summaryText.textContent +=
        ` Plus ${corroborating} corroborating match(es) above threshold.`;
    }
    // Distinct from MATCH_NON_SOCIAL: this is a REAL accepted match that
    // ALSO has high-scoring non-social corroboration (news coverage etc).
    const supplementary = data.candidates.filter(
      (c) => c.decision === "reject-domain" && c.score !== null && c.score >= 0.5
    );
    if (supplementary.length > 0) {
      const domains = supplementary
        .slice(0, 3)
        .map((c) => { try { return new URL(c.page_url).hostname.replace("www.", ""); } catch { return c.source; } })
        .join(", ");
      summaryText.textContent +=
        ` ${supplementary.length} high-scoring non-social source(s) (e.g. ${domains}) also ` +
        `matched this face but are not social media posts, so they are excluded from the ` +
        `result rather than counted as the match.`;
    }
  }

  diagnostics.style.display = "block";
  diagnosticsSummary.textContent = isMatch
    ? `show diagnostics (${data.candidates.length} candidate(s) examined` +
      (corroborating > 0 ? `, ${corroborating} corroborating` : "") + `)`
    : `show diagnostics (${data.candidates.length} candidate(s) examined — noise, not suggestions)`;
  candidateTable.style.display = "table";

  candidateBody.innerHTML = "";
  if (data.candidates.length === 0) {
    candidateBody.innerHTML = '<tr><td colspan="6" class="empty">No candidates returned.</td></tr>';
    return;
  }

  // Lead with the accept, then corroboration, then everything else in the
  // order the pipeline examined it. Previously rows rendered in raw
  // pipeline order, so a real ACCEPT could sit at row 20 of 44 behind a
  // wall of noise — technically visible, but confusing enough in practice
  // that "if everything's rejected, what's accepted?" was a real question
  // asked against a run that DID match. Decision order is the only thing
  // that changes; scores and reasons are untouched.
  const decisionRank = { ACCEPT: 0, corroborating: 1 };
  const ordered = [...data.candidates].sort(
    (a, b) => (decisionRank[a.decision] ?? 2) - (decisionRank[b.decision] ?? 2)
  );

  for (const c of ordered) {
    const tr = document.createElement("tr");
    if (c.decision === "ACCEPT") tr.classList.add("accept");
    // 'corroborating' is a real match above threshold, just not the
    // top-ranked one. It must NOT be styled as a rejection (same honesty
    // principle as R-21 — never misrepresent what the engine concluded).
    if (c.decision === "corroborating") tr.classList.add("corroborating");

    const scorePct = c.score !== null ? Math.max(0, Math.min(100, c.score * 100)) : 0;
    const scoreCell = c.score !== null
      ? `<span class="score-bar"><span class="score-fill ${c.decision === "ACCEPT" || c.decision === "corroborating" ? "accept" : ""}" style="width:${scorePct}%"></span></span>${c.score.toFixed(4)}`
      : "—";

    const postCell = c.page_url
      ? `<a href="${c.page_url}" target="_blank" rel="noopener">${c.page_url.replace("https://", "")}</a>`
      : "—";

    // Reason cell: a short, scannable label in the cell itself, full
    // sentence in a `title` tooltip. Found live 5 Sep 2026: the
    // platform-blocked reason sentence is long enough to turn a table row
    // into a multi-line billboard, which defeats the point of a scannable
    // diagnostics table on a recording. Every row still carries the FULL
    // reason (never dropped, R-24) — just not inline at full length.
    const shortReason = shortenReason(c.decision, c.reason);

    tr.innerHTML = `
      <td>${c.rank + 1}</td>
      <td>${scoreCell}</td>
      <td>${c.source}</td>
      <td>${postCell}</td>
      <td>${c.decision}</td>
      <td class="reason-cell" style="color:#8b949e;" title="${escapeHtml(c.reason)}">${shortReason}</td>
    `;
    candidateBody.appendChild(tr);
  }
}

initCamera();
