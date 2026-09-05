"""Tests for headcrop.py (T2.5 / R-27 / R-08)."""

from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np

from pipeline.face.detect import FaceDetector
from pipeline.face.headcrop import create_head_crop
from pipeline.face.types import DetectedFace


def test_head_crop_dimensions_and_channels():
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    face = DetectedFace(
        bbox=(100.0, 100.0, 200.0, 200.0),
        kps5=np.zeros((5, 2), dtype=np.float32),
        det_score=0.99,
    )
    res = create_head_crop(img, face, target_size=512, bg_color=128)
    assert res.shape == (512, 512, 3)
    assert res.dtype == np.uint8

    # Corners should be neutral mid-grey (128)
    assert np.allclose(res[0, 0], [128, 128, 128], atol=2)
    assert np.allclose(res[511, 511], [128, 128, 128], atol=2)


def test_head_crop_on_real_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "obama1.jpg"
    img = cv2.imread(str(fixture_path))
    assert img is not None

    detector = FaceDetector()
    faces = detector.detect(img)
    assert len(faces) > 0

    crop = create_head_crop(img, faces[0], target_size=512)
    assert crop.shape == (512, 512, 3)

    # The center of the crop should have content from the face (not pure grey)
    center_color = crop[256, 256]
    assert not np.allclose(center_color, [128, 128, 128], atol=5)


def test_head_crop_isolated_from_arcface_alignment():
    """R-08: head crop is 512x512 search representation, NOT 112x112 ArcFace crop."""
    face = DetectedFace(
        bbox=(50.0, 50.0, 150.0, 150.0),
        kps5=np.zeros((5, 2), dtype=np.float32),
        det_score=0.95,
    )
    img = np.ones((200, 200, 3), dtype=np.uint8) * 200
    crop = create_head_crop(img, face, target_size=512)
    assert crop.shape == (512, 512, 3)
    assert crop.shape != (112, 112, 3)


def test_prepare_search_image_with_head_crop():
    from pipeline.search.image_prep import prepare_search_image
    face = DetectedFace(
        bbox=(50.0, 50.0, 150.0, 150.0),
        kps5=np.zeros((5, 2), dtype=np.float32),
        det_score=0.95,
    )
    img = np.ones((200, 200, 3), dtype=np.uint8) * 200
    encoded = prepare_search_image(img, face=face, use_head_crop=True, head_crop_size=512)
    decoded = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert decoded.shape == (512, 512, 3)

