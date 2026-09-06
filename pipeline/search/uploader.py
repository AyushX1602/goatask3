"""Temporary public image hosting for Lens/SerpApi (F1a).

SerpApi Google Lens requires a publicly reachable image URL; GCV accepts raw
base64.  When SEARCH_PUBLIC_UPLOAD=1 and IMGBB_KEY is set this module posts the
probe image to imgbb.com with a 5-minute expiry, returning the hosted URL.

Design constraints
------------------
- is_head_crop is keyword-only and is structurally enforced (raises TypeError if
  omitted, ValueError if False) — not left to a comment (spec §F1a).
- Privacy: expiration=300 (5 min). The hosted image is never linked, discovered
  only via unguessable random URL, and expires before a human could act on it.
- Quota: imgbb free tier = 32 MB per image, unlimited images.
  HTTP_CACHE wraps the POST so repeated calls in tests are free (R-04).
- Exceptions: UploaderDisabled if key or upload flag is missing;
  UploaderError for network / API failures.
"""

from __future__ import annotations

import base64

from pipeline.cache.http_cache import HttpCache, get_http_cache
from pipeline.config import get_config

IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"
_EXPIRATION_SECONDS = 300  # 5 minutes — short-lived, see D-31 analog


class UploaderDisabled(Exception):
    """Raised when upload is attempted but SEARCH_PUBLIC_UPLOAD=0 or IMGBB_KEY unset."""


class UploaderError(Exception):
    """Raised on network or imgbb API failure."""


def upload_for_search(
    jpeg_bytes: bytes,
    http: HttpCache | None = None,
    *,
    is_head_crop: bool,
) -> str:
    """Upload *jpeg_bytes* to imgbb and return the hosted URL.

    Parameters
    ----------
    jpeg_bytes:
        Raw JPEG bytes of the image to host. Must be the face/head crop —
        never the full probe image — so that the public URL reveals no
        context the subject has not already published.
    http:
        HttpCache instance. Defaults to the shared cache so repeated calls in
        tests use the cached response (R-04).
    is_head_crop:
        Keyword-only. **Must be True.** Structural guard ensuring callers
        have explicitly confirmed they are uploading a crop, not the original.
        Raises ValueError if False.

    Returns
    -------
    str
        The public, time-limited URL of the uploaded image.

    Raises
    ------
    TypeError
        If is_head_crop is omitted (keyword-only enforcement by Python).
    ValueError
        If is_head_crop is False — caller must explicitly acknowledge crop.
    UploaderDisabled
        If SEARCH_PUBLIC_UPLOAD != 1 or IMGBB_KEY is not configured.
    UploaderError
        On network / API failure.
    """
    if not is_head_crop:
        raise ValueError(
            "upload_for_search: is_head_crop must be True. "
            "Only upload the face/head crop, never the full probe image."
        )

    cfg = get_config()
    if not bool(getattr(cfg, "search_public_upload", 0)):
        raise UploaderDisabled(
            "SEARCH_PUBLIC_UPLOAD is not enabled. "
            "Set SEARCH_PUBLIC_UPLOAD=1 in .env to allow temporary public hosting."
        )

    key = getattr(cfg, "imgbb_key", None)
    if not key:
        raise UploaderDisabled(
            "IMGBB_KEY is not configured. "
            "Add IMGBB_KEY=<your-key> to .env to enable public upload."
        )

    if http is None:
        http = get_http_cache()

    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    try:
        resp = http.post(
            IMGBB_UPLOAD_URL,
            params={"key": key, "expiration": str(_EXPIRATION_SECONDS)},
            form_data={"image": b64},
            timeout=30.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise UploaderError(f"imgbb upload failed: {exc}") from exc

    data = resp.json()
    url = (data.get("data") or {}).get("url")
    if not url:
        raise UploaderError(
            f"imgbb returned unexpected response shape: {list(data.keys())}"
        )
    return url
