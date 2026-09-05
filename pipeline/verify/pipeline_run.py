"""F4 — the candidate verification loop. See phases.md FINAL PLAN.

This is the project's core technical claim: web-detection candidates are
noisy image-similarity results, and OUR ArcFace re-verification is what
converts them into an actual face match (R-03). Everything upstream of
run_pipeline() may be swapped (which providers ran, which image came from
where) without touching the decision logic, because that logic lives here
and nowhere else.

Both the CLI and the local demo UI call this ONE function, so there is
exactly one verification implementation in the codebase (architecture.md
5a: "not a second implementation to maintain").
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import cv2
import numpy as np

from pipeline.cache.http_cache import HttpCache, get_http_cache
from pipeline.config import MatchPolicy, get_config, load_match_policy
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.face.quality import passes as quality_passes
from pipeline.search.base import Candidate, ProviderReport, SearchProvider
from pipeline.search.orchestrator import gather
from pipeline.verify.allowlist import SOCIAL_ALLOW, is_media_blocked, platform_name
from pipeline.verify.dedupe import dedupe
from pipeline.verify.matcher import MatchResult, score_candidates


@dataclass(frozen=True)
class PipelineResult:
    match: MatchResult
    provider_reports: list[ProviderReport]
    identity_signals: list[str]  # context only, per R-03 — never used above
    images_fetched: int
    images_deduped: int


def _fetch_image(http: HttpCache, url: str, timeout: float = 12.0) -> bytes | None:
    if not url:
        return None
    try:
        resp = http.get(url, timeout=timeout)
        if not resp.ok:
            return None
        return resp.content
    except Exception:
        return None


def _score_one_candidate(
    detector: FaceDetector,
    embedder: FaceEmbedder,
    probe_vec: np.ndarray,
    image_bytes: bytes,
    min_face_px: int,
) -> tuple[float | None, int, str | None]:
    """Returns (best_score_or_None, faces_found, prereject_reason_or_None).

    A prereject_reason means this candidate must not enter matcher's
    threshold/margin logic (design.md 1.7: too-small faces produce
    unreliable scores, not weak ones).
    """
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None, 0, None  # not a prereject — goes through as reject-no-face

    faces = detector.detect(img)
    if not faces:
        return None, 0, None

    usable = [f for f in faces if quality_passes(f, min_face_px)[0]]
    if not usable:
        return None, len(faces), "reject-face-too-small"

    best = -1.0
    for f in usable:
        crop = align(img, f.kps5)
        emb = embedder.embed(crop)
        best = max(best, float(np.dot(emb.vec, probe_vec)))
    return best, len(usable), None


def run_pipeline(
    search_image_bytes: bytes,
    probe_vec: np.ndarray,
    providers: list[SearchProvider],
    detector: FaceDetector,
    embedder: FaceEmbedder,
    policy: MatchPolicy | None = None,
    allowed_domains: set[str] | None = None,
    max_workers: int = 8,
    public_image_url: str | None = None,
) -> PipelineResult:
    """The single shared verification loop (F4).

    search_image_bytes: the ORIGINAL photograph (JPEG), NOT the aligned
    crop — see pipeline/search/image_prep.py for why this distinction is
    load-bearing. Sent as base64 request content by the GCV backend
    (design.md 2.1a).

    public_image_url: forwarded to providers whose search() accepts it (the
    serpapi backend, D-31 — it needs a publicly reachable URL and cannot
    take raw bytes). Without it, serpapi raises internally and the
    orchestrator (R-14) logs the error and returns zero candidates rather
    than aborting the run. GCV is primary specifically because it has no
    such dependency (D-28). A full S3-presigned-URL solution for arbitrary
    local images remains future work; passing a known public URL (e.g. the
    approved demo subject's Wikimedia portrait) is sufficient today.

    providers: e.g. [WebDetectProvider(), BlueskyProvider()] — anything
    implementing SearchProvider.
    """
    policy = policy or load_match_policy()
    allowed_domains = SOCIAL_ALLOW if allowed_domains is None else allowed_domains
    min_face_px = get_config().min_face_px
    http = get_http_cache()

    all_candidates, provider_reports = gather(
        search_image_bytes, probe_vec, providers, public_image_url=public_image_url
    )

    identity_signals: list[str] = []
    for p in providers:
        sigs = getattr(p, "last_identity_signals", None)
        if sigs:
            identity_signals.extend(sigs)

    deduped_pre_fetch_count = len(all_candidates)

    # Fetch every candidate image concurrently (I/O-bound, same pattern as
    # the orchestrator itself). Failed fetches become reject-fetch-failed
    # rather than aborting the run (R-14 spirit, applied to candidates too).
    images: dict[str, bytes] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_image, http, c.image_url): c.image_url
            for c in all_candidates
            if c.image_url
        }
        for future in as_completed(futures):
            url = futures[future]
            data = future.result()
            if data is not None:
                images[url] = data

    deduped = dedupe(all_candidates, images)

    scored: list[tuple[Candidate, float | None, int]] = []
    prerejected: list[tuple[Candidate, str, str]] = []

    for cand in deduped:
        if not cand.image_url:
            # The search provider gave us a page but no retrievable image
            # for it, and no thumbnail could be derived. Distinct from
            # reject-no-face: we never obtained an image to look at, so
            # claiming "no face detected" would be misreporting.
            prerejected.append(
                (
                    cand,
                    "reject-no-image",
                    "search result had no retrievable image URL, so the face "
                    "could not be verified",
                )
            )
            continue

        data = images.get(cand.image_url)
        if data is None:
            # Distinguish a platform that deliberately refuses programmatic
            # media access from an incidental network failure. Verified
            # 5 Sep 2026: Instagram/Facebook return a tiny text/html stub
            # and TikTok returns 403 on signed URLs, identically under a
            # browser UA and a Referer — so this is not something we can
            # route around, and saying "could not fetch" alone reads as a
            # defect on our side when it is not.
            if is_media_blocked(cand.image_url) or is_media_blocked(cand.page_url):
                plat = platform_name(cand.image_url) or platform_name(cand.page_url)
                prerejected.append(
                    (
                        cand,
                        "reject-platform-blocked",
                        f"{plat} serves media only to its own crawler, so the face "
                        "cannot be independently verified — post found but unverifiable",
                    )
                )
            else:
                prerejected.append(
                    (cand, "reject-fetch-failed", f"could not fetch {cand.image_url}")
                )
            continue

        score, faces_found, prereject_reason = _score_one_candidate(
            detector, embedder, probe_vec, data, min_face_px
        )
        if prereject_reason == "reject-face-too-small":
            prerejected.append(
                (cand, prereject_reason, f"all {faces_found} face(s) below {min_face_px}px minimum")
            )
            continue

        scored.append((cand, score, faces_found))

    match = score_candidates(
        scored, policy, allowed_domains=allowed_domains, prerejected=prerejected
    )

    return PipelineResult(
        match=match,
        provider_reports=provider_reports,
        identity_signals=identity_signals,
        images_fetched=len(images),
        images_deduped=deduped_pre_fetch_count - len(deduped),
    )
