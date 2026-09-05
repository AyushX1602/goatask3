"""Research probe: measure what actually degrades match accuracy.

Motivated by a real observation: scanning a face returned a ranked list that
included obviously-wrong people (different sex/ethnicity). The verdict was
correctly NO_MATCH, but the ranked list looked like the engine was
"suggesting" those people.

This measures three quality levers so the gates can be set from data
instead of taste:
  A. face pixel size vs embedding stability  -> minimum face size gate
  B. blur vs embedding stability             -> blur gate
  C. score distribution for TRUE non-matches -> what "no match" looks like

Usage:  python scripts/probe_accuracy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.face.align import align  # noqa: E402
from pipeline.face.detect import FaceDetector  # noqa: E402
from pipeline.face.embed import FaceEmbedder  # noqa: E402

FIX = REPO_ROOT / "tests" / "fixtures"


def embed_largest(det, emb, img):
    faces = det.detect(img)
    if not faces:
        return None, None
    f = faces[0]
    return emb.embed(align(img, f.kps5)).vec, f


def face_px(face) -> float:
    x1, y1, x2, y2 = face.bbox
    return min(x2 - x1, y2 - y1)


def main() -> int:
    det = FaceDetector()
    emb = FaceEmbedder()

    ref_img = cv2.imread(str(FIX / "obama1.jpg"))
    ref_vec, ref_face = embed_largest(det, emb, ref_img)
    print(f"reference: obama1.jpg, face {face_px(ref_face):.0f}px\n")

    # ---- A. face size vs embedding stability -------------------------
    print("=" * 68)
    print("A. FACE SIZE vs SELF-SIMILARITY  (same photo, downscaled)")
    print("   -> tells us the minimum face size worth embedding")
    print("=" * 68)
    print(f"{'scale':>7} {'face px':>9} {'cos vs ref':>11}  {'detected?':>10}")
    for scale in [1.0, 0.75, 0.5, 0.35, 0.25, 0.18, 0.12, 0.08]:
        h, w = ref_img.shape[:2]
        small = cv2.resize(ref_img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        vec, face = embed_largest(det, emb, small)
        if vec is None:
            print(f"{scale:>7.2f} {'-':>9} {'-':>11}  {'NO FACE':>10}")
            continue
        print(f"{scale:>7.2f} {face_px(face):>9.0f} {float(np.dot(vec, ref_vec)):>11.4f}  {'yes':>10}")

    # ---- B. blur vs embedding stability ------------------------------
    print()
    print("=" * 68)
    print("B. BLUR vs SELF-SIMILARITY  (same photo, gaussian blur)")
    print("=" * 68)
    print(f"{'kernel':>7} {'lap var':>9} {'cos vs ref':>11}")
    for k in [1, 3, 7, 13, 21, 31]:
        blurred = ref_img if k == 1 else cv2.GaussianBlur(ref_img, (k, k), 0)
        vec, face = embed_largest(det, emb, blurred)
        gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(gray, cv2.CV_64F).var()
        if vec is None:
            print(f"{k:>7} {lap:>9.1f} {'NO FACE':>11}")
            continue
        print(f"{k:>7} {lap:>9.1f} {float(np.dot(vec, ref_vec)):>11.4f}")

    # ---- C. true non-match distribution ------------------------------
    print()
    print("=" * 68)
    print("C. TRUE NON-MATCH SCORES  (different people, all faces in t1.jpg)")
    print("   -> this is the band a 'no match' should live in")
    print("=" * 68)
    group = cv2.imread(str(FIX / "t1.jpg"))
    faces = det.detect(group)
    scores = []
    for i, f in enumerate(faces):
        v = emb.embed(align(group, f.kps5)).vec
        s = float(np.dot(v, ref_vec))
        scores.append(s)
        print(f"  face {i}: {face_px(f):>4.0f}px  det {f.det_score:.3f}  cos {s:>8.4f}")

    others = [
        embed_largest(det, emb, cv2.imread(str(FIX / n)))[0]
        for n in ["lin_manuel_miranda.png", "alex_lacamoire.png", "tom_hanks.png"]
    ]
    for n, v in zip(["miranda", "lacamoire", "tom_hanks"], others):
        if v is not None:
            s = float(np.dot(v, ref_vec))
            scores.append(s)
            print(f"  {n:<12} cos {s:>8.4f}")

    arr = np.array(scores)
    print(f"\n  non-match stats: n={len(arr)} max={arr.max():.4f} "
          f"mean={arr.mean():.4f} std={arr.std():.4f}")
    print(f"  mean + 4*std = {arr.mean() + 4 * arr.std():.4f}   <- a defensible floor")
    print(f"  same-person reference (obama1 vs obama2): ", end="")
    v2, _ = embed_largest(det, emb, cv2.imread(str(FIX / "obama2.jpg")))
    print(f"{float(np.dot(v2, ref_vec)):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
