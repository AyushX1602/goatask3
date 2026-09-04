"""Candidate dedupe by perceptual hash. See design.md 2.4.

The same image is often served from many CDNs at many sizes. Deduplicate
before spending face-detection inference on it.
"""

from __future__ import annotations

import io

import imagehash
from PIL import Image

from pipeline.search.base import Candidate


def _phash(image_bytes: bytes) -> imagehash.ImageHash | None:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return imagehash.phash(img)
    except Exception:
        return None


def dedupe(candidates: list[Candidate], images: dict[str, bytes], max_hamming: int = 6) -> list[Candidate]:
    """images maps candidate.image_url -> fetched bytes (or is missing the
    key if the fetch failed, in which case the candidate is kept as-is so
    a later stage can log reject-fetch-failed)."""
    seen_urls: set[str] = set()
    kept: list[Candidate] = []
    kept_hashes: list[imagehash.ImageHash] = []

    for cand in candidates:
        if cand.image_url in seen_urls:
            continue

        data = images.get(cand.image_url)
        if data is None:
            kept.append(cand)
            seen_urls.add(cand.image_url)
            continue

        h = _phash(data)
        if h is None:
            kept.append(cand)
            seen_urls.add(cand.image_url)
            continue

        if any((h - kh) <= max_hamming for kh in kept_hashes):
            continue

        kept.append(cand)
        kept_hashes.append(h)
        seen_urls.add(cand.image_url)

    return kept
