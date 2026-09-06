"""Artifact re-verification and disk integrity checking. See docs/design.md 5.4, 5.6.

Rule R-25: Re-verification recomputes every digest from the source artifact.
A stored digest is never reused as its own proof.

Rebuilding the bundle from disk artifacts ensures that swapping or mutating
any artifact on disk (e.g. match_image.jpg) causes the recomputed bundle hash
to diverge from what was anchored on-chain.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image


@dataclass(frozen=True)
class ArtifactCheck:
    path: str
    expected_sha256: str
    actual_sha256: str
    match: bool
    expected_phash: str | None = None
    actual_phash: str | None = None
    phash_match: bool | None = None
    detail: str = ""


def extract_artifact_manifest(bundle_data: dict) -> list[dict]:
    """Extracts the list of artifact references committed by this bundle.
    Schema v3 records an explicit 'artifacts' list.
    Schema v2 / v1 falls back to 'match_image.jpg' if match.image_sha256 is present,
    ensuring backward compatibility with all historical sample runs.
    """
    if "artifacts" in bundle_data and isinstance(bundle_data["artifacts"], list):
        return [dict(a) for a in bundle_data["artifacts"]]

    # Schema v2 fallback: match.image_sha256 pins match_image.jpg
    match_sec = bundle_data.get("match", {})
    img_sha = match_sec.get("image_sha256")
    if img_sha:
        return [
            {
                "path": "match_image.jpg",
                "sha256": img_sha,
                "phash": match_sec.get("image_phash"),
            }
        ]
    return []


def rebuild_from_artifacts(
    run_dir: Path, bundle_filename: str = "evidence.json"
) -> tuple[dict, list[ArtifactCheck], str | None]:
    """Loads the stored bundle from run_dir, inspects every referenced artifact
    on disk, recomputes each SHA-256 and perceptual hash from source bytes,
    and OVERWRITES the recorded digests in the returned bundle copy.

    Returns:
        (rebuilt_data, checks, failure_state)
        failure_state: None if all artifacts match, 'ARTIFACT_MISSING' if any file
        is absent, or 'ARTIFACT_MISMATCH' if any digest differs.
    """
    bundle_path = run_dir / bundle_filename
    if not bundle_path.exists():
        return {}, [], "FILE_NOT_FOUND"

    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    rebuilt = copy.deepcopy(data)

    manifest = extract_artifact_manifest(data)
    checks: list[ArtifactCheck] = []
    failure_state: str | None = None

    new_artifacts_list = []

    for item in manifest:
        rel_path = item.get("path", "")
        exp_sha = item.get("sha256", "")
        exp_phash = item.get("phash")

        file_path = run_dir / rel_path
        if not file_path.exists():
            checks.append(
                ArtifactCheck(
                    path=rel_path,
                    expected_sha256=exp_sha,
                    actual_sha256="",
                    match=False,
                    expected_phash=exp_phash,
                    actual_phash=None,
                    phash_match=False if exp_phash else None,
                    detail=f"artifact not found on disk: {rel_path}",
                )
            )
            if failure_state is None:
                failure_state = "ARTIFACT_MISSING"
            continue

        raw_bytes = file_path.read_bytes()
        actual_sha = hashlib.sha256(raw_bytes).hexdigest()
        sha_match = actual_sha.lower() == exp_sha.lower()

        actual_phash: str | None = None
        phash_match: bool | None = None
        if exp_phash:
            try:
                img = Image.open(io.BytesIO(raw_bytes))
                actual_phash = str(imagehash.phash(img))
                # Phash exact match
                phash_match = actual_phash.lower() == exp_phash.lower()
            except Exception:
                actual_phash = "decode_error"
                phash_match = False

        is_match = sha_match and (phash_match is not False)
        detail = "OK" if is_match else "digest mismatch"
        if not sha_match:
            detail = f"sha256 mismatch (expected {exp_sha[:12]}..., got {actual_sha[:12]}...)"
            if failure_state is None:
                failure_state = "ARTIFACT_MISMATCH"

        checks.append(
            ArtifactCheck(
                path=rel_path,
                expected_sha256=exp_sha,
                actual_sha256=actual_sha,
                match=is_match,
                expected_phash=exp_phash,
                actual_phash=actual_phash,
                phash_match=phash_match,
                detail=detail,
            )
        )

        # R-25: OVERWRITE every recorded digest with the recomputed value
        new_item = dict(item)
        new_item["sha256"] = actual_sha
        if actual_phash:
            new_item["phash"] = actual_phash
        new_artifacts_list.append(new_item)

        # If this artifact is match_image.jpg, overwrite match.image_sha256 and match.image_phash
        if rel_path == "match_image.jpg" and "match" in rebuilt:
            rebuilt["match"]["image_sha256"] = actual_sha
            if actual_phash:
                rebuilt["match"]["image_phash"] = actual_phash

    if "artifacts" in rebuilt:
        rebuilt["artifacts"] = new_artifacts_list

    return rebuilt, checks, failure_state
