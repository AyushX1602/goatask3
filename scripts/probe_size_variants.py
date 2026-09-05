"""Research probe (no pipeline changes): are we fetching the WRONG SIZE
variant of social-CDN images, and losing verifiable matches to the 50px
quality gate as a result?

Triggered by a live SRK run where an X/Twitter image was rejected as:
    pbs.twimg.com/media/HRSsRstbMAEzQxl.jpg?format=jpg&name=thumb
    -> reject-face-too-small: all 1 face(s) below 50px minimum

A face WAS detected. It was simply too small to trust, because `name=thumb`
is X's ~150px variant. X supports name=thumb|small|medium|large|orig, and
YouTube supports several thumbnail tiers with different availability.

If the larger variants resolve, this is pure lost recall we can recover.

Usage:  python scripts/probe_size_variants.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.face.detect import FaceDetector  # noqa: E402
from pipeline.face.quality import face_size_px  # noqa: E402

UA = {"User-Agent": "face-chain-verify/0.1 (research probe)"}

# Real URLs taken verbatim from the failing live runs.
X_BASE = "https://pbs.twimg.com/media/HRSsRstbMAEzQxl.jpg?format=jpg"
X_VARIANTS = ["thumb", "small", "medium", "large", "orig"]

# maxresdefault 404s for many videos; hqdefault effectively always exists.
YT_VIDEO_IDS = ["e2uRUMgozFQ", "nDj8MIyitUs", "V8UwSQAPDxs"]
YT_VARIANTS = ["maxresdefault", "sddefault", "hqdefault", "mqdefault"]


def probe(url: str, detector: FaceDetector) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=15)
    except requests.RequestException as e:
        return f"{type(e).__name__}"
    if not r.ok:
        return f"HTTP {r.status_code}"
    ct = (r.headers.get("content-type") or "").split(";")[0]
    if not ct.startswith("image/"):
        return f"{ct} (not an image)"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return "undecodable"
    faces = detector.detect(img)
    if not faces:
        return f"{img.shape[1]}x{img.shape[0]}  0 faces"
    px = face_size_px(faces[0])
    gate = "PASSES gate" if px >= 50 else "below 50px gate"
    return f"{img.shape[1]}x{img.shape[0]}  face {px:.0f}px  {gate}"


def main() -> int:
    det = FaceDetector()

    print("=" * 78)
    print("X / TWITTER  pbs.twimg.com  ?name= variant")
    print("=" * 78)
    for v in X_VARIANTS:
        print(f"  {v:<10} {probe(f'{X_BASE}&name={v}', det)}")

    print()
    print("=" * 78)
    print("YOUTUBE  i.ytimg.com thumbnail tiers")
    print("=" * 78)
    for vid in YT_VIDEO_IDS:
        print(f"  video {vid}:")
        for v in YT_VARIANTS:
            print(f"    {v:<16} {probe(f'https://i.ytimg.com/vi/{vid}/{v}.jpg', det)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
