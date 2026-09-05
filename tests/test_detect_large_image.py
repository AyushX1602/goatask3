"""Regression test for a real bug found during live smoke testing (5 Sep
2026): YuNet's confidence degrades on very large images. A 3356x2687
Wikimedia portrait scored 0.63-0.77 for real faces -- all below the 0.85
default threshold -- while the same faces scored 0.94 on a 0.4x downscale.

Fix: FaceDetector runs detection on a capped copy (max_detect_side) and
rescales results back to source coordinates, so alignment/embedding still
use full source resolution.
"""

from __future__ import annotations

from pathlib import Path

import cv2

from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder, cosine

FIX = Path(__file__).parent / "fixtures"
LARGE_PORTRAIT = FIX / "obama_portrait_wiki.jpg"


def test_large_image_face_detected_at_default_threshold():
    if not LARGE_PORTRAIT.exists():
        import pytest
        pytest.skip("large fixture not present in this environment")

    img = cv2.imread(str(LARGE_PORTRAIT))
    assert max(img.shape[:2]) > 1600, "fixture must actually exceed the detect cap to test the fix"

    det = FaceDetector()  # default score_threshold=0.85
    faces = det.detect(img)
    assert faces, "a real face in a large image must be detected at the default threshold"
    assert faces[0].det_score >= 0.85


def test_rescaled_bbox_stays_within_source_image_bounds():
    if not LARGE_PORTRAIT.exists():
        import pytest
        pytest.skip("large fixture not present in this environment")

    img = cv2.imread(str(LARGE_PORTRAIT))
    h, w = img.shape[:2]
    det = FaceDetector()
    faces = det.detect(img)
    assert faces
    x1, y1, x2, y2 = faces[0].bbox
    assert 0 <= x1 < x2 <= w
    assert 0 <= y1 < y2 <= h


def test_rescaled_landmarks_produce_a_correctly_aligned_embedding():
    """Proves the rescale is not just plausible-looking but numerically
    correct: the resulting embedding must still match a known-same-person
    fixture at the same cosine level as any other same-person pair."""
    if not LARGE_PORTRAIT.exists():
        import pytest
        pytest.skip("large fixture not present in this environment")

    det = FaceDetector()
    emb = FaceEmbedder()

    large_img = cv2.imread(str(LARGE_PORTRAIT))
    large_faces = det.detect(large_img)
    assert large_faces
    large_emb = emb.embed(align(large_img, large_faces[0].kps5))

    ref_img = cv2.imread(str(FIX / "obama1.jpg"))
    ref_faces = det.detect(ref_img)
    ref_emb = emb.embed(align(ref_img, ref_faces[0].kps5))

    score = cosine(large_emb, ref_emb)
    assert score > 0.5, f"same-person score too low after rescale: {score}"
