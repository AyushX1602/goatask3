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

import json

from pipeline import __version__ as PIPELINE_VERSION
from pipeline.audit.run_log import build_audit, new_run_id, write_audit
from pipeline.config import RUNS_DIR, ensure_dirs, get_commitment_salt, get_config
from pipeline.evidence.bundle import build_evidence, detect_image_extension
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.face.liveness import LivenessChecker
from pipeline.face.quality import passes as quality_passes, probe_error_message
from pipeline.cache.http_cache import get_http_cache
from pipeline.chain.evm import ContractNotDeployedError, EvmClient
from pipeline.chain.reverify import reverify_bundle
from pipeline.search.bluesky import BlueskyProvider
from pipeline.search.image_prep import prepare_search_image
from pipeline.search.web_detect import WebDetectProvider
from pipeline.verify.pipeline_run import PipelineResult, run_pipeline

app = FastAPI(title="face-chain-verify local demo UI")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Loaded once at startup — same models the CLI uses.
ensure_dirs()
_detector = FaceDetector()
_embedder = FaceEmbedder()
_liveness = LivenessChecker()
_web_detect = WebDetectProvider()
_bluesky = BlueskyProvider(crawl_limit=get_config().bluesky_crawl_limit)
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
    evidence_hash: str | None = None
    # Candidates GCV asserted are the SAME image (match_kind full/partial)
    # but that live on a platform we cannot fetch from. Reported alongside
    # the verdict, never folded into it (R-03; see verify/matcher.py).
    unverifiable_platform_hits: list[dict] = []


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

    # The SEARCH QUERY is the original photograph, NOT the aligned crop.
    # Reverse image search matches against full photographs; sending the
    # 112x112 warped crop measurably degraded results and, on one probe,
    # caused the API to not recognise the subject at all. See
    # pipeline/search/image_prep.py for the measured comparison.
    search_image = prepare_search_image(img)

    liveness_label = liveness.label if is_live_capture else "not_applicable"
    liveness_passed = liveness.passed if is_live_capture else True

    _runs[run_id] = {
        "started_at": time.time(),
        "probe_vec": embedding.vec,
        "embedding": embedding,  # F7: build_evidence needs the full object, R-01: never leaves this process
        "is_live_capture": is_live_capture,
        "aligned_png": crop_bytes,  # for the UI preview + embedding provenance
        "search_image": search_image,  # what actually goes to the search provider
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
            evidence_hash=None,
        )

    run_state = _runs[run_id]
    probe_vec = run_state["probe_vec"]
    # Original photograph, not the aligned crop — see image_prep.py
    search_image = run_state["search_image"]
    public_image_url = run_state.get("public_image_url")

    primary_result = None
    primary_providers_queried: list[str] = []
    http = get_http_cache()
    cache_stats: dict[str, dict[str, int]] = {}

    def _snapshot() -> tuple[int, int]:
        s = http.stats()
        return s["hits"], s["misses"]

    def _record_delta(provider_name: str, before: tuple[int, int]) -> None:
        after = _snapshot()
        cache_stats[provider_name] = {
            "hits": after[0] - before[0],
            "misses": after[1] - before[1],
        }

    if _web_detect.available():
        before = _snapshot()
        primary_result = run_pipeline(
            search_image, probe_vec, [_web_detect], _detector, _embedder,
            public_image_url=public_image_url,
        )
        _record_delta(_web_detect.name, before)
        primary_providers_queried = [_web_detect.name]

    # available()==True only means a key exists, not that a search will
    # SUCCEED — e.g. the serpapi backend without public_image_url raises
    # internally, R-14 catches it, and it surfaces as zero candidates
    # returned. architecture.md 9 requires an actual fallback in that
    # case, not a silent NO_MATCH that hides "the primary path never ran."
    primary_produced_nothing = primary_result is None or not any(
        r.candidates_returned > 0 for r in primary_result.provider_reports
    )

    if primary_produced_nothing:
        if not _bluesky_crawled:
            _bluesky.crawl(_detector, _embedder)
            _bluesky_crawled = True
        before = _snapshot()
        fallback_result = run_pipeline(search_image, probe_vec, [_bluesky], _detector, _embedder)
        _record_delta(_bluesky.name, before)

        # Merge so the audit trail shows BOTH the failed/empty primary
        # attempt (if one was made) and the fallback attempt — never
        # silently swap one report set for the other (rules.md never-cut:
        # full candidate/provider trail).
        combined_reports = (
            primary_result.provider_reports if primary_result else []
        ) + fallback_result.provider_reports
        result = PipelineResult(
            match=fallback_result.match,
            provider_reports=combined_reports,
            identity_signals=fallback_result.identity_signals,
            images_fetched=fallback_result.images_fetched,
            images_deduped=fallback_result.images_deduped,
            best_image_bytes=fallback_result.best_image_bytes,
            candidate_diagnostics=fallback_result.candidate_diagnostics,
        )
        providers_queried = primary_providers_queried + [_bluesky.name]
        degraded = True
    else:
        result = primary_result
        providers_queried = primary_providers_queried
        degraded = False

    audit = build_audit(
        run_id=run_id,
        started_at=run_state["started_at"],
        liveness=run_state["liveness"],
        provider_reports=result.provider_reports,
        match=result.match,
        cache_stats=cache_stats,
        candidate_diagnostics=result.candidate_diagnostics,
    )
    audit["identity_signals"] = result.identity_signals  # context only, R-03
    audit["degraded_closed_corpus"] = degraded
    write_audit(RUNS_DIR / run_id, audit)

    evidence_hash_hex = None
    if result.match.verdict == "MATCH":
        # F7: build and persist the evidence bundle. No chain exists yet
        # (owner instruction: stop before blockchain) — this only proves
        # the bundle is canonical and reproducible, ready for a future
        # anchor step to consume.
        liveness_dict = run_state["liveness"]
        from pipeline.face.types import LivenessResult

        liveness_obj = LivenessResult(
            passed=liveness_dict["passed"],
            score=liveness_dict["score"] or 0.0,
            label=liveness_dict["label"],
        )
        bundle = build_evidence(
            run_id=run_id,
            embedding=run_state["embedding"],
            salt=get_commitment_salt(),
            liveness=liveness_obj,
            is_live_capture=run_state["is_live_capture"],
            match=result.match,
            providers_queried=providers_queried,
            degraded_closed_corpus=degraded,
            identity_signals=result.identity_signals,
            candidates_examined=len(result.match.all_scored),
            pipeline_version=PIPELINE_VERSION,
            image_bytes=result.best_image_bytes,
        )
        (RUNS_DIR / run_id).mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / run_id / "evidence.json").write_bytes(bundle.canonical_json)
        # Saved so the run directory is self-contained (T0.2): a judge can
        # open the exact image that produced image_sha256/image_phash
        # without re-fetching a URL that may have since changed or 404'd.
        if result.best_image_bytes:
            ext = detect_image_extension(result.best_image_bytes)
            (RUNS_DIR / run_id / f"match_image{ext}").write_bytes(result.best_image_bytes)
        evidence_hash_hex = bundle.evidence_hash_hex

    unverifiable_hits = [
        {
            "page_url": r.candidate.page_url,
            "source": r.candidate.source,
            "match_kind": r.candidate.match_kind,
        }
        for r in result.match.unverifiable_platform_hits
    ]

    return SearchResponse(
        run_id=run_id,
        crawl_size=len(_bluesky.index) if degraded else 0,
        verdict=result.match.verdict,
        threshold=result.match.threshold,
        margin_required=result.match.margin_required,
        candidates=audit["candidates"],
        degraded_closed_corpus=degraded,
        evidence_hash=evidence_hash_hex,
        unverifiable_platform_hits=unverifiable_hits,
    )


