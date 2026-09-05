"""Tests for SERP-based profile resolution and Instagram owner recovery (T2.6, R-28, R-14)."""

from pathlib import Path
import cv2
from pipeline.config import MatchPolicy
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.search.base import Candidate, SearchProvider
from pipeline.search.expand import expand_verified_candidates
from pipeline.search.serp_resolve import (
    SERPAPI_ENDPOINT,
    linkedin_profiles,
    post_owner,
    sanitize_handle,
    search_profiles,
)
from pipeline.verify.pipeline_run import run_pipeline

FIX = Path(__file__).resolve().parent / "fixtures"
POLICY = MatchPolicy(threshold=0.42, margin=0.08, model="w600k_r50", target_fmr=0.01, is_placeholder=True)


class FakeProvider(SearchProvider):
    name = "fake"

    def __init__(self, candidates: list[Candidate]) -> None:
        self._cands = candidates

    def available(self) -> bool:
        return True

    def search(self, image_bytes: bytes, probe_vec, *, public_image_url: str | None = None) -> list[Candidate]:
        return list(self._cands)


class MockSerpHttp:
    """Mock HttpCache for SerpApi responses."""

    def __init__(self, response_data: dict | None = None, status_code: int = 200, raises: bool = False) -> None:
        self.response_data = response_data or {}
        self.status_code = status_code
        self.raises = raises
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        if self.raises:
            raise ConnectionError("Network down")

        class Resp:
            def __init__(self, ok: bool, status: int, data: dict):
                self.ok = ok
                self.status_code = status
                self._data = data

            def json(self):
                return self._data

        return Resp(self.status_code == 200, self.status_code, self.response_data)


def test_linkedin_with_thumbnail_produces_origin_face(monkeypatch):
    """Synthetic response with LinkedIn organic result + thumbnail produces Candidate with origin='face'."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://www.linkedin.com/in/satyanadella",
                "title": "Satya Nadella - Chairman and CEO - Microsoft | LinkedIn",
                "thumbnail": "https://example.com/satya-thumb.jpg",
            }
        ]
    }
    http = MockSerpHttp(mock_resp)

    cands = linkedin_profiles("satyanadella", http)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.source == "serp-linkedin"
    assert cand.origin == "face"
    assert cand.image_url == "https://example.com/satya-thumb.jpg"
    assert cand.page_url == "https://www.linkedin.com/in/satyanadella"


def test_linkedin_without_thumbnail_produces_origin_linked(monkeypatch):
    """Synthetic response without thumbnail produces Candidate with origin='linked' and image_url=''."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://www.linkedin.com/in/satyanadella",
                "title": "Satya Nadella - Chairman and CEO - Microsoft | LinkedIn",
            }
        ]
    }
    http = MockSerpHttp(mock_resp)

    cands = linkedin_profiles("satyanadella", http)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.source == "serp-linkedin"
    assert cand.origin == "linked"
    assert cand.image_url == ""
    assert cand.page_url == "https://www.linkedin.com/in/satyanadella"


def test_non_profile_urls_filtered_out(monkeypatch):
    """Non-profile LinkedIn URLs (/jobs/, /company/, /school/, /pub/dir/, etc.) are discarded."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    mock_resp = {
        "organic_results": [
            {"link": "https://www.linkedin.com/jobs/view/123456", "title": "Job Posting"},
            {"link": "https://www.linkedin.com/company/microsoft", "title": "Microsoft Company Page"},
            {"link": "https://www.linkedin.com/school/harvard-university", "title": "Harvard School Page"},
            {"link": "https://www.linkedin.com/pub/dir/John/Doe", "title": "Directory Page"},
            {"link": "https://www.linkedin.com/posts/satyanadella_ai-activity-12345", "title": "Post Page"},
            {"link": "https://www.linkedin.com/in/real-satya", "title": "Real Profile", "thumbnail": "https://example.com/thumb.jpg"},
        ]
    }
    http = MockSerpHttp(mock_resp)

    cands = linkedin_profiles("satya", http)
    assert len(cands) == 1
    assert cands[0].page_url == "https://www.linkedin.com/in/real-satya"


def test_profile_url_rewriting_canonical(monkeypatch):
    """Regional subdomains, query params, and trailing slashes are canonicalized to https://www.linkedin.com/in/{slug}."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://in.linkedin.com/in/satyanadella/?locale=en_US",
                "title": "Satya Nadella",
                "thumbnail": "https://example.com/thumb.jpg",
            }
        ]
    }
    http = MockSerpHttp(mock_resp)

    cands = linkedin_profiles("satyanadella", http)
    assert len(cands) == 1
    assert cands[0].page_url == "https://www.linkedin.com/in/satyanadella"


