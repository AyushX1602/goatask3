"""Shared types for the face core. See design.md 1.1.

R-07: Embedding.vec is ALWAYS L2-normalised. Never construct one by hand
outside of embed.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DetectedFace:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 in source pixels
    kps5: np.ndarray  # (5,2) float32, geometrically ordered — see align.canonical_kps
    det_score: float


@dataclass(frozen=True)
class Embedding:
    vec: np.ndarray  # (512,) float32, L2-normalised (R-07)
    model: str  # e.g. "w600k_r50"
    aligned_png_sha256: str  # provenance: which crop produced this


@dataclass(frozen=True)
class LivenessResult:
    passed: bool
    score: float
    label: str  # "live" | "spoof" | "unknown"
