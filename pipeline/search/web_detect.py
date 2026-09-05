"""Web detection provider — the PRIMARY search path (D-21, D-28).

One call reaches every platform Google has indexed. Verified live during
research: a single query on one face photo returned real post URLs on
instagram, facebook, youtube, x and reddit. That is why this replaced the
per-platform API layer entirely (architecture.md 5, "Rejected").

Two interchangeable backends behind one provider:

  gcv     Google Cloud Vision images:annotate, feature WEB_DETECTION
          PRIMARY. ~1,000 units/month free, and it accepts raw base64 —
          no public image URL needed, which is why it outranks SerpApi.

  serpapi SerpApi engine=google_lens
          SECONDARY. ~100 searches/month, and needs a publicly reachable
          image URL (see D-31: short-expiry S3 presigned GET).

R-03 is the load-bearing rule here: neither backend's own notion of
similarity is ever used to accept or reject. They return candidates. Only
pipeline.verify.matcher decides, using our own ArcFace cosine. The identity
strings these APIs infer (webEntities / related_content) are recorded in the
audit log as context and nothing more.
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from pipeline.cache.http_cache import HttpCache, get_http_cache
from pipeline.config import get_config
from pipeline.search.base import Candidate

GCV_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


# --------------------------------------------------------------------------
# Response parsing — pure functions, so they can be tested against recorded
# fixtures with no network and no API key (D-30).
# --------------------------------------------------------------------------


def parse_gcv(payload: dict[str, Any]) -> tuple[list[Candidate], list[str]]:
    """Maps a Vision WEB_DETECTION response to candidates + identity signals.

    Accepts either the full images:annotate envelope or a bare webDetection
    object, since fixtures are sometimes saved in either shape.

    Empty image arrays are a VALID zero-candidate result, not an error — a
    documented GCV behaviour where only webEntities come back (A-08).
    """
    if "responses" in payload:
        responses = payload.get("responses") or [{}]
        web = (responses[0] or {}).get("webDetection", {}) or {}
    else:
        web = payload.get("webDetection", payload) or {}

    identity_signals = [
        e["description"]
        for e in (web.get("webEntities") or [])
        if e.get("description")
    ]

    candidates: list[Candidate] = []

    # Pages that contain a matching image. These carry the page URL we want
    # to report, and often carry their own image references.
    for page in web.get("pagesWithMatchingImages") or []:
        page_url = page.get("url")
        if not page_url:
            continue
        images = (page.get("fullMatchingImages") or []) + (
            page.get("partialMatchingImages") or []
        )
        image_url = next((i.get("url") for i in images if i.get("url")), None)
        candidates.append(
            Candidate(
                image_url=image_url or page_url,
                page_url=page_url,
                source="gcv_web_detection",
                provider_score=None,  # R-03
                raw={"page_title": page.get("pageTitle"), "kind": "page"},
            )
        )

    # Standalone matching images. The image URL is also the best page URL we
    # have for these, which the allowlist will usually reject — that is
    # correct and gets logged rather than hidden.
    for kind in ("fullMatchingImages", "partialMatchingImages", "visuallySimilarImages"):
        for img in web.get(kind) or []:
            url = img.get("url")
            if not url:
                continue
            candidates.append(
                Candidate(
                    image_url=url,
                    page_url=url,
                    source="gcv_web_detection",
                    provider_score=None,  # R-03
                    raw={"kind": kind},
                )
            )

    return _dedupe_by_image_url(candidates), identity_signals


def parse_serpapi_lens(payload: dict[str, Any]) -> tuple[list[Candidate], list[str]]:
    """Maps a SerpApi google_lens response to candidates + identity signals.

    Field shapes confirmed live during research (scripts/probe_lens.py):
    visual_matches[].{link,title,thumbnail,image}, organic_results[].{link,title},
    related_content[].query.
    """
    identity_signals = [
        r["query"] for r in (payload.get("related_content") or []) if r.get("query")
    ]

    candidates: list[Candidate] = []

    for m in payload.get("visual_matches") or []:
        link = m.get("link")
        if not link:
            continue
        candidates.append(
            Candidate(
                image_url=m.get("thumbnail") or m.get("image") or "",
                page_url=link,
                source="serpapi_lens",
                provider_score=None,  # R-03: Lens gives no score, and we would ignore it
                raw={"title": m.get("title"), "kind": "visual_match"},
            )
        )

    for o in payload.get("organic_results") or []:
        link = o.get("link")
        if not link:
            continue
        thumb = o.get("thumbnail")
        if not thumb:
            imgs = o.get("images") or []
            thumb = imgs[0] if imgs else ""
        candidates.append(
            Candidate(
                image_url=thumb or "",
                page_url=link,
                source="serpapi_lens",
                provider_score=None,
                raw={"title": o.get("title"), "kind": "organic"},
            )
        )

    # Titles are a weaker identity signal than related_content, but real.
    for m in (payload.get("visual_matches") or [])[:5]:
        if m.get("title"):
            identity_signals.append(m["title"])

    return _dedupe_by_image_url(
        [c for c in candidates if c.image_url]
    ), identity_signals


def _dedupe_by_image_url(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in candidates:
        k = c.image_url or c.page_url
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


class WebDetectProvider:
    """SearchProvider implementation. Satisfies the protocol in search/base.py."""

    name = "web_detect"
    requires_credentials = True

    def __init__(
        self,
        backend: str | None = None,
        http: HttpCache | None = None,
        max_results: int = 25,
    ) -> None:
        cfg = get_config()
        self._gcv_key = getattr(cfg, "gcv_api_key", None)
        self._serpapi_key = cfg.serpapi_key
        self._http = http or get_http_cache()
        self.max_results = max_results
        self.backend = self._resolve_backend(backend or getattr(cfg, "web_detect_backend", "auto"))
        # Populated per search, read by the audit log. Context only (R-03).
        self.last_identity_signals: list[str] = []

    def _resolve_backend(self, requested: str) -> str | None:
        """'auto' prefers gcv for its ~10x larger free quota (D-28)."""
        if requested == "gcv":
            return "gcv" if self._gcv_key else None
        if requested == "serpapi":
            return "serpapi" if self._serpapi_key else None
        if self._gcv_key:
            return "gcv"
        if self._serpapi_key:
            return "serpapi"
        return None

    def available(self) -> bool:
        return self.backend is not None

    # --- backends ------------------------------------------------------

    def _search_gcv(self, image_bytes: bytes) -> tuple[list[Candidate], list[str]]:
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
                    "features": [
                        {"type": "WEB_DETECTION", "maxResults": self.max_results}
                    ],
                }
            ]
        }
        resp = self._http.post(
            GCV_ENDPOINT,
            params={"key": self._gcv_key},
            json_body=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        return parse_gcv(resp.json())

    def _search_serpapi(self, public_image_url: str) -> tuple[list[Candidate], list[str]]:
        resp = self._http.get(
            SERPAPI_ENDPOINT,
            params={
                "engine": "google_lens",
                "url": public_image_url,
                "api_key": self._serpapi_key,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return parse_serpapi_lens(resp.json())

    # --- SearchProvider interface --------------------------------------

    def search(
        self,
        aligned_face_png: bytes,
        probe_vec: np.ndarray,
        public_image_url: str | None = None,
    ) -> list[Candidate]:
        """probe_vec is accepted to satisfy the protocol but deliberately
        unused: a provider never scores a face (R-03).

        public_image_url is only needed by the serpapi backend (D-31).
        """
        if self.backend == "gcv":
            candidates, signals = self._search_gcv(aligned_face_png)
        elif self.backend == "serpapi":
            if not public_image_url:
                raise ValueError(
                    "serpapi backend needs a publicly reachable image URL "
                    "(see memory.md D-31: short-expiry S3 presigned GET)"
                )
            candidates, signals = self._search_serpapi(public_image_url)
        else:
            return []

        self.last_identity_signals = signals
        return candidates
