"""F4 exit criterion (phases.md FINAL PLAN):

'An end-to-end run on a public figure yields verdict: MATCH with a real
social post URL, a score above threshold, and an audit.json listing every
candidate with its score and reject reason.'

Runs entirely offline: a fake provider returns fixed candidates (one real
image bytes-wise — the obama1 fixture re-served under several fake URLs),
so the loop is exercised without any network call or API key.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from pipeline.cache.http_cache import HttpCache
from pipeline.config import MatchPolicy
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.search.base import Candidate
from pipeline.verify.pipeline_run import run_pipeline

FIX = Path(__file__).parent / "fixtures"

POLICY = MatchPolicy(threshold=0.42, margin=0.08, model="w600k_r50", target_fmr=0.01, is_placeholder=True)


class FakeHttpCache:
    """Serves fixed bytes for known URLs without any network call."""

    def __init__(self, url_to_path: dict[str, Path]) -> None:
        self._map = url_to_path

    def get(self, url: str, **kw):
        path = self._map.get(url)

        class R:
            def __init__(self, ok: bool, content: bytes):
                self.ok = ok
                self.content = content

        if path is None:
            return R(False, b"")
        return R(True, path.read_bytes())


class FakeProvider:
    name = "fake"
    requires_credentials = False
    last_identity_signals: list[str] = ["Barack Obama"]

    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates

    def available(self) -> bool:
        return True

    def search(self, aligned_face_png: bytes, probe_vec) -> list[Candidate]:
        return self._candidates


@pytest.fixture(scope="module")
def detector() -> FaceDetector:
    return FaceDetector()


@pytest.fixture(scope="module")
def embedder() -> FaceEmbedder:
    return FaceEmbedder()


def _probe(detector, embedder, path: Path):
    img = cv2.imread(str(path))
    faces = detector.detect(img)
    crop = align(img, faces[0].kps5)
    emb = embedder.embed(crop)
    ok, png = cv2.imencode(".png", crop)
    return emb.vec, png.tobytes()


def _patch_http_cache(monkeypatch, fake: FakeHttpCache) -> None:
    monkeypatch.setattr("pipeline.verify.pipeline_run.get_http_cache", lambda: fake)


def test_matching_candidate_yields_match_verdict(detector, embedder, monkeypatch):
    """Same-person candidate (obama2.jpg re-served as a fake social URL)
    must be ACCEPTed above the placeholder threshold."""
    probe_vec, png = _probe(detector, embedder, FIX / "obama1.jpg")

    match_url = "https://instagram.com/p/fake123"
    noise_url = "https://reddit.com/r/fake/comments/fake"

    fake_http = FakeHttpCache(
        {
            match_url: FIX / "obama2.jpg",
            noise_url: FIX / "lin_manuel_miranda.png",
        }
    )
    _patch_http_cache(monkeypatch, fake_http)

    provider = FakeProvider(
        [
            Candidate(image_url=match_url, page_url=match_url, source="fake"),
            Candidate(image_url=noise_url, page_url=noise_url, source="fake"),
        ]
    )

    result = run_pipeline(png, probe_vec, [provider], detector, embedder, policy=POLICY)

    assert result.match.verdict == "MATCH"
    assert result.match.best is not None
    assert result.match.best.candidate.page_url == match_url
    assert result.match.best.score >= POLICY.threshold
    assert "Barack Obama" in result.identity_signals  # context only, R-03

    # every candidate must appear in the audit trail, with a reason
    assert len(result.match.all_scored) == 2
    reasons = {r.decision: r.reason for r in result.match.all_scored}
    assert "ACCEPT" in reasons
    # the noise candidate must be rejected, not silently dropped
    rejected = [r for r in result.match.all_scored if r.decision != "ACCEPT"]
    assert len(rejected) == 1
    assert rejected[0].reason  # never an empty explanation


def test_no_match_when_all_candidates_are_different_people(detector, embedder, monkeypatch):
    """Honest NO_MATCH (R-16): every candidate is a different person."""
    probe_vec, png = _probe(detector, embedder, FIX / "obama1.jpg")

    fake_http = FakeHttpCache(
        {
            "https://x.com/fake1": FIX / "lin_manuel_miranda.png",
            "https://youtube.com/fake2": FIX / "alex_lacamoire.png",
        }
    )
    _patch_http_cache(monkeypatch, fake_http)

    provider = FakeProvider(
        [
            Candidate(image_url="https://x.com/fake1", page_url="https://x.com/fake1", source="fake"),
            Candidate(image_url="https://youtube.com/fake2", page_url="https://youtube.com/fake2", source="fake"),
        ]
    )

    result = run_pipeline(png, probe_vec, [provider], detector, embedder, policy=POLICY)

    assert result.match.verdict == "NO_MATCH"
    assert result.match.best is None
    assert len(result.match.all_scored) == 2
    assert all(r.decision != "ACCEPT" for r in result.match.all_scored)


def test_off_allowlist_candidate_never_reported_even_if_it_would_match(detector, embedder, monkeypatch):
    """design.md 2.5: a high-scoring candidate on a non-social domain must
    be logged as rejected, never surfaced as the match."""
    probe_vec, png = _probe(detector, embedder, FIX / "obama1.jpg")

    off_allowlist_url = "https://britannica.com/fake-obama-photo"
    fake_http = FakeHttpCache({off_allowlist_url: FIX / "obama2.jpg"})
    _patch_http_cache(monkeypatch, fake_http)

    provider = FakeProvider(
        [Candidate(image_url=off_allowlist_url, page_url=off_allowlist_url, source="fake")]
    )

    result = run_pipeline(png, probe_vec, [provider], detector, embedder, policy=POLICY)

    assert result.match.verdict == "NO_MATCH"
    assert result.match.all_scored[0].decision == "reject-domain"


def test_generic_fetch_failure_is_logged_not_fatal(detector, embedder, monkeypatch):
    """A non-platform host that simply fails to serve the image."""
    probe_vec, png = _probe(detector, embedder, FIX / "obama1.jpg")
    _patch_http_cache(monkeypatch, FakeHttpCache({}))  # nothing resolves

    provider = FakeProvider(
        [Candidate(image_url="https://reddit.com/dead.jpg", page_url="https://reddit.com/r/x/comments/dead", source="fake")]
    )
    result = run_pipeline(png, probe_vec, [provider], detector, embedder, policy=POLICY)

    assert result.match.verdict == "NO_MATCH"
    assert result.match.all_scored[0].decision == "reject-fetch-failed"


def test_media_blocked_platform_gets_its_own_honest_reason(detector, embedder, monkeypatch):
    """Instagram/Facebook/TikTok refuse programmatic media access (verified
    live under three client profiles). Reporting that as a plain
    "could not fetch" reads as our defect; it is theirs by design, and the
    post WAS found — it just cannot be independently verified."""
    probe_vec, png = _probe(detector, embedder, FIX / "obama1.jpg")
    _patch_http_cache(monkeypatch, FakeHttpCache({}))

    provider = FakeProvider(
        [
            Candidate(
                image_url="https://lookaside.fbsbx.com/lookaside/crawler/media/?media_id=1",
                page_url="https://www.instagram.com/p/DXmJY6plgbV/",
                source="fake",
            )
        ]
    )
    result = run_pipeline(png, probe_vec, [provider], detector, embedder, policy=POLICY)

    row = result.match.all_scored[0]
    assert row.decision == "reject-platform-blocked"
    assert "unverifiable" in row.reason


def test_too_small_face_is_prerejected_not_scored(detector, embedder, monkeypatch):
    """design.md 1.7 / F2: a too-small face must never enter threshold logic
    as a weak score — it is excluded before scoring."""
    probe_vec, png = _probe(detector, embedder, FIX / "obama1.jpg")

    # Downscale obama2 so its face falls below the 50px gate.
    big = cv2.imread(str(FIX / "obama2.jpg"))
    small = cv2.resize(big, (int(big.shape[1] * 0.1), int(big.shape[0] * 0.1)))
    ok, small_bytes = cv2.imencode(".jpg", small)
    assert ok

    class InlineHttp:
        def get(self, url, **kw):
            class R:
                ok = True
                content = small_bytes.tobytes()
            return R()

    monkeypatch.setattr("pipeline.verify.pipeline_run.get_http_cache", lambda: InlineHttp())

    provider = FakeProvider(
        [Candidate(image_url="https://instagram.com/tiny", page_url="https://instagram.com/tiny", source="fake")]
    )

    result = run_pipeline(png, probe_vec, [provider], detector, embedder, policy=POLICY)

    assert result.match.all_scored[0].decision == "reject-face-too-small"


def test_provider_error_does_not_abort_the_run(detector, embedder, monkeypatch):
    """R-14, exercised through run_pipeline: a raising provider still yields
    a valid (NO_MATCH) result, not an exception."""
    probe_vec, png = _probe(detector, embedder, FIX / "obama1.jpg")
    monkeypatch.setattr("pipeline.verify.pipeline_run.get_http_cache", lambda: FakeHttpCache({}))

    class BrokenProvider:
        name = "broken"
        requires_credentials = False

        def available(self) -> bool:
            return True

        def search(self, *a, **kw):
            raise RuntimeError("simulated provider failure")

    result = run_pipeline(png, probe_vec, [BrokenProvider()], detector, embedder, policy=POLICY)
    assert result.match.verdict == "NO_MATCH"
    assert result.provider_reports[0].error is not None
