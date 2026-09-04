"""Local demo UI backend. See architecture.md 5a, prd.md G8/S13.

Amended into scope 5 Sep 2026 at the owner's explicit request: a thin
visualization layer for judges, built on top of the SAME pipeline.* code
the CLI uses. No duplicate scoring logic exists here — this module wires
HTTP endpoints onto pipeline.face and pipeline.search.orchestrator and
nothing else.

Binds to 127.0.0.1 only. No auth. Not for network exposure — this is a
local demo tool, not a service (documented in the README).

Owner instruction: stop after this scanning interface. No blockchain code
is wired in here.
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.audit.run_log import build_audit, new_run_id, write_audit
from pipeline.config import RUNS_DIR, ensure_dirs, load_match_policy
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.face.liveness import LivenessChecker
from pipeline.search.base import ProviderReport
from pipeline.search.bluesky import BlueskyProvider
from pipeline.verify.allowlist import SOCIAL_ALLOW
from pipeline.verify.matcher import score_candidates

app = FastAPI(title="face-chain-verify local demo UI")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Loaded once at startup — same models the CLI uses.
ensure_dirs()
_detector = FaceDetector()
_embedder = FaceEmbedder()
_liveness = LivenessChecker()
_bluesky = BlueskyProvider(crawl_limit=300)
_bluesky_crawled = False

# In-memory run state, keyed by run_id. Fine for a single local demo user.
_runs: dict[str, dict] = {}


class ScanResponse(BaseModel):
    run_id: str
    face_found: bool
    aligned_crop_png_b64: str | None = None
    liveness_passed: bool | None = None
    liveness_score: float | None = None
    liveness_label: str | None = None
    det_score: float | None = None
    message: str | None = None


class SearchResponse(BaseModel):
    run_id: str
    crawl_size: int
    verdict: str
    threshold: float
    margin_required: float
    candidates: list[dict]


def _decode_upload(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/scan", response_model=ScanResponse)
async def scan(frame: UploadFile = File(...)) -> ScanResponse:
    """Runs pipeline.face.detect + align + embed + liveness on an uploaded
    frame (a single JPEG/PNG captured from the browser's webcam feed).
    Stores the probe embedding in the in-memory run state, keyed by a new
    run_id, for /api/search to pick up."""
    data = await frame.read()
    img = _decode_upload(data)
    run_id = new_run_id()

    if img is None:
        return ScanResponse(run_id=run_id, face_found=False, message="could not decode image")

    faces = _detector.detect(img)
    if not faces:
        return ScanResponse(run_id=run_id, face_found=False, message="no face detected")

    face = faces[0]
    liveness = _liveness.check(img, face)
    crop = align(img, face.kps5)
    embedding = _embedder.embed(crop)

    ok, png_bytes = cv2.imencode(".png", crop)
    crop_b64 = base64.b64encode(png_bytes.tobytes()).decode("ascii") if ok else None

    _runs[run_id] = {
        "started_at": time.time(),
        "probe_vec": embedding.vec,
        "liveness": {
            "passed": liveness.passed,
            "score": liveness.score,
            "label": liveness.label,
        },
        "det_score": face.det_score,
    }

    return ScanResponse(
        run_id=run_id,
        face_found=True,
        aligned_crop_png_b64=crop_b64,
        liveness_passed=liveness.passed,
        liveness_score=liveness.score,
        liveness_label=liveness.label,
        det_score=face.det_score,
    )


@app.post("/api/search/{run_id}", response_model=SearchResponse)
def search(run_id: str) -> SearchResponse:
    """Runs pipeline.search against the Bluesky live index for the probe
    stored by /api/scan, then re-verifies every candidate with our own
    face core (R-03) and scores via pipeline.verify.matcher — the exact
    same decision function the CLI uses."""
    global _bluesky_crawled

    if run_id not in _runs:
        return SearchResponse(run_id=run_id, crawl_size=0, verdict="ERROR", threshold=0, margin_required=0, candidates=[])

    if not _bluesky_crawled:
        _bluesky.crawl(_detector, _embedder)
        _bluesky_crawled = True

    probe_vec = _runs[run_id]["probe_vec"]
    start = time.perf_counter()
    candidates = _bluesky.search(b"", probe_vec)
    latency_ms = (time.perf_counter() - start) * 1000

    provider_reports = [
        ProviderReport(
            name=_bluesky.name,
            available=True,
            attempted=True,
            latency_ms=latency_ms,
            candidates_returned=len(candidates),
        )
    ]

    # Re-verify every candidate with our own face core (R-03) — the
    # Bluesky index score is never trusted for the final decision, only
    # our own ArcFace cosine, recomputed here against the candidate's
    # already-fetched thumbnail via a fresh HTTP GET.
    scored = []
    for cand in candidates:
        try:
            resp = requests.get(cand.image_url, timeout=8)
            resp.raise_for_status()
            arr = np.frombuffer(resp.content, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                scored.append((cand, None, 0))
                continue
            faces = _detector.detect(img)
            if not faces:
                scored.append((cand, None, 0))
                continue
            best_score = -1.0
            for f in faces:
                crop = align(img, f.kps5)
                emb = _embedder.embed(crop)
                s = float(np.dot(emb.vec, probe_vec))
                best_score = max(best_score, s)
            scored.append((cand, best_score, len(faces)))
        except Exception:
            scored.append((cand, None, 0))

    policy = load_match_policy()
    match = score_candidates(scored, policy, allowed_domains=SOCIAL_ALLOW)

    audit = build_audit(
        run_id=run_id,
        started_at=_runs[run_id]["started_at"],
        liveness=_runs[run_id]["liveness"],
        provider_reports=provider_reports,
        match=match,
    )
    write_audit(RUNS_DIR / run_id, audit)

    return SearchResponse(
        run_id=run_id,
        crawl_size=len(_bluesky.index),
        verdict=match.verdict,
        threshold=match.threshold,
        margin_required=match.margin_required,
        candidates=audit["candidates"],
    )
