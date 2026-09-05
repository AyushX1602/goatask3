"""Tests for profile expansion with strict face gating (T2.6 / R-28 / D-45)."""

from __future__ import annotations

from pathlib import Path
import cv2

from pipeline.config import MatchPolicy
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.search.base import Candidate
from pipeline.search.expand import (
    derive_profile_urls,
    expand_verified_candidates,
    extract_handle,
    extract_outbound_social_links,
)
from pipeline.verify.pipeline_run import run_pipeline

FIX = Path(__file__).parent / "fixtures"
POLICY = MatchPolicy(threshold=0.42, margin=0.08, model="w600k_r50", target_fmr=0.01, is_placeholder=True)


class FakeHttpCache:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self._map = responses

    def get(self, url: str, **kw):
        data = self._map.get(url)

        class R:
            def __init__(self, ok: bool, content: bytes):
                self.ok = ok
                self.content = content

        if data is None:
            return R(False, b"")
        return R(True, data)


class FakeProvider:
    name = "fake"
    requires_credentials = False

    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates

    def available(self) -> bool:
        return True

    def search(self, search_image_bytes: bytes, probe_vec) -> list[Candidate]:
        return self._candidates


def test_reserved_segments_never_become_handles():
    """R-28 / T2.6: /p/, /reel/, /pub/, /dir/, /shorts/, /issues/ never become handles."""
    reserved_urls = [
        "https://www.instagram.com/p/B_123456789/",
        "https://instagram.com/reel/C8_xyz123/",
        "https://www.linkedin.com/pub/dir/John/Doe",
        "https://www.linkedin.com/dir/Jane/Smith",
        "https://youtube.com/shorts/abcdefghijk",
        "https://github.com/torvalds/linux/issues/123",
        "https://github.com/torvalds/linux/pulls/456",
        "https://x.com/i/flow/login",
        "https://twitter.com/status/123456789",
        "https://reddit.com/r/technology/comments/12345",
    ]
    for url in reserved_urls:
        assert extract_handle(url) is None, f"Expected None for reserved URL: {url}"


def test_valid_profile_handles_extracted():
    """Profile URLs on known platforms correctly yield (platform, handle)."""
    cases = [
        ("https://github.com/octocat", ("github", "octocat")),
        ("https://www.github.com/torvalds/", ("github", "torvalds")),
        ("https://x.com/BarackObama", ("x", "BarackObama")),
        ("https://twitter.com/elonmusk", ("x", "elonmusk")),
        ("https://www.linkedin.com/in/williamhgates", ("linkedin", "williamhgates")),
        ("https://instagram.com/natgeo", ("instagram", "natgeo")),
        ("https://youtube.com/@mkbhd", ("youtube", "mkbhd")),
        ("https://bsky.app/profile/alice.bsky.social", ("bluesky", "alice.bsky.social")),
    ]
    for url, expected in cases:
        assert extract_handle(url) == expected


def test_derive_profile_urls():
    """Deriving profile URLs yields expected targets and excludes the source platform."""
    derived = derive_profile_urls("torvalds", exclude_platform="github")
    plats = {p for p, _ in derived}
    assert "github" not in plats
    assert "linkedin" in plats
    assert "x" in plats
    assert "https://www.linkedin.com/in/torvalds" in {u for _, u in derived}


def test_extract_outbound_social_links_and_link_in_bio():
    """Outbound links and link-in-bio are extracted, ignoring non-profile/reserved links."""
    html = """
    <div>
        <a href="https://twitter.com/satyanadella">Twitter</a>
        <a href="https://www.linkedin.com/in/satyanadella">LinkedIn</a>
        <a href="https://linktr.ee/satya">Linktree</a>
        <a href="https://instagram.com/p/randompost">Post</a>
        <a href="https://twitter.com/intent/tweet">Share</a>
    </div>
    """
    links = extract_outbound_social_links(html)
    assert "https://twitter.com/satyanadella" in links
    assert "https://www.linkedin.com/in/satyanadella" in links
    assert "https://linktr.ee/satya" in links
    assert "https://instagram.com/p/randompost" not in links  # reserved segment /p/
    assert "https://twitter.com/intent/tweet" not in links  # reserved segment /intent/


def test_expand_verified_candidates_generates_face_and_linked_origins():
    """Candidates are expanded with correct origin classification (R-28)."""
    verified = [
        Candidate(
            page_url="https://github.com/alice",
            image_url="https://github.com/alice.png",
            source="fake",
            origin="face",
        )
    ]

    expanded = expand_verified_candidates(verified)
    assert len(expanded) > 0

    by_plat = {c.source: c for c in expanded}
    # LinkedIn & Instagram are known media-blocked platforms: origin="linked"
    assert "expand-linkedin" in by_plat
    assert by_plat["expand-linkedin"].origin == "linked"
    assert by_plat["expand-linkedin"].image_url == ""

    # Other derived profiles
    assert "expand-x" in by_plat
    assert by_plat["expand-x"].origin == "linked"


