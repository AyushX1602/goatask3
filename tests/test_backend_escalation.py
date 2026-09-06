"""Tests for quality-triggered search backend escalation (Item 4 / F1b).

Tests 1-8: escalation behaviour (quality signals, generic entities, merge, etc.)
Test 9: auto ordering with SEARCH_LENS_UPLOAD=1 prefers serpapi first (F1b)
Test 10: GENERIC_ENTITIES contains all 11 required terms
"""

import json
from pathlib import Path
import numpy as np

from pipeline.search.base import Candidate
from pipeline.search.web_detect import (
    GENERIC_ENTITIES,
    WebDetectProvider,
    merge_candidates,
    parse_gcv,
    result_carries_identity_signal,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def test_celebrity_entity_prevents_escalation(monkeypatch):
    """1. GCV returns celebrity entity ('Barack Obama') -> does not escalate to SerpApi."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")

    provider = WebDetectProvider()
    serpapi_called = False

    def mock_gcv(bytes_):
        return ([], ["Barack Obama"])

    def mock_serp(*, url=None, image_id=None):
        nonlocal serpapi_called
        serpapi_called = True
        return ([], ["SerpApi Signal"])

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)
    monkeypatch.setattr(provider, "_search_serpapi", mock_serp)

    cands = provider.search(b"image", np.zeros(512), public_image_url="https://example.com/pub.jpg")
    assert not serpapi_called
    assert cands == []
    assert provider.last_identity_signals == ["Barack Obama"]
    assert provider.last_backends_attempted == ["gcv"]


def test_generic_entities_only_triggers_escalation(monkeypatch):
    """2. GCV returns generic entities only ('Human', 'Face', 'Forehead') and 0 candidates -> escalates to SerpApi."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")

    provider = WebDetectProvider()
    serpapi_called = False

    def mock_gcv(bytes_):
        return ([], ["Human", "Face", "Forehead"])

    def mock_serp(*, url=None, image_id=None):
        nonlocal serpapi_called
        serpapi_called = True
        cand = Candidate(
            image_url="https://pbs.twimg.com/media/test.jpg",
            page_url="https://x.com/user/status/1",
            source="serpapi_lens",
        )
        return ([cand], ["Specific Person"])

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)
    monkeypatch.setattr(provider, "_search_serpapi", mock_serp)

    cands = provider.search(b"image", np.zeros(512), public_image_url="https://example.com/pub.jpg")
    assert serpapi_called
    assert provider.last_backends_attempted == ["gcv", "serpapi"]
    assert len(cands) == 1
    assert cands[0].source == "serpapi_lens"


def test_allowlisted_candidate_with_image_prevents_escalation(monkeypatch):
    """3. GCV returns candidate on allowlisted domain with image -> does not escalate."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")

    provider = WebDetectProvider()
    serpapi_called = False

    def mock_gcv(bytes_):
        cand = Candidate(
            image_url="https://pbs.twimg.com/media/x.jpg",
            page_url="https://x.com/barackobama",
            source="gcv_web_detection",
            match_kind="full",
        )
        return ([cand], ["Human"])

    def mock_serp(*, url=None, image_id=None):
        nonlocal serpapi_called
        serpapi_called = True
        return ([], [])

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)
    monkeypatch.setattr(provider, "_search_serpapi", mock_serp)

    cands = provider.search(b"image", np.zeros(512), public_image_url="https://example.com/pub.jpg")
    assert not serpapi_called
    assert len(cands) == 1
    assert provider.last_backends_attempted == ["gcv"]


def test_non_allowlisted_candidate_without_image_triggers_escalation(monkeypatch):
    """4. GCV returns candidate on non-allowlisted domain without image -> escalates."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")

    provider = WebDetectProvider()
    serpapi_called = False

    def mock_gcv(bytes_):
        cand = Candidate(
            image_url="",
            page_url="https://random-unrelated-blog.com/page",
            source="gcv_web_detection",
            match_kind="page",
        )
        return ([cand], ["Human"])

    def mock_serp(*, url=None, image_id=None):
        nonlocal serpapi_called
        serpapi_called = True
        cand2 = Candidate(
            image_url="https://i.redd.it/post.jpg",
            page_url="https://reddit.com/r/pics/1",
            source="serpapi_lens",
        )
        return ([cand2], ["Reddit User"])

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)
    monkeypatch.setattr(provider, "_search_serpapi", mock_serp)

    cands = provider.search(b"image", np.zeros(512), public_image_url="https://example.com/pub.jpg")
    assert serpapi_called
    assert provider.last_backends_attempted == ["gcv", "serpapi"]
    assert len(cands) == 2


