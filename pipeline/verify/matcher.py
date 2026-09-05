"""Accept/reject decision. See design.md 3.1.

R-03: this is the ONLY place the accept/reject decision is made. Provider
scores are never consulted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

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
    # The highest-scoring candidate BELOW threshold — i.e. the best
    # non-match. Margin is measured against this, deliberately NOT against
    # the second-best match; see the reasoning in score_candidates().
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

    # Margin is measured against the best NON-MATCHING candidate, i.e. the
    # highest scorer that falls BELOW threshold — not against the runner-up.
    #
    # Why: the margin rule exists to answer "is the top match clearly
    # separated from the population of things that are NOT this person?"
    # Measuring against the runner-up answers a different and wrong
    # question, because a public figure has many genuine photos indexed
    # across the web and they all score similarly high. In a real run for
    # Alia Bhatt we saw 0.9225 / 0.9211 / 0.9164 / 0.9072 across linkedin,
    # youtube and reddit — four independent confirmations of the correct
    # person, all rejected because they agreed with each other to within
    # 0.0014. That inverted the rule's intent: abundant corroboration was
    # being treated as ambiguity.
    #
    # Multiple above-threshold candidates are therefore CORROBORATION, and
    # are reported as such rather than as rejections.
    above = [r for r in scoreable if r.score >= policy.threshold]
    below = [r for r in scoreable if r.score < policy.threshold]
    best_non_match = below[0] if below else None  # already sorted descending
    margin = best.score - (best_non_match.score if best_non_match is not None else -1.0)

    passes_threshold = best.score >= policy.threshold
    passes_margin = margin >= policy.margin
    corroborating = max(0, len(above) - 1)

    verdict = "NO_MATCH"
    for i, r in enumerate(results):
        if r.decision != "pending":
            continue

        if r is best and passes_threshold and passes_margin:
            extra = (
                f", {corroborating} corroborating match(es) above threshold"
                if corroborating
                else ""
            )
            sep = (
                f"margin {margin:.4f} >= {policy.margin} vs best non-match "
                f"{best_non_match.score:.4f}"
                if best_non_match is not None
                else "no sub-threshold candidate to compare against"
            )
            results[i] = ScoredCandidate(
                r.candidate, r.score, r.faces_found, "ACCEPT",
                f"score {r.score:.4f} >= threshold {policy.threshold}, {sep}{extra}",
            )
            verdict = "MATCH"
        elif r.score < policy.threshold:
            results[i] = ScoredCandidate(
                r.candidate, r.score, r.faces_found, "reject-below-threshold",
                f"score {r.score:.4f} < threshold {policy.threshold}",
            )
        elif r is not best:
            # Above threshold but not the top-ranked result. This is a real
            # match too; we report a single best one and record the rest as
            # supporting evidence, never as a rejection.
            results[i] = ScoredCandidate(
                r.candidate, r.score, r.faces_found, "corroborating",
                f"score {r.score:.4f} >= threshold {policy.threshold}; "
                "same identity, not the top-ranked result",
            )
        else:
            # best, above threshold, but not separated from the non-matching
            # population — the genuine ambiguity case the rule guards against.
            results[i] = ScoredCandidate(
                r.candidate, r.score, r.faces_found, "reject-margin",
                f"score {r.score:.4f} not separated from best non-match "
                f"{best_non_match.score:.4f} (margin {margin:.4f} < {policy.margin})",
            )

    best_final = next((r for r in results if r.decision == "ACCEPT"), None)

    return MatchResult(
        best=best_final,
        runner_up=best_non_match,
        all_scored=results,
        threshold=policy.threshold,
        margin_required=policy.margin,
        verdict=verdict,
    )
