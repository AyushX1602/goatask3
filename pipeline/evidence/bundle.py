"""Evidence bundle assembly. See design.md 4.2.

Produces the exact structure that gets canonicalised and hashed. This is
the ONLY place a bundle is constructed — a future chain/ module must never
build its own dict, or drift between "what we anchored" and "what we
verify against" becomes possible (the classic hash-anchoring demo failure,
design.md 4.1).

schema_version is frozen at 1 once this lands in a committed sample run
(rules.md R-02). Any structural change after that requires a version bump,
not an in-place edit.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from pipeline.evidence.canonical import canonical_bytes, evidence_hash_hex
from pipeline.evidence.commitment import face_commitment_hex
from pipeline.face.types import Embedding, LivenessResult
from pipeline.verify.matcher import MatchResult

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EvidenceBundle:
    data: dict  # the canonical structure itself
    evidence_hash_hex: str  # keccak256(canonical_bytes(data)), 0x-prefixed
    canonical_json: bytes  # the exact bytes that were hashed


def _score_bps(score: float) -> int:
    """cosine in [-1, 1] -> integer basis points, R-02: no floats hashed.
    Clamped defensively; a cosine score should never fall outside [-1, 1]
    but a corrupted embedding could produce something pathological."""
    return int(round(max(-1.0, min(1.0, score)) * 10000))


def build_evidence(
    *,
    run_id: str,
    embedding: Embedding,
    salt: bytes,
    liveness: LivenessResult,
    is_live_capture: bool,
    match: MatchResult,
    providers_queried: list[str],
    degraded_closed_corpus: bool,
    identity_signals: list[str],
    candidates_examined: int,
    pipeline_version: str,
    captured_at: int | None = None,
) -> EvidenceBundle:
    """Builds and hashes an evidence bundle for an ACCEPTed match.

    Raises ValueError if match.verdict != "MATCH" — there is nothing to
    anchor for a NO_MATCH run (rules.md R-16: NO_MATCH is a valid outcome,
    but it produces no evidence bundle, only an audit log entry).
    """
    if match.verdict != "MATCH" or match.best is None:
        raise ValueError(
            "build_evidence requires an accepted match; NO_MATCH runs are "
            "recorded in the audit log only (R-16), never as an evidence bundle"
        )

    best = match.best
    runner_up_score = match.runner_up.score if match.runner_up else None
    margin = best.score - (runner_up_score if runner_up_score is not None else -1.0)

    post_meta = best.candidate.post_meta or {}

    data = {
        "schema_version": SCHEMA_VERSION,
        "probe": {
            "face_commitment": face_commitment_hex(embedding, salt),  # R-01: never the raw vector
            "liveness_passed": bool(liveness.passed) if is_live_capture else True,
            "liveness_label": liveness.label if is_live_capture else "not_applicable",
            "captured_at": int(captured_at if captured_at is not None else time.time()),
            "aligned_sha256": embedding.aligned_png_sha256,
        },
        "match": {
            "page_url": best.candidate.page_url,
            "image_url": best.candidate.image_url,
            "image_sha256": post_meta.get("image_sha256", ""),
            "provider": best.candidate.source,
            "score_bps": _score_bps(best.score),
            "margin_bps": _score_bps(margin),
            "threshold_bps": _score_bps(match.threshold),
        },
        "post": {
            "platform": post_meta.get("platform", best.candidate.source),
            "author_handle": post_meta.get("author_handle", ""),
            "author_display": post_meta.get("author_display", ""),
            "text": post_meta.get("text", ""),
            "published_at": _to_unix_seconds(post_meta.get("published_at")),
            "permalink": post_meta.get("permalink", best.candidate.page_url),
        },
        "run": {
            "run_id": run_id,
            "providers_queried": sorted(providers_queried),
            "candidates_examined": candidates_examined,
            "candidates_rejected": candidates_examined - 1,
            "degraded_closed_corpus": degraded_closed_corpus,
            "identity_signals": sorted(set(identity_signals)),  # context only, R-03
            "pipeline_version": pipeline_version,
            "model": embedding.model,
        },
    }

    canonical = canonical_bytes(data)
    return EvidenceBundle(
        data=data,
        evidence_hash_hex=evidence_hash_hex(data),
        canonical_json=canonical,
    )


def _to_unix_seconds(value) -> int:
    """post_meta timestamps arrive as ISO-8601 strings from most providers,
    or may already be an int. R-02 forbids floats in the hashed structure,
    so this always returns an int, defaulting to 0 if unparseable rather
    than raising — a missing timestamp should not block anchoring."""
    if value is None:
        return 0
    if isinstance(value, (int,)):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            # Handles "...Z" ISO-8601 as emitted by Bluesky/most APIs.
            from datetime import datetime

            v = value.replace("Z", "+00:00")
            return int(datetime.fromisoformat(v).timestamp())
        except (ValueError, TypeError):
            return 0
    return 0


def image_sha256(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()
