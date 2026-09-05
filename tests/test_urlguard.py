"""Tests for pipeline.cache.urlguard (R-29, R-24)."""

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from pipeline.cache.urlguard import (
    UnsafeUrlError,
    assert_safe_url,
    looks_like_image,
    safe_fetch,
)
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.search.base import Candidate, SearchProvider
from pipeline.verify.pipeline_run import run_pipeline
from pipeline.config import MatchPolicy

FIX = Path(__file__).resolve().parent / "fixtures"
POLICY = MatchPolicy(threshold=0.42, margin=0.08, model="w600k_r50", target_fmr=0.01, is_placeholder=True)


class FakeProvider(SearchProvider):
    name = "fake"

    def __init__(self, candidates: list[Candidate]) -> None:
        self._cands = candidates

    def available(self) -> bool:
        return True

    def search(self, image_bytes: bytes, probe_vec: np.ndarray, *, public_image_url: str | None = None) -> list[Candidate]:
        return list(self._cands)


class FakeHttpCache:
    def __init__(self, responses: dict[str, bytes | Path]) -> None:
        self.responses = responses
        self.hits = 0
        self.misses = 0

    def get(self, url: str, **kwargs):
        if url in self.responses:
            data = self.responses[url]
            if isinstance(data, Path):
                data = data.read_bytes()
            content = data

            class Resp:
                ok = True
                status_code = 200
                content_type = "image/jpeg"

                def __init__(self, c: bytes) -> None:
                    self.content = c

                def json(self):
                    import json
                    return json.loads(self.content.decode("utf-8"))

            return Resp(content)

        class Resp404:
            ok = False
            status_code = 404
            content = b"Not found"
            content_type = "text/plain"

            def json(self):
                return None

        return Resp404()


def test_scheme_validation():
    """R-29: only https scheme is permitted; http, file, data, etc. rejected."""
    with pytest.raises(UnsafeUrlError) as exc_http:
        assert_safe_url("http://example.com/photo.jpg")
    assert exc_http.value.cause == "scheme"

    with pytest.raises(UnsafeUrlError) as exc_file:
        assert_safe_url("file:///etc/passwd")
    assert exc_file.value.cause == "scheme"

    with pytest.raises(UnsafeUrlError) as exc_data:
        assert_safe_url("data:image/png;base64,iVBORw0KGgo=")
    assert exc_data.value.cause == "scheme"

    with pytest.raises(UnsafeUrlError) as exc_empty:
        assert_safe_url("")
    assert exc_empty.value.cause == "scheme"

    # https to public domain succeeds
    assert_safe_url("https://example.com/photo.jpg")


def test_internal_ip_rejection():
    """R-29: loopback, link-local, private, and reserved IPs rejected."""
    prohibited_urls = [
        "https://127.0.0.1/photo.jpg",
        "https://localhost/photo.jpg",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/internal.jpg",
        "https://192.168.1.1/router.jpg",
        "https://172.16.0.1/admin.jpg",
        "https://[::1]/secret.jpg",
        "https://[fc00::1]/private.jpg",
        "https://[fe80::1]/linklocal.jpg",
        "https://[64:ff9b::7f00:1]/loopback.jpg",
        "https://[64:ff9b::a9fe:a9fe]/metadata.jpg",
    ]
    for url in prohibited_urls:
        with pytest.raises(UnsafeUrlError) as exc:
            assert_safe_url(url)
        assert exc.value.cause == "internal-address", f"Expected internal-address for {url}, got {exc.value.cause}"


def test_unresolvable_host_rejected():
    """R-29: unresolvable hosts must fail safe as internal-address."""
    with pytest.raises(UnsafeUrlError) as exc:
        assert_safe_url("https://nonexistent-subdomain-unresolvable-9992381231.invalid/img.jpg")
    assert exc.value.cause == "internal-address"


