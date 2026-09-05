"""Social-platform allowlist. See design.md 2.5, R-06.

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
"""

from __future__ import annotations

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
}

# Platforms that serve media only to their own crawlers, so we can never
# fetch the image to run our own face check. Verified 5 Sep 2026 against
# our UA, a browser UA, and browser UA + Referer -- all three identical:
#   fbsbx.com / instagram.com / facebook.com -> 200 with a tiny text/html stub
#   tiktokcdn-us.com                         -> 403 (signed, expiring URL)
# This is deliberate access control on their side, not blocking we can
# route around. Recorded so the pipeline can explain a rejection honestly
# instead of reporting a misleading generic fetch failure.
MEDIA_BLOCKED_PLATFORMS: frozenset[str] = frozenset(
    {"Instagram", "Facebook", "Meta", "TikTok"}
)

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
    """True if this platform is known to refuse programmatic media access,
    so a fetch failure is expected and explainable rather than a defect."""
    name = platform_name(url)
    return name in MEDIA_BLOCKED_PLATFORMS if name else False
