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
from dataclasses import dataclass, replace

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
    # The exact bytes fetched and scored for match.best.candidate, if the
    # verdict is MATCH. None on NO_MATCH (there is no "winning" candidate)
    # or if, somehow, the accepted candidate's bytes were not retained —
    # callers building an evidence bundle MUST treat that as a hard error
    # (evidence/bundle.py's build_evidence rejects empty image_bytes with
    # no fallback), not paper over it. This is what fixes the defect where
    # every previously anchored run had an empty match.image_sha256: the
    # winning bytes used to be discarded once scoring finished and never
    # threaded any further than this function.
    best_image_bytes: bytes | None = None


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

    image_bytes must already be known to decode as an image — callers are
    responsible for distinguishing "not an image" from "no face in the
    image" (see _classify_fetch below); conflating the two produces the
    misleading `reject-no-face` observed live on Instagram/Facebook
    lookaside URLs, which actually return an HTML stub, not a photo.
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


@dataclass(frozen=True)
class _VariantOutcome:
    status: str  # "ok" | "blocked" | "fetch-failed" | "not-image"
    url: str
    image_bytes: bytes | None = None
    decoded: np.ndarray | None = None
    # Every URL that was actually tried before landing on `url`, in order.
    # Exists so a fetch-failed message can report "N variant(s) tried,
    # primary was X" instead of naming only the LAST (smallest, least
    # informative) variant attempted — a real bug found live 5 Sep 2026,
    # where a failure message named ?name=thumb even though the size-
    # variant fix had already tried orig/large/medium/small first.
    attempted: tuple[str, ...] = ()


def _classify_fetch(http: HttpCache, url: str) -> _VariantOutcome:
    """Fetches one candidate URL and classifies what we got, so a 200
    response carrying an HTML stub (Instagram/Facebook lookaside URLs,
    verified live 5 Sep 2026) is never conflated with "no face detected".
    """
    data = _fetch_image(http, url)
    if data is None:
        if is_media_blocked(url):
            return _VariantOutcome("blocked", url)
        return _VariantOutcome("fetch-failed", url)

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        # Fetch succeeded (2xx) but the bytes are not a decodable image —
        # e.g. Instagram/Facebook return a tiny text/html stub with a 200
        # status. This is a distinct, honest reject reason from
        # reject-no-face, which means "we saw a photo, no face in it".
        if is_media_blocked(url):
            return _VariantOutcome("blocked", url)
        return _VariantOutcome("not-image", url, image_bytes=data)

    return _VariantOutcome("ok", url, image_bytes=data, decoded=img)


