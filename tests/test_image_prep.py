"""Tests for the search-query image preparation.

Guards the distinction that caused a live failure (5 Sep 2026): the search
provider must receive the ORIGINAL photograph, not the 112x112 aligned
ArcFace crop. See pipeline/search/image_prep.py for the measured evidence.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from pipeline.search.image_prep import (
    JPEG_QUALITY,
    MAX_SEARCH_SIDE,
    prepare_search_image,
)

FIX = Path(__file__).parent / "fixtures"


def _decode(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def test_small_image_is_not_upscaled():
    img = np.zeros((400, 300, 3), dtype=np.uint8)
    out = _decode(prepare_search_image(img))
    assert out.shape[:2] == (400, 300)


def test_large_image_is_downscaled_to_the_cap():
    img = np.zeros((4000, 3000, 3), dtype=np.uint8)
    out = _decode(prepare_search_image(img))
    assert max(out.shape[:2]) == MAX_SEARCH_SIDE
    # aspect ratio preserved
    assert abs((out.shape[1] / out.shape[0]) - (3000 / 4000)) < 0.01


def test_output_is_decodable_jpeg():
    img = np.random.default_rng(0).integers(0, 255, (500, 500, 3), dtype=np.uint8)
    data = prepare_search_image(img)
    assert data[:2] == b"\xff\xd8", "must be JPEG (SOI marker)"
    assert _decode(data) is not None


def test_real_portrait_retains_far_more_detail_than_an_aligned_crop():
    """The whole point: the search query must not be a 112px crop."""
    src = FIX / "obama_portrait_wiki.jpg"
    if not src.exists():
        import pytest

        pytest.skip("large fixture not present")

    img = cv2.imread(str(src))
    out = _decode(prepare_search_image(img))

    assert max(out.shape[:2]) == MAX_SEARCH_SIDE
    # Dramatically more pixels than the 112x112 crop the pipeline used to send
    assert out.shape[0] * out.shape[1] > 100 * (112 * 112)


def test_stays_well_under_the_gcv_request_budget():
    """GCV allows 20 MB per request and base64 inflates by ~33%, so the
    encoded payload must leave comfortable headroom."""
    img = np.random.default_rng(1).integers(0, 255, (4000, 4000, 3), dtype=np.uint8)
    data = prepare_search_image(img)
    base64_size = len(data) * 4 / 3
    assert base64_size < 10 * 1024 * 1024, f"payload too large: {base64_size/1e6:.1f} MB"


def test_quality_constant_is_sane():
    assert 80 <= JPEG_QUALITY <= 95
