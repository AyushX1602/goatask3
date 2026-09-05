"""Social-platform classification: allowlist, platform naming, content kind.
See design.md 2.5, R-06.

A match only counts if it sits on a public social platform, since the brief
asks specifically for a "social media post". Registrable-domain comparison,
not substring matching, so `evil-x.com.attacker.net` cannot pass as x.com.

CDN domains are included deliberately (5 Sep 2026). Measured on a live
Obama probe, the highest-scoring VERIFIABLE social results were served from
CDN hosts, not the platform's www domain:

    preview.redd.it   0.9786   <- was rejected as "redd.it not on allowlist"
    licdn.com         0.9675   <- was rejected
    pbs.twimg.com     0.9594   <- was rejected
    reddit.com        0.9630   <- accepted
    i.ytimg.com       0.9502   <- was rejected

Excluding CDNs discarded most of the usable evidence. A Hrithik Roshan run
threw away a 0.9771 `preview.redd.it` hit the same way.

**Allowlist principle (rules.md R-06):** a platform belongs here iff it is
one where an individual maintains a public identity profile and publishes
content under it. `github.com` was added under this test (5 Sep 2026) after
a live, genuine-search run scored a candidate's GitHub avatar at 0.9363 and
`reject-domain`'d it — an internal inconsistency, since `linkedin.com` was
already allowed under the identical reasoning and GitHub itself describes
the platform as social. The pre-fix audit log for that run is kept (with
identifying URLs sha256-redacted, never the person's identity or handle —
see calibration/quarantine/) alongside a re-run under the corrected list,
so both the honest old verdict and the corrected one are visible.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# registrable domain -> human platform name.
# Both page hosts and media/CDN hosts, so a candidate is judged on which
# platform it belongs to rather than on which subdomain served the bytes.
PLATFORM_DOMAINS: dict[str, str] = {
    # Reddit
    "reddit.com": "Reddit",
    "redd.it": "Reddit",
    # X / Twitter
    "x.com": "X",
    "twitter.com": "X",
    "twimg.com": "X",
    # YouTube
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "ytimg.com": "YouTube",
    # LinkedIn
    "linkedin.com": "LinkedIn",
    "licdn.com": "LinkedIn",
    # Meta
    "instagram.com": "Instagram",
    "cdninstagram.com": "Instagram",
    "facebook.com": "Facebook",
    "fbcdn.net": "Facebook",
    "fbsbx.com": "Meta",
    "threads.net": "Threads",
    # TikTok
    "tiktok.com": "TikTok",
    "tiktokcdn.com": "TikTok",
    "tiktokcdn-us.com": "TikTok",
    # Fediverse / other
    "bsky.app": "Bluesky",
    "mastodon.social": "Mastodon",
    "pinterest.com": "Pinterest",
    "tumblr.com": "Tumblr",
    # GitHub — added 5 Sep 2026 under the R-06 allowlist principle: a public
    # profile, a follower graph, and content (repos, README authorship)
    # published under that identity. Same reasoning already applied to
    # LinkedIn.
    "github.com": "GitHub",
    "githubusercontent.com": "GitHub",
}

# Platforms that serve media only to their own crawlers, so we can never
# fetch the image to run our own face check. Verified twice, six months
# apart in probe terms but same session: our research-tool UA, an ordinary
# desktop browser UA, and browser UA + Referer: https://www.google.com/ —
# RE-MEASURED 6 Sep 2026 (scripts/probe_meta_wall.py, owner instruction:
# "use whats best ... real crawling instead of --") specifically to check
# whether the original finding was a naive UA block rather than a real
# wall. It was not:
#   lookaside.fbsbx.com       -> 200, IDENTICAL 390-byte text/html stub,
#                                all three routes, byte-for-byte
#   lookaside.instagram.com   -> 200, IDENTICAL ~620-680KB text/html blob
#                                (larger than Meta's, still not an image),
#                                all three routes
#   tiktok.com/api/img/...    -> connection TIMEOUT, all three routes
#                                (not even a UA-dependent response —
#                                unreachable regardless of headers)
# A browser UA changes NOTHING for either platform. This is server-side
# access control (likely session/referer-chain validation, not naive
# User-Agent sniffing), and it is not something a header swap defeats.
# We do not send facebookexternalhit/Googlebot/Twitterbot or any UA that
# claims to BE a platform's own privileged crawler — that would be a
# materially different and disallowed move (impersonating a specific
# company's crawler to obtain access granted only to it), distinct from
# sending an ordinary browser UA, which is honest about being an ordinary
# HTTP client. Recorded so the pipeline can explain a rejection honestly
# instead of reporting a misleading generic fetch failure.
MEDIA_BLOCKED_PLATFORMS: frozenset[str] = frozenset(
    {"Instagram", "Facebook", "Meta"}
)

# TikTok is a split case, found live 5 Sep 2026 in the same run: the
# `tiktok.com/api/img/?...` endpoint is blocked, but `tiktokcdn-us.com`
# signed CDN URLs fetched successfully (score 0.0593, a genuine negative —
# see calibration/negatives_harvested.json). Blocking is a property of the
# ENDPOINT here, not the platform as a whole, so TikTok is deliberately
# excluded from MEDIA_BLOCKED_PLATFORMS above and checked by path instead.
TIKTOK_BLOCKED_PATH_PREFIXES: tuple[str, ...] = ("/api/img/", "/api/",)

# Kept for backwards compatibility with existing call sites.
SOCIAL_ALLOW: set[str] = set(PLATFORM_DOMAINS)


def registrable_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_allowed(url: str) -> bool:
    return registrable_domain(url) in PLATFORM_DOMAINS


def platform_name(url: str) -> str | None:
    """Human platform name for a URL, or None if it is not a known social
    platform. Used for display and for explaining rejections."""
    return PLATFORM_DOMAINS.get(registrable_domain(url))


def is_media_blocked(url: str) -> bool:
    """True if this URL is known to refuse programmatic media access, so a
    fetch failure is expected and explainable rather than a defect.

    TikTok is checked by ENDPOINT PATH, not by platform membership alone
    (see TIKTOK_BLOCKED_PATH_PREFIXES): its `tiktokcdn-us.com` signed CDN
    URLs are fetchable (verified live) while `tiktok.com/api/img/...` is
    not. Treating all of TikTok as blocked would misreport a fetchable CDN
    hit as unverifiable.
    """
    name = platform_name(url)
    if not name:
        return False
    if name == "TikTok":
        path = urlparse(url).path
        return any(path.startswith(p) for p in TIKTOK_BLOCKED_PATH_PREFIXES)
    return name in MEDIA_BLOCKED_PLATFORMS


# --------------------------------------------------------------------------
# Content kind — post vs profile (G1.2)
# --------------------------------------------------------------------------

# The brief asks for "at least one real, matching social media POST". A
# profile page is not strictly a post, and quietly reporting one as a post
# would be overclaiming. But profiles matter enormously in practice: for a
# non-celebrity, their profile picture is frequently the ONLY image of them
# that any search engine has indexed, so refusing to accept profiles would
# mean the pipeline only works on public figures. Verified live on a real
# non-celebrity probe, where an x.com profile was the single plausible hit
# among 26 candidates.
#
# So we accept both and label them accurately, rather than either dropping
# profiles or blurring the distinction.

CONTENT_KIND_POST = "post"
CONTENT_KIND_PROFILE = "profile"
CONTENT_KIND_UNKNOWN = "unknown"

# Ordered, most-specific-first. Each entry: (compiled pattern, kind).
# Patterns are matched against the full URL. Post patterns are listed
# before profile patterns for the same platform, since a post URL usually
# contains the profile path as a prefix (e.g. x.com/<handle>/status/<id>).
_CONTENT_KIND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # X / Twitter
    (re.compile(r"(?:x|twitter)\.com/[^/]+/status/\d+"), CONTENT_KIND_POST),
    (re.compile(r"pbs\.twimg\.com/media/"), CONTENT_KIND_POST),
    (re.compile(r"pbs\.twimg\.com/profile_images/"), CONTENT_KIND_PROFILE),
    (re.compile(r"(?:x|twitter)\.com/[A-Za-z0-9_]{1,15}/?$"), CONTENT_KIND_PROFILE),
    # YouTube — a video/short IS the post; @handle or /channel/ is a profile
    (re.compile(r"youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/"), CONTENT_KIND_POST),
    (re.compile(r"i\.ytimg\.com/vi/"), CONTENT_KIND_POST),
    (re.compile(r"youtube\.com/(?:@|channel/|c/|user/)"), CONTENT_KIND_PROFILE),
    # Instagram / Threads
    (re.compile(r"instagram\.com/(?:p|reel|tv)/"), CONTENT_KIND_POST),
    (re.compile(r"threads\.net/@[^/]+/post/"), CONTENT_KIND_POST),
    (re.compile(r"instagram\.com/[A-Za-z0-9_.]+/?$"), CONTENT_KIND_PROFILE),
    # Facebook
    (re.compile(r"facebook\.com/[^/]+/(?:posts|videos|photos)/"), CONTENT_KIND_POST),
    (re.compile(r"facebook\.com/(?:permalink\.php|photo)"), CONTENT_KIND_POST),
    # LinkedIn
    (re.compile(r"linkedin\.com/(?:posts|feed/update)/"), CONTENT_KIND_POST),
    (re.compile(r"linkedin\.com/in/"), CONTENT_KIND_PROFILE),
    (re.compile(r"licdn\.com/.*profile-displayphoto"), CONTENT_KIND_PROFILE),
    # Reddit
    (re.compile(r"reddit\.com/r/[^/]+/comments/"), CONTENT_KIND_POST),
    (re.compile(r"reddit\.com/(?:user|u)/"), CONTENT_KIND_PROFILE),
    # Bluesky
    (re.compile(r"bsky\.app/profile/[^/]+/post/"), CONTENT_KIND_POST),
    (re.compile(r"bsky\.app/profile/[^/]+/?$"), CONTENT_KIND_PROFILE),
    # TikTok
    (re.compile(r"tiktok\.com/@[^/]+/video/"), CONTENT_KIND_POST),
    (re.compile(r"tiktok\.com/@[A-Za-z0-9_.]+/?$"), CONTENT_KIND_PROFILE),
    # GitHub — a repo page is treated as belonging to the owner's profile
    # by the time content_kind() sees it (search/media_urls.derive_github_
    # profile_url rewrites page_url before this runs), so a bare
    # github.com/<user> is always a profile, never a "post": GitHub has no
    # per-post permalink concept for an avatar the way a social feed does.
    (re.compile(r"github\.com/[A-Za-z0-9_-]+/?$"), CONTENT_KIND_PROFILE),
)


def content_kind(page_url: str, image_url: str = "") -> str:
    """Classifies a candidate as a post, a profile, or unknown.

    Checks page_url first (it is the citable artifact), then falls back to
    image_url, which sometimes carries the only usable signal — e.g. a bare
    `pbs.twimg.com/profile_images/...` CDN hit whose parent page X does not
    encode in the URL.

    Returns CONTENT_KIND_UNKNOWN rather than guessing when neither URL
    matches a known shape. `unknown` is an honest answer and is reported as
    such; it must never be silently upgraded to "post".
    """
    for candidate_url in (page_url, image_url):
        if not candidate_url:
            continue
        for pattern, kind in _CONTENT_KIND_PATTERNS:
            if pattern.search(candidate_url):
                return kind
    return CONTENT_KIND_UNKNOWN
