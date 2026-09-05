"""End-to-end pipeline run against a local image, printing the full
candidate table exactly as the audit log records it.

This is the CLI equivalent of clicking through the web UI, so a run can be
inspected without a browser.

Usage:  python scripts/run_probe.py --image srk.jpg
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import cv2

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import get_config, load_match_policy  # noqa: E402
from pipeline.face.align import align  # noqa: E402
from pipeline.face.detect import FaceDetector  # noqa: E402
from pipeline.face.embed import FaceEmbedder  # noqa: E402
from pipeline.face.quality import face_size_px, passes as quality_passes  # noqa: E402
from pipeline.search.image_prep import prepare_search_image  # noqa: E402
from pipeline.search.web_detect import WebDetectProvider  # noqa: E402
from pipeline.verify.allowlist import platform_name  # noqa: E402
from pipeline.verify.pipeline_run import run_pipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    args = ap.parse_args()

    path = Path(args.image)
    img = cv2.imread(str(path))
    if img is None:
        print(f"could not read {path}")
        return 1

    cfg = get_config()
    policy = load_match_policy()
    det, emb = FaceDetector(), FaceEmbedder()

    print(f"probe:      {path.name}")
    print(f"dimensions: {img.shape[1]}x{img.shape[0]}")

    faces = det.detect(img)
    if not faces:
        print("RESULT: no face detected -> cannot search")
        return 0

    f0 = faces[0]
    ok_q, why = quality_passes(f0, cfg.min_face_px)
    print(f"face:       {face_size_px(f0):.0f}px, det_score {f0.det_score:.3f}, quality {'OK' if ok_q else why}")
    if not ok_q:
        print("RESULT: probe face failed the quality gate")
        return 0

    probe_vec = emb.embed(align(img, f0.kps5)).vec
    search_bytes = prepare_search_image(img)
    print(f"search query sent: {len(search_bytes)/1024:.0f} KB JPEG (original photo, not the 112px crop)")

    provider = WebDetectProvider()
    print(f"backend:    {provider.backend} (available={provider.available()})")
    if not provider.available():
        print("RESULT: no web-detection key configured")
        return 0

    print(f"threshold:  {policy.threshold} | margin: {policy.margin}"
          f"{'  (PLACEHOLDER, not calibrated)' if policy.is_placeholder else ''}")
    print("\nsearching...\n")

    result = run_pipeline(search_bytes, probe_vec, [provider], det, emb, policy=policy)

    print(f"identity signals from the API: {result.identity_signals[:5] or '(NONE - subject not recognised)'}")
    print(f"candidates examined: {len(result.match.all_scored)}")
    print(f"decision breakdown:  {dict(Counter(r.decision for r in result.match.all_scored))}")
    print(f"\nVERDICT: {result.match.verdict}\n")

    rows = sorted(
        result.match.all_scored,
        key=lambda r: (r.decision not in ("ACCEPT", "corroborating"), -(r.score or -9)),
    )
    print(f"{'score':>8}  {'platform':<12} {'decision':<26} page")
    print("-" * 108)
    for r in rows[:28]:
        s = f"{r.score:.4f}" if r.score is not None else "—"
        plat = platform_name(r.candidate.page_url) or platform_name(r.candidate.image_url) or "-"
        print(f"{s:>8}  {plat:<12} {r.decision:<26} {r.candidate.page_url[:56]}")

    if result.match.best:
        print(f"\nMATCHED POST: {result.match.best.candidate.page_url}")
        print(f"REASON:       {result.match.best.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
