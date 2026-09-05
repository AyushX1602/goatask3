"""Resolver cascade for candidates whose own image URL is missing or
unfetchable. See rules.md I-05/R-12, memory.md 6 Sep 2026 ("real crawling
instead of --").

verify/pipeline_run.py's existing size-variant walk (media_urls.py)
recovers a candidate when the SAME image exists at a different resolution.
This module is a different, complementary recovery path: it fetches the
candidate's PAGE and extracts a representative image from it, using only
mechanisms the page itself publishes for exactly this purpose — OpenGraph/
Twitter Card meta tags, keyless oEmbed endpoints, and Reddit's public
`.json` suffix. None of these require credentials, a scraper package, or a
UA claiming to be a specific company's crawler (I-05).

Each route is tried in order and the FIRST one that returns a decodable
image wins. Every attempt is recorded (route name, whether it succeeded)
so a `reject-*` reason can honestly say how many routes were tried instead
of just naming the last one — the exact category of defect R-24 exists to
forbid.

Deliberately NOT built as a general-purpose scraper: no headless browser,
no login, no residential proxy, no pagination, no per-host request budget
beyond what a single candidate needs. If none of these routes work, the
candidate is reported as unresolved and the existing prereject reasons
apply — this cascade only ADDS recovery paths, it never removes the
existing honest-failure behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.cache.http_cache import BROWSER_USER_AGENT, HttpCache
from pipeline.cache.urlguard import UnsafeUrlError, assert_safe_url, safe_fetch

# --------------------------------------------------------------------------
# Route 1: OpenGraph / Twitter Card image extraction
# --------------------------------------------------------------------------

# Deliberately simple regex extraction, not a full HTML parser — OpenGraph
# meta tags are a fixed, well-known shape and pulling in a dependency
# (bs4/lxml) for one attribute is not worth it. Matches og:image or
# og:image:secure_url, then twitter:image as a fallback, content= in either
# attribute order (content before/after property).
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?)["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?)["\']',
    re.IGNORECASE,
)
_TWITTER_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']twitter:image(?::src)?["\']',
    re.IGNORECASE,
)

# Only fetch pages as HTML, and only up to this many bytes of the response
# body before giving up — meta tags are always in <head>, so there is no
# reason to download an entire multi-megabyte page to find them.
_HTML_HEAD_SNIFF_BYTES = 200_000


def extract_opengraph_image(html: bytes) -> str | None:
    """Extracts og:image (preferred) or twitter:image from raw HTML bytes.
    Pure function, no network — testable against fixture HTML with no
    live request."""
    text = html[:_HTML_HEAD_SNIFF_BYTES].decode("utf-8", errors="replace")
    m = _OG_IMAGE_RE.search(text)
    if m:
        return m.group(1) or m.group(2)
    m = _TWITTER_IMAGE_RE.search(text)
    if m:
        return m.group(1) or m.group(2)
    return None


# --------------------------------------------------------------------------
# Route 2: keyless oEmbed endpoints
# --------------------------------------------------------------------------

_YOUTUBE_PAGE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[A-Za-z0-9_-]{11}"
)
_X_STATUS_RE = re.compile(r"(?:x|twitter)\.com/[^/]+/status/\d+")


def oembed_endpoint_for(page_url: str) -> str | None:
    """Returns the keyless oEmbed request URL for a known platform's post
    page, or None if the platform has no public oEmbed endpoint (most
    don't — this only covers YouTube and X/Twitter, which are the only two
    with a documented, keyless, unauthenticated oEmbed API).
    """
    if _YOUTUBE_PAGE_RE.search(page_url):
        return f"https://www.youtube.com/oembed?url={page_url}&format=json"
    if _X_STATUS_RE.search(page_url):
        return f"https://publish.twitter.com/oembed?url={page_url}"
    return None


def extract_oembed_thumbnail(payload: dict) -> str | None:
    """oEmbed responses use `thumbnail_url` by convention (both YouTube's
    and Twitter's implementations do). Pure function over a parsed JSON
    dict, no network."""
    return payload.get("thumbnail_url") or None


# --------------------------------------------------------------------------
# Route 3: Reddit's public .json suffix
# --------------------------------------------------------------------------

_REDDIT_POST_RE = re.compile(r"reddit\.com/r/[^/]+/comments/[A-Za-z0-9]+")


def reddit_json_url(page_url: str) -> str | None:
    """Reddit serves a documented, public, keyless JSON representation of
    any post by appending `.json` to its URL. Returns None for non-Reddit
    or non-post URLs (e.g. a bare subreddit or profile page)."""
    if not _REDDIT_POST_RE.search(page_url):
        return None
    return page_url.rstrip("/") + ".json"


def extract_reddit_image(payload) -> str | None:
    """Reddit's .json response is a listing; the post's own image (for an
    image post) is at data.children[0].data.url_overridden_by_dest, or
    data.preview.images[0].source.url for a preview-generated image. Pure
    function, no network."""
    try:
        post = payload[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None

    url = post.get("url_overridden_by_dest")
    if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return url

    try:
        preview_url = post["preview"]["images"][0]["source"]["url"]
        # Reddit's preview URLs are HTML-entity-encoded (&amp; for &).
        return preview_url.replace("&amp;", "&")
    except (KeyError, IndexError, TypeError):
        return None


# --------------------------------------------------------------------------
# The cascade itself
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolveAttempt:
    route: str  # "opengraph" | "oembed" | "reddit_json" | "none_applicable"
    succeeded: bool
    resolved_image_url: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class CascadeResult:
    image_url: str | None  # the URL that ultimately worked, or None
    attempts: tuple[ResolveAttempt, ...] = field(default_factory=tuple)

    @property
    def routes_tried(self) -> tuple[str, ...]:
        return tuple(a.route for a in self.attempts)


def resolve_page_to_image_url(http: HttpCache, page_url: str) -> CascadeResult:
    """Given a candidate's PAGE url (not its image url), tries each
    recovery route in order and returns the first resolved image URL.

    Does NOT fetch/verify the image itself — that is
    verify/pipeline_run.py's job, reusing the existing size-variant/face-
    gate machinery. This function's only responsibility is "what image URL
    should we try", using only the page's own published metadata.
    """
    attempts: list[ResolveAttempt] = []

    try:
        assert_safe_url(page_url)
    except UnsafeUrlError as e:
        return CascadeResult(
            None,
            (ResolveAttempt("urlguard", False, detail=f"unsafe page_url ({e.cause}): {e}"),),
        )

    oembed_url = oembed_endpoint_for(page_url)
    if oembed_url:
        try:
            assert_safe_url(oembed_url)
            body, status_code, _ = safe_fetch(
                http,
                oembed_url,
                timeout=10.0,
                headers={"User-Agent": BROWSER_USER_AGENT},
                is_image=False,
                max_bytes=100_000,
            )
            if 200 <= status_code < 300:
                import json

                thumb = extract_oembed_thumbnail(json.loads(body.decode("utf-8")))
                if thumb:
                    assert_safe_url(thumb)
                    attempts.append(ResolveAttempt("oembed", True, thumb))
                    return CascadeResult(thumb, tuple(attempts))
            attempts.append(ResolveAttempt("oembed", False, detail=f"http {status_code}"))
        except Exception as e:
            attempts.append(ResolveAttempt("oembed", False, detail=f"{type(e).__name__}: {e}"))

    reddit_url = reddit_json_url(page_url)
    if reddit_url:
        try:
            assert_safe_url(reddit_url)
            body, status_code, _ = safe_fetch(
                http,
                reddit_url,
                timeout=10.0,
                headers={"User-Agent": BROWSER_USER_AGENT},
                is_image=False,
                max_bytes=500_000,
            )
            if 200 <= status_code < 300:
                import json

                img = extract_reddit_image(json.loads(body.decode("utf-8")))
                if img:
                    assert_safe_url(img)
                    attempts.append(ResolveAttempt("reddit_json", True, img))
                    return CascadeResult(img, tuple(attempts))
            attempts.append(ResolveAttempt("reddit_json", False, detail=f"http {status_code}"))
        except Exception as e:
            attempts.append(ResolveAttempt("reddit_json", False, detail=f"{type(e).__name__}: {e}"))

    # OpenGraph last: broadest applicability (works on almost any page),
    # so it is the general fallback tried after the two platform-specific,
    # more-reliable routes above.
    try:
        assert_safe_url(page_url)
        body, status_code, _ = safe_fetch(
            http,
            page_url,
            timeout=10.0,
            headers={"User-Agent": BROWSER_USER_AGENT},
            is_image=False,
            max_bytes=_HTML_HEAD_SNIFF_BYTES,
        )
        if 200 <= status_code < 300:
            img = extract_opengraph_image(body)
            if img:
                assert_safe_url(img)
                attempts.append(ResolveAttempt("opengraph", True, img))
                return CascadeResult(img, tuple(attempts))
        attempts.append(ResolveAttempt("opengraph", False, detail=f"http {status_code}"))
    except Exception as e:
        attempts.append(ResolveAttempt("opengraph", False, detail=f"{type(e).__name__}: {e}"))

    return CascadeResult(None, tuple(attempts))