def test_serp_call_count_bounded_by_config(monkeypatch):
    """Expansion respects EXPAND_SERP_MAX_CALLS and bounds SERP queries."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.setenv("EXPAND_SERP_MAX_CALLS", "1")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://www.linkedin.com/in/user1",
                "thumbnail": "https://example.com/user1.jpg",
            }
        ]
    }
    http = MockSerpHttp(mock_resp)

    verified = [
        Candidate(page_url="https://github.com/user1", image_url="https://github.com/user1.png", source="fake", origin="face"),
        Candidate(page_url="https://x.com/user2", image_url="https://x.com/user2.jpg", source="fake", origin="face"),
    ]

    expanded = expand_verified_candidates(verified, http=http)
    # Only 1 SERP call should have been made despite 2 candidates
    assert len(http.calls) == 1
    serp_cands = [c for c in expanded if c.source == "serp-linkedin"]
    assert len(serp_cands) == 1


def test_empty_or_error_serp_returns_empty_never_raises(monkeypatch):
    """Empty / 404 / 500 error / network exception from SERP response returns empty list (R-14)."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    # 1. 500 status code
    http_err = MockSerpHttp({}, status_code=500)
    assert linkedin_profiles("test", http_err) == []

    # 2. Network exception
    http_raise = MockSerpHttp({}, raises=True)
    assert linkedin_profiles("test", http_raise) == []

    # 3. Empty organic results
    http_empty = MockSerpHttp({"organic_results": []})
    assert linkedin_profiles("test", http_empty) == []


