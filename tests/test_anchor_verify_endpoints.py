"""Tests for the G4 anchor/verify/tamper endpoints (6 Sep 2026).

These exercise the SAME pipeline.chain.* functions the CLI's `anchor`/
`verify` commands use (architecture.md 5a) — no second implementation.
Requires a real Anvil chain at EVM_RPC_URL with the EvidenceRegistry
contract deployed (same precondition as tests/test_chain_evm.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.config import RUNS_DIR
from pipeline.evidence.bundle import build_evidence
from pipeline.evidence.commitment import face_commitment_hex
from pipeline.face.types import Embedding, LivenessResult
from pipeline.search.base import Candidate
from pipeline.verify.matcher import MatchResult, ScoredCandidate

import numpy as np


def _anvil_available() -> bool:
    try:
        from pipeline.chain.evm import EvmClient
        client = EvmClient()
        client.w3.eth.chain_id
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _anvil_available(), reason="Anvil not reachable at EVM_RPC_URL")


def _write_sample_run(run_id: str) -> None:
    """Writes a real, valid evidence.json under runs/<run_id>/, same shape
    build_evidence() produces, so the endpoints have something real to
    anchor/verify without going through the whole scan+search pipeline.

    Seeded from run_id (not a fixed seed) so each test gets a distinct
    embedding -> distinct evidence_hash. Anvil persists anchored records
    across test runs within the same process lifetime (it only resets on
    restart), so a fixed, deterministic bundle would collide with a hash
    anchored by an EARLIER test or an earlier pytest invocation entirely —
    exactly the kind of test-isolation bug a `NOT_ANCHORED` assertion
    would otherwise flake on.
    """
    rng = np.random.default_rng(abs(hash(run_id)) % (2**32))
    v = rng.normal(size=512).astype(np.float32)
    v = v / np.linalg.norm(v)
    embedding = Embedding(vec=v, model="w600k_r50", aligned_png_sha256="deadbeef")

    cand = Candidate(
        image_url=f"https://i.ytimg.com/vi/{run_id[-11:].rjust(11, '0')}/maxresdefault.jpg",
        page_url=f"https://www.youtube.com/watch?v={run_id[-11:].rjust(11, '0')}",
        source="gcv_web_detection",
    )
    other = Candidate(image_url="https://x.test/2", page_url="https://x.test/2", source="gcv_web_detection")
    best = ScoredCandidate(cand, 0.95, 1, "ACCEPT", "score 0.95 >= threshold 0.42")
    runner_up = ScoredCandidate(other, 0.05, 1, "reject-below-threshold", "low")
    match = MatchResult(best=best, runner_up=runner_up, all_scored=[best, runner_up],
                         threshold=0.42, margin_required=0.08, verdict="MATCH")

    bundle = build_evidence(
        run_id=run_id, embedding=embedding, salt=b"s" * 32,
        liveness=LivenessResult(passed=True, score=0.99, label="not_applicable"),
        is_live_capture=False, match=match, providers_queried=["web_detect"],
        degraded_closed_corpus=False, identity_signals=[], candidates_examined=2,
        pipeline_version="0.1.0",
        image_bytes=(Path(__file__).parent / "fixtures" / "obama1.jpg").read_bytes(),
    )
    face_commitment_hex(embedding, b"s" * 32)  # sanity: does not raise

    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence.json").write_bytes(bundle.canonical_json)


@pytest.fixture
def client():
    import webapp.server as srv
    return TestClient(srv.app)


@pytest.fixture
def sample_run_id(request):
    import time
    run_id = f"test-anchor-{request.node.name[-20:]}-{int(time.time() * 1000) % 100000}"
    _write_sample_run(run_id)
    yield run_id
    import shutil
    shutil.rmtree(RUNS_DIR / run_id, ignore_errors=True)


def test_anchor_endpoint_missing_run_returns_ok_false(client):
    resp = client.post("/api/anchor/does-not-exist-run-id")
    data = resp.json()
    assert data["ok"] is False
    assert "no evidence bundle" in data["error"]


def test_anchor_then_verify_endpoint_round_trip(client, sample_run_id):
    anchor_resp = client.post(f"/api/anchor/{sample_run_id}")
    anchor_data = anchor_resp.json()
    assert anchor_data["ok"] is True
    assert anchor_data["tx_hash"] is not None
    assert anchor_data["chain_id"] == 31337

    verify_resp = client.post(f"/api/verify/{sample_run_id}")
    verify_data = verify_resp.json()
    assert verify_data["overall"] == "PASS"
    assert verify_data["on_chain_exists"] is True


def test_verify_endpoint_before_anchor_reports_not_anchored(client, sample_run_id):
    resp = client.post(f"/api/verify/{sample_run_id}")
    data = resp.json()
    assert data["overall"] == "NOT_ANCHORED"


def test_tamper_endpoint_never_mutates_the_real_file(client, sample_run_id):
    """The core safety property: the file on disk must be byte-identical
    before and after calling /api/tamper/{run_id}."""
    bundle_path = RUNS_DIR / sample_run_id / "evidence.json"
    before = bundle_path.read_bytes()

    client.post(f"/api/anchor/{sample_run_id}")
    resp = client.post(f"/api/tamper/{sample_run_id}")
    data = resp.json()

    after = bundle_path.read_bytes()
    assert before == after, "the real evidence.json must never be mutated by the tamper endpoint"
    assert data["overall"] == "TAMPERED"
    assert data["original_value"] != data["tampered_value"]


def test_tamper_endpoint_reports_pass_shaped_response_before_anchor(client, sample_run_id):
    """Without an anchor.json, the tampered copy's hash is simply not on
    chain at all — NOT_ANCHORED, not TAMPERED (there is no claim of prior
    anchoring to contradict). Distinguishing these two is the same
    reasoning as reverify_bundle's expected_hash parameter."""
    resp = client.post(f"/api/tamper/{sample_run_id}")
    data = resp.json()
    assert data["overall"] == "NOT_ANCHORED"


def test_verify_endpoint_missing_bundle_is_an_error_not_a_500(client):
    resp = client.post("/api/verify/does-not-exist-run-id")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"] == "ERROR"
