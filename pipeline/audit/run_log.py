"""Run audit log. See docs/design.md 6.

Write-only, first-class deliverable — this is the artifact that answers
"prove it isn't hardcoded" (docs/rules.md never-cut list).
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
    cache_stats: dict[str, dict[str, int]] | None = None,
    candidate_diagnostics: dict | None = None,
) -> dict:
    """cache_stats: optional per-provider {"hits": N, "misses": N}. Exists
    because pipeline.cache.http_cache.get_http_cache()'s own docstring
    claims hit/miss stats "land in the audit log" — before this parameter
    existed, that claim was false; build_audit() never referenced them.
    Recording-day discipline (G1.6) depends on this being real: a replayed
    (all-cache-hit) run must be visibly distinguishable from a live one.

    candidate_diagnostics: optional {image_url: FetchDiagnostics}, from
    PipelineResult.candidate_diagnostics (verify/pipeline_run.py). Real,
    measured fetch/decode observations — 6 Sep 2026, owner instruction:
    replace a bare `—` for structurally-unscoreable rows with actual
    numbers rather than leaving a gap or fabricating a confidence value.
    """
    diag_by_url = candidate_diagnostics or {}

    candidates = []
    for rank, r in enumerate(match.all_scored):
        diag = diag_by_url.get(r.candidate.image_url)
        candidates.append(
            {
                "rank": rank,
                "page_url": r.candidate.page_url,
                "image_url": r.candidate.image_url,
                "source": r.candidate.source,
                "origin": getattr(r.candidate, "origin", "face"),
                "faces_found": r.faces_found,
                "score": r.score,
                "decision": r.decision,
                "reason": r.reason,
                # Diagnostic only (R-03): the provider's own claim about
                # how confident it is this is the SAME image. T1.2.
                "match_kind": r.candidate.match_kind,
                # Real, measured observations — never present unless we
                # actually fetched something. None (not a fabricated 0 or
                # "n/a" string) when no fetch was ever attempted.
                "diagnostics": (
                    {
                        "http_status": diag.http_status,
                        "content_type": diag.content_type,
                        "content_bytes": diag.content_bytes,
                        "image_width": diag.image_width,
                        "image_height": diag.image_height,
                        "faces_found": diag.faces_found,
                        "largest_face_px": diag.largest_face_px,
                        "routes_tried": diag.routes_tried,
                    }
                    if diag is not None
                    else None
                ),
            }
        )

    audit = {
        "run_id": run_id,
        "started_at": started_at,
        "liveness": liveness,
        "providers": [asdict(r) for r in provider_reports],
        "candidates": candidates,
        "verdict": match.verdict,
        "threshold": match.threshold,
        "margin_required": match.margin_required,
    }

    if cache_stats:
        total_hits = sum(s.get("hits", 0) for s in cache_stats.values())
        total_misses = sum(s.get("misses", 0) for s in cache_stats.values())
        audit["cache"] = {
            **cache_stats,
            "aggregate": {"hits": total_hits, "misses": total_misses},
        }

    return audit


def write_audit(run_dir: Path, audit: dict) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "audit.json"
    path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return path


def new_run_id() -> str:
    return time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
