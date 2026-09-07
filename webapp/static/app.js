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
    chainStatus.textContent = "";
    chainResult.textContent = "";
    verifyBtn.disabled = true;
    tamperBtn.disabled = true;
  } else {
    chainPanel.style.display = "none";
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

// ---------------- chain: anchor / verify / tamper (G4 / T2.2) --------

const chainPanel = document.getElementById("chainPanel");
const anchorBtn = document.getElementById("anchorBtn");
const verifyBtn = document.getElementById("verifyBtn");
const tamperBtn = document.getElementById("tamperBtn");
const chainStatus = document.getElementById("chainStatus");
const chainResult = document.getElementById("chainResult");

anchorBtn.addEventListener("click", async () => {
  if (!currentRunId) return;
  anchorBtn.disabled = true;
  chainStatus.textContent = "Anchoring on chain...";
  chainResult.textContent = "";
  try {
    const resp = await fetch(`/api/anchor/${currentRunId}`, { method: "POST" });
    const data = await resp.json();
    if (data.ok) {
      chainStatus.textContent = "Anchored.";
      chainResult.textContent =
        `tx:       ${data.tx_hash}\n` +
        `chain_id: ${data.chain_id}\n` +
        `block:    ${data.block_number}\n` +
        `contract: ${data.contract_address}\n` +
        `evidence: ${data.evidence_hash}`;
      verifyBtn.disabled = false;
      tamperBtn.disabled = false;
      await loadRunsList();
    } else {
      chainStatus.textContent = "Anchor failed.";
      chainResult.textContent = data.error || "unknown error";
    }
  } catch (err) {
    chainStatus.textContent = "Anchor failed: " + err.message;
  } finally {
    anchorBtn.disabled = false;
  }
});

verifyBtn.addEventListener("click", async () => {
  if (!currentRunId) return;
  await runVerify(`/api/verify/${currentRunId}`, "Verifying against the on-chain record...");
});

tamperBtn.addEventListener("click", async () => {
  if (!currentRunId) return;
  await runVerify(`/api/tamper/${currentRunId}?mode=swap-artifact`, "Tampering scratch copy (swap-artifact)...", true);
});

async function runVerify(endpoint, statusText, isTamper = false) {
  chainStatus.textContent = statusText;
  chainResult.textContent = "";
  try {
    const resp = await fetch(endpoint, { method: "POST" });
    const data = await resp.json();
    const badge = data.overall === "PASS" ? "PASS" : data.overall;
    chainStatus.innerHTML = `<span class="badge ${data.overall === "PASS" ? "match" : "no-match"}">${badge}</span>`;
    const lines = [`detail: ${data.detail}`, `recomputed: ${data.recomputed_hash}`];
    if (isTamper) {
      lines.unshift(`tampered ${data.tampered_field}: ${data.original_value} -> ${data.tampered_value}`);
    }
    if (data.anchored_hash) lines.push(`anchored:   ${data.anchored_hash}`);
    chainResult.textContent = lines.join("\n");
  } catch (err) {
    chainStatus.textContent = "Failed: " + err.message;
  }
}

// ---------------- Evidence Explorer (T2.4) ----------------

const runsSelect = document.getElementById("runsSelect");
const refreshRunsBtn = document.getElementById("refreshRunsBtn");
const selectedRunCard = document.getElementById("selectedRunCard");
const expRunId = document.getElementById("expRunId");
const expMeta = document.getElementById("expMeta");
const expVerdictBadge = document.getElementById("expVerdictBadge");
const expArtifactsBody = document.getElementById("expArtifactsBody");
const expVerifyBtn = document.getElementById("expVerifyBtn");
const expTamperArtifactBtn = document.getElementById("expTamperArtifactBtn");
const expTamperBundleBtn = document.getElementById("expTamperBundleBtn");
const expTamperForgeBtn = document.getElementById("expTamperForgeBtn");
const expActionStatus = document.getElementById("expActionStatus");
const expActionResult = document.getElementById("expActionResult");

let activeExplorerRunId = null;

async function loadRunsList() {
  if (!runsSelect) return;
  try {
    const resp = await fetch("/api/runs");
    if (!resp.ok) return;
    const runs = await resp.json();
    runsSelect.innerHTML = '<option value="">-- Choose a past run --</option>';
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
  } catch (err) {
    console.error("Failed loading runs list:", err);
  }
}

if (refreshRunsBtn) {
  refreshRunsBtn.addEventListener("click", loadRunsList);
}

if (runsSelect) {
  runsSelect.addEventListener("change", async () => {
    const runId = runsSelect.value;
    if (!runId) {
      selectedRunCard.style.display = "none";
      activeExplorerRunId = null;
      return;
    }
    await selectRun(runId);
  });
}

async function selectRun(runId) {
  activeExplorerRunId = runId;
  expActionStatus.textContent = "";
  expActionResult.style.display = "none";
  expActionResult.textContent = "";

  try {
    const resp = await fetch(`/api/run/${runId}`);
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.error) {
      alert(data.error);
      return;
    }

    selectedRunCard.style.display = "block";
    expRunId.textContent = data.run_id;

    const b = data.bundle;
    const a = data.anchor;
    const aud = data.audit;

    const verdict = (aud && aud.verdict) || (b && "MATCH") || "UNKNOWN";
    expVerdictBadge.innerHTML = `<span class="badge ${verdict === "MATCH" ? "match" : "no-match"}">${verdict}</span>`;

    const metaParts = [];
    if (b && b.match) {
      metaParts.push(`Score: ${(b.match.score_bps / 10000).toFixed(4)}`);
      metaParts.push(`Provider: ${b.match.provider}`);
      if (b.post && b.post.platform) metaParts.push(`Platform: ${b.post.platform}`);
      if (b.post && b.post.content_kind) metaParts.push(`Kind: ${b.post.content_kind}`);
    }
    if (a) {
      metaParts.push(`Anchored: tx=${a.tx_hash ? a.tx_hash.slice(0, 10) + "..." : "none"}`);
    }
    expMeta.textContent = metaParts.join(" · ") || "No bundle metadata";

    expArtifactsBody.innerHTML = "";
    for (const art of (data.artifacts || [])) {
      const tr = document.createElement("tr");
      const tdName = document.createElement("td");
      tdName.textContent = art.name;
      const tdSize = document.createElement("td");
      tdSize.textContent = art.size_bytes >= 1024 ? `${Math.round(art.size_bytes / 1024)} KB` : `${art.size_bytes} B`;
      const tdSha = document.createElement("td");
      tdSha.style.fontFamily = "ui-monospace, monospace";
      tdSha.style.fontSize = "11px";
      tdSha.textContent = (art.sha256 || "").slice(0, 16) + "...";
      const tdStatus = document.createElement("td");
      tdStatus.id = `exp-art-status-${art.name.replace(/[^a-zA-Z0-9]/g, "_")}`;
      tdStatus.innerHTML = `<span style="color:#8b949e;">present</span>`;

      tr.appendChild(tdName);
      tr.appendChild(tdSize);
      tr.appendChild(tdSha);
      tr.appendChild(tdStatus);
      expArtifactsBody.appendChild(tr);
    }
  } catch (err) {
    console.error("Failed loading run details:", err);
  }
}

