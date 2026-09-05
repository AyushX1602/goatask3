"""Prepares the image that gets SENT TO a web-detection provider.

Critical distinction, learned from a live failure (5 Sep 2026):

  * the 112x112 ALIGNED CROP is for OUR ArcFace embedding. It is
    tightly cropped, low-resolution, and geometrically warped onto the
    ArcFace template.
  * the SEARCH QUERY sent to Google must be the ORIGINAL photograph,
    because reverse image search matches against an index of full
    photographs -- background, clothing, framing and resolution all
    contribute to a match.

The pipeline originally sent the aligned crop as the search query. Measured
consequences on the same Obama portrait (scripts/probe_crop_vs_original.py):

    query               webEntities  fullMatchingImages  parsed candidates
    aligned crop 112px       9                0                 85
    original image          11               40                130

`fullMatchingImages` is the "this exact image exists at these URLs" signal
and it was empty for the crop -- the crop only ever produced *partial*
matches. Worse, on a Shah Rukh Khan probe the crop produced ZERO
webEntities and 20 unrelated people (max score 0.2644), i.e. the API did
not recognise the subject at all.

Privacy note (R-01 still holds): no embedding is transmitted. This sends
the user's own input photograph to the search provider, which is inherent
to "search the web for this face" and is disclosed in the README.
"""

from __future__ import annotations

import cv2
import numpy as np

# Google Cloud Vision accepts up to 20 MB per request and base64 inflates
# by ~33%, so a hard cap well under that keeps requests fast and safe.
# 2048px on the long edge preserves far more matchable detail than the
# 112px crop while keeping a typical JPEG in the hundreds of KB.
MAX_SEARCH_SIDE = 2048
JPEG_QUALITY = 90


def prepare_search_image(
    bgr: np.ndarray,
    face: object | None = None,
    use_head_crop: bool = False,
    head_crop_size: int = 512,
) -> bytes:
    """Returns JPEG bytes of the image to send to the search provider.

    By default, returns JPEG bytes of the original image, downscaled only if it
    exceeds MAX_SEARCH_SIDE on its longest edge. Downscaling is intentionally
    mild: unlike face embedding, reverse image search benefits from resolution
    and context.

    If use_head_crop is True and face is provided, creates a normalized
    head-crop query representation (T2.5 / R-27) via pipeline.face.headcrop,
    isolating the head/hair while masking out background/garments that may bias
    the search engine away from the person.
    """
    if use_head_crop and face is not None:
        from pipeline.face.headcrop import create_head_crop
        bgr = create_head_crop(bgr, face, target_size=head_crop_size)

    h, w = bgr.shape[:2]
    longest = max(h, w)

    if longest > MAX_SEARCH_SIDE:
        scale = MAX_SEARCH_SIDE / longest
        bgr = cv2.resize(
            bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
        )

    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise ValueError("could not JPEG-encode the search image")
    return buf.tobytes()
