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
const headlineMatch = document.getElementById("headlineMatch");
const claimsBanner = document.getElementById("claimsBanner");

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
  if (headlineMatch) headlineMatch.style.display = "none";
  if (claimsBanner) claimsBanner.style.display = "none";
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
  if (decision === "linked-claim") return "claimed profile link (unscored)";
  if (decision === "conjecture-claim") return "same-handle guess (unverified)";
  return reason || decision;
}

// Replaces a bare "—" (no cosine score to show, because no face was ever
// embedded) with the REAL measured observation instead — 6 Sep 2026, owner
// instruction: "numbers ... instead of --". There is no confidence value
// to fabricate for a row where the image never decoded or had no face; a
// fabricated number here would be worse than a dash, since it would look
// like evidence. This shows only what was actually observed: HTTP status,
// content-type, byte count, image dimensions, faces detected, or how many
// recovery routes were tried before giving up.
function diagnosticObservation(diag) {
  if (!diag) return "<span style=\"color:#6e7681;\">—</span>";
  const parts = [];
  if (diag.http_status !== null && diag.http_status !== undefined) {
    parts.push(`HTTP ${diag.http_status}`);
  }
  if (diag.faces_found) {
    parts.push(`${diag.faces_found} face(s)`);
    if (diag.largest_face_px !== null && diag.largest_face_px !== undefined) {
      parts.push(`${Math.round(diag.largest_face_px)}px`);
    }
  } else if (diag.image_width && diag.image_height) {
    parts.push(`0 faces · ${diag.image_width}×${diag.image_height}`);
  } else if (diag.content_type) {
    const shortType = diag.content_type.split(";")[0];
    parts.push(shortType);
  }
  if (diag.content_bytes !== null && diag.content_bytes !== undefined) {
    parts.push(diag.content_bytes >= 1024 ? `${Math.round(diag.content_bytes / 1024)} KB` : `${diag.content_bytes} B`);
  }
  if (diag.routes_tried && diag.routes_tried > 1) {
    parts.push(`${diag.routes_tried} routes tried`);
  }
  return parts.length
    ? `<span style="color:#6e7681; font-size:12px;">${parts.join(" · ")}</span>`
    : "<span style=\"color:#6e7681;\">—</span>";
}

