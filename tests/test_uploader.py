"""Tests for pipeline/search/uploader.py (F1a, revised: SerpApi direct upload).

The Lens escalation no longer hosts the head crop on a public image host
(imgbb was removed 7 Sep 2026). The crop is uploaded directly to SerpApi
(POST /image → image_id, held ~10 min on SerpApi's side).

Covers:
  1. is_head_crop=False raises ValueError (structural guard)
  2. is_head_crop omitted raises TypeError (keyword-only enforcement)
  3. SEARCH_LENS_UPLOAD=0 raises UploadForSearchDisabled
  4. SERPAPI_KEY unset raises UploadForSearchDisabled
  5. Successful upload path: mock SerpApi response, returns image_id
  6. Network error raises UploadError
  7. Unexpected response shape raises UploadError
  8. Oversized crop (>500 KB documented limit) raises ValueError
"""

from __future__ import annotations

import pytest

from pipeline.search.uploader import (
    UploadError,
    UploadForSearchDisabled,
    upload_crop_to_serpapi,
)


FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG header bytes


# ── test 1: is_head_crop=False raises ValueError ──────────────────────────────


def test_upload_raises_value_error_when_not_head_crop():
    """is_head_crop=False must raise ValueError — structural guard."""
    with pytest.raises(ValueError, match="is_head_crop"):
        upload_crop_to_serpapi(FAKE_JPEG, is_head_crop=False)


# ── test 2: omitting is_head_crop raises TypeError ────────────────────────────


def test_upload_raises_type_error_when_is_head_crop_omitted():
    """is_head_crop is keyword-only — omitting it raises TypeError."""
    with pytest.raises(TypeError):
        upload_crop_to_serpapi(FAKE_JPEG)  # type: ignore[call-arg]


# ── test 3: SEARCH_LENS_UPLOAD=0 raises UploadForSearchDisabled ──────────────


def test_upload_disabled_when_upload_flag_off(monkeypatch):
    """UploadForSearchDisabled raised when SEARCH_LENS_UPLOAD is 0 (default)."""
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "0")
    monkeypatch.setenv("SERPAPI_KEY", "some-key")
    with pytest.raises(UploadForSearchDisabled, match="SEARCH_LENS_UPLOAD"):
        upload_crop_to_serpapi(FAKE_JPEG, is_head_crop=True)


# ── test 4: SERPAPI_KEY unset raises UploadForSearchDisabled ─────────────────


def test_upload_disabled_when_no_serpapi_key(monkeypatch):
    """UploadForSearchDisabled raised when SERPAPI_KEY is not configured."""
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "1")
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    with pytest.raises(UploadForSearchDisabled, match="SERPAPI_KEY"):
        upload_crop_to_serpapi(FAKE_JPEG, is_head_crop=True)


# ── test 5: Successful upload ─────────────────────────────────────────────────


class _FakeCachedResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.last_params: dict = {}
        self.last_files: dict = {}

    def post(
        self, url: str, *, params=None, files=None, timeout=30.0
    ) -> _FakeCachedResponse:
        self.last_params = params or {}
        self.last_files = files or {}
        return _FakeCachedResponse(self._payload)


def test_upload_returns_image_id_on_success(monkeypatch):
    """Happy path: valid SerpApi upload response returns the image_id."""
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "1")
    monkeypatch.setenv("SERPAPI_KEY", "test-serpapi-key")

    fake_http = _FakeHttp({"image_id": "serp-image-123"})

    result = upload_crop_to_serpapi(FAKE_JPEG, http=fake_http, is_head_crop=True)

    assert result == "serp-image-123"
    # api_key must travel in params (redacted before any disk write, R-10)
    assert fake_http.last_params.get("api_key") == "test-serpapi-key"
    # the crop must be sent as a multipart file named "image"
    assert "image" in fake_http.last_files
    filename, content, content_type = fake_http.last_files["image"]
    assert content == FAKE_JPEG
    assert content_type == "image/jpeg"


# ── test 6: network error raises UploadError ─────────────────────────────────


class _FailingHttp:
    def post(self, url: str, **kw):
        raise ConnectionError("network failure")


def test_upload_raises_upload_error_on_network_failure(monkeypatch):
    """Network/API error becomes UploadError."""
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "1")
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    with pytest.raises(UploadError, match="upload failed"):
        upload_crop_to_serpapi(FAKE_JPEG, http=_FailingHttp(), is_head_crop=True)


# ── test 7: unexpected response shape raises UploadError ─────────────────────


class _BadResponseHttp:
    def post(self, url: str, **kw):
        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"status": 200}  # missing "image_id" key

        return R()


def test_upload_raises_upload_error_on_bad_response_shape(monkeypatch):
    """Missing 'image_id' in the SerpApi upload response raises UploadError."""
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "1")
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    with pytest.raises(UploadError, match="unexpected response shape"):
        upload_crop_to_serpapi(FAKE_JPEG, http=_BadResponseHttp(), is_head_crop=True)


# ── test 8: oversized crop raises ValueError ─────────────────────────────────


def test_upload_raises_value_error_when_crop_exceeds_500kb(monkeypatch):
    """The documented SerpApi upload limit is 500 KB — a larger crop must be
    rejected before any network call (defensive, crops are ~30-80 KB)."""
    monkeypatch.setenv("SEARCH_LENS_UPLOAD", "1")
    monkeypatch.setenv("SERPAPI_KEY", "test-key")

    big = FAKE_JPEG + b"\x00" * (501 * 1024)
    with pytest.raises(ValueError, match="limit"):
        upload_crop_to_serpapi(big, is_head_crop=True)