# --------------------------------------------------------------------------
# G4 (6 Sep 2026): anchor/verify/tamper endpoints. Same pipeline.chain.*
# functions the CLI uses (pipeline/cli.py's anchor/verify commands) — no
# second implementation, per architecture.md 5a. Exists so the anchor ->
# verify -> tamper -> restore cycle can be demonstrated by clicking buttons
# in the recording instead of switching to a terminal and hand-editing
# JSON, which is both slower and easier to fumble on a single take.
# --------------------------------------------------------------------------


class AnchorResponse(BaseModel):
    ok: bool
    tx_hash: str | None = None
    chain_id: int | None = None
    block_number: int | None = None
    contract_address: str | None = None
    evidence_hash: str | None = None
    error: str | None = None


class VerifyResponse(BaseModel):
    overall: str  # "PASS" | "TAMPERED" | "NOT_ANCHORED" | "ERROR"
    detail: str
    recomputed_hash: str
    anchored_hash: str | None = None
    on_chain_exists: bool | None = None


class TamperResponse(BaseModel):
    """Result of tampering a SCRATCH COPY of the bundle, never the real
    one. See /api/tamper/{run_id} below."""
    overall: str
    detail: str
    tampered_field: str
    original_value: str
    tampered_value: str


def _load_evidence_bundle(run_id: str):
    from pipeline.evidence.bundle import EvidenceBundle
    from pipeline.evidence.canonical import evidence_hash_hex

    bundle_path = RUNS_DIR / run_id / "evidence.json"
    if not bundle_path.exists():
        return None
    raw = bundle_path.read_bytes()
    data = json.loads(raw)
    return EvidenceBundle(data=data, evidence_hash_hex=evidence_hash_hex(data), canonical_json=raw)


