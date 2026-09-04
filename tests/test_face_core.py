"""Phase 1 exit criteria (phases.md). All four must be observed:

1. Two different photos of the same person score > 0.5
2. Two different people score < 0.3
3. A horizontally flipped input scores > 0.9 against its unflipped self
   (proves landmark ordering, design.md 1.3)
4. Every embedding satisfies ||v|| ~= 1.0

Fixtures under tests/fixtures/ are sourced from deepinsight/insightface and
ageitgey/face_recognition sample data (both permissively licensed for this
kind of use) — see memory.md for provenance.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from pipeline.face.align import align, canonical_kps
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder, cosine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def detector() -> FaceDetector:
    return FaceDetector()


@pytest.fixture(scope="module")
def embedder() -> FaceEmbedder:
    return FaceEmbedder()


def _embed_largest_face(detector: FaceDetector, embedder: FaceEmbedder, path: Path):
    img = cv2.imread(str(path))
    assert img is not None, f"could not read {path}"
    faces = detector.detect(img)
    assert faces, f"no face detected in {path}"
    crop = align(img, faces[0].kps5)
    return embedder.embed(crop)


def test_norm_is_unit_length(detector, embedder):
    """Exit criterion 4: every embedding satisfies ||v|| ~= 1.0 (R-07)."""
    emb = _embed_largest_face(detector, embedder, FIXTURES / "obama1.jpg")
    assert np.isclose(np.linalg.norm(emb.vec), 1.0, atol=1e-4)


def test_same_person_scores_high(detector, embedder):
    """Exit criterion 1: two different photos of the same person score > 0.5.

    obama.jpg and obama2.jpg are different photos of Barack Obama from the
    face_recognition sample set.
    """
    a = _embed_largest_face(detector, embedder, FIXTURES / "obama1.jpg")
    b = _embed_largest_face(detector, embedder, FIXTURES / "obama2.jpg")
    score = cosine(a, b)
    assert score > 0.5, f"same-person score too low: {score}"


def test_different_people_score_low(detector, embedder):
    """Exit criterion 2: two different people score < 0.3.

    lin-manuel-miranda.png and alex-lacamoire.png are two different named
    individuals from the face_recognition sample set.
    """
    a = _embed_largest_face(detector, embedder, FIXTURES / "lin_manuel_miranda.png")
    b = _embed_largest_face(detector, embedder, FIXTURES / "alex_lacamoire.png")
    score = cosine(a, b)
    assert score < 0.3, f"different-person score too high: {score}"


def test_flip_invariance(detector, embedder):
    """Exit criterion 3: a horizontally flipped input scores > 0.9 against
    its unflipped self.

    design.md 1.3: this is the guard against the landmark-ordering bug.
    canonical_kps must reorder geometrically (by x-position), not trust
    library-provided left/right names, or a flipped image silently produces
    a mirrored (wrong) alignment with degraded accuracy and no error.
    """
    img = cv2.imread(str(FIXTURES / "obama1.jpg"))
    faces = detector.detect(img)
    assert faces

    face = faces[0]
    crop_original = align(img, face.kps5)
    emb_original = embedder.embed(crop_original)

    flipped = cv2.flip(img, 1)  # horizontal flip
    w = img.shape[1]
    kps5_flipped = face.kps5.copy()
    kps5_flipped[:, 0] = w - kps5_flipped[:, 0]

    crop_flipped = align(flipped, kps5_flipped)
    emb_flipped = embedder.embed(crop_flipped)

    score = cosine(emb_original, emb_flipped)
    assert score > 0.9, f"flip invariance failed: {score} (landmark ordering bug?)"


def test_canonical_kps_orders_by_x_not_by_name():
    """Unit-level guard: canonical_kps must sort eyes/mouth corners by
    x-position regardless of input order (design.md 1.3)."""
    # Deliberately feed points with eyes swapped (right eye first).
    raw = np.array(
        [
            [80.0, 50.0],  # "first" point is on the right
            [40.0, 50.0],  # "second" point is on the left
            [60.0, 70.0],  # nose
            [70.0, 90.0],  # "first" mouth point is on the right
            [45.0, 90.0],  # "second" mouth point is on the left
        ],
        dtype=np.float32,
    )
    ordered = canonical_kps(raw)
    assert ordered[0][0] < ordered[1][0], "left eye must precede right eye by x"
    assert ordered[3][0] < ordered[4][0], "left mouth corner must precede right by x"


def test_multi_face_image_detects_several(detector):
    """t1.jpg is a group photo from the insightface sample set — should
    yield multiple faces sorted largest-first (design.md 1.2)."""
    img = cv2.imread(str(FIXTURES / "t1.jpg"))
    faces = detector.detect(img)
    assert len(faces) >= 2

    areas = [(f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]) for f in faces]
    assert areas == sorted(areas, reverse=True), "faces must be sorted largest-first"


def test_empty_detection_is_valid_not_an_error(detector):
    """design.md 1.2: an image with no face is a valid empty result."""
    blank = np.zeros((200, 200, 3), dtype=np.uint8)
    faces = detector.detect(blank)
    assert faces == []
