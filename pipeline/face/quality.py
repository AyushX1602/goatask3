"""Face quality gate. See docs/design.md 1.7 and docs/architecture.md 2a.

Every number here was derived by measurement (scripts/probe_accuracy.py),
not chosen by taste. Recorded so the gate is defensible if questioned.

Measured: cosine self-similarity of the SAME photo downscaled, versus the
full-resolution embedding —

    face px | 232   174   119   87    61    43    31     20
    cosine  | 1.000 0.972 0.971 0.970 0.962 0.954 0.868  0.767

Stable to ~43 px, then it falls off a cliff. Hence a 50 px floor: below it
the embedding is unreliable and will produce scores that look like real
similarity but are not.

Also measured, and deliberately NOT gated: blur. ArcFace held 0.904 under a
31-px gaussian kernel (Laplacian variance 1.8), so a blur gate would add
cost and reject usable faces for no accuracy gain.
"""

from __future__ import annotations

from pipeline.face.types import DetectedFace

# Overridable via MIN_FACE_PX. Derived above, not a guess (R-09 in spirit:
# thresholds come from measurement, and the measurement is cited).
DEFAULT_MIN_FACE_PX = 50

# Below this, the measured degradation is severe (0.868 at 31 px).
SEVERE_DEGRADATION_PX = 40


def face_size_px(face: DetectedFace) -> float:
    """Shorter side of the detection box, in source pixels."""
    x1, y1, x2, y2 = face.bbox
    return min(x2 - x1, y2 - y1)


def passes(face: DetectedFace, min_px: int = DEFAULT_MIN_FACE_PX) -> tuple[bool, str]:
    """Returns (ok, reason). reason is '' when ok.

    Applied in two places using this same function, so probe and candidate
    are held to an identical standard:
      - probe     -> a failure is a hard, actionable error
      - candidate -> a failure is `reject-face-too-small`, logged with size
    """
    px = face_size_px(face)
    if px < min_px:
        return False, f"face too small: {px:.0f}px < {min_px}px minimum"
    return True, ""


def probe_error_message(face: DetectedFace, min_px: int = DEFAULT_MIN_FACE_PX) -> str:
    """Actionable message for a probe that fails the gate. Users can fix
    this; a bare rejection is not helpful."""
    px = face_size_px(face)
    return (
        f"Detected face is only {px:.0f}px across, below the {min_px}px minimum. "
        "Move closer to the camera, or supply a higher-resolution image. "
        f"(Embedding accuracy degrades sharply below ~{SEVERE_DEGRADATION_PX}px.)"
    )