def test_escalation_disabled_by_config(monkeypatch):
    """5. WEB_DETECT_ESCALATE=0 disables escalation even on empty/generic GCV result."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")
    monkeypatch.setenv("WEB_DETECT_ESCALATE", "0")

    provider = WebDetectProvider()
    serpapi_called = False

    def mock_gcv(bytes_):
        return ([], ["Human"])

    def mock_serp(*, url=None, image_id=None):
        nonlocal serpapi_called
        serpapi_called = True
        return ([], [])

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)
    monkeypatch.setattr(provider, "_search_serpapi", mock_serp)

    cands = provider.search(b"image", np.zeros(512), public_image_url="https://example.com/pub.jpg")
    assert not serpapi_called
    assert cands == []
    assert provider.last_backends_attempted == ["gcv"]


def test_escalation_skips_serpapi_when_no_public_url_and_upload_disabled(monkeypatch):
    """6. Escalation skips SerpApi when public_image_url is None and SEARCH_LENS_UPLOAD=0, recording skip reason."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "0")

    provider = WebDetectProvider()

    def mock_gcv(bytes_):
        return ([], ["Human"])

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)

    cands = provider.search(b"image", np.zeros(512), public_image_url=None)
    assert cands == []
    assert provider.last_backends_attempted == ["gcv", "serpapi"]
    assert provider.last_skip_reason == "serpapi escalation skipped: no public URL and SEARCH_LENS_UPLOAD=0"


def test_candidate_merging_preserves_higher_quality_match_kind():
    """7. When escalation runs, candidates from both backends are merged and deduped, preserving higher match_kind."""
    cand_gcv = Candidate(
        image_url="https://example.com/photo.jpg",
        page_url="https://x.com/post/1",
        source="gcv_web_detection",
        match_kind="page",
    )
    cand_lens = Candidate(
        image_url="https://example.com/photo.jpg",
        page_url="https://x.com/post/1",
        source="serpapi_lens",
        match_kind="similar",
    )
    cand_other = Candidate(
        image_url="https://example.com/photo2.jpg",
        page_url="https://github.com/user",
        source="serpapi_lens",
        match_kind="partial",
    )

    merged = merge_candidates([cand_gcv], [cand_lens, cand_other])
    assert len(merged) == 2
    # photo.jpg should have been updated to similar (higher quality than page)
    assert merged[0].image_url == "https://example.com/photo.jpg"
    assert merged[0].match_kind == "similar"
    assert merged[1].image_url == "https://example.com/photo2.jpg"


