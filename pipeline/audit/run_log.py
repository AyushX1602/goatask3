"""Run audit log. See design.md 6.

Write-only, first-class deliverable — this is the artifact that answers
"prove it isn't hardcoded" (rules.md never-cut list).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from pipeline.search.base import ProviderReport
from pipeline.verify.matcher import MatchResult


def build_audit(
    run_id: str,
    started_at: float,
    liveness: dict,
    provider_reports: list[ProviderReport],
    match: MatchResult,
) -> dict:
    candidates = []
    for rank, r in enumerate(match.all_scored):
        candidates.append(
            {
                "rank": rank,
                "page_url": r.candidate.page_url,
                "image_url": r.candidate.image_url,
                "source": r.candidate.source,
                "faces_found": r.faces_found,
                "score": r.score,
                "decision": r.decision,
                "reason": r.reason,
            }
        )

    return {
        "run_id": run_id,
        "started_at": started_at,
        "liveness": liveness,
        "providers": [asdict(r) for r in provider_reports],
        "candidates": candidates,
        "verdict": match.verdict,
        "threshold": match.threshold,
        "margin_required": match.margin_required,
    }


def write_audit(run_dir: Path, audit: dict) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "audit.json"
    path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return path


def new_run_id() -> str:
    return time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
