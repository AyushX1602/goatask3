"""Face alignment: geometric landmark ordering + similarity transform to the
ArcFace 5-point template. See docs/design.md 1.3-1.4.

R-08: never pass an unaligned crop to embed(). ArcFace was trained on this
exact geometry; skipping alignment degrades accuracy with no error raised.
"""

from __future__ import annotations

import cv2
import numpy as np

# Standard ArcFace 5-point destination template for a 112x112 output.
# Order: left eye, right eye, nose tip, left mouth corner, right mouth corner.
ARCFACE_TEMPLATE_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def canonical_kps(raw: np.ndarray) -> np.ndarray:
    """Reorders 5 raw landmarks geometrically by x-position, never by name.

    docs/design.md 1.3: naming conventions for "left"/"right" differ between
    libraries (subject's left vs viewer's left). Trusting names silently
    mirrors the alignment and quietly wrecks accuracy. Order geometrically
    instead: eyes are the two smallest-y points, mouth corners the two
    largest-y points; within each pair, sort ascending by x.

    raw: (5,2) as [eye_a, eye_b, nose, mouth_a, mouth_b] in any convention,
         which matches YuNet's kps5 output order.
    """
    eyes = sorted([raw[0], raw[1]], key=lambda p: p[0])
    mouths = sorted([raw[3], raw[4]], key=lambda p: p[0])
    return np.array(
        [eyes[0], eyes[1], raw[2], mouths[0], mouths[1]], dtype=np.float32
    )


def align(bgr: np.ndarray, kps5: np.ndarray, size: int = 112) -> np.ndarray:
    """Similarity transform (rotation + uniform scale + translation) — NOT a
    full affine, which would introduce shear and degrade ArcFace.

    kps5 must already be geometrically ordered (see canonical_kps).
    """
    template = ARCFACE_TEMPLATE_112
    if size != 112:
        template = template * (size / 112.0)

    ordered = canonical_kps(kps5)
    matrix, _ = cv2.estimateAffinePartial2D(
        ordered, template, method=cv2.LMEDS
    )
    if matrix is None:
        raise ValueError("Could not estimate alignment transform from landmarks")

    return cv2.warpAffine(
        bgr,
        matrix,
        (size, size),
        flags=cv2.INTER_LINEAR,
        borderValue=(0, 0, 0),
    )
