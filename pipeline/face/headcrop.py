"""Head-crop search query representation. See design.md 1.7, phases.md T2.5, rules.md R-27.

Creates an asymmetrically-grown, square-padded, soft-masked head crop composited
on neutral mid-grey (RGB/BGR 128, 128, 128).

Used ONLY as an optional search query representation to prevent reverse-image
search engines from locking onto clothing/garments rather than the face.

Rule R-08: This output NEVER touches the face embedding stage. The 112x112 aligned
crop remains the ONLY input to ArcFace embed().
"""

from __future__ import annotations

import cv2
import numpy as np

from pipeline.face.types import DetectedFace


def create_head_crop(
    img: np.ndarray,
    face: DetectedFace,
    target_size: int = 512,
    bg_color: int = 128,
) -> np.ndarray:
    """Generates an asymmetrically grown head crop composited on mid-grey.

    Asymmetry rationale:
      - 55% upward: includes forehead and hair (essential identity features)
      - 30% sides: includes ears and jawline
      - 18% downward: stops near chin/neck, deliberately excluding torso/clothing

    Composited on mid-grey (128) rather than white (255) to prevent reverse-image
    engines from biasing toward e-commerce/stock catalogue imagery.
    """
    img_h, img_w = img.shape[:2]
    x1, y1, x2, y2 = face.bbox
    w = x2 - x1
    h = y2 - y1

    # Asymmetric expansion
    exp_y1 = y1 - 0.55 * h
    exp_y2 = y2 + 0.18 * h
    exp_x1 = x1 - 0.30 * w
    exp_x2 = x2 + 0.30 * w

    # Make square centered on the expanded bbox
    cx = (exp_x1 + exp_x2) / 2.0
    cy = (exp_y1 + exp_y2) / 2.0
    box_size = max(exp_x2 - exp_x1, exp_y2 - exp_y1)
    half = box_size / 2.0

    sq_x1 = int(round(cx - half))
    sq_y1 = int(round(cy - half))
    sq_x2 = int(round(cx + half))
    sq_y2 = int(round(cy + half))

    sq_w = sq_x2 - sq_x1
    sq_h = sq_y2 - sq_y1
    if sq_w <= 0 or sq_h <= 0:
        return np.full((target_size, target_size, 3), bg_color, dtype=np.uint8)

    # Intersection with source image
    src_x1 = max(0, sq_x1)
    src_y1 = max(0, sq_y1)
    src_x2 = min(img_w, sq_x2)
    src_y2 = min(img_h, sq_y2)

    # Canvas at square size
    canvas = np.full((sq_h, sq_w, 3), bg_color, dtype=np.uint8)

    if src_x2 > src_x1 and src_y2 > src_y1:
        dst_x1 = src_x1 - sq_x1
        dst_y1 = src_y1 - sq_y1
        dst_x2 = dst_x1 + (src_x2 - src_x1)
        dst_y2 = dst_y1 + (src_y2 - src_y1)
        canvas[dst_y1:dst_y2, dst_x1:dst_x2] = img[src_y1:src_y2, src_x1:src_x2]

    # Resize to target_size
    resized = cv2.resize(canvas, (target_size, target_size), interpolation=cv2.INTER_LINEAR)

    # Soft elliptical mask
    mask = np.zeros((target_size, target_size), dtype=np.float32)
    center = (target_size // 2, target_size // 2)
    axes = (int(target_size * 0.44), int(target_size * 0.46))
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, -1)

    # Feather the edge with Gaussian blur
    mask = cv2.GaussianBlur(mask, (31, 31), 11)
    mask = np.clip(mask, 0.0, 1.0)[:, :, np.newaxis]

    # Alpha composite onto neutral background
    bg = np.full((target_size, target_size, 3), bg_color, dtype=np.float32)
    composite = resized.astype(np.float32) * mask + bg * (1.0 - mask)

    return np.clip(composite, 0, 255).astype(np.uint8)
