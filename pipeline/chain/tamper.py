"""Tamper demonstration engine. See docs/design.md 5.5, docs/phases.md T2.2 / D-40.

Three distinct tamper modes, each proving a different security property:
  1. swap-artifact: flips one byte in match_image.jpg.
     Proves: the bundle genuinely commits to source image bytes on disk,
     triggering ARTIFACT_MISMATCH.
  2. edit-bundle: modifies match.score_bps in evidence.json.
     Proves: the on-chain hash pins the bundle's contents,
     triggering BUNDLE_MODIFIED.
  3. forge-bundle: builds a fresh, internally consistent, correctly-hashed bundle
     that was never anchored.
     Proves: consistency is not provenance,
     triggering NOT_ANCHORED.

Every mode operates exclusively on a temporary scratch copy of the run directory.
The real run directory on disk is guaranteed never to be mutated.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.chain.evm import EvmClient
from pipeline.chain.reverify import ReverifyReport, reverify_bundle
from pipeline.evidence.artifacts import ArtifactCheck, extract_artifact_manifest
from pipeline.evidence.canonical import canonical_bytes

VALID_TAMPER_MODES = ("swap-artifact", "edit-bundle", "forge-bundle")


@dataclass(frozen=True)
class TamperReport:
    mode: str
    overall: str  # ARTIFACT_MISMATCH | BUNDLE_MODIFIED | NOT_ANCHORED | ERROR
    detail: str
    recomputed_hash: str
    anchored_hash: str | None
    tampered_target: str
    original_value: str
    tampered_value: str
    artifact_checks: list[ArtifactCheck] = field(default_factory=list)


def tamper_run(
    run_dir: Path,
    mode: str = "swap-artifact",
    client: EvmClient | None = None,
) -> TamperReport:
    """Copies run_dir to an isolated temp directory, applies the specified tamper
    mutation, runs reverify_bundle on the scratch copy, and cleans up.
    Guarantees run_dir is never modified.
    """
    if mode not in VALID_TAMPER_MODES:
        return TamperReport(
            mode=mode,
            overall="ERROR",
            detail=f"unknown tamper mode: {mode}. Must be one of {VALID_TAMPER_MODES}",
            recomputed_hash="",
            anchored_hash=None,
            tampered_target="",
            original_value="",
            tampered_value="",
        )

    bundle_path = run_dir / "evidence.json"
    if not bundle_path.exists():
        return TamperReport(
            mode=mode,
            overall="ERROR",
            detail=f"no evidence.json found in {run_dir}",
            recomputed_hash="",
            anchored_hash=None,
            tampered_target="",
            original_value="",
            tampered_value="",
        )

    # 1. Create temporary working directory copy
    scratch_dir = Path(tempfile.mkdtemp(prefix="fcv_tamper_"))
    try:
        for item in run_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, scratch_dir / item.name)

        anchor_file = scratch_dir / "anchor.json"
        expected_hash: str | None = None
        if anchor_file.exists():
            try:
                expected_hash = json.loads(anchor_file.read_text(encoding="utf-8")).get("evidence_hash")
            except Exception:
                expected_hash = None

        scratch_bundle = scratch_dir / "evidence.json"
        bundle_data = json.loads(scratch_bundle.read_text(encoding="utf-8"))

        tampered_target = ""
        original_value = ""
        tampered_value = ""

        # 2. Apply tamper mode
        if mode == "swap-artifact":
            manifest = extract_artifact_manifest(bundle_data)
            target_file: Path | None = None
            for item in manifest:
                p = scratch_dir / item["path"]
                if p.exists():
                    target_file = p
                    break
            if target_file is None:
                # check for any match_image file
                for ext in (".jpg", ".jpeg", ".png", ".webp"):
                    candidate = scratch_dir / f"match_image{ext}"
                    if candidate.exists():
                        target_file = candidate
                        break

            if target_file is None or not target_file.exists():
                return TamperReport(
                    mode=mode,
                    overall="ERROR",
                    detail=f"no artifact file to swap found in {run_dir}",
                    recomputed_hash="",
                    anchored_hash=expected_hash,
                    tampered_target="artifacts",
                    original_value="file present",
                    tampered_value="missing",
                )

            raw = bytearray(target_file.read_bytes())
            if len(raw) > 0:
                idx = min(100, len(raw) - 1)
                raw[idx] ^= 0xFF
            else:
                raw.extend(b"\xff")
            target_file.write_bytes(raw)

            tampered_target = target_file.name
            original_value = "verified bytes"
            tampered_value = "1 byte modified (XOR 0xFF)"

        elif mode == "edit-bundle":
            orig_score = bundle_data.get("match", {}).get("score_bps", 9806)
            tampered_score = 9999 if orig_score != 9999 else 9998
            bundle_data["match"]["score_bps"] = tampered_score
            scratch_bundle.write_bytes(canonical_bytes(bundle_data))

            tampered_target = "match.score_bps"
            original_value = str(orig_score)
            tampered_value = str(tampered_score)

        elif mode == "forge-bundle":
            # Build a fresh unanchored bundle
            forged = copy.deepcopy(bundle_data)
            orig_run_id = forged.get("run", {}).get("run_id", "original")
            forged_run_id = f"forged-{orig_run_id}"
            if "run" in forged:
                forged["run"]["run_id"] = forged_run_id

            scratch_bundle.write_bytes(canonical_bytes(forged))
            # Delete anchor.json in scratch copy so it tests unanchored provenance
            if anchor_file.exists():
                anchor_file.unlink()
            expected_hash = None

            tampered_target = "run.run_id"
            original_value = orig_run_id
            tampered_value = forged_run_id

        # 3. Re-verify the scratch directory
        report: ReverifyReport = reverify_bundle(
            scratch_bundle,
            client=client or EvmClient(),
            expected_hash=expected_hash,
        )

        return TamperReport(
            mode=mode,
            overall=report.overall,
            detail=report.detail,
            recomputed_hash=report.recomputed_hash,
            anchored_hash=report.anchored_hash,
            tampered_target=tampered_target,
            original_value=original_value,
            tampered_value=tampered_value,
            artifact_checks=report.artifact_checks,
        )

    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)
