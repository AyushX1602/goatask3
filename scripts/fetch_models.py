"""Downloads the three ONNX models the pipeline needs, verifies each by
sha256, and writes them to models/ (gitignored — rules.md "Models" section).

Idempotent: re-running skips any file that already matches its expected hash.
Run this before recording (phases.md Phase 13) so nothing downloads on camera.

Usage:
    python scripts/fetch_models.py
    python scripts/fetch_models.py --verify-only   # check without downloading
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "models"

USER_AGENT = "face-chain-verify-model-fetcher/0.1 (+https://github.com/)"


@dataclass(frozen=True)
class ModelSpec:
    filename: str
    url: str
    sha256: str
    size_bytes: int
    purpose: str


MODELS: list[ModelSpec] = [
    ModelSpec(
        filename="face_detection_yunet_2023mar.onnx",
        url=(
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
            "main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
        ),
        sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        size_bytes=232589,
        purpose="face detection (bbox + 5 landmarks) — pipeline/face/detect.py",
    ),
    ModelSpec(
        filename="w600k_r50.onnx",
        url="https://huggingface.co/deepghs/insightface/resolve/main/buffalo_l/w600k_r50.onnx",
        sha256="4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
        size_bytes=174383860,
        purpose="ArcFace 512-d embedding — pipeline/face/embed.py",
    ),
    ModelSpec(
        filename="anti_spoof_minifasnet_v2.onnx",
        url="https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx",
        sha256="b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907",
        size_bytes=1743581,
        purpose="liveness / anti-spoof — pipeline/face/liveness.py",
    ),
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(spec: ModelSpec, dest: Path) -> None:
    print(f"  downloading {spec.filename} ({spec.size_bytes / 1e6:.1f} MB) ...")
    req = Request(spec.url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)


def ensure_model(spec: ModelSpec, verify_only: bool) -> bool:
    dest = MODELS_DIR / spec.filename
    if dest.exists():
        actual = sha256_of(dest)
        if actual == spec.sha256:
            print(f"[ok]   {spec.filename} — checksum verified, skipping download")
            return True
        print(f"[warn] {spec.filename} — checksum mismatch, re-downloading")
        dest.unlink()

    if verify_only:
        print(f"[miss] {spec.filename} — not present ({spec.purpose})")
        return False

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    download(spec, dest)
    actual = sha256_of(dest)
    if actual != spec.sha256:
        print(f"[FAIL] {spec.filename} — checksum mismatch after download")
        print(f"       expected {spec.sha256}")
        print(f"       actual   {actual}")
        dest.unlink(missing_ok=True)
        return False

    print(f"[ok]   {spec.filename} — downloaded and verified")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Check presence/checksums without downloading anything",
    )
    args = parser.parse_args()

    print(f"Model directory: {MODELS_DIR}")
    ok = True
    for spec in MODELS:
        if not ensure_model(spec, args.verify_only):
            ok = False

    if not ok:
        print("\nOne or more models are missing or failed verification.")
        return 1

    print("\nAll models present and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
