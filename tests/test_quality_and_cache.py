"""F1 + F2 exit criteria (phases.md FINAL PLAN).

F1: a repeated request is served from cache and makes no network call.
F2: a 30px face is rejected, a 200px face passes.
"""

from __future__ import annotations

import numpy as np

from pipeline.cache.http_cache import HttpCache, cache_key
from pipeline.face.quality import DEFAULT_MIN_FACE_PX, face_size_px, passes
from pipeline.face.types import DetectedFace


def _face(px: float) -> DetectedFace:
    return DetectedFace(
        bbox=(10.0, 10.0, 10.0 + px, 10.0 + px),
        kps5=np.zeros((5, 2), dtype=np.float32),
        det_score=0.95,
    )


# ---------------- F2: quality gate ----------------


def test_small_face_rejected():
    ok, reason = passes(_face(30))
    assert not ok
    assert "30px" in reason and str(DEFAULT_MIN_FACE_PX) in reason


def test_large_face_passes():
    ok, reason = passes(_face(200))
    assert ok
    assert reason == ""


def test_boundary_is_inclusive_at_minimum():
    assert passes(_face(DEFAULT_MIN_FACE_PX))[0]
    assert not passes(_face(DEFAULT_MIN_FACE_PX - 1))[0]


def test_face_size_uses_shorter_side():
    wide = DetectedFace(
        bbox=(0.0, 0.0, 300.0, 40.0),  # wide but short
        kps5=np.zeros((5, 2), dtype=np.float32),
        det_score=0.9,
    )
    assert face_size_px(wide) == 40.0
    assert not passes(wide)[0]


# ---------------- F1: http cache ----------------


def test_cache_key_is_stable_and_param_order_independent():
    a = cache_key("GET", "https://x.test/a", {"b": 2, "a": 1})
    b = cache_key("GET", "https://x.test/a", {"a": 1, "b": 2})
    assert a == b


def test_cache_key_changes_with_url_and_body():
    base = cache_key("GET", "https://x.test/a", {"a": 1})
    assert base != cache_key("GET", "https://x.test/b", {"a": 1})
    assert base != cache_key("POST", "https://x.test/a", {"a": 1})
    assert base != cache_key("GET", "https://x.test/a", {"a": 2})


def test_second_request_served_from_cache_without_network(tmp_path, monkeypatch):
    """F1 exit criterion. Uses a fake session so a network call would be
    detectable: the counter must not advance on the second request."""
    cache = HttpCache(enabled=True, cache_dir=tmp_path)
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        content = b'{"hello":"world"}'
        url = "https://x.test/thing"
        ok = True

    class FakeSession:
        headers: dict = {}

        def request(self, *a, **kw):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(cache, "_session", FakeSession())

    r1 = cache.get("https://x.test/thing", params={"q": "1"})
    assert r1.from_cache is False
    assert calls["n"] == 1
    assert r1.json() == {"hello": "world"}

    r2 = cache.get("https://x.test/thing", params={"q": "1"})
    assert r2.from_cache is True, "second identical request must hit the cache"
    assert calls["n"] == 1, "no network call may be made on a cache hit"
    assert r2.json() == {"hello": "world"}

    assert cache.stats() == {"hits": 1, "misses": 1}


def test_disabled_cache_always_calls_network(tmp_path, monkeypatch):
    cache = HttpCache(enabled=False, cache_dir=tmp_path)
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        content = b"{}"
        url = "https://x.test/thing"
        ok = True

    class FakeSession:
        headers: dict = {}

        def request(self, *a, **kw):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(cache, "_session", FakeSession())
    cache.get("https://x.test/thing")
    cache.get("https://x.test/thing")
    assert calls["n"] == 2


def test_error_responses_are_not_cached(tmp_path, monkeypatch):
    """A transient 429 must not get baked in permanently."""
    cache = HttpCache(enabled=True, cache_dir=tmp_path)
    calls = {"n": 0}

    class FakeResp:
        status_code = 429
        content = b"rate limited"
        url = "https://x.test/thing"
        ok = False

    class FakeSession:
        headers: dict = {}

        def request(self, *a, **kw):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(cache, "_session", FakeSession())
    cache.get("https://x.test/thing")
    cache.get("https://x.test/thing")
    assert calls["n"] == 2, "non-2xx must not be served from cache"


def test_secrets_are_redacted_in_cache_metadata(tmp_path, monkeypatch):
    """R-10: an api_key value must never be written to a readable sidecar."""
    cache = HttpCache(enabled=True, cache_dir=tmp_path)

    class FakeResp:
        status_code = 200
        content = b"{}"
        url = "https://x.test/thing"
        ok = True

    class FakeSession:
        headers: dict = {}

        def request(self, *a, **kw):
            return FakeResp()

    monkeypatch.setattr(cache, "_session", FakeSession())
    cache.get("https://x.test/thing", params={"api_key": "SUPERSECRET123", "q": "hi"})

    written = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.meta.json"))
    assert "SUPERSECRET123" not in written
    assert "<redacted>" in written
    assert "hi" in written  # non-secret params still recorded
