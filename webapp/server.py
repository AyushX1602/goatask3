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
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.audit.run_log import build_audit, new_run_id, write_audit
from pipeline.config import RUNS_DIR, ensure_dirs, get_config
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.face.liveness import LivenessChecker
from pipeline.face.quality import passes as quality_passes, probe_error_message
from pipeline.search.bluesky import BlueskyProvider
from pipeline.search.web_detect import WebDetectProvider
from pipeline.verify.pipeline_run import run_pipeline

app = FastAPI(title="face-chain-verify local demo UI")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Loaded once at startup — same models the CLI uses.
ensure_dirs()
_detector = FaceDetector()
_embedder = FaceEmbedder()
_liveness = LivenessChecker()
_web_detect = WebDetectProvider()
_bluesky = BlueskyProvider(crawl_limit=get_config().bluesky_crawl_limit or 300)
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
    degraded_closed_corpus: bool = False


def _decode_upload(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


async def _handle_probe(
    frame: UploadFile, is_live_capture: bool, public_image_url: str | None = None
) -> ScanResponse:
    """Shared body for /api/scan (webcam) and /api/upload (file).

    is_live_capture controls whether liveness is meaningful (R-22, D-20):
    an anti-spoof model detects capture artifacts in a camera frame, and a
    clean uploaded file will typically score "live" while proving nothing
    about physical presence. Uploads always report `not_applicable`.

    public_image_url: optional. If the uploaded photo is already publicly
    hosted (e.g. the approved demo subject's Wikimedia portrait), pass its
    URL so the SerpApi backend can be used without needing S3 presigning
    (D-31 remains future work for arbitrary local files).
    """
    data = await frame.read()
    img = _decode_upload(data)
    run_id = new_run_id()

    if img is None:
        return ScanResponse(run_id=run_id, face_found=False, message="could not decode image")

    faces = _detector.detect(img)
    if not faces:
        return ScanResponse(run_id=run_id, face_found=False, message="no face detected")

    face = faces[0]
    ok_quality, quality_reason = quality_passes(face, get_config().min_face_px)
    if not ok_quality:
        return ScanResponse(
            run_id=run_id, face_found=False, message=probe_error_message(face, get_config().min_face_px)
        )

    liveness = _liveness.check(img, face)
    crop = align(img, face.kps5)
    embedding = _embedder.embed(crop)

    ok, png_bytes = cv2.imencode(".png", crop)
    crop_bytes = png_bytes.tobytes() if ok else b""
    crop_b64 = base64.b64encode(crop_bytes).decode("ascii") if ok else None

    liveness_label = liveness.label if is_live_capture else "not_applicable"
    liveness_passed = liveness.passed if is_live_capture else True

    _runs[run_id] = {
        "started_at": time.time(),
        "probe_vec": embedding.vec,
        "aligned_png": crop_bytes,
        "liveness": {
            "passed": liveness_passed,
            "score": liveness.score if is_live_capture else None,
            "label": liveness_label,
        },
        "det_score": face.det_score,
        "public_image_url": public_image_url,
    }

    return ScanResponse(
        run_id=run_id,
        face_found=True,
        aligned_crop_png_b64=crop_b64,
        liveness_passed=liveness_passed,
        liveness_score=liveness.score if is_live_capture else None,
        liveness_label=liveness_label,
        det_score=face.det_score,
    )


@app.post("/api/scan", response_model=ScanResponse)
async def scan(frame: UploadFile = File(...)) -> ScanResponse:
    """Webcam capture path. Liveness is meaningful here."""
    return await _handle_probe(frame, is_live_capture=True)


@app.post("/api/upload", response_model=ScanResponse)
async def upload(frame: UploadFile = File(...), public_image_url: str | None = Form(None)) -> ScanResponse:
    """Uploaded-file path (D-19). Required, not a convenience — the
    web-detection path only works on a public figure, and you cannot put
    a public figure in front of your own webcam. Liveness is NOT meaningful
    here (D-20) and is reported as `not_applicable`, never `LIVE`.

    public_image_url is optional: if the uploaded photo is already hosted
    publicly, pass its URL so SerpApi can be used (D-31 note above).
    """
    return await _handle_probe(frame, is_live_capture=False, public_image_url=public_image_url)


@app.post("/api/search/{run_id}", response_model=SearchResponse)
def search(run_id: str) -> SearchResponse:
    """Runs the ONE shared verification loop (pipeline.verify.pipeline_run,
    F4) for the probe stored by /api/scan or /api/upload. This endpoint
    contains no scoring logic of its own — architecture.md 5a: 'not a
    second implementation to maintain.'

    web_detect (D-28: GCV primary / SerpApi secondary) is the primary
    provider; bluesky is a keyless fallback, run only if web_detect is
    unavailable or returns nothing, and any resulting match is labelled a
    degraded closed-corpus run (architecture.md 9, D-21).
    """
    global _bluesky_crawled

    if run_id not in _runs:
        return SearchResponse(
            run_id=run_id, crawl_size=0, verdict="ERROR", threshold=0,
            margin_required=0, candidates=[], degraded_closed_corpus=False,
        )

    run_state = _runs[run_id]
    probe_vec = run_state["probe_vec"]
    aligned_png = run_state["aligned_png"]

    providers = []
    if _web_detect.available():
        providers.append(_web_detect)
    else:
        # No web_detect key configured — fall back to the keyless demo
        # provider so the pipeline still runs end to end (prd.md S10).
        if not _bluesky_crawled:
            _bluesky.crawl(_detector, _embedder)
            _bluesky_crawled = True
        providers.append(_bluesky)

    result = run_pipeline(
        aligned_png, probe_vec, providers, _detector, _embedder,
        public_image_url=run_state.get("public_image_url"),
    )

    degraded = not _web_detect.available()

    audit = build_audit(
        run_id=run_id,
        started_at=run_state["started_at"],
        liveness=run_state["liveness"],
        provider_reports=result.provider_reports,
        match=result.match,
    )
    audit["identity_signals"] = result.identity_signals  # context only, R-03
    audit["degraded_closed_corpus"] = degraded
    write_audit(RUNS_DIR / run_id, audit)

    return SearchResponse(
        run_id=run_id,
        crawl_size=len(_bluesky.index) if degraded else 0,
        verdict=result.match.verdict,
        threshold=result.match.threshold,
        margin_required=result.match.margin_required,
        candidates=audit["candidates"],
        degraded_closed_corpus=degraded,
    )
