"""Probe query representation comparison (T2.5 / R-27).

Compares original photograph vs head-crop query representation across
test fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from rich.console import Console
from rich.table import Table

from pipeline.face.detect import FaceDetector
from pipeline.face.headcrop import create_head_crop
from pipeline.search.image_prep import prepare_search_image

console = Console()


def probe_fixtures() -> None:
    fixtures_dir = Path(__file__).parent.parent / "tests" / "fixtures"
    detector = FaceDetector()

    table = Table(title="Query Representation Comparison (R-27)")
    table.add_column("Fixture", style="cyan")
    table.add_column("Orig Size", justify="right")
    table.add_column("Face BBox", justify="right")
    table.add_column("Orig Query Size", justify="right")
    table.add_column("Head-Crop Size", justify="right")
    table.add_column("Face Area % (Orig)", justify="right")
    table.add_column("Face Area % (Crop)", justify="right")

    fixture_files = sorted(fixtures_dir.glob("*.jpg")) + sorted(fixtures_dir.glob("*.png"))

    for f in fixture_files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        h, w = img.shape[:2]
        faces = detector.detect(img)
        if not faces:
            continue
        face = faces[0]
        x1, y1, x2, y2 = face.bbox
        fw, fh = x2 - x1, y2 - y1
        face_area = fw * fh
        orig_area = w * h
        face_pct_orig = (face_area / orig_area) * 100.0

        orig_query_bytes = prepare_search_image(img)
        head_crop = create_head_crop(img, face, target_size=512)
        head_crop_bytes = cv2.imencode(".jpg", head_crop)[1].tobytes()

        crop_face_pct = (face_area / (512 * 512)) * 100.0 if fw > 0 else 0

        table.add_row(
            f.name,
            f"{w}x{h}",
            f"{int(fw)}x{int(fh)}",
            f"{len(orig_query_bytes) // 1024} KB",
            f"{head_crop.shape[1]}x{head_crop.shape[0]} ({len(head_crop_bytes) // 1024} KB)",
            f"{face_pct_orig:.1f}%",
            f"{min(100.0, crop_face_pct):.1f}%",
        )

    console.print(table)


if __name__ == "__main__":
    probe_fixtures()