@app.post("/api/anchor/{run_id}", response_model=AnchorResponse)
def anchor_run(run_id: str) -> AnchorResponse:
    """Anchors runs/<run_id>/evidence.json on the configured EVM chain.
    Identical logic to `python -m pipeline anchor <run_id>` (cli.py) —
    same EvmClient, same anchor.json written afterward."""
    bundle = _load_evidence_bundle(run_id)
    if bundle is None:
        return AnchorResponse(ok=False, error=f"no evidence bundle for run {run_id} (was the verdict MATCH?)")

    try:
        client = EvmClient()
        receipt = client.anchor(bundle)
    except ContractNotDeployedError as e:
        return AnchorResponse(ok=False, error=str(e))
    except Exception as e:
        return AnchorResponse(ok=False, error=f"{type(e).__name__}: {e}")

    anchor_record = {
        "tx_hash": receipt.tx_hash,
        "chain_id": receipt.chain_id,
        "block_number": receipt.block_number,
        "contract_address": receipt.contract_address,
        "gas_used": receipt.gas_used,
        "evidence_hash": receipt.evidence_hash_hex,
    }
    (RUNS_DIR / run_id / "anchor.json").write_text(json.dumps(anchor_record, indent=2), encoding="utf-8")

    return AnchorResponse(
        ok=True,
        tx_hash=receipt.tx_hash,
        chain_id=receipt.chain_id,
        block_number=receipt.block_number,
        contract_address=receipt.contract_address,
        evidence_hash=receipt.evidence_hash_hex,
    )


@app.post("/api/verify/{run_id}", response_model=VerifyResponse)
def verify_run(run_id: str) -> VerifyResponse:
    """Re-verifies runs/<run_id>/evidence.json against the on-chain
    record. Identical logic to `python -m pipeline verify <run_id>`
    (cli.py) — the literal brief requirement 3, "demonstrate re-verifying
    the data against the on-chain record."""
    run_dir = RUNS_DIR / run_id
    bundle_path = run_dir / "evidence.json"
    anchor_path = run_dir / "anchor.json"

    if not bundle_path.exists():
        return VerifyResponse(overall="ERROR", detail=f"no evidence bundle at {bundle_path}", recomputed_hash="")

    expected_hash = None
    if anchor_path.exists():
        expected_hash = json.loads(anchor_path.read_text(encoding="utf-8"))["evidence_hash"]

    try:
        report = reverify_bundle(bundle_path, client=EvmClient(), expected_hash=expected_hash)
    except Exception as e:
        return VerifyResponse(overall="ERROR", detail=f"{type(e).__name__}: {e}", recomputed_hash="")

    return VerifyResponse(
        overall=report.overall,
        detail=report.detail,
        recomputed_hash=report.recomputed_hash,
        anchored_hash=report.anchored_hash,
        on_chain_exists=report.on_chain.exists if report.on_chain else None,
    )


@app.post("/api/tamper/{run_id}", response_model=TamperResponse)
def tamper_run(run_id: str) -> TamperResponse:
    """Demonstrates the tamper-detection property WITHOUT mutating the
    real evidence.json: builds an in-memory scratch copy of the bundle
    with one field changed (match.score_bps incremented by 1), runs it
    through the SAME reverify_bundle() the real /api/verify/{run_id} and
    `python -m pipeline verify` use, and reports the result. The file on
    disk is never touched — a click-to-demonstrate button beats hand-
    editing JSON on a single-take recording, but it must not risk leaving
    the real run corrupted if something goes wrong mid-demo.
    """
    import tempfile

    run_dir = RUNS_DIR / run_id
    bundle_path = run_dir / "evidence.json"
    anchor_path = run_dir / "anchor.json"

    if not bundle_path.exists():
        return TamperResponse(
            overall="ERROR", detail=f"no evidence bundle at {bundle_path}",
            tampered_field="", original_value="", tampered_value="",
        )

    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    original_score = data["match"]["score_bps"]
    tampered_score = original_score + 1
    data["match"]["score_bps"] = tampered_score

    from pipeline.evidence.canonical import canonical_bytes

    tampered_canonical = canonical_bytes(data)

    expected_hash = None
    if anchor_path.exists():
        expected_hash = json.loads(anchor_path.read_text(encoding="utf-8"))["evidence_hash"]

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as tmp:
        tmp.write(tampered_canonical)
        tmp_path = Path(tmp.name)

    try:
        report = reverify_bundle(tmp_path, client=EvmClient(), expected_hash=expected_hash)
    except Exception as e:
        return TamperResponse(
            overall="ERROR", detail=f"{type(e).__name__}: {e}",
            tampered_field="match.score_bps",
            original_value=str(original_score), tampered_value=str(tampered_score),
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    return TamperResponse(
        overall=report.overall,
        detail=report.detail,
        tampered_field="match.score_bps",
        original_value=str(original_score),
        tampered_value=str(tampered_score),
    )
