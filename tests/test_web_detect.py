"""F3 exit criteria (phases.md FINAL PLAN).

Fixture JSON must parse into Candidate objects, and empty image arrays must
be treated as a valid zero-candidate result rather than an error (A-08).

Runs entirely offline against fixtures — no API key, no network (D-30).
The SerpApi fixture is trimmed from a REAL captured response, so these
tests exercise the parser against what the API actually returns.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.search.base import Candidate
from pipeline.search.web_detect import (
    WebDetectProvider,
    parse_gcv,
    parse_serpapi_lens,
)
from pipeline.verify.allowlist import is_allowed

FIX = Path(__file__).parent / "fixtures" / "search"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ---------------- GCV parsing ----------------


def test_gcv_parses_into_candidates():
    cands, signals = parse_gcv(load("gcv_web_detection_obama.json"))

    assert cands, "fixture must yield candidates"
    assert all(isinstance(c, Candidate) for c in cands)
    assert all(c.source == "gcv_web_detection" for c in cands)
    assert all(c.page_url for c in cands)


def test_gcv_extracts_identity_signals_skipping_entries_without_description():
    _, signals = parse_gcv(load("gcv_web_detection_obama.json"))
    assert "Barack Obama" in signals
    assert "President" in signals
    # The third webEntity has a score but no description; it must be skipped
    # rather than producing a None/empty entry.
    assert all(s for s in signals)


def test_gcv_never_sets_provider_score():
    """R-03: a provider must not express an opinion the matcher could use."""
    cands, _ = parse_gcv(load("gcv_web_detection_obama.json"))
    assert all(c.provider_score is None for c in cands)


def test_gcv_finds_social_pages():
    cands, _ = parse_gcv(load("gcv_web_detection_obama.json"))
    social = [c for c in cands if is_allowed(c.page_url)]
    assert len(social) >= 2, "instagram + reddit pages should survive the allowlist"
    domains = {c.page_url.split("/")[2] for c in social}
    assert any("instagram" in d for d in domains)
    assert any("reddit" in d for d in domains)


def test_gcv_keeps_non_social_candidates_for_the_audit_log():
    """Off-allowlist candidates must still be returned so they can be scored
    and logged as rejected, not silently dropped (design.md 2.5)."""
    cands, _ = parse_gcv(load("gcv_web_detection_obama.json"))
    assert any(not is_allowed(c.page_url) for c in cands)


def test_gcv_page_without_image_arrays_yields_a_candidate_with_no_image_url():
    """The wikipedia entry in the fixture has no fullMatchingImages and no
    partialMatchingImages.

    UPDATED 5 Sep 2026: this test previously asserted `image_url == page_url`,
    which encoded a real bug as expected behaviour. Downloading a page URL
    as an image fetches HTML, fails to decode, and gets misreported as
    `reject-no-face` (see tests/test_web_detect_image_urls.py). The
    candidate is still produced — so the page appears in the audit trail —
    but with an empty image_url, which the pipeline reports honestly as
    `reject-no-image`."""
    cands, _ = parse_gcv(load("gcv_web_detection_obama.json"))
    wiki = [c for c in cands if "wikipedia.org" in c.page_url]
    assert wiki, "page with no image arrays must still produce a candidate"
    assert wiki[0].image_url == "", "must not reuse the page URL as an image URL"


def test_gcv_entities_only_response_is_zero_candidates_not_an_error():
    """A-08: documented GCV behaviour where all image arrays are absent."""
    cands, signals = parse_gcv(load("gcv_web_detection_entities_only.json"))
    assert cands == []
    assert "Barack Obama" in signals


def test_gcv_accepts_bare_webdetection_object():
    """Fixtures/callers sometimes hand us the inner object directly."""
    full = load("gcv_web_detection_obama.json")
    bare = full["responses"][0]
    from_full, _ = parse_gcv(full)
    from_bare, _ = parse_gcv(bare)
    assert len(from_full) == len(from_bare)


def test_gcv_empty_payload_is_safe():
    assert parse_gcv({}) == ([], [])
    assert parse_gcv({"responses": [{}]}) == ([], [])


# ---------------- SerpApi parsing ----------------


def test_serpapi_parses_real_captured_response():
    cands, signals = parse_serpapi_lens(load("serpapi_lens_obama.json"))
    assert cands
    assert all(c.source == "serpapi_lens" for c in cands)
    assert all(c.provider_score is None for c in cands)  # R-03
    # every candidate needs a fetchable image to verify against
    assert all(c.image_url for c in cands)


def test_serpapi_identity_signal_from_related_content():
    """The real captured response had related_content[0].query == 'Barack Obama'."""
    _, signals = parse_serpapi_lens(load("serpapi_lens_obama.json"))
    assert "Barack Obama" in signals


def test_serpapi_finds_social_domains_in_real_data():
    """Validates the core research finding that Lens returns social posts."""
    cands, _ = parse_serpapi_lens(load("serpapi_lens_obama.json"))
    social = [c for c in cands if is_allowed(c.page_url)]
    assert len(social) >= 3, "real captured data contained multiple social hits"
    domains = {c.page_url.split("/")[2].replace("www.", "") for c in social}
    # Reach across more than one platform is the point (prd.md S15)
    assert len(domains) >= 2


def test_serpapi_empty_payload_is_safe():
    assert parse_serpapi_lens({}) == ([], [])


def test_parsers_dedupe_repeated_image_urls():
    payload = {
        "visual_matches": [
            {"link": "https://a.test/1", "thumbnail": "https://img.test/same.jpg"},
            {"link": "https://b.test/2", "thumbnail": "https://img.test/same.jpg"},
        ]
    }
    cands, _ = parse_serpapi_lens(payload)
    assert len(cands) == 1


# ---------------- provider wiring ----------------


def test_provider_unavailable_without_any_key(monkeypatch):
    monkeypatch.delenv("GCV_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")
    p = WebDetectProvider()
    assert p.available() is False
    # An unavailable provider must return nothing rather than raising, so the
    # orchestrator can skip it silently (R-14).
    assert p.search(b"", np.zeros(512, dtype=np.float32)) == []


def test_auto_backend_prefers_gcv_for_larger_quota(monkeypatch):
    """D-28: 'auto' must pick gcv when both keys are present."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")
    assert WebDetectProvider().backend == "gcv"


