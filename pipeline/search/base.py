"""Provider contract. See design.md 2.1 and architecture.md 4.

R-03: Candidate.provider_score is recorded in the audit log and NEVER used
to decide accept/reject. Only pipeline.verify.matcher does that, using our
own ArcFace cosine similarity. A provider's job is to return candidates,
nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Candidate:
    image_url: str
    page_url: str
    source: str  # provider name, for the audit log
    provider_score: float | None = None  # recorded only — see R-03 above
    raw: dict = field(default_factory=dict)  # untouched provider response
    post_meta: dict | None = None  # author/text/timestamp if the provider supplies it
    # Ordered, largest-resolution-first alternates for image_url. Exists
    # because some platforms' thumbnails are missing at the URL we'd
    # otherwise use (YouTube's maxresdefault.jpg 404s for some videos —
    # measured, scripts/probe_size_variants.py, 5 Sep 2026) rather than
    # simply being a different size. verify/pipeline_run.py walks this
    # list and keeps the first variant that both fetches and decodes as an
    # image, then rewrites the candidate to record THAT url — so evidence
    # always cites the exact URL that was actually verified.
    image_url_fallbacks: tuple[str, ...] = ()
    # GCV's own classification of how confident it is this is the SAME
    # image: "full" (fullMatchingImages — Google asserts pixel-identical),
    # "partial" (partialMatchingImages), "similar" (visuallySimilarImages —
    # just a lookalike), or "" when the provider gives no such signal
    # (SerpApi, Bluesky). R-03 still applies in full: this is NEVER used to
    # accept or reject a candidate. It exists purely for reporting —
    # specifically to distinguish "the search engine asserts this exact
    # image appears on a platform we cannot fetch from" (recorded, not
    # accepted — a real signal about cross-platform image reuse worth
    # showing) from "just another lookalike, ignore it".
    match_kind: str = ""


@dataclass(frozen=True)
class ProviderReport:
    """One per provider per run. Written into audit.json even when the
    provider was skipped or failed (R-14: no provider can abort a run)."""

    name: str
    available: bool
    attempted: bool
    latency_ms: float | None = None
    candidates_returned: int = 0
    error: str | None = None


class SearchProvider(Protocol):
    name: str
    requires_credentials: bool

    def available(self) -> bool:
        """True if this provider has what it needs (an API key, etc.) to
        run. The orchestrator skips unavailable providers silently — this
        is what makes the zero-API-key quickstart work (prd.md S10)."""
        ...

    def search(self, search_image_bytes: bytes, probe_vec) -> list[Candidate]:
        """probe_vec: the (512,) L2-normalised probe embedding, passed as a
        plain array so this module has no dependency on pipeline.face
        (architecture.md 3 boundary table)."""
        ...
