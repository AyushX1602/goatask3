"""Sanity-check that a matched post URL is a REAL, live, openable page.

An accepted match is only worth anything if a human (or judge) can open the
URL and see the person. Where the post URL was DERIVED from a CDN image url
(YouTube), this confirms the derivation resolves to a live video, not a 404.

For YouTube this uses the oEmbed endpoint rather than scraping the watch
page. Scraping is unreliable: a watch page returns HTTP 200 and contains
strings like "Video unavailable" inside its JS bundle even for perfectly
live videos, which produces false negatives. oEmbed returns 200 + metadata
for a live video and 404 for a removed or private one, so it is
authoritative.

Usage:  python scripts/verify_match_urls.py <url> [<url> ...]
"""

from __future__ import annotations

import re
import sys

import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    )
}


def check_youtube(url: str) -> bool:
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            headers=UA,
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"  FAIL   {type(e).__name__}  {url}")
        return False

    if r.status_code == 404:
        print(f"  DEAD   removed/private  {url}")
        return False
    if not r.ok:
        print(f"  ?      oembed HTTP {r.status_code}  {url}")
        return False

    d = r.json()
    print(f"  LIVE   {url}")
    print(f"         title:   {d.get('title', '')[:96]}")
    print(f"         channel: {d.get('author_name', '')}  ({d.get('author_url', '')})")
    return True


def check_generic(url: str) -> bool:
    try:
        r = requests.get(url, headers=UA, timeout=20)
    except requests.RequestException as e:
        print(f"  FAIL   {type(e).__name__}  {url}")
        return False
    if not r.ok:
        print(f"  FAIL   HTTP {r.status_code}  {url}")
        return False
    m = re.search(r"<title>([^<]{0,120})</title>", r.text)
    print(f"  LIVE   HTTP {r.status_code}  {url}")
    if m:
        print(f"         title: {m.group(1).strip()[:96]}")
    return True


def main() -> int:
    urls = sys.argv[1:]
    if not urls:
        print("usage: verify_match_urls.py <url> [<url> ...]")
        return 1

    print(f"checking {len(urls)} URL(s)\n")
    live = 0
    for u in urls:
        if "youtube.com" in u or "youtu.be" in u:
            live += check_youtube(u)
        else:
            live += check_generic(u)
    print(f"\n{live} of {len(urls)} URL(s) are live and openable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