def test_auto_backend_falls_back_to_serpapi(monkeypatch):
    monkeypatch.delenv("GCV_API_KEY", raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")
    assert WebDetectProvider().backend == "serpapi"


def test_explicit_backend_is_respected(monkeypatch):
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "serpapi")
    assert WebDetectProvider().backend == "serpapi"


def test_serpapi_backend_requires_a_public_url(monkeypatch):
    """D-31: SerpApi cannot take raw bytes, and the error must say so clearly
    rather than failing obscurely at request time."""
    monkeypatch.delenv("GCV_API_KEY", raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "serpapi")
    p = WebDetectProvider()
    with pytest.raises(ValueError, match="publicly reachable image URL"):
        p.search(b"fakebytes", np.zeros(512, dtype=np.float32))


def test_gcv_backend_sends_base64_and_parses(monkeypatch):
    """End-to-end through the provider with a stubbed HTTP layer: proves the
    gcv path needs no public URL (the D-28 advantage) and that the request
    body carries base64 image content."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "gcv")

    captured: dict = {}
    fixture = load("gcv_web_detection_obama.json")

    class StubHttp:
        def post(self, url, params=None, json_body=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            captured["body"] = json_body

            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return fixture

            return R()

    p = WebDetectProvider(http=StubHttp())
    cands = p.search(b"\xff\xd8\xff-fake-jpeg", np.zeros(512, dtype=np.float32))

    assert cands, "provider must return parsed candidates"
    assert "vision.googleapis.com" in captured["url"]
    assert captured["params"]["key"] == "fake-gcv"
    req = captured["body"]["requests"][0]
    assert req["features"][0]["type"] == "WEB_DETECTION"
    assert req["image"]["content"], "image must be sent as base64 content"
    assert "Barack Obama" in p.last_identity_signals


# ---------------- G-series: match_kind, GitHub profile rewrite ----------------


def test_full_matching_image_gets_match_kind_full():
    payload = {
        "webDetection": {
            "pagesWithMatchingImages": [
                {
                    "url": "https://github.com/SilenNaihin/isomorphic",
                    "fullMatchingImages": [{"url": "https://avatars.githubusercontent.com/u/1?v=4"}],
                }
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    assert cands[0].match_kind == "full"


def test_github_repo_page_url_is_rewritten_to_profile():
    """Verified live 5 Sep 2026: GCV returned page_url =
    github.com/<user>/<repo> for a real candidate. The avatar belongs to
    the user's profile, not the repo, so page_url must be rewritten before
    the allowlist/content_kind layers ever see it."""
    payload = {
        "webDetection": {
            "pagesWithMatchingImages": [
                {
                    "url": "https://github.com/SilenNaihin/isomorphic",
                    "fullMatchingImages": [{"url": "https://avatars.githubusercontent.com/u/1?v=4"}],
                }
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    assert cands[0].page_url == "https://github.com/SilenNaihin"
    assert cands[0].raw["found_on"] == "https://github.com/SilenNaihin/isomorphic"


def test_github_bare_profile_page_is_not_rewritten():
    payload = {
        "webDetection": {
            "pagesWithMatchingImages": [
                {
                    "url": "https://github.com/SilenNaihin",
                    "fullMatchingImages": [{"url": "https://avatars.githubusercontent.com/u/1?v=4"}],
                }
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    assert cands[0].page_url == "https://github.com/SilenNaihin"
    assert cands[0].raw["found_on"] is None


def test_visually_similar_image_gets_match_kind_similar():
    payload = {"webDetection": {"visuallySimilarImages": [{"url": "https://real.test/lookalike.jpg"}]}}
    cands, _ = parse_gcv(payload)
    assert cands[0].match_kind == "similar"


# ---------------- F-05 fix: prefer full-size image over thumbnail ----------------


def test_serpapi_visual_match_prefers_image_over_thumbnail():
    """Ordering was previously backwards (`thumbnail or image`), and
    thumbnails are small enough to routinely fail the 50px face-quality
    gate. `image` (full-size) must win when both are present."""
    payload = {
        "visual_matches": [
            {"link": "https://a.test/1", "thumbnail": "https://img.test/thumb.jpg", "image": "https://img.test/full.jpg"},
        ]
    }
    cands, _ = parse_serpapi_lens(payload)
    assert cands[0].image_url == "https://img.test/full.jpg"
    assert cands[0].raw["image_field_used"] == "image"


def test_serpapi_visual_match_falls_back_to_thumbnail_when_no_full_image():
    payload = {
        "visual_matches": [
            {"link": "https://a.test/1", "thumbnail": "https://img.test/thumb.jpg"},
        ]
    }
    cands, _ = parse_serpapi_lens(payload)
    assert cands[0].image_url == "https://img.test/thumb.jpg"
    assert cands[0].raw["image_field_used"] == "thumbnail"


def test_serpapi_organic_result_prefers_images_array_over_thumbnail():
    payload = {
        "organic_results": [
            {"link": "https://a.test/1", "thumbnail": "https://img.test/thumb.jpg", "images": ["https://img.test/full.jpg"]},
        ]
    }
    cands, _ = parse_serpapi_lens(payload)
    assert cands[0].image_url == "https://img.test/full.jpg"
    assert cands[0].raw["image_field_used"] == "images[0]"


# ---------------- T1.2 remainder: match_kind defaults, "page" kind, SerpApi ----------------


def test_candidate_match_kind_defaults_to_unknown():
    from pipeline.search.base import Candidate

    c = Candidate(image_url="https://x.test/1", page_url="https://x.test/1", source="fake")
    assert c.match_kind == "unknown"


def test_gcv_page_with_no_image_gets_match_kind_page():
    """A pagesWithMatchingImages entry with no fullMatchingImages/
    partialMatchingImages of its own is a page-level signal only — a
    distinct, honestly-labelled weak signal, not "unknown" (which would
    mean nobody classified it) and not "" (which reads as unset data)."""
    payload = {
        "webDetection": {
            "pagesWithMatchingImages": [
                {"url": "https://open.spotify.com/track/abc", "pageTitle": "a track"},
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    assert cands[0].match_kind == "page"


def test_serpapi_visual_match_gets_match_kind_similar():
    payload = {"visual_matches": [{"link": "https://a.test/1", "image": "https://img.test/full.jpg"}]}
    cands, _ = parse_serpapi_lens(payload)
    assert cands[0].match_kind == "similar"


def test_serpapi_organic_result_defaults_to_unknown_match_kind():
    payload = {"organic_results": [{"link": "https://a.test/1", "thumbnail": "https://img.test/t.jpg"}]}
    cands, _ = parse_serpapi_lens(payload)
    assert cands[0].match_kind == "unknown"
