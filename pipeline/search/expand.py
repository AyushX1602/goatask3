"""Profile expansion with strict face gating (T2.6 / R-28 / D-45).

Closes the recall gap where social profiles (e.g. LinkedIn, GitHub, X) exist
for a verified subject but were not directly returned by reverse-image search.

Invariants (R-28):
  1. Handles are resolved strictly from URL shape — never invented or guessed.
  2. Reserved path segments (/p/, /reel/, /pub/, /dir/, /shorts/, /issues/, etc.)
     are strictly excluded and never treated as usernames.
  3. Outbound links on verified pages (and 1-hop link-in-bio hosts) are inspected
     for explicit claims.
  4. Face-gating is strict: any expanded candidate with an image must re-enter
     the verification pipeline and clear threshold and margin on its own ArcFace
     score to be accepted as a face match.
  5. Origin distinction:
       origin="face": biometrically verifiable candidate (scored).
       origin="linked": published claim on a verified page where media is blocked
                        or unavailable (unscored, never counted as a match).
"""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlparse

from pipeline.search.base import Candidate

# Reserved segments that must NEVER be parsed as a profile handle
RESERVED_SEGMENTS = frozenset(
    {
        "p",
        "reel",
        "reels",
        "stories",
        "explore",
        "direct",
        "accounts",
        "shorts",
        "watch",
        "channel",
        "playlist",
        "feed",
        "c",
        "issues",
        "pulls",
        "orgs",
        "settings",
        "marketplace",
        "topics",
        "pub",
        "dir",
        "company",
        "school",
        "jobs",
        "posts",
        "learning",
        "status",
        "hashtag",
        "i",
        "home",
        "r",
        "u",
        "user",
        "comments",
        "login",
        "signup",
        "about",
        "contact",
        "terms",
        "privacy",
        "help",
        "intent",
        "share",
        "sharearticle",
        "post",
        "search",
        "notifications",
    }
)

LINK_IN_BIO_DOMAINS = frozenset(
    {"linktr.ee", "campsite.bio", "bio.link", "beacons.ai", "lnk.bio", "flowcode.com"}
)

KNOWN_PLATFORMS = ("github", "x", "linkedin", "instagram", "youtube", "bluesky")


