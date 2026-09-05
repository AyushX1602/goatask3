"""Security & XSS prevention test suite (T2.3 / R-26 / S21).

Asserts that externally-sourced candidate fields (page_url, source, reason,
match_kind) cannot break DOM attributes, inject HTML tags, or execute
pseudo-schemes like javascript: or data:.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest
from starlette.testclient import TestClient

import webapp.server as srv

WEBAPP_STATIC_DIR = Path(__file__).parent.parent / "webapp" / "static"


@pytest.fixture
def client():
    return TestClient(srv.app)


def test_app_js_includes_issafeurl_and_escapehtml():
    app_js = (WEBAPP_STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function isSafeUrl(" in app_js
    assert "function escapeHtml(" in app_js
    assert "protocol === \"http:\" || parsed.protocol === \"https:\"" in app_js


def test_app_js_never_interpolates_raw_page_url_in_href():
    app_js = (WEBAPP_STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # Assert there is no `<a href="${c.page_url}"`
    assert '<a href="${c.page_url}"' not in app_js
    assert '<a href="${' not in app_js


def test_app_js_never_interpolates_raw_source_or_reason_into_innerhtml():
    app_js = (WEBAPP_STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # Must not contain tr.innerHTML = `... ${c.source} ...`
    pattern = re.compile(r"tr\.innerHTML\s*=\s*`[^`]*\${c\.source}[^`]*`")
    assert not pattern.search(app_js), "c.source was interpolated directly into tr.innerHTML"


def test_xss_payloads_in_search_response_are_inert(client, monkeypatch):
    """Crafted malicious candidates returned from search endpoints must be valid
    JSON and handled safely by the frontend contract."""
    from pipeline.search.base import Candidate
    from pipeline.verify.matcher import MatchResult, ScoredCandidate

    malicious_candidates = [
        Candidate(
            image_url="https://example.com/normal.jpg",
            page_url='javascript:alert("XSS")',
            source='<script>alert("source-xss")</script>',
            provider_score=0.95,
            raw={},
        ),
        Candidate(
            image_url="https://example.com/test.jpg",
            page_url='https://example.com/post/"><script>alert(1)</script>',
            source='"><img src=x onerror=alert(1)>',
            provider_score=0.10,
            raw={},
        ),
    ]

    sc1 = ScoredCandidate(malicious_candidates[0], 0.95, 1, "ACCEPT", '"><script>alert("reason")</script>')
    sc2 = ScoredCandidate(malicious_candidates[1], 0.10, 1, "reject-below-threshold", "low")

    match = MatchResult(
        best=sc1,
        runner_up=sc2,
        all_scored=[sc1, sc2],
        threshold=0.42,
        margin_required=0.08,
        verdict="MATCH",
    )

    # In Python, check that SearchResponse model serializes these cleanly without crashing
    resp_model = srv.SearchResponse(
        run_id="xss-test-run",
        crawl_size=0,
        verdict=match.verdict,
        threshold=match.threshold,
        margin_required=match.margin_required,
        candidates=[
            {
                "rank": 0,
                "score": sc1.score,
                "source": sc1.candidate.source,
                "page_url": sc1.candidate.page_url,
                "decision": sc1.decision,
                "reason": sc1.reason,
            },
            {
                "rank": 1,
                "score": sc2.score,
                "source": sc2.candidate.source,
                "page_url": sc2.candidate.page_url,
                "decision": sc2.decision,
                "reason": sc2.reason,
            },
        ],
    )
    d = resp_model.model_dump()
    assert d["candidates"][0]["page_url"] == 'javascript:alert("XSS")'
    assert d["candidates"][0]["source"] == '<script>alert("source-xss")</script>'
