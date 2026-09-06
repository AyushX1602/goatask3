"""SerpApi image upload for the Lens escalation path (F1a, revised 7 Sep 2026).

Discovered in the hhgoa-provenance (TRACE) review: SerpApi documents a direct
upload endpoint — POST https://serpapi.com/image → {"image_id": ...} — which
google_lens accepts as `image_id=` instead of `url=`. That removes the imgbb
hop entirely: the crop is held by SerpApi for ~10 minutes, never placed on a
public image host, and needs no extra API key beyond SERPAPI_KEY.

Documented limits (serpapi.com/image-api): JPG/PNG/WebP, 500 KB max.
Our escalation query is always the 512px head crop (well under the cap).

Design constraints
------------------
- is_head_crop is keyword-only and structurally enforced (TypeError if
  omitted, ValueError if False) — the escalation query must be the isolated
  head, never the full photo (garment-hijack guard, T2.5).
- SEARCH_LENS_UPLOAD=1 is required: sending the face crop to SerpApi is a
  disclosure the user must opt into.
- Goes through HttpCache (R-04) so repeat runs reuse the upload.
- UploadForSearchDisabled if flag off; UploadError on network/API failure.
"""

from __future__ import annotations

from pipeline.cache.http_cache import HttpCache, get_http_cache
from pipeline.config import get_config

SERPAPI_UPLOAD_URL = "https://serpapi.com/image"
MAX_UPLOAD_BYTES = 500 * 1024  # documented SerpApi limit
SUPPORTED_FORMATS = ("jpg", "jpeg", "png", "webp")


class UploadForSearchDisabled(Exception):
    """Raised when the upload is attempted but SEARCH_LENS_UPLOAD=0."""


class UploadError(Exception):
    """Raised on network or SerpApi upload failure."""


def upload_crop_to_serpapi(
    jpeg_bytes: bytes,
    http: HttpCache | None = None,
    *,
    is_head_crop: bool,
) -> str:
    """Uploads *jpeg_bytes* to SerpApi and returns the image_id for
    engine=google_lens.

    Parameters
    ----------
    jpeg_bytes:
        Raw JPEG bytes of the head crop — never the full probe photo.
    http:
        HttpCache instance. Defaults to the shared cache so repeated calls
        in tests reuse the cached response (R-04).
    is_head_crop:
        Keyword-only. **Must be True.** Structural guard ensuring callers
        have explicitly confirmed they are uploading the isolated head crop.
        Raises ValueError if False.

    Returns
    -------
    str
        The image_id to pass to google_lens as `image_id=`.

    Raises
    ------
    TypeError
        If is_head_crop is omitted (keyword-only enforcement by Python).
    ValueError
        If is_head_crop is False, or the bytes exceed the documented
        500 KB upload limit.
    UploadForSearchDisabled
        If SEARCH_LENS_UPLOAD != 1.
    UploadError
        On network / API failure or an unexpected response shape.
    """
    if not is_head_crop:
        raise ValueError(
            "upload_crop_to_serpapi: is_head_crop must be True. "
            "Only upload the head crop, never the full probe photo."
        )
    if len(jpeg_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"head crop is {len(jpeg_bytes)} bytes; SerpApi's upload limit "
            f"is {MAX_UPLOAD_BYTES} bytes"
        )

    cfg = get_config()
    if not bool(getattr(cfg, "search_lens_upload", 0)):
        raise UploadForSearchDisabled(
            "SEARCH_LENS_UPLOAD is not enabled. "
            "Set SEARCH_LENS_UPLOAD=1 in .env to allow uploading the head "
            "crop to SerpApi for the Lens escalation."
        )
    key = getattr(cfg, "serpapi_key", None)
    if not key:
        raise UploadForSearchDisabled(
            "SERPAPI_KEY is not configured — the Lens escalation cannot run."
        )

    if http is None:
        http = get_http_cache()

    try:
        resp = http.post(
            SERPAPI_UPLOAD_URL,
            params={"api_key": key},  # R-10: redacted before any disk write
            files={"image": ("head_crop.jpg", jpeg_bytes, "image/jpeg")},
            timeout=30.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise UploadError(f"SerpApi image upload failed: {exc}") from exc

    try:
        data = resp.json()
    except Exception as exc:
        raise UploadError(f"SerpApi upload returned non-JSON: {exc}") from exc
    image_id = data.get("image_id") if isinstance(data, dict) else None
    if not image_id:
        raise UploadError(
            f"SerpApi upload returned unexpected response shape: {list(data) if isinstance(data, dict) else type(data)}"
        )
    return str(image_id)
