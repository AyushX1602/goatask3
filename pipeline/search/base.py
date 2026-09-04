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

    def search(self, aligned_face_png: bytes, probe_vec) -> list[Candidate]:
        """probe_vec: the (512,) L2-normalised probe embedding, passed as a
        plain array so this module has no dependency on pipeline.face
        (architecture.md 3 boundary table)."""
        ...
