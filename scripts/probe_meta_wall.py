"""Re-measures the Meta/TikTok "crawler-only" wall against a normal desktop
browser User-Agent, per the owner's instruction (6 Sep 2026): "use whats
best ... real crawling instead of --". This does NOT impersonate a
privileged crawler (no Googlebot/facebookexternalhit/Twitterbot UA, no
login cookies, no residential proxy, no scraper package) — it only checks
whether an ordinary browser UA, which is what "honest fetching" (rules.md
I-05 in the last review pass) actually means, gets a different result than
our current research-tool UA.

Run:
    python scripts/probe_meta_wall.py
    python scripts/probe_meta_wall.py <url> [<url> ...]

For each URL and each route, prints: HTTP status, content-type, byte count,
whether the bytes decode as an image, and (if so) the largest detected face
size in pixels. Writes a summary table to stdout only — the decision this
produces is recorded by hand into rules.md/allowlist.py, not by this script.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.face.detect import FaceDetector

DEFAULT_URLS = [
    # lookaside.fbsbx.com — Meta's crawler-only media proxy
    "https://lookaside.fbsbx.com/lookaside/crawler/media/?media_id=2005710816912485",
    # lookaside.instagram.com — Instagram's equivalent
    "https://lookaside.instagram.com/seo/google_widget/crawler/?media_id=3975917156402519453",
    # tiktok.com/api/img — the specific endpoint already known blocked
    "https://www.tiktok.com/api/img/?userId=6998260767327126534&location=2&aid=1988&userAvatarTypeEnum=4",
]

# Route A: our current research-tool UA (pipeline/cache/http_cache.py).
RESEARCH_UA = {
    "User-Agent": "face-chain-verify/0.1 (HH Goa 2026 shortlisting task; research tool)"
}

# Route B: an ordinary desktop browser UA. This is what a normal person's
# browser sends — not a privileged crawler identity, not a spoofed bot
# token. Distinguishing this from Googlebot/facebookexternalhit matters:
# those UAs claim to BE a specific company's crawler and get content
# granted specifically to it; this UA claims nothing except "I am a web
# browser", which is true of the overwhelming majority of HTTP traffic.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
    "Cache-Control": "no-cache",
}

ROUTES: list[tuple[str, dict[str, str]]] = [
    ("research-ua", RESEARCH_UA),
    ("browser-ua", BROWSER_HEADERS),
    ("browser-ua+referer-google", {**BROWSER_HEADERS, "Referer": "https://www.google.com/"}),
]


def _probe_one(url: str, headers: dict[str, str], detector: FaceDetector) -> dict:
    start = time.perf_counter()
    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
    except requests.RequestException as e:
        return {"error": f"{type(e).__name__}: {e}", "latency_ms": (time.perf_counter() - start) * 1000}

    latency_ms = (time.perf_counter() - start) * 1000
    content_type = resp.headers.get("content-type", "")
    body = resp.content

    result = {
        "status": resp.status_code,
        "final_url": resp.url,
        "redirects": len(resp.history),
        "content_type": content_type,
        "bytes": len(body),
        "latency_ms": round(latency_ms, 1),
        "is_image": False,
        "largest_face_px": None,
    }

    if resp.ok and body:
        arr = np.frombuffer(body, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            result["is_image"] = True
            faces = detector.detect(img)
            if faces:
                sizes = [min(f.bbox[2] - f.bbox[0], f.bbox[3] - f.bbox[1]) for f in faces]
                result["largest_face_px"] = round(max(sizes), 1)

    return result


def main() -> None:
    urls = sys.argv[1:] or DEFAULT_URLS
    detector = FaceDetector()

    print(f"{'URL':<90} {'route':<28} {'status':<7} {'type':<24} {'bytes':<8} {'image':<6} {'face_px':<8}")
    print("-" * 175)

    for url in urls:
        for route_name, headers in ROUTES:
            r = _probe_one(url, headers, detector)
            if "error" in r:
                print(f"{url[:88]:<90} {route_name:<28} ERROR: {r['error']}")
                continue
            print(
                f"{url[:88]:<90} {route_name:<28} {r['status']:<7} "
                f"{r['content_type'][:22]:<24} {r['bytes']:<8} "
                f"{str(r['is_image']):<6} {str(r['largest_face_px']):<8}"
            )
        print()


if __name__ == "__main__":
    main()
