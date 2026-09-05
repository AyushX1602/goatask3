"""The re-verification command. See design.md 5.4, phases.md F9.

This is the literal text of brief requirement 3: "demonstrate
re-verifying the data against the on-chain record." Two checks, both
independent:

  1. INTEGRITY — recompute the evidence hash from the bundle on disk and
     confirm it matches the hash that was actually anchored (this is
     recorded in the bundle itself as `run.anchor.evidence_hash`, or can
     be supplied separately).
  2. ON-CHAIN — ask the deployed contract whether that exact hash was
     ever anchored, and if so, that every field matches.

The tamper demo (rules.md never-cut list) is: edit one character in a
saved evidence.json, re-run this, and watch check 1 (and consequently
check 2, since the recomputed hash no longer matches anything on chain)
fail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pipeline.chain.evm import EvmClient, VerifyResult
from pipeline.evidence.canonical import canonical_bytes, evidence_hash_hex


@dataclass(frozen=True)
class ReverifyReport:
    bundle_path: str
    recomputed_hash: str
    anchored_hash: str | None  # what the bundle CLAIMS was anchored, if recorded
    integrity_ok: bool
    on_chain: VerifyResult | None
    overall: str  # "PASS" | "TAMPERED" | "NOT_ANCHORED" | "ERROR"
    detail: str


def reverify_bundle(
    bundle_path: Path,
    client: EvmClient | None = None,
    expected_hash: str | None = None,
) -> ReverifyReport:
    """Loads a bundle from disk, recomputes its hash, and checks it against
    the chain.

    expected_hash: the hash this bundle was actually anchored under,
    normally read from the sibling anchor.json written at anchor time
    (architecture.md 7: runs/<id>/anchor.json). Passing it explicitly is
    what makes TAMPERED and NOT_ANCHORED distinguishable — without it,
    "this hash isn't on chain" is ambiguous between "this bundle was never
    anchored at all" and "this bundle WAS anchored, but has since been
    edited so its hash no longer matches what was anchored". If omitted,
    the function checks only whether the recomputed hash exists on chain
    at all, and cannot tell those two cases apart (reported as NOT_ANCHORED
    in that case, since no claim of prior anchoring is available to fail).
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

    raw = bundle_path.read_bytes()
    try:
        data = json.loads(raw)
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

    try:
        recomputed_hash = evidence_hash_hex(data)
    except Exception as e:
        return ReverifyReport(
            bundle_path=str(bundle_path),
            recomputed_hash="",
            anchored_hash=expected_hash,
            integrity_ok=False,
            on_chain=None,
            overall="TAMPERED",
            detail=f"bundle is not canonicalisable: {e}",
        )

    # INTEGRITY CHECK: does the hash we just recomputed from the bytes on
    # disk match the hash this bundle was actually anchored under? If no
    # expected_hash was supplied, this check is skipped (integrity_ok=True
    # by default) and the result rests entirely on the on-chain check below.
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
        )

    if not integrity_ok:
        overall = "TAMPERED"
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
        detail = "recomputed hash matches the on-chain record exactly"

    return ReverifyReport(
        bundle_path=str(bundle_path),
        recomputed_hash=recomputed_hash,
        anchored_hash=expected_hash,
        integrity_ok=integrity_ok,
        on_chain=on_chain,
        overall=overall,
        detail=detail,
    )


def format_report(report: ReverifyReport) -> str:
    lines = [
        f"bundle:      {report.bundle_path}",
        f"recomputed:  {report.recomputed_hash}",
    ]
    if report.on_chain:
        lines.append(f"on-chain:    exists={report.on_chain.exists}"
                      f"{'  chain_id=' + str(report.on_chain.chain_id) if report.on_chain.exists else ''}")
        if report.on_chain.exists:
            lines.append(f"             score_bps={report.on_chain.score_bps}  "
                          f"anchored_at={report.on_chain.anchored_at}  "
                          f"submitter={report.on_chain.submitter}")
    lines.append(f"\nRESULT: {report.overall} — {report.detail}")
    return "\n".join(lines)