def _resolve_candidate_image(http: HttpCache, cand: Candidate) -> _VariantOutcome | None:
    """Walks image_url + image_url_fallbacks (largest-first) and returns
    the first variant that decodes as an actual image, stopping early
    when a platform-blocked verdict is reached (retrying a different size
    of a domain that refuses programmatic access cannot help).

    A variant that decodes but has a too-small or absent face is still
    "ok" here — quality gating happens one layer up, where the detector
    and embedder live. We do NOT keep walking past the first decodable
    image, because every fallback list here is largest-resolution-first,
    so a face too small in the largest available variant will only get
    smaller in the rest (measured: X's medium/large/orig are pixel-
    identical at 1080x1080; only size ever shrinks going down the list).

    Repeat fetches of the same URL across runs are absorbed by HttpCache
    (R-04), so this sequential per-candidate walk does not re-hit the
    network on a cached run even though it may issue up to len(urls)
    requests on a cold one.

    Returns None if there were no URLs to try at all.
    """
    urls = [cand.image_url, *cand.image_url_fallbacks]
    urls = [u for u in urls if u]
    if not urls:
        return None

    tried: list[str] = []
    last_non_ok: _VariantOutcome | None = None
    for url in urls:
        tried.append(url)
        outcome = _classify_fetch(http, url)
        outcome = replace(outcome, attempted=tuple(tried))
        if outcome.status == "ok":
            return outcome
        if outcome.status == "blocked":
            return outcome  # no point trying a different size of a blocked domain
        last_non_ok = outcome

    return last_non_ok


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

    # Resolve every candidate's image concurrently. "Resolve" means: walk
    # image_url_fallbacks (largest-first — media_urls.py) and keep the
    # first variant that actually decodes as an image, rather than a
    # single fixed URL. This is what recovers X/Twitter (?name=thumb ->
    # ?name=orig) and YouTube (maxresdefault 404 -> sddefault/hqdefault)
    # from the 50px quality gate. Each candidate's own walk is sequential
    # (largest-first, stop at first success), but candidates run in
    # parallel with each other, same pattern as the orchestrator itself.
    with_urls = [c for c in all_candidates if c.image_url or c.image_url_fallbacks]
    outcomes: dict[int, _VariantOutcome] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_resolve_candidate_image, http, c): c for c in with_urls}
        for future in as_completed(futures):
            cand = futures[future]
            outcomes[id(cand)] = future.result()

    # Rewrite each candidate's image_url to the variant that was actually
    # used, so the evidence bundle (which records image_url verbatim,
    # evidence/bundle.py) always cites the exact URL that was scored —
    # never a smaller/unverified variant that merely happened to be first.
    resolved_candidates: list[Candidate] = []
    outcome_by_url: dict[str, _VariantOutcome] = {}
    images: dict[str, bytes] = {}
    for cand in all_candidates:
        outcome = outcomes.get(id(cand))
        if outcome is None:
            resolved_candidates.append(cand)
            continue
        resolved = replace(cand, image_url=outcome.url, image_url_fallbacks=())
        resolved_candidates.append(resolved)
        outcome_by_url[outcome.url] = outcome
        if outcome.image_bytes is not None:
            images[outcome.url] = outcome.image_bytes

    deduped = dedupe(resolved_candidates, images)

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

        outcome = outcome_by_url.get(cand.image_url)
        data = outcome.image_bytes if outcome else None

        if outcome is not None and outcome.status == "blocked":
            # Distinguish a platform that deliberately refuses programmatic
            # media access from an incidental network failure. Verified
            # 5 Sep 2026: Instagram/Facebook return a tiny text/html stub
            # and TikTok returns 403 on signed URLs, identically under a
            # browser UA and a Referer — so this is not something we can
            # route around, and saying "could not fetch" alone reads as a
            # defect on our side when it is not.
            plat = platform_name(cand.image_url) or platform_name(cand.page_url)
            prerejected.append(
                (
                    cand,
                    "reject-platform-blocked",
                    f"{plat} serves media only to its own crawler, so the face "
                    "cannot be independently verified — post found but unverifiable",
                )
            )
            continue

        if outcome is not None and outcome.status == "not-image":
            # A 200 response that is not decodable as an image (e.g. an
            # HTML stub served with a 200 status). Distinct from
            # reject-no-face, which means we DID see a photo and found no
            # face in it — this candidate was never a photo to begin with.
            prerejected.append(
                (
                    cand,
                    "reject-not-an-image",
                    f"fetched {cand.image_url} successfully but it is not a "
                    "decodable image, so the face could not be verified",
                )
            )
            continue

        if data is None:
            attempted = outcome.attempted if outcome is not None else (cand.image_url,)
            if len(attempted) > 1:
                reason = (
                    f"could not fetch {attempted[0]} — tried {len(attempted)} size "
                    f"variant(s), all failed (last: {attempted[-1]})"
                )
            else:
                reason = f"could not fetch {cand.image_url}"
            prerejected.append((cand, "reject-fetch-failed", reason))
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

    best_image_bytes = None
    if match.best is not None:
        best_image_bytes = images.get(match.best.candidate.image_url)

    return PipelineResult(
        match=match,
        provider_reports=provider_reports,
        identity_signals=identity_signals,
        images_fetched=len(images),
        images_deduped=deduped_pre_fetch_count - len(deduped),
        best_image_bytes=best_image_bytes,
    )
