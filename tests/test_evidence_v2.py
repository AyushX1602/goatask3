"""Tier 0 regression tests, written FIRST (agreed definition of done,
5 Sep 2026), for the two defects found in the same review session:

1. `match.image_sha256` was ALWAYS "" in every real anchored run.
   `bundle.py` read `post_meta.get("image_sha256", "")`, but no provider
   (GCV or Bluesky) ever wrote that key into post_meta — the winning
   candidate's actual image bytes were discarded inside run_pipeline and
   never threaded out to build_evidence at all. `evm.py` then papered over
   the empty hash with `or "0" * 64`, so every anchored on-chain record has
   an `imageHash` of 64 zeros. A judge reading evidence.json next to the
   explorer would find this in seconds and it would discredit the whole
   verifier demo.

2. `bundle.py`'s own docstring claimed a "v2 (G1.2)" schema with
   `post.content_kind`, while `SCHEMA_VERSION` was still 1 and the field
   was never emitted — a doc-vs-code lie, the exact category R-24 exists
   to prevent.

Fix (agreed): thread the winning image bytes out of run_pipeline on
PipelineResult, make build_evidence() take them as an explicit required
argument (no post_meta lookup, no fallback), and have evm.anchor() raise
rather than silently zero-fill if it is ever empty. Bump to schema v2,
carrying image_phash + verified_against + post.content_kind together in
one bump, since a second bump before any sample run is committed is free
and a second bump after is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.chain.evm import EvmClient
from pipeline.evidence.bundle import SCHEMA_VERSION, EvidenceBundle, build_evidence
from pipeline.evidence.canonical import canonical_bytes, round_trip_bytes
from pipeline.face.types import Embedding, LivenessResult
from pipeline.search.base import Candidate
from pipeline.verify.matcher import MatchResult, ScoredCandidate

import numpy as np


def _embedding() -> Embedding:
    rng = np.random.default_rng(0)
    v = rng.normal(size=512).astype(np.float32)
    v = v / np.linalg.norm(v)
    return Embedding(vec=v, model="w600k_r50", aligned_png_sha256="deadbeef")


def _match_result(page_url: str = "https://www.youtube.com/watch?v=nDj8MIyitUs") -> MatchResult:
    cand = Candidate(
        image_url="https://i.ytimg.com/vi/nDj8MIyitUs/maxresdefault.jpg",
        page_url=page_url,
        source="gcv_web_detection",
    )
    other = Candidate(image_url="https://x.test/2", page_url="https://x.test/2", source="gcv_web_detection")
    best = ScoredCandidate(cand, 0.9618, 1, "ACCEPT", "score 0.9618 >= threshold 0.42")
    runner_up = ScoredCandidate(other, 0.05, 1, "reject-below-threshold", "score too low")
    return MatchResult(
        best=best, runner_up=runner_up, all_scored=[best, runner_up],
        threshold=0.42, margin_required=0.08, verdict="MATCH",
    )


def _liveness() -> LivenessResult:
    return LivenessResult(passed=True, score=0.99, label="not_applicable")


REAL_IMAGE_BYTES = (Path(__file__).parent / "fixtures" / "obama1.jpg").read_bytes()


# --- test 1: image_sha256 + image_phash computed from real bytes ----------


def test_bundle_hashes_the_actual_image_bytes_that_were_scored():
    match = _match_result()
    image_bytes = REAL_IMAGE_BYTES  # a real, decodable image, for both sha256 and phash

    import hashlib
    expected_sha256 = hashlib.sha256(image_bytes).hexdigest()

    bundle = build_evidence(
        run_id="2026-09-05T12-00-00Z", embedding=_embedding(), salt=b"s" * 32,
        liveness=_liveness(), is_live_capture=False, match=match,
        providers_queried=["web_detect"], degraded_closed_corpus=False,
        identity_signals=[], candidates_examined=2, pipeline_version="0.1.0",
        image_bytes=image_bytes,
    )

    assert bundle.data["match"]["image_sha256"] == expected_sha256
    assert bundle.data["match"]["image_sha256"] != "", "must never be empty for an ACCEPTed match"

    phash = bundle.data["match"]["image_phash"]
    assert isinstance(phash, str)
    assert len(phash) == 16, "64-bit phash as hex must be exactly 16 characters"
    int(phash, 16)  # must be valid hex


def test_build_evidence_raises_on_empty_image_bytes():
    """No fallback path. An accepted match with no provable image bytes
    must fail loudly, not anchor a record with a meaningless hash field."""
    match = _match_result()
    with pytest.raises(ValueError, match="image_bytes"):
        build_evidence(
            run_id="x", embedding=_embedding(), salt=b"s" * 32, liveness=_liveness(),
            is_live_capture=False, match=match, providers_queried=[], degraded_closed_corpus=False,
            identity_signals=[], candidates_examined=1, pipeline_version="0.1.0",
            image_bytes=b"",
        )


def test_build_evidence_raises_on_none_image_bytes():
    match = _match_result()
    with pytest.raises(ValueError, match="image_bytes"):
        build_evidence(
            run_id="x", embedding=_embedding(), salt=b"s" * 32, liveness=_liveness(),
            is_live_capture=False, match=match, providers_queried=[], degraded_closed_corpus=False,
            identity_signals=[], candidates_examined=1, pipeline_version="0.1.0",
            image_bytes=None,
        )


# --- test 2: evm.anchor() refuses an empty/zero image hash -----------------


def _bundle_with_raw_match_overrides(**match_overrides) -> EvidenceBundle:
    data = {
        "schema_version": 2,
        "probe": {
            "face_commitment": "0x" + "ab" * 32,
            "liveness_passed": True,
            "liveness_label": "not_applicable",
            "captured_at": 1757000000,
            "aligned_sha256": "cd" * 32,
        },
        "match": {
            "page_url": "https://www.youtube.com/watch?v=nDj8MIyitUs",
            "image_url": "https://i.ytimg.com/vi/nDj8MIyitUs/maxresdefault.jpg",
            "image_sha256": "ef" * 32,
            "image_phash": "aabbccdd11223344",
            "verified_against": "search_engine_cache",
            "provider": "gcv_web_detection",
            "score_bps": 9618,
            "margin_bps": 8475,
            "threshold_bps": 4200,
            **match_overrides,
        },
        "post": {
            "platform": "youtube",
            "content_kind": "post",
            "author_handle": "",
            "author_display": "bijay filmy",
            "text": "test",
            "published_at": 1756900000,
            "permalink": "https://www.youtube.com/watch?v=nDj8MIyitUs",
        },
        "run": {
            "run_id": "test-run",
            "providers_queried": ["web_detect"],
            "candidates_examined": 2,
            "candidates_rejected": 1,
            "degraded_closed_corpus": False,
            "identity_signals": [],
            "pipeline_version": "0.1.0",
            "model": "w600k_r50",
        },
    }
    from pipeline.evidence.canonical import evidence_hash_hex
    canonical = canonical_bytes(data)
    return EvidenceBundle(data=data, evidence_hash_hex=evidence_hash_hex(data), canonical_json=canonical)


def test_evm_anchor_raises_on_empty_image_sha256(monkeypatch):
    monkeypatch.setenv("EVM_CHAIN", "anvil")
    monkeypatch.setenv("EVM_RPC_URL", "http://127.0.0.1:8545")
    monkeypatch.setenv("EVM_CONTRACT_ADDRESS", "0x5FbDB2315678afecb367f032d93F642f64180aa3")
    monkeypatch.delenv("EVM_PRIVATE_KEY", raising=False)

    bundle = _bundle_with_raw_match_overrides(image_sha256="")
    client = EvmClient()
    with pytest.raises(ValueError, match="image_sha256"):
        client.anchor(bundle)


def test_evm_anchor_has_no_silent_zero_fallback_for_image_hash():
    """Static guard: the actual `or "0" * 64` FALLBACK EXPRESSION must not
    exist in the module's executable code — only in prose describing why
    it was removed. Deliberately source-level, not just behavioural: the
    point is that no fallback path exists at all, so we grep the live
    expression form rather than the string literal, which also appears
    (quoted, as documentation) in a comment explaining this very fix."""
    import inspect
    from pipeline.chain import evm

    src = inspect.getsource(evm)
    assert 'or "0" * 64' not in src, "the silent zero-fill fallback expression must be removed"


# --- test 3: schema v2 fields -----------------------------------------------


def test_schema_version_is_2():
    assert SCHEMA_VERSION == 2


def test_content_kind_is_never_null_in_a_built_bundle():
    match = _match_result()
    bundle = build_evidence(
        run_id="x", embedding=_embedding(), salt=b"s" * 32, liveness=_liveness(),
        is_live_capture=False, match=match, providers_queried=[], degraded_closed_corpus=False,
        identity_signals=[], candidates_examined=1, pipeline_version="0.1.0",
        image_bytes=REAL_IMAGE_BYTES,
    )
    assert bundle.data["post"]["content_kind"] in ("post", "profile", "unknown")


def test_verified_against_is_a_known_value():
    match = _match_result()
    bundle = build_evidence(
        run_id="x", embedding=_embedding(), salt=b"s" * 32, liveness=_liveness(),
        is_live_capture=False, match=match, providers_queried=[], degraded_closed_corpus=False,
        identity_signals=[], candidates_examined=1, pipeline_version="0.1.0",
        image_bytes=REAL_IMAGE_BYTES,
    )
    assert bundle.data["match"]["verified_against"] in ("search_engine_cache", "platform_origin")


# --- test 4: v1 fixture still canonicalises; v2 fixture round-trips --------


V1_FIXTURE = {
    "schema_version": 1,
    "probe": {
        "face_commitment": "0x" + "ab" * 32,
        "liveness_passed": True,
        "liveness_label": "not_applicable",
        "captured_at": 1757000000,
        "aligned_sha256": "cd" * 32,
    },
    "match": {
        "page_url": "https://www.youtube.com/watch?v=nDj8MIyitUs",
        "image_url": "https://i.ytimg.com/vi/nDj8MIyitUs/oardefault.jpg",
        "image_sha256": "ef" * 32,
        "provider": "gcv_web_detection",
        "score_bps": 9618,
        "margin_bps": 8475,
        "threshold_bps": 4200,
    },
    "post": {
        "platform": "youtube",
        "author_handle": "",
        "author_display": "bijay filmy",
        "text": "a v1 bundle, recorded before the schema bump",
        "published_at": 1756900000,
        "permalink": "https://www.youtube.com/watch?v=nDj8MIyitUs",
    },
    "run": {
        "run_id": "v1-fixture",
        "providers_queried": ["web_detect"],
        "candidates_examined": 44,
        "candidates_rejected": 43,
        "degraded_closed_corpus": False,
        "identity_signals": ["Shah Rukh Khan"],
        "pipeline_version": "0.1.0",
        "model": "w600k_r50",
    },
}


def test_v1_fixture_still_canonicalises_to_its_recorded_bytes():
    """A pre-bump bundle must remain a valid, hashable structure forever —
    old evidence is never invalidated by a later schema version existing."""
    once = canonical_bytes(V1_FIXTURE)
    twice = round_trip_bytes(once)
    assert once == twice
    assert json.loads(once)["schema_version"] == 1


def test_v2_fixture_round_trips_byte_identically():
    v2 = dict(V1_FIXTURE)
    v2["schema_version"] = 2
    v2["match"] = dict(V1_FIXTURE["match"])
    v2["match"]["image_phash"] = "aabbccdd11223344"
    v2["match"]["verified_against"] = "search_engine_cache"
    v2["post"] = dict(V1_FIXTURE["post"])
    v2["post"]["content_kind"] = "post"

    once = canonical_bytes(v2)
    twice = round_trip_bytes(once)
    assert once == twice
    assert json.loads(once)["schema_version"] == 2
    assert json.loads(once)["post"]["content_kind"] == "post"


# --- test 5: cache stats reach the audit log --------------------------------


def test_build_audit_includes_cache_stats():
    """get_http_cache()'s own docstring claims hit/miss stats 'land in the
    audit log'. Before this fix, build_audit() never included them at all."""
    from pipeline.audit.run_log import build_audit
    from pipeline.search.base import ProviderReport
    from pipeline.verify.matcher import MatchResult as MR

    empty_match = MR(best=None, runner_up=None, all_scored=[], threshold=0.42, margin_required=0.08, verdict="NO_MATCH")
    report = ProviderReport(name="web_detect", available=True, attempted=True, candidates_returned=3)

    audit = build_audit(
        run_id="x", started_at=0.0, liveness={}, provider_reports=[report], match=empty_match,
        cache_stats={"web_detect": {"hits": 2, "misses": 5}},
    )
    assert "cache" in audit
    assert audit["cache"]["web_detect"] == {"hits": 2, "misses": 5}
    assert "aggregate" in audit["cache"]
    assert audit["cache"]["aggregate"] == {"hits": 2, "misses": 5}


# --- test 6: bundle.py's docstring describes v2 and nothing else -----------


def test_bundle_module_docstring_does_not_claim_an_unimplemented_schema():
    """The specific doc-vs-code lie that triggered this whole fix: the
    docstring claimed 'v2 (G1.2) — added post.content_kind' while
    SCHEMA_VERSION was still 1 and the field was never emitted. After the
    fix, the docstring must not reference a version number higher than
    SCHEMA_VERSION actually is."""
    import re

    import pipeline.evidence.bundle as bundle_module

    doc = bundle_module.__doc__ or ""
    versions_mentioned = [int(m) for m in re.findall(r"\bv(\d+)\b", doc)]
    assert all(v <= SCHEMA_VERSION for v in versions_mentioned), (
        f"docstring mentions a schema version higher than SCHEMA_VERSION={SCHEMA_VERSION}: "
        f"{versions_mentioned}"
    )
