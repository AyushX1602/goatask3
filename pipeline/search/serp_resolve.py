"""SERP-based profile resolution and metadata recovery (T2.6, R-28, R-14).

Retrieves social profiles (e.g. LinkedIn) and post owners (e.g. Instagram)
via targeted Google SERP queries through SerpApi.
Strictly separates origin="face" (biometrically verifiable with thumbnail)
from origin="linked" (unscored claim when no media is present).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from pipeline.cache.http_cache import HttpCache
from pipeline.cache.urlguard import UnsafeUrlError, assert_safe_url
from pipeline.config import get_config
from pipeline.search.base import Candidate
from pipeline.search.expand import RESERVED_SEGMENTS

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"

LINKEDIN_PROFILE_RE = re.compile(r"^/in/([A-Za-z0-9\-_%]{3,120})/?$", re.IGNORECASE)
LINKEDIN_DISCARD_SEGMENTS = frozenset(
    {"jobs", "company", "school", "posts", "pub", "dir", "learning", "pulse", "feed", "salary"}
)

INSTAGRAM_POST_RE = re.compile(r"/(?:p|reel|tv)/([A-Za-z0-9_\-]+)", re.IGNORECASE)
INSTAGRAM_TITLE_HANDLE_RE = re.compile(r"\(@([A-Za-z0-9._]+)\)")
INSTAGRAM_DISPLAYED_HANDLE_RE = re.compile(r"instagram\.com\s*[›/]\s*([A-Za-z0-9._]+)", re.IGNORECASE)


@dataclass(frozen=True)
class SerpProfileHit:
    url: str
    title: str = ""
    snippet: str = ""
    thumbnail: str | None = None
    source: str = ""
    displayed_link: str = ""


def sanitize_handle(handle: str) -> str:
    """Strip quotes, control characters, and leading @ from a handle."""
    if not handle:
        return ""
    clean = re.sub(r'[\r\n"\'<>\\/]', "", handle).strip().lstrip("@")
    return clean


def search_profiles(
    handle: str,
    site: str,
    http: HttpCache,
) -> list[SerpProfileHit]:
    """Queries SerpApi Google search for site:{site} "{handle}".

    R-14: Returns empty list on network error, HTTP error, or missing API key.
    Never raises.
    """
    clean_handle = sanitize_handle(handle)
    if not clean_handle or len(clean_handle) < 2:
        return []

    cfg = get_config()
    api_key = cfg.serpapi_key
    if not api_key:
        return []

    params = {
        "engine": "google",
        "q": f'site:{site} "{clean_handle}"',
        "hl": "en",
        "num": 8,
        "api_key": api_key,
    }

    try:
        resp = http.get(SERPAPI_ENDPOINT, params=params, timeout=30.0)
        if not getattr(resp, "ok", False):
            return []
        data = resp.json()
    except Exception:
        return []

    if not isinstance(data, dict):
        return []

    organic = data.get("organic_results", [])
    if not isinstance(organic, list):
        return []

    hits: list[SerpProfileHit] = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        link = item.get("link")
        if not link or not isinstance(link, str):
            continue

        title = str(item.get("title") or "")
        snippet = str(item.get("snippet") or "")
        source = str(item.get("source") or "")
        displayed_link = str(item.get("displayed_link") or "")
        thumb = item.get("thumbnail") or item.get("thumbnail_url")
        if thumb and isinstance(thumb, str):
            try:
                assert_safe_url(thumb)
            except UnsafeUrlError:
                thumb = None
        else:
            thumb = None

        hits.append(
            SerpProfileHit(
                url=link,
                title=title,
                snippet=snippet,
                thumbnail=thumb,
                source=source,
                displayed_link=displayed_link,
            )
        )

    return hits


def linkedin_profiles(
    handle: str,
    http: HttpCache,
    max_results: int = 3,
) -> list[Candidate]:
    """Retrieves LinkedIn profiles matching handle via Google SERP.

    Invariants (R-28, R-14):
    - Profile path must match ^/in/([A-Za-z0-9\\-_%]{3,120})/?$.
    - Non-profile segments (/jobs/, /company/, /school/, etc.) strictly discarded.
    - If a thumbnail is present, candidate gets origin="face" and image_url=thumbnail.
    - If no thumbnail is present, candidate gets origin="linked" and image_url="",
      preserving R-28 so it is reported as an unscored linked claim.
    - Profile URL is canonicalized to https://www.linkedin.com/in/{slug}.
    - Max 3 results returned.
    """
    hits = search_profiles(handle, "linkedin.com/in", http)
    candidates: list[Candidate] = []
    seen_urls: set[str] = set()

    for hit in hits:
        try:
            parsed = urllib.parse.urlsplit(hit.url)
            host = (parsed.hostname or "").lower().removeprefix("www.")
            if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
                continue

            match = LINKEDIN_PROFILE_RE.match(parsed.path)
            if not match:
                continue

            slug = match.group(1)
            slug_lower = slug.lower()
            if slug_lower in RESERVED_SEGMENTS or slug_lower in LINKEDIN_DISCARD_SEGMENTS:
                continue

            profile_url = f"https://www.linkedin.com/in/{slug}"
            if profile_url in seen_urls:
                continue
            seen_urls.add(profile_url)

            if hit.thumbnail:
                candidates.append(
                    Candidate(
                        image_url=hit.thumbnail,
                        page_url=profile_url,
                        source="serp-linkedin",
                        origin="face",
                        match_kind="similar",
                    )
                )
            else:
                candidates.append(
                    Candidate(
                        image_url="",
                        page_url=profile_url,
                        source="serp-linkedin",
                        origin="linked",
                        match_kind="similar",
                    )
                )

            if len(candidates) >= max_results:
                break
        except Exception:
            continue

    return candidates


def post_owner(page_url: str, http: HttpCache) -> str | None:
    """Recovers the owner handle of an Instagram post from Google SERP results.

    Invariants:
    - Matches organic result by shortcode equality against page_url.
    - Extracts handle from title, source, or displayed_link.
    - NEVER extracts from snippet (hallucination / mention risk).
    - Handle validated against RESERVED_SEGMENTS.
    - Returns None if not found or on any error (R-14).
    """
    if not page_url:
        return None

    match = INSTAGRAM_POST_RE.search(page_url)
    if not match:
        return None

    target_shortcode = match.group(1)

    cfg = get_config()
    api_key = cfg.serpapi_key
    if not api_key:
        return None

    # Query for the exact post
    params = {
        "engine": "google",
        "q": f'site:instagram.com/p/{target_shortcode} OR site:instagram.com/reel/{target_shortcode}',
        "hl": "en",
        "num": 5,
        "api_key": api_key,
    }

    try:
        resp = http.get(SERPAPI_ENDPOINT, params=params, timeout=30.0)
        if not getattr(resp, "ok", False):
            return None
        data = resp.json()
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    organic = data.get("organic_results", [])
    if not isinstance(organic, list):
        return None

    for item in organic:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "")
        item_match = INSTAGRAM_POST_RE.search(link)
        if not item_match or item_match.group(1) != target_shortcode:
            continue

        title = str(item.get("title") or "")
        displayed_link = str(item.get("displayed_link") or "")
        source = str(item.get("source") or "")

        # 1. Try extracting handle from title: "Author (@handle) on Instagram: ..."
        title_match = INSTAGRAM_TITLE_HANDLE_RE.search(title)
        if title_match:
            candidate_handle = title_match.group(1).strip().lower()
            if candidate_handle not in RESERVED_SEGMENTS and len(candidate_handle) >= 2:
                return candidate_handle

        # 2. Try extracting handle from displayed link: "instagram.com › handle › p › ..."
        disp_match = INSTAGRAM_DISPLAYED_HANDLE_RE.search(displayed_link)
        if disp_match:
            candidate_handle = disp_match.group(1).strip().lower()
            if candidate_handle not in RESERVED_SEGMENTS and len(candidate_handle) >= 2:
                return candidate_handle

        # 3. Try source if it looks like a handle
        if source and source.lower() not in ("instagram", "meta"):
            clean_source = sanitize_handle(source).lower()
            if clean_source and clean_source not in RESERVED_SEGMENTS and len(clean_source) >= 2:
                return clean_source

    return None
