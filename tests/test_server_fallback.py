"""Regression test for a real bug found during deep review (5 Sep 2026):

`WebDetectProvider.available()` returns True whenever an API key is
configured, but that does not mean a search will SUCCEED. In particular,
the serpapi backend raises internally when no public_image_url is
supplied (by design, R-14 catches it), which surfaced as the primary path
silently returning zero candidates and NO_MATCH — with no indication that
the "web search" never actually happened. architecture.md 9's documented
fallback behaviour ("Both web-detection backends down -> Bluesky fallback,
labelled degraded") was not implemented for this specific failure mode.

Fixed in webapp/server.py's search() endpoint: primary path is judged by
provider_reports (did it actually return candidates), not by available().
"""

from __future__ import annotations

import importlib

import numpy as np
import pytest


@pytest.fixture
def server_module(monkeypatch):
    """Reloads webapp.server with SERPAPI_KEY set but no GCV key, matching
    the exact conditions of the bug: a key exists, but the primary path
    cannot succeed without a public_image_url."""
    monkeypatch.setenv("SERPAPI_KEY", "fake-key-for-test")
    monkeypatch.setenv("WEB_DETECT_BACKEND", "serpapi")
    monkeypatch.delenv("GCV_API_KEY", raising=False)
    import webapp.server as srv

    importlib.reload(srv)
    yield srv
    # leave module state clean for any subsequent test importing it
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("WEB_DETECT_BACKEND", raising=False)
    importlib.reload(srv)


def test_web_detect_available_but_cannot_succeed_without_public_url(server_module):
    """Documents the exact precondition that caused the bug."""
    assert server_module._web_detect.available() is True
    with pytest.raises(ValueError, match="publicly reachable image URL"):
        server_module._web_detect.search(b"", np.zeros(512, dtype=np.float32))


def test_primary_produced_nothing_triggers_bluesky_fallback(server_module, monkeypatch, tmp_path):
    """The actual regression: run the /api/search logic path (not the full
    HTTP layer) and confirm it falls back rather than silently reporting
    an empty MATCH search as if it were a real, exhausted web search."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server_module, "RUNS_DIR", tmp_path)
    client = TestClient(server_module.app)

    with open("tests/fixtures/obama1.jpg", "rb") as f:
        scan_resp = client.post("/api/upload", files={"frame": ("obama.jpg", f, "image/jpeg")})
    scan_data = scan_resp.json()
    assert scan_data["face_found"] is True
    run_id = scan_data["run_id"]

    # Force a tiny, fast Bluesky crawl instead of the real network crawl,
    # so this test doesn't depend on live network access.
    server_module._bluesky.crawl_limit = 5

    search_resp = client.post(f"/api/search/{run_id}")
    data = search_resp.json()

    # BEFORE the fix: degraded_closed_corpus=False, candidates=[] — a
    # silent, misleading "the web search ran and found nothing."
    # AFTER the fix: degraded_closed_corpus=True, and the audit trail
    # shows the bluesky provider was actually attempted.
    assert data["degraded_closed_corpus"] is True


def test_audit_trail_shows_both_the_failed_primary_and_the_fallback(server_module, monkeypatch, tmp_path):
    """The fix must not silently swap providers — both attempts must be
    visible (rules.md never-cut: full candidate/provider trail)."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server_module, "RUNS_DIR", tmp_path)
    client = TestClient(server_module.app)
    server_module._bluesky.crawl_limit = 5

    with open("tests/fixtures/obama1.jpg", "rb") as f:
        scan_resp = client.post("/api/upload", files={"frame": ("obama.jpg", f, "image/jpeg")})
    run_id = scan_resp.json()["run_id"]

    client.post(f"/api/search/{run_id}")

    import json

    audit = json.loads((tmp_path / run_id / "audit.json").read_text(encoding="utf-8"))
    provider_names = {p["name"] for p in audit["providers"]}
    assert "web_detect" in provider_names, "the failed primary attempt must still be logged"
    assert "bluesky" in provider_names, "the fallback attempt must be logged"

    web_detect_report = next(p for p in audit["providers"] if p["name"] == "web_detect")
    assert web_detect_report["error"] is not None
    assert "publicly reachable image URL" in web_detect_report["error"]

