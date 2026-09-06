"""The re-verification command. See docs/design.md 5.4, docs/phases.md T2.1 / F9.

Literal brief requirement 3: "demonstrate re-verifying the data against
the on-chain record."

Three ordered, independent checks (docs/design.md 5.4, revised 7 Sep 2026):

  1. ARTIFACT DIGESTS (R-25) — recompute sha256 / phash of every file the bundle
     references directly from the files on disk and compare against the digests
     recorded in the bundle. Overwrite the bundle's digest fields with the recomputed
     values. Failure: ARTIFACT_MISMATCH or ARTIFACT_MISSING.
  2. BUNDLE INTEGRITY — canonicalise the REBUILT bundle and compare its keccak256
     against the hash actually anchored (from anchor.json or expected_hash).
     Failure: BUNDLE_MODIFIED.
  3. ON-CHAIN RECORD — verify via eth_call that the exact hash was anchored on
     this chain and matches contract storage. Failure: NOT_ANCHORED.

TAMPERED is retired as a verdict string: it conflated an artifact swap with
a bundle edit. Both are tampering, but they are not the same tampering (R-24).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.chain.evm import EvmClient, VerifyResult
from pipeline.evidence.artifacts import ArtifactCheck, rebuild_from_artifacts
from pipeline.evidence.canonical import evidence_hash_hex


@dataclass(frozen=True)
class ReverifyReport:
    bundle_path: str
    recomputed_hash: str
    anchored_hash: str | None  # what the bundle CLAIMS was anchored, if recorded
    integrity_ok: bool
    on_chain: VerifyResult | None
    overall: str  # "PASS" | "ARTIFACT_MISMATCH" | "ARTIFACT_MISSING" | "BUNDLE_MODIFIED" | "NOT_ANCHORED" | "ERROR"
    detail: str
    artifact_checks: list[ArtifactCheck] = field(default_factory=list)


def reverify_bundle(
    bundle_path: Path,
    client: EvmClient | None = None,
    expected_hash: str | None = None,
) -> ReverifyReport:
    """Loads a bundle from disk, recomputes artifact digests from source files,
    recomputes the canonical hash, and checks it against the on-chain record.

    expected_hash: the hash this bundle was actually anchored under (from anchor.json).
    """
    if not bundle_path.exists():
        return ReverifyReport(
            bundle_path=str(bundle_path),
            recomputed_hash="",
            anchored_hash=expected_hash,
            integrity_ok=False,
            on_chain=None,
            overall="ERROR",
            detail=f"file not found: {bundle_path}",
        )

    run_dir = bundle_path.parent

    # CHECK 1: Artifact digests (R-25). Recomputes from disk and rebuilds bundle.
    try:
        rebuilt_data, artifact_checks, failure_state = rebuild_from_artifacts(
            run_dir, bundle_filename=bundle_path.name
        )
    except json.JSONDecodeError as e:
        return ReverifyReport(
            bundle_path=str(bundle_path),
            recomputed_hash="",
            anchored_hash=expected_hash,
            integrity_ok=False,
            on_chain=None,
            overall="ERROR",
            detail=f"not valid JSON: {e}",
        )
    except Exception as e:
        return ReverifyReport(
            bundle_path=str(bundle_path),
            recomputed_hash="",
            anchored_hash=expected_hash,
            integrity_ok=False,
            on_chain=None,
            overall="ERROR",
            detail=f"failed reading artifacts: {e}",
        )

    # Recompute keccak256 over the rebuilt bundle data
    try:
        recomputed_hash = evidence_hash_hex(rebuilt_data)
    except Exception as e:
        return ReverifyReport(
            bundle_path=str(bundle_path),
            recomputed_hash="",
            anchored_hash=expected_hash,
            integrity_ok=False,
            on_chain=None,
            overall="BUNDLE_MODIFIED",
            detail=f"rebuilt bundle is not canonicalisable: {e}",
            artifact_checks=artifact_checks,
        )

    # Check 1 outcome
    if failure_state in ("ARTIFACT_MISMATCH", "ARTIFACT_MISSING"):
        first_bad = next((c for c in artifact_checks if not c.match), None)
        detail_msg = first_bad.detail if first_bad else failure_state
        return ReverifyReport(
            bundle_path=str(bundle_path),
            recomputed_hash=recomputed_hash,
            anchored_hash=expected_hash,
            integrity_ok=False,
            on_chain=None,
            overall=failure_state,
            detail=detail_msg,
            artifact_checks=artifact_checks,
        )

    # CHECK 2: Bundle integrity — does recomputed_hash match expected_hash?
    integrity_ok = expected_hash is None or recomputed_hash == expected_hash

    client = client or EvmClient()
    try:
        on_chain = client.verify(expected_hash or recomputed_hash)
    except Exception as e:
        return ReverifyReport(
            bundle_path=str(bundle_path),
            recomputed_hash=recomputed_hash,
            anchored_hash=expected_hash,
            integrity_ok=integrity_ok,
            on_chain=None,
            overall="ERROR",
            detail=f"chain read failed: {type(e).__name__}: {e}",
            artifact_checks=artifact_checks,
        )

    if not integrity_ok:
        overall = "BUNDLE_MODIFIED"
        detail = (
            f"recomputed hash {recomputed_hash} does not match the "
            f"anchored hash {expected_hash} — the bundle has been "
            "modified since it was anchored"
        )
    elif not on_chain.exists:
        overall = "NOT_ANCHORED"
        detail = "this exact hash was never anchored on this chain"
    else:
        overall = "PASS"
        detail = "all artifact digests match and recomputed hash matches the on-chain record exactly"

    return ReverifyReport(
        bundle_path=str(bundle_path),
        recomputed_hash=recomputed_hash,
        anchored_hash=expected_hash,
        integrity_ok=integrity_ok,
        on_chain=on_chain,
        overall=overall,
        detail=detail,
        artifact_checks=artifact_checks,
    )


def format_report(report: ReverifyReport) -> str:
    lines = [
        f"bundle:      {report.bundle_path}",
        f"recomputed:  {report.recomputed_hash}",
    ]
    if report.artifact_checks:
        lines.append("artifacts:")
        for ac in report.artifact_checks:
            status = "OK" if ac.match else "FAIL"
            lines.append(f"  [{status}] {ac.path}: {ac.detail}")
    if report.on_chain:
        lines.append(
            f"on-chain:    exists={report.on_chain.exists}"
            f"{'  chain_id=' + str(report.on_chain.chain_id) if report.on_chain.exists else ''}"
        )
        if report.on_chain.exists:
            lines.append(
                f"             score_bps={report.on_chain.score_bps}  "
                f"anchored_at={report.on_chain.anchored_at}  "
                f"submitter={report.on_chain.submitter}"
            )
    lines.append(f"\nRESULT: {report.overall} — {report.detail}")
    return "\n".join(lines)