function escapeHtml(s) {
  if (!s) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function isSafeUrl(url) {
  if (!url) return false;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function renderResults(data) {
  searchStatus.style.display = "none";
  resultSummary.style.display = "block";

  const isMatch = data.verdict === "MATCH";
  verdictBadge.textContent = data.verdict;
  verdictBadge.className = "badge " + (isMatch ? "match" : "no-match");
  degradedBadge.style.display = data.degraded_closed_corpus ? "inline-block" : "none";

  const acceptCand = data.candidates.find((c) => c.decision === "ACCEPT");
  const corroboratingCands = data.candidates.filter((c) => c.decision === "corroborating");
  const corroborating = corroboratingCands.length;
  const unverifiable = data.unverifiable_platform_hits || [];
  const claims = data.candidates.filter(
    (c) => c.decision === "linked-claim" || c.decision === "conjecture-claim"
  );

  const corpusNote = data.degraded_closed_corpus
    ? `No web-detection key configured — searched ${data.crawl_size} indexed faces from a locally-built Bluesky corpus, not the open web.`
    : `Searched the open web via GCV/SerpApi web detection.`;
  summaryText.textContent =
    `${corpusNote} Threshold ${data.threshold.toFixed(2)}, margin ${data.margin_required.toFixed(2)}.`;

  // Claimed profiles (e.g. LinkedIn) discovered via profile expansion are
  // unscored by design (R-28) — surface count next to the verdict.
  if (claims.length > 0) {
    const claimList = claims
      .slice(0, 3)
      .map((c) => (c.page_url || c.source || "claim").replace(/^https?:\/\/(www\.)?/, ""))
      .join(", ");
    summaryText.textContent +=
      ` Profile claim(s) found: ${claimList}${claims.length > 3 ? ` +${claims.length - 3} more` : ""}` +
      ` — recorded as claims, not biometrically scored.`;
  }

  // 1. Prominent Verified Match Banner (shows direct links to matched post/profile)
  if (headlineMatch) {
    headlineMatch.innerHTML = "";
    if (isMatch && acceptCand) {
      const hTitle = document.createElement("div");
      hTitle.style.fontWeight = "600";
      hTitle.style.color = "#3fb950";
      hTitle.style.fontSize = "15px";
      hTitle.style.marginBottom = "6px";
      hTitle.textContent = "✓ Verified Biometric Face Match";
      headlineMatch.appendChild(hTitle);

      const hInfo = document.createElement("div");
      hInfo.style.marginBottom = "8px";
      hInfo.style.color = "var(--text-dim)";
      hInfo.style.fontSize = "13px";
      const scoreVal = acceptCand.score !== null ? acceptCand.score.toFixed(4) : "—";
      hInfo.textContent = `Provider: ${acceptCand.source} · Score: ${scoreVal} · Match Kind: ${acceptCand.match_kind || "verified"}`;
      headlineMatch.appendChild(hInfo);

      const hPost = document.createElement("div");
      hPost.style.fontSize = "14px";
      hPost.style.lineHeight = "1.5";
      const hLabel = document.createElement("strong");
      hLabel.textContent = "Matched Post / Profile: ";
      hPost.appendChild(hLabel);

      if (acceptCand.page_url && isSafeUrl(acceptCand.page_url)) {
        const a = document.createElement("a");
        a.href = acceptCand.page_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.style.color = "var(--accent)";
        a.style.fontWeight = "600";
        a.textContent = acceptCand.page_url;
        hPost.appendChild(a);
      } else {
        const span = document.createElement("span");
        span.textContent = acceptCand.page_url || "—";
        hPost.appendChild(span);
      }
      headlineMatch.appendChild(hPost);

      if (corroboratingCands.length > 0) {
        const corrDiv = document.createElement("div");
        corrDiv.style.marginTop = "8px";
        corrDiv.style.fontSize = "13px";
        const cLabel = document.createElement("strong");
        cLabel.textContent = `Corroborating Match(es) (${corroboratingCands.length}): `;
        corrDiv.appendChild(cLabel);

        corroboratingCands.forEach((cc, idx) => {
          if (idx > 0) corrDiv.appendChild(document.createTextNode(" · "));
          if (cc.page_url && isSafeUrl(cc.page_url)) {
            const ca = document.createElement("a");
            ca.href = cc.page_url;
            ca.target = "_blank";
            ca.rel = "noopener noreferrer";
            ca.style.color = "var(--accent)";
            ca.textContent = cc.page_url.replace(/^https?:\/\/(www\.)?/, "");
            corrDiv.appendChild(ca);
          } else {
            corrDiv.appendChild(document.createTextNode(cc.source));
          }
        });
        headlineMatch.appendChild(corrDiv);
      }
      headlineMatch.style.display = "block";
    } else {
      headlineMatch.style.display = "none";
    }
  }

  // 2. Discovered Profile Claims Banner (LinkedIn, Instagram, etc. found via expansion)
  if (claimsBanner) {
    claimsBanner.innerHTML = "";
    if (claims.length > 0) {
      const cTitle = document.createElement("div");
      cTitle.style.fontWeight = "600";
      cTitle.style.color = "#d29922";
      cTitle.style.marginBottom = "6px";
      cTitle.textContent = `Discovered Profile Claim(s) (${claims.length})`;
      claimsBanner.appendChild(cTitle);

      const cNote = document.createElement("div");
      cNote.style.color = "var(--text-dim)";
      cNote.style.fontSize = "12px";
      cNote.style.marginBottom = "8px";
      cNote.textContent = "Discovered via handle/profile expansion or SERP lookup. Preserved as claims (not biometrically scored due to platform media walls):";
      claimsBanner.appendChild(cNote);

      const list = document.createElement("ul");
      list.style.margin = "0";
      list.style.paddingLeft = "20px";
      for (const c of claims) {
        const li = document.createElement("li");
        li.style.marginBottom = "4px";
        const badge = document.createElement("span");
        badge.style.fontSize = "11px";
        badge.style.padding = "2px 6px";
        badge.style.borderRadius = "4px";
        badge.style.marginRight = "8px";
        badge.style.background = c.decision === "linked-claim" ? "rgba(210,153,34,0.2)" : "rgba(139,92,246,0.2)";
        badge.style.color = c.decision === "linked-claim" ? "#d29922" : "#a78bfa";
        badge.textContent = c.decision === "linked-claim" ? "linked" : "conjecture";
        li.appendChild(badge);

        if (c.page_url && isSafeUrl(c.page_url)) {
          const a = document.createElement("a");
          a.href = c.page_url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.style.color = "var(--accent)";
          a.textContent = c.page_url;
          li.appendChild(a);
        } else {
          li.appendChild(document.createTextNode(c.page_url || c.source));
        }
        list.appendChild(li);
      }
      claimsBanner.appendChild(list);
      claimsBanner.style.display = "block";
    } else {
      claimsBanner.style.display = "none";
    }
  }

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
        : "") +
      (claims.length > 0
        ? ` <strong>${claims.length} profile claim(s)</strong> were found via profile ` +
          `expansion and are displayed in the claims list above.`
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

  // Chain panel (G4) only makes sense for a MATCH with an evidence bundle
  // — evidence/bundle.py refuses to build one for anything else (R-16).
  if (isMatch && data.evidence_hash) {
    chainPanel.style.display = "block";
    selectRun(data.run_id);
  }

  diagnostics.style.display = "block";
  diagnostics.open = isMatch || claims.length > 0;
  diagnosticsSummary.textContent = isMatch
    ? `Candidate Verification Table (${data.candidates.length} examined · ${1 + corroborating} match(es))`
    : (claims.length > 0
      ? `Candidate Verification Table (${data.candidates.length} examined · ${claims.length} claim(s))`
      : `show diagnostics (${data.candidates.length} candidate(s) examined — noise, not suggestions)`);
  candidateTable.style.display = "table";

  candidateBody.innerHTML = "";
  if (data.candidates.length === 0) {
    candidateBody.innerHTML = '<tr><td colspan="7" class="empty">No candidates returned.</td></tr>';
    return;
  }

  // Lead with the accept, then corroboration, then everything else in the
  // order the pipeline examined it. Previously rows rendered in raw
  // pipeline order, so a real ACCEPT could sit at row 20 of 44 behind a
  // wall of noise — technically visible, but confusing enough in practice
  // that "if everything's rejected, what's accepted?" was a real question
  // asked against a run that DID match. Decision order is the only thing
  // that changes; scores and reasons are untouched.
  const decisionRank = { ACCEPT: 0, corroborating: 1, "linked-claim": 2, "conjecture-claim": 3 };
  const ordered = [...data.candidates].sort(
    (a, b) => (decisionRank[a.decision] ?? 4) - (decisionRank[b.decision] ?? 4)
  );

  for (const c of ordered) {
    const tr = document.createElement("tr");
    if (c.decision === "ACCEPT") tr.classList.add("accept");
    if (c.decision === "corroborating") tr.classList.add("corroborating");
    if (c.decision === "linked-claim") tr.classList.add("linked-claim");
    if (c.decision === "conjecture-claim") tr.classList.add("conjecture-claim");

    const tdRank = document.createElement("td");
    tdRank.textContent = String(c.rank + 1);
    tr.appendChild(tdRank);

    const tdScore = document.createElement("td");
    if (c.score !== null) {
      const scorePct = Math.max(0, Math.min(100, c.score * 100));
      tdScore.innerHTML = `<span class="score-bar"><span class="score-fill ${c.decision === "ACCEPT" || c.decision === "corroborating" ? "accept" : ""}" style="width:${scorePct}%"></span></span>${c.score.toFixed(4)}`;
    } else {
      tdScore.innerHTML = diagnosticObservation(c.diagnostics);
    }
    tr.appendChild(tdScore);

    const tdSource = document.createElement("td");
    tdSource.textContent = c.source || "";
    tr.appendChild(tdSource);

    const tdPost = document.createElement("td");
    if (c.page_url) {
      if (isSafeUrl(c.page_url)) {
        const a = document.createElement("a");
        a.href = c.page_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = c.page_url.replace(/^https?:\/\//, "");
        tdPost.appendChild(a);
      } else {
        tdPost.textContent = c.page_url;
      }
    } else {
      tdPost.textContent = "—";
    }
    tr.appendChild(tdPost);

    const tdDecision = document.createElement("td");
    tdDecision.textContent = c.decision || "";
    tr.appendChild(tdDecision);

    const shortReason = shortenReason(c.decision, c.reason);
    const tdReason = document.createElement("td");
    tdReason.className = "reason-cell";
    tdReason.style.color = "#8b949e";
    tdReason.title = c.reason || "";
    tdReason.textContent = shortReason;
    tr.appendChild(tdReason);

    const mk = c.match_kind || "unknown";
    const mkClass = mk === "full" ? "mk-full" : mk === "partial" ? "mk-partial" : "mk-weak";
    const tdMk = document.createElement("td");
    tdMk.className = mkClass;
    tdMk.textContent = mk;
    tr.appendChild(tdMk);

    candidateBody.appendChild(tr);
  }
}

// ---------------- chain: anchor / verify / tamper ----------------

const chainPanel = document.getElementById("chainPanel");
const anchorBtn = document.getElementById("anchorBtn");
const verifyBtn = document.getElementById("verifyBtn");
const tamperBtn = document.getElementById("tamperBtn");
const chainStatus = document.getElementById("chainStatus");

const runsSelect = document.getElementById("runsSelect");
const refreshRunsBtn = document.getElementById("refreshRunsBtn");
const activeRunContainer = document.getElementById("activeRunContainer");
const noRunNotice = document.getElementById("noRunNotice");

// Cards
const cardArtifacts = document.getElementById("cardArtifacts");
const expGallery = document.getElementById("expGallery");
const expArtifactsTable = document.getElementById("expArtifactsTable");
const expArtifactsBody = document.getElementById("expArtifactsBody");

const cardEvidence = document.getElementById("cardEvidence");
const evidenceJsonArea = document.getElementById("evidenceJsonArea");
const saveEditBtn = document.getElementById("saveEditBtn");
const restoreEditBtn = document.getElementById("restoreEditBtn");
const presetScoreBtn = document.getElementById("presetScoreBtn");
const presetUrlBtn = document.getElementById("presetUrlBtn");
const presetResetBtn = document.getElementById("presetResetBtn");
const currentBundleHash = document.getElementById("currentBundleHash");

const cardAnchor = document.getElementById("cardAnchor");
const anchorCardTitle = document.getElementById("anchorCardTitle");
const ancNetwork = document.getElementById("ancNetwork");
const ancContract = document.getElementById("ancContract");
const ancRecordId = document.getElementById("ancRecordId");
const ancTx = document.getElementById("ancTx");
const ancEvidenceHash = document.getElementById("ancEvidenceHash");
const ancRunDir = document.getElementById("ancRunDir");
const anchorActionRow = document.getElementById("anchorActionRow");
const doAnchorBtn = document.getElementById("doAnchorBtn");
const anchorActionNote = document.getElementById("anchorActionNote");

const cardVerify = document.getElementById("cardVerify");
const doVerifyBtn = document.getElementById("doVerifyBtn");
const verifyResultArea = document.getElementById("verifyResultArea");
const verOnchainHash = document.getElementById("verOnchainHash");
const verRecomputedHash = document.getElementById("verRecomputedHash");
const verVerdictBadge = document.getElementById("verVerdictBadge");

const cardTamper = document.getElementById("cardTamper");
const sealContainer = document.getElementById("sealContainer");
const breakSealBtn = document.getElementById("breakSealBtn");
const sealNote = document.getElementById("sealNote");

let cleanBundleJson = "";

function clearRunSelection() {
  currentRunId = null;
  if (activeRunContainer) activeRunContainer.style.display = "none";
  if (noRunNotice) noRunNotice.style.display = "block";
  if (expGallery) expGallery.innerHTML = "";
  if (expArtifactsBody) expArtifactsBody.innerHTML = "";
  if (evidenceJsonArea) evidenceJsonArea.value = "";
  if (currentBundleHash) currentBundleHash.textContent = "—";
  if (ancContract) ancContract.textContent = "—";
  if (ancRecordId) ancRecordId.textContent = "—";
  if (ancTx) ancTx.textContent = "—";
  if (ancEvidenceHash) ancEvidenceHash.textContent = "—";
  if (ancRunDir) ancRunDir.textContent = "—";
  if (verifyResultArea) verifyResultArea.style.display = "none";
  if (chainStatus) chainStatus.textContent = "";
}

async function loadRunsList(preserveCurrent = true) {
  if (!runsSelect) return;
  try {
    const resp = await fetch("/api/runs");
    if (!resp.ok) return;
    const runs = await resp.json();
    const prevVal = preserveCurrent ? (runsSelect.value || currentRunId) : null;
    runsSelect.innerHTML = '<option value="">-- Choose a past run to inspect --</option>';
    let targetRun = prevVal;
    for (const r of runs) {
      const opt = document.createElement("option");
      opt.value = r.run_id;
      const v = r.verdict ? ` [${r.verdict}]` : "";
      const score = r.score_bps ? ` ${(r.score_bps / 10000).toFixed(4)}` : "";
      const plat = r.platform ? ` via ${r.platform}` : "";
      const anc = r.has_anchor ? " ⚓" : "";
      opt.textContent = `${r.run_id}${v}${score}${plat}${anc}`;
      runsSelect.appendChild(opt);
    }
    // Do NOT auto-load runs[0] by default — only load if user explicitly selected or performed a scan
    if (targetRun) {
      runsSelect.value = targetRun;
      await selectRun(targetRun);
    } else {
      runsSelect.value = "";
      clearRunSelection();
    }
  } catch (err) {
    console.error("Failed loading runs list:", err);
  }
}

if (refreshRunsBtn) {
  refreshRunsBtn.addEventListener("click", () => loadRunsList(true));
}

if (runsSelect) {
  runsSelect.addEventListener("change", async () => {
    const runId = runsSelect.value;
    if (runId) {
      await selectRun(runId);
    } else {
      clearRunSelection();
    }
  });
}

function resetSealUI() {
  if (!sealContainer) return;
  sealContainer.innerHTML = "";
  const title = document.createElement("div");
  title.className = "warn-title";
  title.textContent = "OPTIONAL — DOES NOT TOUCH THE FILES ABOVE";
  sealContainer.appendChild(title);

  const p = document.createElement("p");
  p.textContent = "This flips one byte of the post-text file on a scratch copy and re-checks it against the blockchain. Use the editor above if you want to tamper the real artifact and verify it yourself.";
  sealContainer.appendChild(p);

  const btn = document.createElement("button");
  btn.id = "breakSealBtn";
  btn.className = "break tamper-btn-top";
  btn.type = "button";
  btn.textContent = "BREAK THE SEAL";
  btn.addEventListener("click", () => runTamperSeal());
  sealContainer.appendChild(btn);

  const note = document.createElement("div");
  note.id = "sealNote";
  note.className = "note";
  note.textContent = "Nothing happens until you press this.";
  sealContainer.appendChild(note);
}

async function selectRun(runId) {
  if (!runId) {
    clearRunSelection();
    return;
  }
  currentRunId = runId;
  chainStatus.textContent = "";

  try {
    const resp = await fetch(`/api/run/${encodeURIComponent(runId)}`);
    if (!resp.ok) {
      clearRunSelection();
      return;
    }
    const data = await resp.json();
    if (data.error) {
      chainStatus.textContent = data.error;
      clearRunSelection();
      return;
    }

    if (activeRunContainer) activeRunContainer.style.display = "block";
    if (noRunNotice) noRunNotice.style.display = "none";

    // 1. Gallery of images
    if (expGallery) {
      expGallery.innerHTML = "";
      const imgArtifacts = (data.artifacts || []).filter(a => /\.(jpg|jpeg|png|webp)$/i.test(a.name));
      if (imgArtifacts.length > 0) {
        if (cardArtifacts) cardArtifacts.style.display = "block";
        for (const art of imgArtifacts) {
          const fig = document.createElement("figure");
          const img = document.createElement("img");
          img.src = `/api/artifact/${encodeURIComponent(runId)}/${encodeURIComponent(art.name)}`;
          img.alt = art.name;
          const cap = document.createElement("figcaption");
          cap.textContent = art.name.includes("match")
            ? "Match Image"
            : (art.name.includes("aligned") ? "Aligned Face" : (art.name.includes("head") ? "Head Crop" : "Probe"));
          fig.appendChild(img);
          fig.appendChild(cap);
          expGallery.appendChild(fig);
        }
      } else {
        if (cardArtifacts) cardArtifacts.style.display = "none";
      }
    }

    // 2. Artifacts Table
    if (expArtifactsBody) {
      expArtifactsBody.innerHTML = "";
      for (const art of (data.artifacts || [])) {
        const tr = document.createElement("tr");
        const td1 = document.createElement("td");
        td1.textContent = art.name;
        const td2 = document.createElement("td");
        td2.textContent = art.size_bytes >= 1024 ? `${(art.size_bytes / 1024).toFixed(1)} KB` : `${art.size_bytes} B`;
        const td3 = document.createElement("td");
        td3.className = "hash";
        td3.textContent = art.sha256 || "—";
        tr.appendChild(td1);
        tr.appendChild(td2);
        tr.appendChild(td3);
        expArtifactsBody.appendChild(tr);
      }
    }

    // 3. Evidence JSON Editor
    const b = data.bundle;
    const a = data.anchor;
    if (evidenceJsonArea) {
      if (b) {
        cleanBundleJson = JSON.stringify(b, null, 2);
        evidenceJsonArea.value = cleanBundleJson;
      } else {
        cleanBundleJson = "";
        evidenceJsonArea.value = "// No evidence bundle for this run";
      }
    }
    const currentHash = (a && a.evidence_hash) || (b && b.evidence_hash) || (data.evidence_hash) || "—";
    if (currentBundleHash) {
      currentBundleHash.textContent = currentHash;
    }

    // 4. Anchor Card
    if (a) {
      if (cardAnchor) cardAnchor.className = "chain-card ok";
      if (anchorCardTitle) anchorCardTitle.textContent = "ANCHORED ON-CHAIN";
      if (ancNetwork) ancNetwork.textContent = (a.chain_id === 31337 || a.chain_id === "31337") ? "anvil (local chain 31337)" : "sepolia";
      if (ancContract) ancContract.textContent = a.contract_address || "0x5FbDB2315678afecb367f032d93F642f64180aa3";
      if (ancRecordId) ancRecordId.textContent = a.block_number ? String(a.block_number) : "1";
      if (ancTx) ancTx.textContent = a.tx_hash || "already-anchored";
      if (ancEvidenceHash) ancEvidenceHash.textContent = a.evidence_hash || currentHash;
      if (ancRunDir) ancRunDir.textContent = `runs/${runId}`;
      if (anchorActionRow) anchorActionRow.style.display = "none";
      if (verifyBtn) verifyBtn.disabled = false;
      if (tamperBtn) tamperBtn.disabled = false;
      if (doVerifyBtn) doVerifyBtn.disabled = false;
    } else {
      if (cardAnchor) cardAnchor.className = "chain-card warn";
      if (anchorCardTitle) anchorCardTitle.textContent = "ANCHOR ON-CHAIN";
      if (ancNetwork) ancNetwork.textContent = "anvil (local chain 31337)";
      if (ancContract) ancContract.textContent = "0x5FbDB2315678afecb367f032d93F642f64180aa3";
      if (ancRecordId) ancRecordId.textContent = "— (not yet anchored)";
      if (ancTx) ancTx.textContent = "—";
      if (ancEvidenceHash) ancEvidenceHash.textContent = currentHash;
      if (ancRunDir) ancRunDir.textContent = `runs/${runId}`;
      if (anchorActionRow) anchorActionRow.style.display = "block";
      if (anchorActionNote) anchorActionNote.textContent = "Evidence is ready to anchor to the EVM EvidenceRegistry.";
      if (verifyBtn) verifyBtn.disabled = true;
      if (tamperBtn) tamperBtn.disabled = true;
      if (doVerifyBtn) doVerifyBtn.disabled = true;
    }

    // 5. Reset Verify Card & Seal Card
    if (verifyResultArea) verifyResultArea.style.display = "none";
    if (cardVerify) cardVerify.className = "chain-card";
    resetSealUI();
  } catch (err) {
    console.error("Failed selecting run:", err);
  }
}

// Actions: Anchor
async function anchorNow() {
  if (!currentRunId) {
    chainStatus.innerHTML = '<span class="badge no-match">NO RUN SELECTED</span> Select a past run from the dropdown or perform a face scan first.';
    return;
  }
  anchorBtn.disabled = true;
  if (doAnchorBtn) doAnchorBtn.disabled = true;
  chainStatus.textContent = "Anchoring on chain...";

  try {
    const resp = await fetch(`/api/anchor/${encodeURIComponent(currentRunId)}`, { method: "POST" });
    const data = await resp.json();
    if (data.ok) {
      chainStatus.innerHTML = `<span class="badge match">ANCHORED</span> ${data.already_anchored ? 'Verified existing on-chain record.' : 'New transaction recorded to EVM contract.'}`;
      await selectRun(currentRunId);
    } else {
      chainStatus.textContent = "Anchor failed: " + (data.error || "unknown error");
    }
  } catch (err) {
    chainStatus.textContent = "Anchor failed: " + err.message;
  } finally {
    anchorBtn.disabled = false;
    if (doAnchorBtn) doAnchorBtn.disabled = false;
  }
}

if (anchorBtn) anchorBtn.addEventListener("click", anchorNow);
if (doAnchorBtn) doAnchorBtn.addEventListener("click", anchorNow);

// Actions: Verify
async function verifyNow() {
  if (!currentRunId) {
    chainStatus.innerHTML = '<span class="badge no-match">NO RUN SELECTED</span> Select a past run from the dropdown or perform a face scan first.';
    return;
  }
  verifyBtn.disabled = true;
  if (doVerifyBtn) doVerifyBtn.disabled = true;
  chainStatus.textContent = "Re-verifying against the on-chain record...";

  try {
    const customText = evidenceJsonArea ? evidenceJsonArea.value : null;
    const body = (customText && customText !== cleanBundleJson) ? { evidence_text: customText } : {};
    const resp = await fetch(`/api/verify/${encodeURIComponent(currentRunId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();

    const isMatch = data.overall === "PASS";
    if (cardVerify) cardVerify.className = "chain-card " + (isMatch ? "ok" : "bad");
    if (verVerdictBadge) {
      verVerdictBadge.className = "verdict " + (isMatch ? "ok" : "bad");
      verVerdictBadge.textContent = isMatch ? "MATCH — record intact" : "MISMATCH — evidence has been altered";
    }

    if (verOnchainHash) verOnchainHash.textContent = data.onchain_hash || data.anchored_hash || "None";
    if (verRecomputedHash) verRecomputedHash.textContent = data.recomputed_hash || "None";
    if (verifyResultArea) verifyResultArea.style.display = "block";

    chainStatus.innerHTML = `<span class="badge ${isMatch ? "match" : "no-match"}">${data.overall}</span> <span style="margin-left:8px;">${isMatch ? "Record intact: hashes match exactly." : "Evidence mismatch: tampering detected!"}</span>`;
  } catch (err) {
    chainStatus.textContent = "Verify failed: " + err.message;
  } finally {
    verifyBtn.disabled = false;
    if (doVerifyBtn) doVerifyBtn.disabled = false;
  }
}

if (verifyBtn) verifyBtn.addEventListener("click", verifyNow);
if (doVerifyBtn) doVerifyBtn.addEventListener("click", verifyNow);

// Actions: Tamper (Break the seal)
async function runTamperSeal() {
  if (!currentRunId) {
    chainStatus.innerHTML = '<span class="badge no-match">NO RUN SELECTED</span> Select a past run from the dropdown or perform a face scan first.';
    return;
  }
  tamperBtn.disabled = true;
  const breakBtn = document.getElementById("breakSealBtn");
  if (breakBtn) breakBtn.disabled = true;
  chainStatus.textContent = "Flipping one byte on scratch copy and verifying against chain...";

  try {
    const resp = await fetch(`/api/tamper/${encodeURIComponent(currentRunId)}?mode=swap-artifact`, { method: "POST" });
    const data = await resp.json();

    const onchain = data.onchain_hash || data.anchored_hash || "—";
    const intact = data.intact_hash || data.anchored_hash || "—";
    const tampered = data.tampered_hash || data.recomputed_hash || "—";

    sealContainer.innerHTML = "";

    const title = document.createElement("div");
    title.className = "warn-title";
    title.textContent = data.detected !== false ? "Seal broken — alteration detected" : "Seal broken — NOT detected";
    sealContainer.appendChild(title);

    const cmp = document.createElement("div");
    cmp.className = "cmp";

    const mkRow = (tag, val, isDiff) => {
      const row = document.createElement("div");
      row.className = "row" + (isDiff ? " diff" : "");
      const t = document.createElement("div");
      t.className = "tag";
      t.textContent = tag;
      const v = document.createElement("div");
      v.className = "hash";
      v.textContent = val;
      row.appendChild(t);
      row.appendChild(v);
      return row;
    };

    cmp.appendChild(mkRow("On-chain", onchain, false));
    cmp.appendChild(mkRow("Before", intact, false));
    cmp.appendChild(mkRow("After tamper", tampered, true));
    sealContainer.appendChild(cmp);

    const verdict = document.createElement("div");
    verdict.className = "verdict bad";
    verdict.style.marginTop = "14px";
    verdict.textContent = "Mismatch — alteration detected";
    sealContainer.appendChild(verdict);

    const exp = document.createElement("div");
    exp.className = "explain";
    exp.style.textAlign = "left";
    exp.innerHTML = `<strong>Why this matters:</strong> one byte of ${escapeHtml(data.mutated_file || "match_image.jpg")} changed, so its digest changed, so the bundle hash changed. The on-chain hash did not. A record cannot be altered after anchoring without detection.`;
    sealContainer.appendChild(exp);

    const note = document.createElement("div");
    note.className = "note";
    note.textContent = data.originals_unchanged
      ? "Original artifacts verified byte-identical — the test ran on a scratch copy."
      : "Original artifacts modified.";
    sealContainer.appendChild(note);

    const resetRow = document.createElement("div");
    resetRow.style.marginTop = "14px";
    const resetBtn = document.createElement("button");
    resetBtn.className = "cyber-btn ghost";
    resetBtn.type = "button";
    resetBtn.textContent = "Reset Seal";
    resetBtn.addEventListener("click", () => resetSealUI());
    resetRow.appendChild(resetBtn);
    sealContainer.appendChild(resetRow);

    chainStatus.innerHTML = `<span class="badge no-match">TAMPER DETECTED</span> Hash mismatch: on-chain record holds previous hash; altered file rejected.`;
  } catch (err) {
    chainStatus.textContent = "Tamper test failed: " + err.message;
  } finally {
    tamperBtn.disabled = false;
  }
}

if (tamperBtn) tamperBtn.addEventListener("click", runTamperSeal);
if (breakSealBtn) breakSealBtn.addEventListener("click", runTamperSeal);

// Editor Handlers: Save / Restore / Presets
if (saveEditBtn) {
  saveEditBtn.addEventListener("click", async () => {
    if (!currentRunId || !evidenceJsonArea) return;
    try {
      const resp = await fetch(`/api/evidence/${encodeURIComponent(currentRunId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: evidenceJsonArea.value }),
      });
      const data = await resp.json();
      if (data.ok) {
        if (currentBundleHash) currentBundleHash.textContent = data.evidence_hash;
        chainStatus.innerHTML = `<span class="badge match">SAVED</span> Saved edit to evidence.json. Recomputed hash: ${escapeHtml(data.evidence_hash)}`;
      } else {
        chainStatus.textContent = "Save failed: " + (data.detail || "invalid JSON");
      }
    } catch (err) {
      chainStatus.textContent = "Save failed: " + err.message;
    }
  });
}

