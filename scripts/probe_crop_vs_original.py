"""Research probe: does sending GCV the 112x112 ALIGNED CROP instead of the
ORIGINAL image degrade web-detection quality?

Motivated by a live failure (5 Sep 2026): a Shah Rukh Khan upload produced
ZERO webEntities and 20 unrelated people (Don Francisco, Donny Osmond,
doximity doctor headshots), max score 0.2644. Hypothesis: reverse image
search matches against Google's index of FULL photographs, so a tiny,
tightly-cropped, geometrically-warped 112x112 face is poor query input.
The aligned crop exists for OUR ArcFace embedding; it was never meant to
be the search query.

Costs 2 GCV units per run (free tier is ~1,000/month). Responses cached.

Usage:  python scripts/probe_crop_vs_original.py --image <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import cv2

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import get_config  # noqa: E402
from pipeline.face.align import align  # noqa: E402
from pipeline.face.detect import FaceDetector  # noqa: E402
from pipeline.search.web_detect import WebDetectProvider, parse_gcv  # noqa: E402

CACHE = REPO_ROOT / ".cache" / "crop_vs_original"

SOCIAL = {
    "instagram.com", "facebook.com", "x.com", "twitter.com", "reddit.com",
    "youtube.com", "tiktok.com", "linkedin.com", "threads.net", "bsky.app",
}


def registrable(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def run_gcv(label: str, image_bytes: bytes, key: str) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{label}.json"
    if f.exists():
        print(f"  [cache] {label} (0 units)")
        return json.loads(f.read_text(encoding="utf-8"))

    print(f"  [live]  {label} (1 unit)")
    import base64

    import requests

    resp = requests.post(
        "https://vision.googleapis.com/v1/images:annotate",
        params={"key": key},
        json={
            "requests": [
                {
                    "image": {"content": base64.b64encode(image_bytes).decode()},
                    "features": [{"type": "WEB_DETECTION", "maxResults": 40}],
                }
            ]
        },
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def report(label: str, payload: dict) -> None:
    cands, signals = parse_gcv(payload)
    web = (payload.get("responses") or [{}])[0].get("webDetection", {}) or {}

    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    print(f"  webEntities:              {len(web.get('webEntities') or [])}")
    print(f"  bestGuessLabels:          {[b.get('label') for b in web.get('bestGuessLabels') or []]}")
    print(f"  pagesWithMatchingImages:  {len(web.get('pagesWithMatchingImages') or [])}")
    print(f"  fullMatchingImages:       {len(web.get('fullMatchingImages') or [])}")
    print(f"  partialMatchingImages:    {len(web.get('partialMatchingImages') or [])}")
    print(f"  visuallySimilarImages:    {len(web.get('visuallySimilarImages') or [])}")
    print(f"  -> parsed candidates:     {len(cands)}")

    print(f"\n  IDENTITY SIGNALS (this is what tells us it recognised the person):")
    if signals:
        for s in signals[:8]:
            print(f"    * {s}")
    else:
        print("    (NONE — the API did not identify the subject)")

    doms = Counter(registrable(c.page_url) for c in cands)
    social_hits = [c for c in cands if registrable(c.page_url) in SOCIAL]
    print(f"\n  social-domain candidates: {len(social_hits)} of {len(cands)}")
    for c in social_hits[:8]:
        print(f"    * {c.page_url[:88]}")
    print(f"\n  top domains: {[d for d, _ in doms.most_common(8)]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    args = ap.parse_args()

    key = get_config().gcv_api_key
    if not key:
        print("GCV_API_KEY not set")
        return 1

    src = Path(args.image)
    img = cv2.imread(str(src))
    if img is None:
        print(f"could not read {src}")
        return 1

    det = FaceDetector()
    faces = det.detect(img)
    if not faces:
        print("no face detected in probe")
        return 1

    original_bytes = src.read_bytes()
    crop = align(img, faces[0].kps5)
    ok, crop_png = cv2.imencode(".png", crop)
    crop_bytes = crop_png.tobytes()

    print(f"probe: {src.name}")
    print(f"  original: {img.shape[1]}x{img.shape[0]}, {len(original_bytes)/1024:.0f} KB")
    print(f"  aligned crop: {crop.shape[1]}x{crop.shape[0]}, {len(crop_bytes)/1024:.0f} KB")
    print()

    stem = src.stem
    report(f"A. ALIGNED CROP 112x112 (what the pipeline sends today)",
           run_gcv(f"{stem}__crop", crop_bytes, key))
    report(f"B. ORIGINAL IMAGE (what it should probably send)",
           run_gcv(f"{stem}__original", original_bytes, key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
