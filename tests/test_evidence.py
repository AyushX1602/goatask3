"""F7 exit criterion (phases.md FINAL PLAN):

'Two identical runs produce byte-identical evidence.json and the same
hash. No floats in hashed output.'
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from pathlib import Path

from pipeline.config import MatchPolicy
from pipeline.evidence.bundle import build_evidence, image_sha256
from pipeline.evidence.canonical import (
    NonCanonicalValueError,
    canonical_bytes,
    evidence_hash,
    evidence_hash_hex,
    keccak256,
    round_trip_bytes,
    sha256_hex,
)
from pipeline.evidence.commitment import face_commitment, face_commitment_hex, quantise
from pipeline.face.types import Embedding, LivenessResult
from pipeline.search.base import Candidate
from pipeline.verify.matcher import MatchResult, ScoredCandidate


# ---------------- canonicalisation ----------------


def test_sorted_keys_and_no_whitespace():
    obj = {"b": 2, "a": 1}
    out = canonical_bytes(obj)
    assert out == b'{"a":1,"b":2}'


def test_float_anywhere_raises():
    with pytest.raises(NonCanonicalValueError):
        canonical_bytes({"score": 0.42})


def test_float_nested_in_list_raises():
    with pytest.raises(NonCanonicalValueError):
        canonical_bytes({"a": [1, 2, {"b": 3.5}]})


def test_bool_is_not_mistaken_for_float():
    # bool is a subclass of int, must NOT be rejected as a float
    out = canonical_bytes({"flag": True})
    assert out == b'{"flag":true}'


def test_round_trip_is_byte_identical():
    """design.md 4.1 CI test, verbatim."""
    obj = {"z": [3, 1, 2], "a": {"nested": True, "n": 7}}
    once = canonical_bytes(obj)
    twice = round_trip_bytes(once)
    assert once == twice


def test_evidence_hash_is_deterministic():
    obj = {"a": 1, "b": [1, 2, 3]}
    h1 = evidence_hash(obj)
    h2 = evidence_hash(obj)
    assert h1 == h2
    assert len(h1) == 32  # keccak256 digest size


def test_evidence_hash_changes_on_one_character_edit():
    """This IS the tamper-detection property the eventual verify() step
    depends on — proven here at the hash layer, before any chain exists."""
    a = {"text": "hello world"}
    b = {"text": "hello worlD"}
    assert evidence_hash_hex(a) != evidence_hash_hex(b)


def test_keccak256_differs_from_sha256():
    """Sanity check that we are not accidentally using the wrong hash —
    keccak256 and SHA3-256 (and definitely SHA-256) all differ."""
    data = b"test"
    assert keccak256(data) != bytes.fromhex(sha256_hex(data))


# ---------------- face commitment (R-01) ----------------


def _embedding(seed: int = 0) -> Embedding:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=512).astype(np.float32)
    v = v / np.linalg.norm(v)
    return Embedding(vec=v, model="w600k_r50", aligned_png_sha256="deadbeef")


def test_commitment_is_reproducible_with_same_salt():
    emb = _embedding()
    salt = b"0" * 32
    assert face_commitment(emb, salt) == face_commitment(emb, salt)


def test_commitment_differs_with_different_salt():
    emb = _embedding()
    assert face_commitment(emb, b"0" * 32) != face_commitment(emb, b"1" * 32)


def test_commitment_absorbs_tiny_float_noise():
    """R-01's whole point: the SAME face re-embedded with negligible
    numerical noise must produce the SAME commitment, or anchoring is
    useless. Quantisation exists specifically for this."""
    emb = _embedding()
    noisy_vec = emb.vec + np.random.default_rng(1).normal(scale=1e-6, size=512).astype(np.float32)
    noisy = Embedding(vec=noisy_vec, model=emb.model, aligned_png_sha256=emb.aligned_png_sha256)
    salt = b"fixedsalt-fixedsalt-fixedsalt12"
    assert face_commitment(emb, salt) == face_commitment(noisy, salt)


def test_commitment_never_contains_the_raw_vector_bytes():
    """A crude but meaningful R-01 check: the raw float32 vector's byte
    representation must not appear verbatim inside the commitment
    (impossible anyway since it's hashed, but this documents the intent)."""
    emb = _embedding()
    salt = b"s" * 32
    commitment = face_commitment(emb, salt)
    assert emb.vec.tobytes() not in commitment
    assert len(commitment) == 32  # keccak256 output, not a variable-length blob


def test_commitment_rejects_short_salt():
    with pytest.raises(ValueError):
        face_commitment(_embedding(), b"short")


def test_quantise_is_deterministic_int16():
    emb = _embedding()
    q1 = quantise(emb.vec)
    q2 = quantise(emb.vec)
    assert np.array_equal(q1, q2)
    assert q1.dtype == np.int16


# ---------------- bundle assembly ----------------


def _match_result_accept() -> MatchResult:
    cand = Candidate(
        image_url="https://scontent.cdninstagram.com/example.jpg",
        page_url="https://www.instagram.com/p/Dc5KuZdDZiX/",
        source="serpapi_lens",
        post_meta={
            "platform": "instagram",
            "author_handle": "example",
            "text": "example post",
            "published_at": "2026-06-18T19:57:45.326Z",
            "permalink": "https://www.instagram.com/p/Dc5KuZdDZiX/",
        },
    )
    other = Candidate(image_url="https://x.test/2", page_url="https://x.test/2", source="serpapi_lens")
    best = ScoredCandidate(cand, 0.7685, 1, "ACCEPT", "score 0.7685 >= threshold 0.42")
    runner_up = ScoredCandidate(other, 0.05, 1, "reject-below-threshold", "score too low")
    return MatchResult(
        best=best,
        runner_up=runner_up,
        all_scored=[best, runner_up],
        threshold=0.42,
        margin_required=0.08,
        verdict="MATCH",
    )


def _liveness_live() -> LivenessResult:
    return LivenessResult(passed=True, score=0.99, label="live")


_FAKE_IMAGE_BYTES = (Path(__file__).parent / "fixtures" / "obama1.jpg").read_bytes()


def test_build_evidence_from_a_real_shaped_match():
    match = _match_result_accept()
    emb = _embedding()
    salt = b"s" * 32

    bundle = build_evidence(
        run_id="2026-09-05T12-00-00Z",
        embedding=emb,
        salt=salt,
        liveness=_liveness_live(),
        is_live_capture=True,
        match=match,
        providers_queried=["web_detect"],
        degraded_closed_corpus=False,
        identity_signals=["Barack Obama", "Barack Obama"],  # dupes must dedupe
        candidates_examined=2,
        pipeline_version="0.1.0",
        image_bytes=_FAKE_IMAGE_BYTES,
        captured_at=1757000000,
    )

    assert bundle.data["schema_version"] == 2
    assert bundle.data["match"]["page_url"] == "https://www.instagram.com/p/Dc5KuZdDZiX/"
    assert bundle.data["match"]["score_bps"] == 7685
    assert bundle.data["match"]["image_sha256"] == image_sha256(_FAKE_IMAGE_BYTES)
    assert bundle.data["probe"]["liveness_label"] == "live"
    assert bundle.data["run"]["identity_signals"] == ["Barack Obama"]  # deduped
    assert bundle.evidence_hash_hex.startswith("0x")
    assert len(bundle.evidence_hash_hex) == 2 + 64  # 0x + 32 bytes hex

    # R-01: the raw embedding must never appear in the bundle
    dumped = json.dumps(bundle.data)
    assert str(list(emb.vec[:5])) not in dumped


def test_build_evidence_rejects_no_match():
    match = MatchResult(best=None, runner_up=None, all_scored=[], threshold=0.42, margin_required=0.08, verdict="NO_MATCH")
    with pytest.raises(ValueError, match="NO_MATCH"):
        build_evidence(
            run_id="x", embedding=_embedding(), salt=b"s" * 32, liveness=_liveness_live(),
            is_live_capture=True, match=match, providers_queried=[], degraded_closed_corpus=False,
            identity_signals=[], candidates_examined=0, pipeline_version="0.1.0",
            image_bytes=_FAKE_IMAGE_BYTES,
        )


def test_upload_probe_never_reports_live(monkeypatch):
    """R-22: an uploaded-file probe must report not_applicable regardless
    of what the liveness model itself returned."""
    match = _match_result_accept()
    live_but_unverifiable = LivenessResult(passed=True, score=0.99, label="live")

    bundle = build_evidence(
        run_id="x", embedding=_embedding(), salt=b"s" * 32, liveness=live_but_unverifiable,
        is_live_capture=False,  # upload path
        match=match, providers_queried=["web_detect"], degraded_closed_corpus=False,
        identity_signals=[], candidates_examined=2, pipeline_version="0.1.0",
        image_bytes=_FAKE_IMAGE_BYTES,
    )
    assert bundle.data["probe"]["liveness_label"] == "not_applicable"


def test_two_identical_runs_produce_byte_identical_bundles():
    """The F7 exit criterion, stated directly."""
    match = _match_result_accept()
    emb = _embedding()
    salt = b"s" * 32
    kwargs = dict(
        run_id="2026-09-05T12-00-00Z", embedding=emb, salt=salt, liveness=_liveness_live(),
        is_live_capture=True, match=match, providers_queried=["web_detect"],
        degraded_closed_corpus=False, identity_signals=["Barack Obama"],
        candidates_examined=2, pipeline_version="0.1.0", captured_at=1757000000,
        image_bytes=_FAKE_IMAGE_BYTES,
    )
    b1 = build_evidence(**kwargs)
    b2 = build_evidence(**kwargs)

    assert b1.canonical_json == b2.canonical_json
    assert b1.evidence_hash_hex == b2.evidence_hash_hex


def test_missing_published_at_defaults_to_zero_not_a_crash():
    match = _match_result_accept()
    match.best.candidate.post_meta["published_at"] = None
    bundle = build_evidence(
        run_id="x", embedding=_embedding(), salt=b"s" * 32, liveness=_liveness_live(),
        is_live_capture=True, match=match, providers_queried=[], degraded_closed_corpus=False,
        identity_signals=[], candidates_examined=1, pipeline_version="0.1.0",
        image_bytes=_FAKE_IMAGE_BYTES,
    )
    assert bundle.data["post"]["published_at"] == 0


def test_build_evidence_rejects_empty_image_bytes():
    match = _match_result_accept()
    with pytest.raises(ValueError, match="image_bytes"):
        build_evidence(
            run_id="x", embedding=_embedding(), salt=b"s" * 32, liveness=_liveness_live(),
            is_live_capture=True, match=match, providers_queried=[], degraded_closed_corpus=False,
            identity_signals=[], candidates_examined=1, pipeline_version="0.1.0",
            image_bytes=b"",
        )


def test_image_sha256_helper():
    assert image_sha256(b"hello") == sha256_hex(b"hello")