def test_expansion_face_gated_and_linked_claims_uncounted(monkeypatch):
    """R-28 core invariant:
    1. Expanded candidate below threshold is rejected like any other.
    2. Linked claims appear as decision='linked-claim', score=None, and never in match count.
    3. Media-refused URLs (e.g. LinkedIn) stay listed as linked-claim, never silently dropped.
    """
    detector = FaceDetector()
    embedder = FaceEmbedder()

    img = cv2.imread(str(FIX / "obama1.jpg"))
    faces = detector.detect(img)
    crop = align(img, faces[0].kps5)
    probe_vec = embedder.embed(crop).vec

    # Initial matched candidate
    obama_page = "https://github.com/barackobama"
    obama_avatar = "https://github.com/barackobama.png"
    obama_bytes = (FIX / "obama2.jpg").read_bytes()

    fake_html = """
    <html>
      <body>
        <a href="https://www.linkedin.com/in/barackobama">LinkedIn</a>
        <a href="https://x.com/barackobama">X</a>
      </body>
    </html>
    """.encode("utf-8")

    fake_http = FakeHttpCache(
        {
            obama_page: fake_html,
            obama_avatar: obama_bytes,
        }
    )
    monkeypatch.setattr("pipeline.verify.pipeline_run.get_http_cache", lambda: fake_http)

    provider = FakeProvider(
        [
            Candidate(
                image_url=obama_avatar,
                page_url=obama_page,
                source="fake",
                origin="face",
            )
        ]
    )

    result = run_pipeline(
        cv2.imencode(".png", crop)[1].tobytes(),
        probe_vec,
        [provider],
        detector,
        embedder,
        policy=POLICY,
        expand_profiles=True,
    )

    assert result.match.verdict == "MATCH"
    assert result.match.best is not None
    assert result.match.best.candidate.page_url == obama_page

    # Check that linked claims are present
    linked_claims = [r for r in result.match.all_scored if r.decision == "linked-claim"]
    assert len(linked_claims) > 0

    # Linked claims MUST have score=None and origin="linked"
    for lc in linked_claims:
        assert lc.score is None
        assert lc.candidate.origin == "linked"

    # LinkedIn profile was discovered and preserved, not dropped
    linkedin_claims = [lc for lc in linked_claims if "linkedin.com" in lc.candidate.page_url]
    assert len(linkedin_claims) == 1

    # Match count (ACCEPT + corroborating) must ONLY count biometric face matches
    face_matches = [r for r in result.match.all_scored if r.decision in ("ACCEPT", "corroborating")]
    assert all(r.score is not None and r.score >= POLICY.threshold for r in face_matches)
    assert not any(r.decision == "linked-claim" for r in face_matches)


def test_expanded_candidate_below_threshold_is_rejected(monkeypatch):
    """R-28: an expanded candidate with a different person's face is rejected (reject-below-threshold)."""
    detector = FaceDetector()
    embedder = FaceEmbedder()

    img = cv2.imread(str(FIX / "obama1.jpg"))
    faces = detector.detect(img)
    crop = align(img, faces[0].kps5)
    probe_vec = embedder.embed(crop).vec

    obama_page = "https://x.com/barackobama"
    obama_avatar = "https://pbs.twimg.com/profile_images/obama.jpg"
    obama_bytes = (FIX / "obama2.jpg").read_bytes()

    # Derived candidate will be GitHub (avatar: https://github.com/barackobama.png)
    # Serve different person's face at github avatar
    unrelated_bytes = (FIX / "lin_manuel_miranda.png").read_bytes()

    fake_http = FakeHttpCache(
        {
            obama_page: b"<html></html>",
            obama_avatar: obama_bytes,
            "https://github.com/barackobama.png": unrelated_bytes,
        }
    )
    monkeypatch.setattr("pipeline.verify.pipeline_run.get_http_cache", lambda: fake_http)

    provider = FakeProvider(
        [
            Candidate(
                image_url=obama_avatar,
                page_url=obama_page,
                source="fake",
                origin="face",
            )
        ]
    )

    result = run_pipeline(
        cv2.imencode(".png", crop)[1].tobytes(),
        probe_vec,
        [provider],
        detector,
        embedder,
        policy=POLICY,
        expand_profiles=True,
    )

    assert result.match.verdict == "MATCH"
    assert result.match.best.candidate.page_url == obama_page

    # Find the expanded github candidate
    github_cands = [r for r in result.match.all_scored if "github.com" in r.candidate.page_url]
    assert len(github_cands) == 1
    # It must be rejected below threshold, NOT accepted!
    assert github_cands[0].decision == "reject-below-threshold"
    assert github_cands[0].score < POLICY.threshold

