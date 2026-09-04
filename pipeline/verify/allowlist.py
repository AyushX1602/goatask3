"""Social-domain allowlist. See design.md 2.5, R-06.

A match only counts if it sits on a public social platform. Registrable-
domain comparison, not substring matching, so evil-x.com.attacker.net
cannot pass as x.com.
"""

from __future__ import annotations

from urllib.parse import urlparse

SOCIAL_ALLOW: set[str] = {
    "bsky.app",
    "mastodon.social",
    "x.com",
    "twitter.com",
    "instagram.com",
    "facebook.com",
    "linkedin.com",
    "reddit.com",
    "youtube.com",
    "tiktok.com",
    "threads.net",
}


def registrable_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_allowed(url: str) -> bool:
    return registrable_domain(url) in SOCIAL_ALLOW
