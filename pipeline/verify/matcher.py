"""Accept/reject decision. See design.md 3.1.

R-03: this is the ONLY place the accept/reject decision is made. Provider
scores are never consulted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import numpy as np

from pipeline.config import MatchPolicy
from pipeline.search.base import Candidate


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float | None  # None if no face was found in the candidate image
    faces_found: int
    decision: str  # "ACCEPT" | "reject-<reason>"
    reason: str


@dataclass(frozen=True)
class MatchResult:
    best: ScoredCandidate | None
    runner_up: ScoredCandidate | None
    all_scored: list[ScoredCandidate]
    threshold: float
    margin_required: float
    verdict: str  # "MATCH" | "NO_MATCH"


def _registrable_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def score_candidates(
    scored: list[tuple[Candidate, float | None, int]],
    policy: MatchPolicy,
    allowed_domains: set[str] | None = None,
    prerejected: list[tuple[Candidate, str, str]] | None = None,
) -> MatchResult:
    """scored: (candidate, best_face_score_or_None, faces_found) tuples,
    already produced by re-running the face core on each downloaded image
    (verify/allowlist.py filters domain eligibility separately upstream,
    but domain is re-checked here so the decision function is self-contained).

    prerejected: candidates already decided before scoring — e.g. a failed
    download (`reject-fetch-failed`) or every face too small to trust
    (`reject-face-too-small`, design.md 1.7). Included in the audit trail
    with their decision already fixed; never touched by threshold/margin
    logic, since their decision is never "pending".
    """
    results: list[ScoredCandidate] = []

    for cand, decision, reason in prerejected or []:
        results.append(ScoredCandidate(cand, None, 0, decision, reason))

    for cand, score, faces_found in scored:
        if score is None:
            results.append(
                ScoredCandidate(cand, None, faces_found, "reject-no-face", "no face detected in candidate image")
            )
            continue

        if allowed_domains is not None:
            dom = _registrable_domain(cand.page_url)
            if dom not in allowed_domains:
                results.append(
                    ScoredCandidate(cand, score, faces_found, "reject-domain", f"{dom} not on social allowlist")
                )
                continue

        results.append(ScoredCandidate(cand, score, faces_found, "pending", ""))

    scoreable = [r for r in results if r.score is not None and r.decision == "pending"]
    scoreable.sort(key=lambda r: r.score, reverse=True)

    if not scoreable:
        for i, r in enumerate(results):
            if r.decision == "pending":
                results[i] = ScoredCandidate(r.candidate, r.score, r.faces_found, "reject-below-threshold", "no scoreable candidates")
        return MatchResult(None, None, results, policy.threshold, policy.margin, "NO_MATCH")

    best = scoreable[0]
    runner_up = next(
        (r for r in scoreable[1:] if r.candidate.page_url != best.candidate.page_url),
        None,
    )
    margin = best.score - (runner_up.score if runner_up else -1.0)

    verdict = "NO_MATCH"
    for i, r in enumerate(results):
        if r.decision != "pending":
            continue
        if r is best and best.score >= policy.threshold and margin >= policy.margin:
            results[i] = ScoredCandidate(r.candidate, r.score, r.faces_found, "ACCEPT", f"score {r.score:.4f} >= threshold {policy.threshold}, margin {margin:.4f} >= {policy.margin}")
            verdict = "MATCH"
        elif r.score < policy.threshold:
            results[i] = ScoredCandidate(r.candidate, r.score, r.faces_found, "reject-below-threshold", f"score {r.score:.4f} < threshold {policy.threshold}")
        else:
            results[i] = ScoredCandidate(r.candidate, r.score, r.faces_found, "reject-margin", f"score {r.score:.4f} too close to runner-up (margin {margin:.4f} < {policy.margin})")

    best_final = next((r for r in results if r.decision == "ACCEPT"), None)
    runner_final = runner_up

    return MatchResult(
        best=best_final,
        runner_up=runner_final,
        all_scored=results,
        threshold=policy.threshold,
        margin_required=policy.margin,
        verdict=verdict,
    )