def extract_handle(url: str) -> tuple[str, str] | None:
    """Extracts (platform, handle) from a profile URL based strictly on URL structure.

    Returns None if the URL does not match a profile shape or hits a reserved segment.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.strip("/")
    if not path:
        return None

    segments = [s for s in path.split("/") if s]
    if not segments:
        return None

    # Check for reserved segments in any path component
    if any(s.lower() in RESERVED_SEGMENTS for s in segments):
        return None

    # GitHub: github.com/{handle}
    if host == "github.com":
        if len(segments) == 1:
            return "github", segments[0]
        return None

    # X / Twitter: x.com/{handle} or twitter.com/{handle}
    if host in ("x.com", "twitter.com"):
        if len(segments) == 1:
            return "x", segments[0]
        return None

    # LinkedIn: linkedin.com/in/{handle}
    if host == "linkedin.com":
        raw_segments = [s for s in path.split("/") if s]
        if len(raw_segments) == 2 and raw_segments[0].lower() == "in":
            handle = raw_segments[1]
            if handle.lower() not in RESERVED_SEGMENTS:
                return "linkedin", handle
        return None

    # Instagram: instagram.com/{handle}
    if host == "instagram.com":
        if len(segments) == 1:
            return "instagram", segments[0]
        return None

    # YouTube: youtube.com/@{handle}
    if host == "youtube.com":
        if len(segments) == 1 and segments[0].startswith("@"):
            handle = segments[0].lstrip("@")
            if handle.lower() not in RESERVED_SEGMENTS:
                return "youtube", handle
        return None

    # Bluesky: bsky.app/profile/{handle}
    if host == "bsky.app":
        raw_segments = [s for s in path.split("/") if s]
        if len(raw_segments) == 2 and raw_segments[0].lower() == "profile":
            handle = raw_segments[1]
            if handle.lower() not in RESERVED_SEGMENTS:
                return "bluesky", handle
        return None

    return None


def derive_profile_urls(handle: str, exclude_platform: str | None = None) -> list[tuple[str, str]]:
    """Derives candidate profile URLs on known platforms from a verified handle."""
    if not handle or handle.lower() in RESERVED_SEGMENTS:
        return []

    clean_handle = handle.lstrip("@")
    candidates = [
        ("github", f"https://github.com/{clean_handle}"),
        ("x", f"https://x.com/{clean_handle}"),
        ("linkedin", f"https://www.linkedin.com/in/{clean_handle}"),
        ("instagram", f"https://www.instagram.com/{clean_handle}"),
        ("youtube", f"https://www.youtube.com/@{clean_handle}"),
    ]
    return [(plat, url) for plat, url in candidates if plat != exclude_platform]


def extract_outbound_social_links(html: str, source_url: str = "") -> list[str]:
    """Scans HTML content for links pointing to known social platforms or link-in-bio hosts."""
    if not html:
        return []

    hrefs = re.findall(r'href=["\'](https?://[^"\'>\s]+)["\']', html, re.IGNORECASE)
    valid_links: list[str] = []
    seen = set()

    for raw_url in hrefs:
        clean_url = raw_url.split("#")[0].split("?")[0].rstrip("/")
        if clean_url in seen:
            continue
        try:
            parsed = urlparse(clean_url)
            host = (parsed.hostname or "").lower().removeprefix("www.")
        except Exception:
            continue

        # Check if it's a link-in-bio host
        if host in LINK_IN_BIO_DOMAINS:
            seen.add(clean_url)
            valid_links.append(clean_url)
            continue

        # Check if it's a valid profile link on a known platform
        handle_info = extract_handle(clean_url)
        if handle_info is not None:
            seen.add(clean_url)
            valid_links.append(clean_url)

    return valid_links


def expand_verified_candidates(
    verified_candidates: list[Candidate],
    http_get_fn: Callable[[str], tuple[bool, bytes]] | None = None,
) -> list[Candidate]:
    """Generates expanded candidates from verified candidates (R-28).

    For each candidate:
      - Extracts handle from URL and derives candidate profiles on known platforms.
      - If http_get_fn is provided, fetches page HTML and extracts outbound social links
        (including 1 hop through link-in-bio services).

    Candidates with directly resolvable images (e.g. GitHub avatar) receive origin="face".
    Candidates where media is blocked/unresolvable (e.g. LinkedIn, Instagram) receive
    origin="linked" and image_url="", ensuring they are recorded as claimed profiles
    without being counted as face matches.
    """
    existing_urls = {c.page_url.rstrip("/") for c in verified_candidates}
    discovered_urls: set[str] = set()
    expanded_candidates: list[Candidate] = []

    for cand in verified_candidates:
        handle_info = extract_handle(cand.page_url)
        source_plat = handle_info[0] if handle_info else None
        handle = handle_info[1] if handle_info else None

        # 1. Derive profile URLs from handle
        if handle and source_plat:
            for plat, derived_url in derive_profile_urls(handle, exclude_platform=source_plat):
                clean = derived_url.rstrip("/")
                if clean not in existing_urls and clean not in discovered_urls:
                    discovered_urls.add(clean)
                    _add_expanded_candidate(expanded_candidates, plat, clean, handle)

        # 2. Extract outbound links if HTML fetcher provided
        if http_get_fn:
            try:
                ok, body = http_get_fn(cand.page_url)
                if ok and body:
                    html_text = body.decode("utf-8", errors="ignore")
                    outbound = extract_outbound_social_links(html_text, cand.page_url)
                    for link in outbound:
                        try:
                            phost = (urlparse(link).hostname or "").lower().removeprefix("www.")
                        except Exception:
                            continue

                        # 1-hop link-in-bio expansion
                        if phost in LINK_IN_BIO_DOMAINS:
                            bio_ok, bio_body = http_get_fn(link)
                            if bio_ok and bio_body:
                                bio_html = bio_body.decode("utf-8", errors="ignore")
                                bio_outbound = extract_outbound_social_links(bio_html, link)
                                for bio_link in bio_outbound:
                                    clean_bio = bio_link.rstrip("/")
                                    if clean_bio not in existing_urls and clean_bio not in discovered_urls:
                                        h_info = extract_handle(clean_bio)
                                        if h_info:
                                            discovered_urls.add(clean_bio)
                                            _add_expanded_candidate(expanded_candidates, h_info[0], clean_bio, h_info[1])
                            continue

                        clean = link.rstrip("/")
                        if clean not in existing_urls and clean not in discovered_urls:
                            h_info = extract_handle(clean)
                            if h_info:
                                discovered_urls.add(clean)
                                _add_expanded_candidate(expanded_candidates, h_info[0], clean, h_info[1])
            except Exception:
                pass

    return expanded_candidates


def _add_expanded_candidate(
    target_list: list[Candidate], platform: str, url: str, handle: str
) -> None:
    """Helper to classify expanded candidate into origin='face' or origin='linked'."""
    if platform == "github":
        target_list.append(
            Candidate(
                page_url=url,
                image_url=f"https://github.com/{handle}.png",
                source="expand-github",
                origin="face",
                image_url_fallbacks=(),
            )
        )
    elif platform in ("linkedin", "instagram"):
        target_list.append(
            Candidate(
                page_url=url,
                image_url="",
                source=f"expand-{platform}",
                origin="linked",
                image_url_fallbacks=(),
            )
        )
    else:
        target_list.append(
            Candidate(
                page_url=url,
                image_url="",
                source=f"expand-{platform}",
                origin="linked",
                image_url_fallbacks=(),
            )
        )
