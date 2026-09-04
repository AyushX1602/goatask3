"""Parallel fan-out across search providers. See design.md 2.1.

R-14: a provider that raises, times out, or returns nothing is logged and
contributes zero candidates. This module can never let a single provider
abort a run — that isolation is enforced here, in one place, so no
individual provider implementation has to get it right on its own.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipeline.search.base import Candidate, ProviderReport, SearchProvider


def gather(
    aligned_face_png: bytes,
    probe_vec,
    providers: list[SearchProvider],
    timeout_s: float = 45.0,
) -> tuple[list[Candidate], list[ProviderReport]]:
    """Runs every available provider concurrently. Returns all candidates
    pooled together, plus one ProviderReport per provider (including
    unavailable and failed ones) for the audit log.
    """
    reports: list[ProviderReport] = []
    to_run: list[SearchProvider] = []

    for p in providers:
        if p.available():
            to_run.append(p)
        else:
            reports.append(
                ProviderReport(name=p.name, available=False, attempted=False)
            )

    all_candidates: list[Candidate] = []

    if not to_run:
        return all_candidates, reports

    def _run(provider: SearchProvider) -> tuple[SearchProvider, list[Candidate], float, str | None]:
        start = time.perf_counter()
        try:
            cands = provider.search(aligned_face_png, probe_vec)
            err = None
        except Exception as exc:  # R-14: provider errors never propagate
            cands = []
            err = f"{type(exc).__name__}: {exc}"
        elapsed_ms = (time.perf_counter() - start) * 1000
        return provider, cands, elapsed_ms, err

    with ThreadPoolExecutor(max_workers=max(1, len(to_run))) as pool:
        futures = {pool.submit(_run, p): p for p in to_run}
        for future in as_completed(futures, timeout=timeout_s + 5):
            provider = futures[future]
            try:
                _, cands, elapsed_ms, err = future.result(timeout=timeout_s)
            except Exception as exc:  # timeout or unexpected failure
                cands, elapsed_ms, err = [], None, f"{type(exc).__name__}: {exc}"

            all_candidates.extend(cands)
            reports.append(
                ProviderReport(
                    name=provider.name,
                    available=True,
                    attempted=True,
                    latency_ms=elapsed_ms,
                    candidates_returned=len(cands),
                    error=err,
                )
            )

    return all_candidates, reports
