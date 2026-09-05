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
from pipeline.search.media_urls import (
    derive_github_profile_url,
    derive_image_url_and_fallbacks,
    derive_page_url,
    size_variants_for,
)

GCV_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


# --------------------------------------------------------------------------
# Response parsing — pure functions, so they can be tested against recorded
# fixtures with no network and no API key (D-30).
# --------------------------------------------------------------------------


# Schemes that can never be fetched as an image. GCV returns
# `x-raw-image:///<hash>` for images it holds internally but will not serve.
UNFETCHABLE_SCHEMES = ("x-raw-image:",)


def _derive_page_url(image_url: str) -> str | None:
    """Thin wrapper kept for the existing test surface; see
    pipeline/search/media_urls.py for the actual platform knowledge."""
    return derive_page_url(image_url)


def _derive_image_url(page_url: str) -> str | None:
    """Thin wrapper kept for the existing test surface; see
    pipeline/search/media_urls.py for the actual platform knowledge.
    Returns only the primary (largest) variant — callers that need the
    fallback chain should use derive_image_url_and_fallbacks directly."""
    image_url, _ = derive_image_url_and_fallbacks(page_url)
    return image_url or None


def _resolve_image_url(url: str) -> tuple[str, tuple[str, ...]]:
    """Normalises a provider-supplied image URL to its largest known size
    variant, with the remaining variants as fallbacks.

    Applied at PARSE time (not fetch time) deliberately: the evidence
    bundle records candidate.image_url verbatim (evidence/bundle.py), so
    the URL we cite must be the exact URL we scored. Rewriting later would
    let the anchored record cite a URL (e.g. ?name=thumb) different from
    the one that actually passed the face-quality gate.

    Measured: X's default `?name=thumb` variant is 150x150 (face ~32px,
    below the 50px gate); `?name=orig` is 1080x1080 (face ~235px, passes).
    scripts/probe_size_variants.py, 5 Sep 2026.
    """
    variants = size_variants_for(url)
    if not variants:
        return url, ()
    return variants[0], variants[1:]


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

        full_images = page.get("fullMatchingImages") or []
        partial_images = page.get("partialMatchingImages") or []
        images = full_images + partial_images
        image_url = next(
            (
                i["url"]
                for i in images
                if i.get("url") and not i["url"].startswith(UNFETCHABLE_SCHEMES)
            ),
            None,
        )
        if image_url and any(i.get("url") == image_url for i in full_images):
            match_kind = "full"
        elif image_url:
            match_kind = "partial"
        else:
            # A pagesWithMatchingImages entry with no image of its own —
            # only a page-level signal (Google found this PAGE, not
            # necessarily this exact image on it). Distinct from
            # "unknown": "page" means we know exactly what kind of weak
            # signal this is, not that nobody classified it.
            match_kind = "page"
        # Never fall back to the page URL as an image URL — that downloads
        # HTML and misreports it as "no face detected". Derive a real
        # thumbnail where the platform allows it, otherwise leave it empty
        # and let the pipeline report `reject-no-image` honestly.
        fallbacks: tuple[str, ...] = ()
        if not image_url:
            image_url, fallbacks = derive_image_url_and_fallbacks(page_url)
        else:
            image_url, fallbacks = _resolve_image_url(image_url)

        # GitHub: a repo/blob page carries the OWNER's avatar, not the
        # owner's actual profile. The profile is the citable identity page
        # under the R-06 allowlist principle (verify/allowlist.py); the
        # repo README is not where a person "publishes under their
        # identity" in the sense that principle means.
        github_profile = derive_github_profile_url(page_url)
        effective_page_url = github_profile or page_url

        candidates.append(
            Candidate(
                image_url=image_url,
                page_url=effective_page_url,
                source="gcv_web_detection",
                provider_score=None,  # R-03
                raw={
                    "page_title": page.get("pageTitle"),
                    "kind": "page",
                    "found_on": page_url if github_profile else None,
                },
                image_url_fallbacks=fallbacks,
                match_kind=match_kind,
            )
        )

    # Standalone matching images. The image URL is also the best page URL we
    # have for these, which the allowlist will usually reject — that is
    # correct and gets logged rather than hidden.
    for kind in ("fullMatchingImages", "partialMatchingImages", "visuallySimilarImages"):
        match_kind = {"fullMatchingImages": "full", "partialMatchingImages": "partial", "visuallySimilarImages": "similar"}[kind]
        for img in web.get(kind) or []:
            url = img.get("url")
            if not url or url.startswith(UNFETCHABLE_SCHEMES):
                continue
            # Prefer a real post URL over the bare CDN image URL, so an
            # accepted match cites something a human can actually open.
            derived_page = derive_page_url(url)
            resolved_url, fallbacks = _resolve_image_url(url)
            candidates.append(
                Candidate(
                    image_url=resolved_url,
                    page_url=derived_page or url,
                    source="gcv_web_detection",
                    provider_score=None,  # R-03
                    raw={"kind": kind, "page_derived_from_cdn": bool(derived_page)},
                    image_url_fallbacks=fallbacks,
                    match_kind=match_kind,
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
        # Prefer the full-size `image` over `thumbnail`: thumbnails are
        # small enough to routinely fail the 50px face-quality gate (the
        # exact failure mode measured on GCV thumbnails during G1), while
        # `image` is Lens's full-resolution reference. Ordering was
        # previously backwards (`thumbnail or image`). Recorded which
        # field actually won so this is auditable, not just asserted.
        image_field_used = "image" if m.get("image") else ("thumbnail" if m.get("thumbnail") else None)
        image_url, fallbacks = _resolve_image_url(m.get("image") or m.get("thumbnail") or "")
        candidates.append(
            Candidate(
                image_url=image_url,
                page_url=link,
                source="serpapi_lens",
                provider_score=None,  # R-03: Lens gives no score, and we would ignore it
                raw={"title": m.get("title"), "kind": "visual_match", "image_field_used": image_field_used},
                image_url_fallbacks=fallbacks,
                # Lens's own name for this bucket is "visual match" —
                # visually similar, not an assertion of pixel identity the
                # way GCV's fullMatchingImages is. Maps to our "similar".
                match_kind="similar",
            )
        )

    for o in payload.get("organic_results") or []:
        link = o.get("link")
        if not link:
            continue
        imgs = o.get("images") or []
        full = imgs[0] if imgs else None
        thumb = o.get("thumbnail")
        image_field_used = "images[0]" if full else ("thumbnail" if thumb else None)
        image_url, fallbacks = _resolve_image_url(full or thumb or "")
        candidates.append(
            Candidate(
                image_url=image_url,
                page_url=link,
                source="serpapi_lens",
                provider_score=None,
                raw={"title": o.get("title"), "kind": "organic", "image_field_used": image_field_used},
                image_url_fallbacks=fallbacks,
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

    def _recover_instagram_owners(self, candidates: list[Candidate]) -> list[Candidate]:
        """Item 3: Instagram owner recovery from SERP.

        If a candidate's page_url is an Instagram post (/p/{shortcode}/) and its
        owner is unknown, queries Google SERP to extract the post owner handle.
        If recovered, synthesizes a profile candidate https://www.instagram.com/{owner}
        with origin="linked".
        Capped at at most 1 SERP call per web detection run.
        """
        from pipeline.search.serp_resolve import INSTAGRAM_POST_RE, post_owner

        post_owner_calls = 0
        synthesized: list[Candidate] = []
        known_pages = {c.page_url for c in candidates if c.page_url}

        for c in candidates:
            if post_owner_calls >= 1:
                break
            if c.page_url and INSTAGRAM_POST_RE.search(c.page_url):
                post_owner_calls += 1
                try:
                    owner = post_owner(c.page_url, self._http)
                except Exception:
                    owner = None
                if owner:
                    profile_url = f"https://www.instagram.com/{owner}"
                    if profile_url not in known_pages:
                        known_pages.add(profile_url)
                        synthesized.append(
                            Candidate(
                                image_url="",
                                page_url=profile_url,
                                source="serp_instagram",
                                origin="linked",
                                match_kind="similar",
                            )
                        )
        return synthesized

    # --- SearchProvider interface --------------------------------------

    def search(
        self,
        search_image_bytes: bytes,
        probe_vec: np.ndarray,
        public_image_url: str | None = None,
    ) -> list[Candidate]:
        """probe_vec is accepted to satisfy the protocol but deliberately
        unused: a provider never scores a face (R-03).

        public_image_url is only needed by the serpapi backend (D-31).
        """
        if self.backend == "gcv":
            candidates, signals = self._search_gcv(search_image_bytes)
        elif self.backend == "serpapi":
            if not public_image_url:
                raise ValueError(
                    "serpapi backend needs a publicly reachable image URL "
                    "(see memory.md D-31: short-expiry S3 presigned GET)"
                )
            candidates, signals = self._search_serpapi(public_image_url)
        else:
            return []

        recovered_profiles = self._recover_instagram_owners(candidates)
        if recovered_profiles:
            candidates = candidates + recovered_profiles

        self.last_identity_signals = signals
        return candidates
