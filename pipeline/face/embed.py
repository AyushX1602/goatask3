"""ArcFace embedding via onnxruntime. See docs/design.md 1.5.

Preprocessing matches InsightFace's own ONNX wrapper exactly:
  - input (1,3,112,112) NCHW float32
  - RGB (crops are BGR, so swap channels)
  - scale 1/127.5, mean 127.5 per channel -> range [-1, 1]

R-07: normalisation happens here, once. Every downstream consumer may
assume the vector is unit-length and use a plain dot product for cosine.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from pipeline.config import MODELS_DIR
from pipeline.face.types import Embedding

MODEL_PATH = MODELS_DIR / "w600k_r50.onnx"
MODEL_NAME = "w600k_r50"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FaceEmbedder:
    def __init__(self, model_path: Path = MODEL_PATH) -> None:
        if not model_path.exists():
            raise FileNotFoundError(
                f"{model_path} not found. Run: python scripts/fetch_models.py"
            )
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    @staticmethod
    def _preprocess(aligned_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB)
        blob = (rgb.astype(np.float32) - 127.5) / 127.5  # -> [-1, 1]
        blob = np.transpose(blob, (2, 0, 1))  # HWC -> CHW
        return np.expand_dims(blob, axis=0)  # -> NCHW

    def embed(self, aligned_bgr: np.ndarray) -> Embedding:
        """aligned_bgr must be a 112x112 crop already produced by
        pipeline.face.align.align (R-08) — never an unaligned crop."""
        blob = self._preprocess(aligned_bgr)
        raw = self._session.run(None, {self._input_name: blob})[0][0]

        norm = np.linalg.norm(raw)
        if norm == 0:
            raise ValueError("Degenerate embedding (zero norm) — check input crop")
        vec = (raw / norm).astype(np.float32)

        ok, png_bytes = cv2.imencode(".png", aligned_bgr)
        if not ok:
            raise ValueError("Failed to encode aligned crop for provenance hash")

        return Embedding(
            vec=vec,
            model=MODEL_NAME,
            aligned_png_sha256=_sha256_bytes(png_bytes.tobytes()),
        )

    def embed_batch(self, aligned_crops: list[np.ndarray]) -> list[Embedding]:
        """Batched path for the Bluesky crawl (docs/design.md 1.5, A-04).

        Falls back to a per-image loop if the model's batch axis turns out
        to be fixed rather than dynamic — this is verified empirically
        below, not assumed.
        """
        if not aligned_crops:
            return []
        try:
            blobs = np.concatenate(
                [self._preprocess(c) for c in aligned_crops], axis=0
            )
            raw = self._session.run(None, {self._input_name: blobs})[0]
        except Exception:
            return [self.embed(c) for c in aligned_crops]

        results = []
        for crop, vec_raw in zip(aligned_crops, raw):
            norm = np.linalg.norm(vec_raw)
            if norm == 0:
                continue
            vec = (vec_raw / norm).astype(np.float32)
            ok, png_bytes = cv2.imencode(".png", crop)
            sha = _sha256_bytes(png_bytes.tobytes()) if ok else ""
            results.append(Embedding(vec=vec, model=MODEL_NAME, aligned_png_sha256=sha))
        return results


def cosine(a: Embedding, b: Embedding) -> float:
    """Both vectors are L2-normalised (R-07), so cosine similarity is a
    plain dot product."""
    return float(np.dot(a.vec, b.vec))