def test_redirect_to_loopback_rejected():
    """R-29: redirect hop to loopback address must be blocked."""
    mock_session = MagicMock()

    # First hop: 302 redirect from public URL to internal URL
    hop1 = MagicMock()
    hop1.is_redirect = True
    hop1.status_code = 302
    hop1.headers = {"Location": "https://127.0.0.1:8545/evil.jpg"}

    mock_session.get.return_value = hop1

    mock_http = MagicMock()
    mock_http._session = mock_session
    mock_http.enabled = False

    with pytest.raises(UnsafeUrlError) as exc:
        safe_fetch(mock_http, "https://example.com/hop", is_image=True)

    assert exc.value.cause == "internal-address"
    assert "127.0.0.1" in str(exc.value)


def test_html_with_image_content_type_rejected_by_magic_bytes():
    """R-29: magic bytes validation rejects HTML payload declared as image/jpeg."""
    html_bytes = b"<!DOCTYPE html><html><body>Error 403 Forbidden</body></html>"
    assert not looks_like_image(html_bytes)

    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 50
    assert looks_like_image(jpeg_bytes)

    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 50
    assert looks_like_image(png_bytes)

    fake_http = FakeHttpCache({"https://example.com/html.jpg": html_bytes})

    with pytest.raises(UnsafeUrlError) as exc:
        safe_fetch(fake_http, "https://example.com/html.jpg", is_image=True)

    assert exc.value.cause == "not-an-image"


def test_size_cap_trips_mid_stream():
    """R-29: streaming download terminates when exceeding size cap."""
    mock_session = MagicMock()

    resp = MagicMock()
    resp.is_redirect = False
    resp.status_code = 200
    resp.headers = {"Content-Length": "100000000"}

    # Yields 64KB chunks infinitely
    def infinite_chunks():
        chunk = b"\xff\xd8\xff" + b"X" * 65533
        while True:
            yield chunk

    resp.iter_content.return_value = infinite_chunks()
    resp.close = MagicMock()
    mock_session.get.return_value = resp

    mock_http = MagicMock()
    mock_http._session = mock_session
    mock_http.enabled = False

    # Max 100 KB cap
    with pytest.raises(UnsafeUrlError) as exc:
        safe_fetch(mock_http, "https://example.com/huge.jpg", max_bytes=100 * 1024, is_image=True)

    assert exc.value.cause == "size-cap"
    assert resp.close.called


def test_acceptance_loopback_candidate_gets_reject_unsafe_url(monkeypatch):
    """Acceptance criterion: candidate with https://127.0.0.1:8545/ gets reject-unsafe-url
    while a legitimate public candidate scores normally."""
    detector = FaceDetector()
    embedder = FaceEmbedder()

    img = cv2.imread(str(FIX / "obama1.jpg"))
    faces = detector.detect(img)
    crop = align(img, faces[0].kps5)
    probe_vec = embedder.embed(crop).vec

    valid_url = "https://example.com/valid-obama.jpg"
    valid_bytes = (FIX / "obama2.jpg").read_bytes()

    fake_http = FakeHttpCache({valid_url: valid_bytes})
    monkeypatch.setattr("pipeline.verify.pipeline_run.get_http_cache", lambda: fake_http)

    cands = [
        Candidate(
            image_url="https://127.0.0.1:8545/private.jpg",
            page_url="https://127.0.0.1:8545",
            source="fake",
            origin="face",
        ),
        Candidate(
            image_url=valid_url,
            page_url="https://x.com/barackobama",
            source="fake",
            origin="face",
        ),
    ]

    result = run_pipeline(
        cv2.imencode(".png", crop)[1].tobytes(),
        probe_vec,
        [FakeProvider(cands)],
        detector,
        embedder,
        policy=POLICY,
        expand_profiles=False,
    )

    scored_cands = result.match.all_scored
    # The loopback candidate must be rejected with reject-unsafe-url
    loopback_row = next(r for r in scored_cands if "127.0.0.1" in r.candidate.image_url)
    assert loopback_row.decision == "reject-unsafe-url"
    assert "internal-address" in loopback_row.reason

    # The valid candidate must be scored and accepted
    valid_row = next(r for r in scored_cands if r.candidate.image_url == valid_url)
    assert valid_row.decision == "ACCEPT"
    assert valid_row.score is not None and valid_row.score >= POLICY.threshold
