"""Face detection via OpenCV YuNet. See docs/design.md 1.2.

YuNet returns one row per face with 15 values:
    x y w h  x_re y_re  x_le y_le  x_nt y_nt  x_rcm y_rcm  x_lcm y_lcm  score
(re/le = eyes, nt = nose tip, rcm/lcm = mouth corners)

Chosen over SCRFD specifically because OpenCV performs anchor decoding and
NMS internally (docs/architecture.md 5, D-03 in docs/memory.md) — no extra decode code.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from pipeline.config import MODELS_DIR
from pipeline.face.types import DetectedFace

MODEL_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"


class FaceDetector:
    """Thin wrapper around cv2.FaceDetectorYN.

    Must call setInputSize whenever the frame size changes, or detection
    silently returns zero faces (docs/memory.md "Gotchas" #2).
    """

    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        score_threshold: float = 0.85,
        nms_threshold: float = 0.3,
        top_k: int = 50,
        max_detect_side: int = 1600,
    ) -> None:
        if not model_path.exists():
            raise FileNotFoundError(
                f"{model_path} not found. Run: python scripts/fetch_models.py"
            )
        self._detector = cv2.FaceDetectorYN.create(
            str(model_path),
            "",
            (0, 0),  # placeholder, real size set per-call via setInputSize
            score_threshold,
            nms_threshold,
            top_k,
        )
        self._last_size: tuple[int, int] | None = None
        # Found empirically: on a 3356x2687 portrait, YuNet's own confidence
        # for a real face dropped to ~0.71-0.77 (below the 0.85 default),
        # while the SAME face on a 0.4x downscale scored 0.94. Detection is
        # run on a capped copy and results are scaled back to source
        # coordinates, so downstream alignment/embedding still uses full
        # source resolution — only detection confidence benefits.
        self.max_detect_side = max_detect_side

    def detect(self, bgr: np.ndarray) -> list[DetectedFace]:
        h, w = bgr.shape[:2]
        longest = max(h, w)
        scale = self.max_detect_side / longest if longest > self.max_detect_side else 1.0

        if scale < 1.0:
            detect_img = cv2.resize(
                bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
            )
        else:
            detect_img = bgr

        dh, dw = detect_img.shape[:2]
        if self._last_size != (dw, dh):
            self._detector.setInputSize((dw, dh))
            self._last_size = (dw, dh)

        _, faces = self._detector.detect(detect_img)
        if faces is None:
            return []

        inv_scale = 1.0 / scale
        results: list[DetectedFace] = []
        for row in faces:
            x, y, fw, fh = row[0:4] * inv_scale
            kps5 = (row[4:14].reshape(5, 2) * inv_scale).astype(np.float32)
            score = float(row[14])
            bbox = (float(x), float(y), float(x + fw), float(y + fh))
            results.append(DetectedFace(bbox=bbox, kps5=kps5, det_score=score))

        # Largest face first (docs/design.md 1.2) — the probe uses the largest
        # face; candidate images check all faces.
        results.sort(
            key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
            reverse=True,
        )
        return results
