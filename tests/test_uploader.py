"""Tests for pipeline/search/uploader.py (F1a).

Covers:
  1. is_head_crop=False raises ValueError (structural guard)
  2. is_head_crop omitted raises TypeError (keyword-only enforcement)
  3. SEARCH_PUBLIC_UPLOAD=0 raises UploaderDisabled
  4. IMGBB_KEY unset raises UploaderDisabled
  5. Successful upload path: mock imgbb response, returns URL
  6. imgbb API error raises UploaderError
  7. imgbb unexpected response shape raises UploaderError
"""

from __future__ import annotations

import base64

import pytest

from pipeline.search.uploader import (
    UploaderDisabled,
    UploaderError,
    upload_for_search,
)


FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG header bytes


# ── test 1: is_head_crop=False raises ValueError ──────────────────────────────


def test_upload_raises_value_error_when_not_head_crop():
    """is_head_crop=False must raise ValueError — structural guard."""
    with pytest.raises(ValueError, match="is_head_crop"):
        upload_for_search(FAKE_JPEG, is_head_crop=False)


# ── test 2: omitting is_head_crop raises TypeError ────────────────────────────


def test_upload_raises_type_error_when_is_head_crop_omitted():
    """is_head_crop is keyword-only — omitting it raises TypeError."""
    with pytest.raises(TypeError):
        upload_for_search(FAKE_JPEG)  # type: ignore[call-arg]


# ── test 3: SEARCH_PUBLIC_UPLOAD=0 raises UploaderDisabled ───────────────────


def test_upload_disabled_when_upload_flag_off(monkeypatch):
    """UploaderDisabled raised when SEARCH_PUBLIC_UPLOAD is 0 (default)."""
    monkeypatch.setenv("SEARCH_PUBLIC_UPLOAD", "0")
    monkeypatch.setenv("IMGBB_KEY", "some-key")
    with pytest.raises(UploaderDisabled, match="SEARCH_PUBLIC_UPLOAD"):
        upload_for_search(FAKE_JPEG, is_head_crop=True)


# ── test 4: IMGBB_KEY unset raises UploaderDisabled ──────────────────────────


def test_upload_disabled_when_no_imgbb_key(monkeypatch):
    """UploaderDisabled raised when IMGBB_KEY is not configured."""
    monkeypatch.setenv("SEARCH_PUBLIC_UPLOAD", "1")
    monkeypatch.delenv("IMGBB_KEY", raising=False)
    with pytest.raises(UploaderDisabled, match="IMGBB_KEY"):
        upload_for_search(FAKE_JPEG, is_head_crop=True)


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
        self.last_form: dict = {}

    def post(self, url: str, *, params=None, form_data=None, timeout=30.0) -> _FakeCachedResponse:
        self.last_params = params or {}
        self.last_form = form_data or {}
        return _FakeCachedResponse(self._payload)


def test_upload_returns_url_on_success(monkeypatch):
    """Happy path: valid imgbb response returns the hosted URL."""
    monkeypatch.setenv("SEARCH_PUBLIC_UPLOAD", "1")
    monkeypatch.setenv("IMGBB_KEY", "test-imgbb-key")

    expected_url = "https://i.ibb.co/abcdef/test.jpg"
    fake_http = _FakeHttp({"data": {"url": expected_url}, "success": True})

    result = upload_for_search(FAKE_JPEG, http=fake_http, is_head_crop=True)

    assert result == expected_url
    # key and expiration must be in params, not body
    assert fake_http.last_params.get("key") == "test-imgbb-key"
    assert fake_http.last_params.get("expiration") == "300"
    # image must be base64-encoded and sent as form_data
    assert "image" in fake_http.last_form
    decoded = base64.b64decode(fake_http.last_form["image"])
    assert decoded == FAKE_JPEG


# ── test 6: imgbb API error raises UploaderError ─────────────────────────────


class _FailingHttp:
    def post(self, url: str, **kw):
        raise ConnectionError("network failure")


def test_upload_raises_uploader_error_on_network_failure(monkeypatch):
    """Network/API error becomes UploaderError."""
    monkeypatch.setenv("SEARCH_PUBLIC_UPLOAD", "1")
    monkeypatch.setenv("IMGBB_KEY", "test-key")

    with pytest.raises(UploaderError, match="imgbb upload failed"):
        upload_for_search(FAKE_JPEG, http=_FailingHttp(), is_head_crop=True)


# ── test 7: unexpected response shape raises UploaderError ───────────────────


class _BadResponseHttp:
    def post(self, url: str, **kw):
        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"status": 200}  # missing "data" key

        return R()


def test_upload_raises_uploader_error_on_bad_response_shape(monkeypatch):
    """Missing 'data.url' in imgbb response raises UploaderError."""
    monkeypatch.setenv("SEARCH_PUBLIC_UPLOAD", "1")
    monkeypatch.setenv("IMGBB_KEY", "test-key")

    with pytest.raises(UploaderError, match="unexpected response shape"):
        upload_for_search(FAKE_JPEG, http=_BadResponseHttp(), is_head_crop=True)
