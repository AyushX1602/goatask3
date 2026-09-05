"""Research probe: WHY do candidate image fetches fail, and is it fixable?

Motivated by the live runs, where instagram / facebook / tiktok / news-CDN
candidates repeatedly came back `reject-fetch-failed`. The user's question
was direct: "why cant we fetch insta fb and all".

Tests each failing URL under three client profiles to separate the causes:
  1. our current UA          -> baseline
  2. a browser UA            -> detects plain UA blocking (FIXABLE)
  3. browser UA + Referer    -> detects hotlink protection (FIXABLE)

Anything still failing under all three is structurally unfetchable — most
importantly signed, expiring CDN URLs, which cannot be fixed at all.

Usage:  python scripts/probe_fetch_failures.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

OUR_UA = "face-chain-verify/0.1 (HH Goa 2026 shortlisting task; research tool)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# Real URLs taken verbatim from failing live runs.
CASES = [
    ("instagram via FB crawler proxy",
     "https://lookaside.fbsbx.com/lookaside/crawler/instagram/t_raines/profile_pic.jpg"),
    ("facebook crawler media",
     "https://lookaside.fbsbx.com/lookaside/crawler/media/?media_id=100044498046923"),
    ("tiktok SIGNED cdn (has x-expires + x-signature)",
     "https://p16-common-sign.tiktokcdn-us.com/tos-maliva-p-0068/7a328db4ea6a4f708675f2d0a4b45f42_1658459997~tplv-tiktokx-origin.image?dr=9636&x-expires=1788552000&x-signature=EdGTxkr5Jf%2FvK2Fhlht1rh7PPV4%3D&t=4d5b0474&ps=13740610&shp=81f88b70&shcp=55bbe6a9&idc=useast5"),
    ("tiktok api img endpoint",
     "https://www.tiktok.com/api/img/?userId=6600548875215372293&location=2&aid=1988&userAvatarTypeEnum=4"),
    ("news cdn yabiladi",
     "https://static.yabiladi.com/files/articles/37aef7c9b1f586b7f61d1dd06cb0bb41_646.jpg"),
    ("news cdn uol.com.br",
     "https://f.i.uol.com.br/fotografia/2026/06/01/17803590266a1e1f72e8ea5_1780359026_3x2_md.jpg"),
    ("goturkiye webp",
     "https://cdn2.goturkiye.com/instagram-17966182586960566-500x500.webp?1788415421050"),
    ("known-good control (wikimedia)",
     "https://upload.wikimedia.org/wikipedia/commons/8/8d/President_Barack_Obama.jpg"),
]


def attempt(url: str, headers: dict) -> str:
    try:
        r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        ct = (r.headers.get("content-type") or "").split(";")[0]
        size = len(r.content)
        if r.ok and ct.startswith("image/"):
            return f"OK {r.status_code} {ct} {size/1024:.0f}KB"
        if r.ok:
            return f"NOT-IMAGE {r.status_code} {ct} {size/1024:.0f}KB"
        return f"HTTP {r.status_code}"
    except requests.RequestException as e:
        return f"{type(e).__name__}"


def main() -> int:
    print(f"{'case':<42} {'ours':<26} {'browser UA':<26} {'UA+Referer'}")
    print("-" * 122)

    fixable_by_ua = []
    structurally_broken = []

    for label, url in CASES:
        host = urlparse(url).hostname or ""
        origin = f"https://{host}/"

        a = attempt(url, {"User-Agent": OUR_UA})
        b = attempt(url, {"User-Agent": BROWSER_UA})
        c = attempt(
            url,
            {
                "User-Agent": BROWSER_UA,
                "Referer": origin,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        print(f"{label:<42} {a:<26} {b:<26} {c}")

        ours_ok = a.startswith("OK")
        any_fix_ok = b.startswith("OK") or c.startswith("OK")
        if not ours_ok and any_fix_ok:
            fixable_by_ua.append(label)
        elif not ours_ok and not any_fix_ok:
            structurally_broken.append(label)

    print()
    print("FIXABLE by sending browser-like headers:")
    for x in fixable_by_ua or ["  (none)"]:
        print(f"  + {x}")
    print("STRUCTURALLY UNFETCHABLE (no header change helps):")
    for x in structurally_broken or ["  (none)"]:
        print(f"  - {x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
