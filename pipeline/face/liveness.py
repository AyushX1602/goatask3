"""Liveness / anti-spoof check. See docs/design.md 1.6.

Applies to webcam input only (S2 in docs/prd.md: a printed photo held to the
camera must be rejected). Candidate images downloaded from the web are
already photographs of photographs by definition, so liveness is skipped
there — this asymmetry is documented in the README, not hidden.

Model: MiniFASNetV2 ONNX port (yakhyo/face-anti-spoofing), 3-class softmax
over [spoof, live, ...] — class index 1 is "live" per the model's own
convention (verified against its reference inference script).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from pipeline.config import MODELS_DIR
from pipeline.face.types import DetectedFace, LivenessResult

MODEL_PATH = MODELS_DIR / "anti_spoof_minifasnet_v2.onnx"
INPUT_SIZE = 80  # MiniFASNetV2 default input resolution
LIVE_CLASS_INDEX = 1


class LivenessChecker:
    def __init__(self, model_path: Path = MODEL_PATH) -> None:
        self._available = model_path.exists()
        if self._available:
            self._session = ort.InferenceSession(
                str(model_path), providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
        else:
            self._session = None

    @staticmethod
    def _expand_box(bbox: tuple[float, float, float, float], frame_shape, scale: float = 2.7):
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        w, h = (x2 - x1) * scale, (y2 - y1) * scale
        h_img, w_img = frame_shape[:2]
        nx1 = max(0, int(cx - w / 2))
        ny1 = max(0, int(cy - h / 2))
        nx2 = min(w_img, int(cx + w / 2))
        ny2 = min(h_img, int(cy + h / 2))
        return nx1, ny1, nx2, ny2

    def check(self, bgr: np.ndarray, face: DetectedFace, threshold: float = 0.7) -> LivenessResult:
        if not self._available:
            return LivenessResult(passed=True, score=0.0, label="unknown")

        x1, y1, x2, y2 = self._expand_box(face.bbox, bgr.shape)
        crop = bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return LivenessResult(passed=True, score=0.0, label="unknown")

        resized = cv2.resize(crop, (INPUT_SIZE, INPUT_SIZE))
        blob = resized.astype(np.float32).transpose(2, 0, 1)[None, ...]  # NCHW, BGR, no normalisation

        logits = self._session.run(None, {self._input_name: blob})[0][0]
        exp = np.exp(logits - np.max(logits))
        probs = exp / exp.sum()
        live_score = float(probs[LIVE_CLASS_INDEX])

        passed = live_score >= threshold
        label = "live" if passed else "spoof"
        return LivenessResult(passed=passed, score=live_score, label=label)
