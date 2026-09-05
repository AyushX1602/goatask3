"""THE decisive diagnostic: can we actually find + VERIFY a social media
post for a given person?

The brief requires "at least one real, matching social media post". Live
runs kept ending in NO_MATCH, so this establishes, per platform, which of
the two halves is failing:

  (a) DISCOVERY  -- did the search engine return a social post URL at all?
  (b) VERIFIABILITY -- can we fetch that post's image to run our own face
      check on it? (If not, we cannot honestly call it a verified match.)

Also measures the impact of sending the ORIGINAL photo vs the 112x112
aligned crop as the search query.

Usage:
  python scripts/probe_social_reach.py --name "Shah Rukh Khan"
  python scripts/probe_social_reach.py --image path/to/face.jpg
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import get_config  # noqa: E402
from pipeline.face.align import align  # noqa: E402
from pipeline.face.detect import FaceDetector  # noqa: E402
from pipeline.face.embed import FaceEmbedder  # noqa: E402
from pipeline.search.image_prep import prepare_search_image  # noqa: E402
from pipeline.search.web_detect import parse_gcv  # noqa: E402

CACHE = REPO_ROOT / ".cache" / "social_reach"
UA = {"User-Agent": "face-chain-verify/0.1 (research probe)"}

SOCIAL_PLATFORMS = {
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "x.com": "X/Twitter",
    "twitter.com": "X/Twitter",
    "reddit.com": "Reddit",
    "redd.it": "Reddit (CDN)",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "ytimg.com": "YouTube (CDN)",
    "tiktok.com": "TikTok",
    "tiktokcdn-us.com": "TikTok (CDN)",
    "linkedin.com": "LinkedIn",
    "licdn.com": "LinkedIn (CDN)",
    "threads.net": "Threads",
    "bsky.app": "Bluesky",
    "twimg.com": "X/Twitter (CDN)",
    "fbsbx.com": "Meta (crawler proxy)",
    "cdninstagram.com": "Instagram (CDN)",
    "mastodon.social": "Mastodon",
    "pinterest.com": "Pinterest",
    "tumblr.com": "Tumblr",
}


def registrable(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def platform_of(url: str) -> str | None:
    return SOCIAL_PLATFORMS.get(registrable(url))


def wikimedia_photo(name: str) -> tuple[bytes, str] | None:
    r = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "generator": "search", "gsrsearch": name,
            "gsrnamespace": 6, "gsrlimit": 10,
            "prop": "imageinfo", "iiprop": "url|size", "format": "json",
        },
        headers=UA, timeout=30,
    )
    r.raise_for_status()
    pages = (r.json().get("query", {}) or {}).get("pages", {}) or {}
    best = None
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        url, w, h = ii.get("url"), ii.get("width") or 0, ii.get("height") or 0
        if not url or not url.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        if w < 400 or h < 400:
            continue
        if best is None or w * h > best[1]:
            best = (url, w * h)
    if not best:
        return None
    img = requests.get(best[0], headers=UA, timeout=30)
    img.raise_for_status()
    return img.content, best[0]


def gcv(label: str, image_bytes: bytes, key: str) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{label}.json"
    if f.exists():
        print(f"  [cache] {label} (0 units)")
        return json.loads(f.read_text(encoding="utf-8"))
    print(f"  [live]  {label} (1 GCV unit)")
    r = requests.post(
        "https://vision.googleapis.com/v1/images:annotate",
        params={"key": key},
        json={"requests": [{
            "image": {"content": base64.b64encode(image_bytes).decode()},
            "features": [{"type": "WEB_DETECTION", "maxResults": 50}],
        }]},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def check_fetchable(url: str) -> tuple[bool, str]:
    try:
        r = requests.get(url, headers=UA, timeout=12)
        ct = (r.headers.get("content-type") or "").split(";")[0]
        if not r.ok:
            return False, f"HTTP {r.status_code}"
        if not ct.startswith("image/"):
            return False, f"{ct} ({len(r.content)}B) not an image"
        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return False, "undecodable"
        return True, f"{img.shape[1]}x{img.shape[0]}"
    except requests.RequestException as e:
        return False, type(e).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name")
    ap.add_argument("--image")
    args = ap.parse_args()

    key = get_config().gcv_api_key
    if not key:
        print("GCV_API_KEY not set in .env")
        return 1

    if args.image:
        raw = Path(args.image).read_bytes()
        label = Path(args.image).stem
        src = args.image
    elif args.name:
        got = wikimedia_photo(args.name)
        if not got:
            print(f"no suitable Wikimedia photo found for {args.name!r}")
            return 1
        raw, src = got
        label = args.name.replace(" ", "_").lower()
    else:
        print("need --name or --image")
        return 1

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    print(f"probe source: {src}")
    print(f"probe size:   {img.shape[1]}x{img.shape[0]}\n")

    det, emb = FaceDetector(), FaceEmbedder()
    faces = det.detect(img)
    if not faces:
        print("no face detected in probe")
        return 1
    probe_vec = emb.embed(align(img, faces[0].kps5)).vec

    # --- the two query variants -------------------------------------
    crop = align(img, faces[0].kps5)
    _, crop_png = cv2.imencode(".png", crop)

    variants = {
        "ORIGINAL (new behaviour)": (f"{label}__orig", prepare_search_image(img)),
        "112px ALIGNED CROP (old behaviour)": (f"{label}__crop", crop_png.tobytes()),
    }

    for title, (lab, payload_bytes) in variants.items():
        data = gcv(lab, payload_bytes, key)
        cands, signals = parse_gcv(data)
        web = (data.get("responses") or [{}])[0].get("webDetection", {}) or {}

        print(f"\n{'='*74}\n{title}\n{'='*74}")
        print(f"  webEntities: {len(web.get('webEntities') or [])}"
              f" | bestGuess: {[b.get('label') for b in web.get('bestGuessLabels') or []]}")
        print(f"  identity signals: {signals[:4] if signals else '(NONE - not recognised)'}")
        print(f"  candidates parsed: {len(cands)}")

        by_platform = defaultdict(list)
        for c in cands:
            p = platform_of(c.page_url) or platform_of(c.image_url)
            if p:
                by_platform[p].append(c)

        if not by_platform:
            print("\n  (a) DISCOVERY: no social platform URLs returned at all")
            continue

        print(f"\n  (a) DISCOVERY: social URLs found on {len(by_platform)} platform(s)")
        print("\n  (b) VERIFIABILITY per platform:")
        print(f"  {'platform':<22} {'page':<8} {'image fetch':<26} {'our score'}")
        print("  " + "-" * 72)

        for plat, cs in sorted(by_platform.items()):
            for c in cs[:3]:
                ok, why = check_fetchable(c.image_url) if c.image_url else (False, "no image url")
                score = "-"
                if ok:
                    r = requests.get(c.image_url, headers=UA, timeout=12)
                    cim = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
                    cfaces = det.detect(cim)
                    if cfaces:
                        best = max(
                            float(np.dot(emb.embed(align(cim, f.kps5)).vec, probe_vec))
                            for f in cfaces
                        )
                        score = f"{best:.4f}"
                    else:
                        score = "no face"
                mark = "OK" if ok else "BLOCKED"
                print(f"  {plat:<22} {'yes':<8} {mark+' '+why:<26} {score}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