def test_probe_22_53_02_regression_triggers_escalation(monkeypatch):
    """8. Regression test: probe 22-53-02Z cached GCV response triggers escalation when SerpApi key is present."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")

    fixture_path = FIX / "search" / "gcv_22_53_02.json"
    gcv_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    cands_gcv, signals_gcv = parse_gcv(gcv_payload)

    # Confirm fixture itself produces no identity signal
    assert not result_carries_identity_signal(cands_gcv, signals_gcv)
    assert all(s.lower() in GENERIC_ENTITIES for s in signals_gcv)

    provider = WebDetectProvider()
    serpapi_called = False

    def mock_gcv(bytes_):
        return cands_gcv, signals_gcv

    def mock_serp(*, url=None, image_id=None):
        nonlocal serpapi_called
        serpapi_called = True
        lens_cand = Candidate(
            image_url="https://pbs.twimg.com/media/found.jpg",
            page_url="https://x.com/subject",
            source="serpapi_lens",
            match_kind="similar",
        )
        return ([lens_cand], ["Subject Name"])

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)
    monkeypatch.setattr(provider, "_search_serpapi", mock_serp)

    results = provider.search(b"probe_bytes", np.zeros(512), public_image_url="https://example.com/probe.jpg")

    assert serpapi_called
    assert provider.last_backends_attempted == ["gcv", "serpapi"]
    # Results should include the recovered Lens candidate
    assert any(c.source == "serpapi_lens" for c in results)


def test_auto_backend_ordering_prefers_serpapi_when_upload_enabled(monkeypatch):
    """9. SEARCH_LENS_UPLOAD=1 → auto ordering is ['serpapi', 'gcv'] (F1b / D-28).

    When a public URL is available (upload enabled), SerpApi/Lens goes first because
    it benefits from the public URL and provides richer visual matching results.
    When upload is off, GCV goes first because it accepts raw bytes and has ~10x larger quota.
    """
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "1")

    provider = WebDetectProvider()
    # With upload on, serpapi should come first
    assert provider.backends == ["serpapi", "gcv"]

    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "0")
    provider2 = WebDetectProvider()
    # With upload off, gcv should come first (D-28)
    assert provider2.backends == ["gcv", "serpapi"]


def test_generic_entities_contains_all_11_required_terms():
    """10. GENERIC_ENTITIES must contain all 11 terms from the spec (F2 / §3w)."""
    required = {
        "human", "person", "face", "photograph", "portrait",
        "chin", "forehead", "head", "smile", "hair", "eyebrow",
    }
    assert required.issubset(GENERIC_ENTITIES), (
        f"GENERIC_ENTITIES is missing: {required - GENERIC_ENTITIES}"
    )
    assert len(GENERIC_ENTITIES) >= 11


def test_escalation_uploads_head_crop_when_no_public_url(monkeypatch):
    """11. F1a wiring: with SEARCH_LENS_UPLOAD=1 and no public_image_url, the
    Lens escalation uploads the head crop directly to SerpApi and Lens runs
    on the returned image_id (no public image host involved)."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "1")

    provider = WebDetectProvider()
    lens_image_ids: list[str] = []

    def mock_gcv(bytes_):
        return ([], ["Human"])  # no identity signal -> escalate

    def mock_serp(*, url=None, image_id=None):
        lens_image_ids.append(image_id or "")
        return ([], ["SerpApi Signal"])

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)
    monkeypatch.setattr(provider, "_search_serpapi", mock_serp)

    import pipeline.search.web_detect as wd

    def mock_upload(jpeg_bytes, http, *, is_head_crop):
        assert is_head_crop is True
        assert jpeg_bytes == b"head-crop-jpeg"
        return "serp-image-123"

    monkeypatch.setattr(wd, "upload_crop_to_serpapi", mock_upload)

    cands = provider.search(b"image", np.zeros(512), head_crop_bytes=b"head-crop-jpeg")
    assert lens_image_ids == ["serp-image-123"]
    assert provider.last_skip_reason is None
    # Lens returned a non-generic identity signal, so escalation stops there
    # (quality bar met) — gcv is never attempted. Correct behaviour.
    assert provider.last_backends_attempted == ["serpapi"]
    assert cands == []


def test_upload_failure_keeps_gcv_results(monkeypatch):
    """12. A failed Lens upload must degrade to a skip reason — never lose
    the GCV candidates already merged (R-14)."""
    monkeypatch.setenv("GCV_API_KEY", "fake-gcv")
    monkeypatch.setenv("SERPAPI_KEY", "fake-serp")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "auto")
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "1")

    provider = WebDetectProvider()

    from pipeline.search.base import Candidate as C

    gcv_candidate = C(
        image_url="https://example.com/gcv-hit.jpg",
        page_url="https://example.com/gcv-hit.jpg",
        source="gcv_web_detection",
    )

    def mock_gcv(bytes_):
        return ([gcv_candidate], ["Human"])  # candidate present, no strong signal

    def mock_serp(*, url=None, image_id=None):
        raise AssertionError("Lens must not run when the upload failed")

    monkeypatch.setattr(provider, "_search_gcv", mock_gcv)
    monkeypatch.setattr(provider, "_search_serpapi", mock_serp)

    import pipeline.search.web_detect as wd
    from pipeline.search.uploader import UploadError

    def mock_upload(jpeg_bytes, http, *, is_head_crop):
        raise UploadError("SerpApi upload endpoint down")

    monkeypatch.setattr(wd, "upload_crop_to_serpapi", mock_upload)

    cands = provider.search(b"image", np.zeros(512), head_crop_bytes=b"head-crop-jpeg")
    assert cands == [gcv_candidate]
    assert "upload failed" in (provider.last_skip_reason or "")