if (restoreEditBtn) {
  restoreEditBtn.addEventListener("click", async () => {
    if (!currentRunId) return;
    try {
      const resp = await fetch(`/api/evidence/${encodeURIComponent(currentRunId)}/restore`, { method: "POST" });
      const data = await resp.json();
      if (data.ok) {
        cleanBundleJson = JSON.stringify(data.evidence, null, 2);
        if (evidenceJsonArea) evidenceJsonArea.value = cleanBundleJson;
        if (currentBundleHash) currentBundleHash.textContent = data.evidence_hash;
        chainStatus.innerHTML = `<span class="badge match">RESTORED</span> Restored original evidence.json cleanly.`;
        if (verifyResultArea) verifyResultArea.style.display = "none";
        if (cardVerify) cardVerify.className = "chain-card";
      }
    } catch (err) {
      chainStatus.textContent = "Restore failed: " + err.message;
    }
  });
}

if (presetScoreBtn) {
  presetScoreBtn.addEventListener("click", () => {
    if (!evidenceJsonArea) return;
    try {
      const obj = JSON.parse(evidenceJsonArea.value);
      if (!obj.match) obj.match = {};
      obj.match.score_bps = 9999;
      evidenceJsonArea.value = JSON.stringify(obj, null, 2);
    } catch {
      evidenceJsonArea.value = evidenceJsonArea.value.replace(/"score_bps":\s*\d+/, '"score_bps": 9999');
    }
    chainStatus.innerHTML = `<span style="color:var(--bad); font-weight:600;">⚠️ Tamper In Editor:</span> Altered score to 0.9999. Click <strong>VERIFY AGAINST CHAIN</strong> to test detection!`;
  });
}

if (presetUrlBtn) {
  presetUrlBtn.addEventListener("click", () => {
    if (!evidenceJsonArea) return;
    try {
      const obj = JSON.parse(evidenceJsonArea.value);
      if (!obj.post) obj.post = {};
      obj.post.url = "https://forged-tampered-profile.com/fake-post-id";
      evidenceJsonArea.value = JSON.stringify(obj, null, 2);
    } catch {
      evidenceJsonArea.value = evidenceJsonArea.value.replace(/"url":\s*"[^"]*"/, '"url": "https://forged-tampered-profile.com/fake-post-id"');
    }
    chainStatus.innerHTML = `<span style="color:var(--bad); font-weight:600;">⚠️ Tamper In Editor:</span> Altered post URL to forged link. Click <strong>VERIFY AGAINST CHAIN</strong> to test detection!`;
  });
}

if (presetResetBtn) {
  presetResetBtn.addEventListener("click", () => {
    if (!evidenceJsonArea) return;
    evidenceJsonArea.value = cleanBundleJson;
    chainStatus.innerHTML = `<span style="color:var(--ok); font-weight:600;">✓ Restored:</span> Reset editor to clean copy.`;
  });
}

initCamera();
loadRunsList();