def test_handle_sanitization(monkeypatch):
    """Handles with spaces, quotes, punctuation are sanitized before query construction."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    assert sanitize_handle('@"john_doe" ') == "john_doe"
    assert sanitize_handle("<script>alert(1)</script>") == "scriptalert(1)script"

    http = MockSerpHttp({"organic_results": []})
    search_profiles(' @"john_doe" \n', "linkedin.com/in", http)

    assert len(http.calls) == 1
    query = http.calls[0]["kwargs"]["params"]["q"]
    assert query == 'site:linkedin.com/in "john_doe"'


def test_face_gating_integration_in_pipeline(monkeypatch):
    """Face-gating integration:
    - Expanded candidate with thumbnail is scored by ArcFace in run_pipeline.
    - Expanded candidate without thumbnail is reported as linked-claim.
    """
    detector = FaceDetector()
    embedder = FaceEmbedder()

    img = cv2.imread(str(FIX / "obama1.jpg"))
    faces = detector.detect(img)
    crop = align(img, faces[0].kps5)
    probe_vec = embedder.embed(crop).vec

    obama_page = "https://x.com/barackobama"
    obama_avatar = "https://example.com/obama-x.jpg"
    obama_bytes = (FIX / "obama2.jpg").read_bytes()

    # Two LinkedIn profiles: one with thumbnail (verified face), one without
    linkedin_with_thumb = "https://www.linkedin.com/in/barackobama"
    linkedin_thumb_url = "https://example.com/linkedin-obama.jpg"

    linkedin_no_thumb = "https://www.linkedin.com/in/barackobama-foundation"

    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.setenv("EXPAND_SERP_MAX_CALLS", "2")

    class CompositeHttp:
        def __init__(self):
            self.hits = 0
            self.misses = 0

        def get(self, url: str, **kw):
            if url == SERPAPI_ENDPOINT:
                data = {
                    "organic_results": [
                        {
                            "link": linkedin_with_thumb,
                            "thumbnail": linkedin_thumb_url,
                        },
                        {
                            "link": linkedin_no_thumb,
                        },
                    ]
                }

                class Resp:
                    ok = True
                    status_code = 200

                    def json(self):
                        return data

                return Resp()

            class ImageResp:
                ok = True
                status_code = 200
                content_type = "image/jpeg"

                def __init__(self, c):
                    self.content = c

                def json(self):
                    return None

            if url in (obama_avatar, linkedin_thumb_url):
                return ImageResp(obama_bytes)

            return ImageResp(b"<html></html>")

    composite_http = CompositeHttp()
    monkeypatch.setattr("pipeline.verify.pipeline_run.get_http_cache", lambda: composite_http)

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

    scored_cands = result.match.all_scored

    # 1. Candidate with thumbnail must be scored by ArcFace and accepted (corroborating)
    li_thumb_cand = next((r for r in scored_cands if r.candidate.page_url == linkedin_with_thumb), None)
    assert li_thumb_cand is not None
    assert li_thumb_cand.decision in ("ACCEPT", "corroborating")
    assert li_thumb_cand.score is not None and li_thumb_cand.score >= POLICY.threshold
    assert li_thumb_cand.candidate.origin == "face"

    # 2. Candidate without thumbnail must be reported as linked-claim (unscored)
    li_no_thumb_cand = next((r for r in scored_cands if r.candidate.page_url == linkedin_no_thumb), None)
    assert li_no_thumb_cand is not None
    assert li_no_thumb_cand.decision == "linked-claim"
    assert li_no_thumb_cand.score is None
    assert li_no_thumb_cand.candidate.origin == "linked"


# --- Item 3: Instagram Owner Recovery Tests ---

def test_instagram_post_owner_title_match(monkeypatch):
    """Title 'Author (@handle) on Instagram: ...' extracts handle."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://www.instagram.com/p/C_123456789/",
                "title": "Jane Doe (@janedoe) on Instagram: 'Sunset at the beach'",
                "snippet": "1,234 likes, 56 comments - Follow @otherperson for more",
            }
        ]
    }
    http = MockSerpHttp(mock_resp)

    owner = post_owner("https://www.instagram.com/p/C_123456789/", http)
    assert owner == "janedoe"


def test_instagram_snippet_ignored(monkeypatch):
    """Snippet text containing other @mentions is IGNORED and never hallucinated as owner."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://www.instagram.com/p/C_123456789/",
                "title": "Photo from Instagram",  # No handle in title
                "snippet": "Photo by @hallucinated_user and @photographer with 50 likes",
            }
        ]
    }
    http = MockSerpHttp(mock_resp)

    owner = post_owner("https://www.instagram.com/p/C_123456789/", http)
    assert owner is None


def test_instagram_shortcode_mismatch_returns_none(monkeypatch):
    """Shortcode mismatch between SERP result and queried URL returns None."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://www.instagram.com/p/DIFFERENT_CODE/",
                "title": "Jane Doe (@janedoe) on Instagram",
            }
        ]
    }
    http = MockSerpHttp(mock_resp)

    owner = post_owner("https://www.instagram.com/p/TARGET_CODE/", http)
    assert owner is None


def test_instagram_reserved_words_rejected(monkeypatch):
    """Reserved segments (reels, p, explore, etc.) are never returned as owner handle."""
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://www.instagram.com/p/C_123456789/",
                "title": "Instagram (@reels) on Instagram",
            }
        ]
    }
    http = MockSerpHttp(mock_resp)

    owner = post_owner("https://www.instagram.com/p/C_123456789/", http)
    assert owner is None