async function runExplorerAction(url, actionName) {
  if (!activeExplorerRunId) return;
  expActionStatus.textContent = `${actionName}...`;
  expActionResult.style.display = "none";
  try {
    const resp = await fetch(url, { method: "POST" });
    const data = await resp.json();

    const isPass = data.overall === "PASS";
    expActionStatus.innerHTML = `<span class="badge ${isPass ? "match" : "no-match"}">${data.overall}</span> <span style="margin-left:8px;">${escapeHtml(data.detail)}</span>`;

    const lines = [];
    if (data.mode) lines.push(`Tamper mode:   ${data.mode}`);
    if (data.tampered_field) lines.push(`Mutation:      ${data.tampered_field}: ${data.original_value} -> ${data.tampered_value}`);
    if (data.recomputed_hash) lines.push(`Recomputed:    ${data.recomputed_hash}`);
    if (data.anchored_hash) lines.push(`Anchored:      ${data.anchored_hash}`);
    if (data.on_chain_exists !== undefined) lines.push(`On-chain:      exists=${data.on_chain_exists}`);

    if (data.artifact_checks && data.artifact_checks.length) {
      lines.push("\nArtifact Checks:");
      for (const ac of data.artifact_checks) {
        const ok = ac.match ? "OK" : "FAIL";
        lines.push(`  [${ok}] ${ac.path} — ${ac.detail}`);
        const el = document.getElementById(`exp-art-status-${ac.path.replace(/[^a-zA-Z0-9]/g, "_")}`);
        if (el) {
          el.innerHTML = `<span class="badge ${ac.match ? "match" : "no-match"}">${ok}</span>`;
        }
      }
    }

    expActionResult.textContent = lines.join("\n");
    expActionResult.style.display = "block";
  } catch (err) {
    expActionStatus.textContent = `Action failed: ${err.message}`;
  }
}

if (expVerifyBtn) {
  expVerifyBtn.addEventListener("click", () => {
    if (activeExplorerRunId) runExplorerAction(`/api/verify/${activeExplorerRunId}`, "Verifying against on-chain record");
  });
}
if (expTamperArtifactBtn) {
  expTamperArtifactBtn.addEventListener("click", () => {
    if (activeExplorerRunId) runExplorerAction(`/api/tamper/${activeExplorerRunId}?mode=swap-artifact`, "Tampering artifact (XOR)");
  });
}
if (expTamperBundleBtn) {
  expTamperBundleBtn.addEventListener("click", () => {
    if (activeExplorerRunId) runExplorerAction(`/api/tamper/${activeExplorerRunId}?mode=edit-bundle`, "Tampering bundle JSON");
  });
}
if (expTamperForgeBtn) {
  expTamperForgeBtn.addEventListener("click", () => {
    if (activeExplorerRunId) runExplorerAction(`/api/tamper/${activeExplorerRunId}?mode=forge-bundle`, "Testing forged bundle");
  });
}

initCamera();
loadRunsList();

