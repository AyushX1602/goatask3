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
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from pipeline.cache.http_cache import BROWSER_USER_AGENT, HttpCache, get_http_cache
from pipeline.cache.urlguard import UnsafeUrlError, assert_safe_url, safe_fetch
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
from pipeline.verify.resolver import resolve_page_to_image_url


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
    # Real, measured fetch/decode observations for every candidate that
    # was fetched, keyed by the RESOLVED image_url (matches
    # ScoredCandidate.candidate.image_url after the rewrite in this
    # function). Consumed by audit/run_log.py to replace a bare `—` in the
    # diagnostics table with actual numbers for structurally-unscoreable
    # rows (6 Sep 2026, owner instruction). Never fabricated: a candidate
    # never fetched at all (e.g. reject-no-image, where no URL existed to
    # try) simply has no entry here.
    candidate_diagnostics: dict[str, FetchDiagnostics] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchDiagnostics:
    """Real, measured observations about one fetch attempt — never a
    fabricated confidence value. Exists specifically to replace a bare `—`
    in the diagnostics table with something a viewer can actually check
    (6 Sep 2026, owner instruction: "numbers ... instead of --"). There is
    no cosine similarity to show for a row where no face was ever
    embedded, and putting one there would be worse than a dash — it would
    look like evidence and be fabricated. This is the honest alternative:
    what we actually observed, in numbers.
    """
    http_status: int | None = None
    content_type: str | None = None
    content_bytes: int | None = None
    image_width: int | None = None
    image_height: int | None = None
    faces_found: int = 0
    largest_face_px: float | None = None
    routes_tried: int = 1  # size variants + resolver attempts, see attempted


def _fetch_image(
    http: HttpCache, url: str, timeout: float = 12.0
) -> tuple[bytes | None, FetchDiagnostics, UnsafeUrlError | None]:
    if not url:
        return None, FetchDiagnostics(), None
    try:
        # Browser UA for candidate image fetches (not our own API calls) —
        # see http_cache.BROWSER_USER_AGENT's docstring. Measured to matter:
        # generic sites (news CDNs, university pages) block a self-
        # describing bot UA with no reason to block a browser.
        # safe_fetch enforces assert_safe_url, redirect hop verification,
        # and streaming size cap.
        content, status_code, content_type = safe_fetch(
            http,
            url,
            timeout=timeout,
            headers={"User-Agent": BROWSER_USER_AGENT},
            is_image=True,
        )
        diag = FetchDiagnostics(
            http_status=status_code,
            content_type=content_type,
            content_bytes=len(content) if content else 0,
        )
        if status_code < 200 or status_code >= 300:
            return None, diag, None
        return content, diag, None
    except UnsafeUrlError as e:
        return None, FetchDiagnostics(), e
    except Exception:
        return None, FetchDiagnostics(), None


def _face_size_px(face) -> float:
    x1, y1, x2, y2 = face.bbox
    return float(min(x2 - x1, y2 - y1))


def _score_one_candidate(
    detector: FaceDetector,
    embedder: FaceEmbedder,
    probe_vec: np.ndarray,
    image_bytes: bytes,
    min_face_px: int,
) -> tuple[float | None, int, str | None, float | None]:
    """Returns (best_score_or_None, faces_found, prereject_reason_or_None,
    largest_face_px_or_None).

    A prereject_reason means this candidate must not enter matcher's
    threshold/margin logic (design.md 1.7: too-small faces produce
    unreliable scores, not weak ones).

    largest_face_px is a REAL measured observation (6 Sep 2026, owner
    instruction: numbers instead of a bare dash) — it is reported even
    when the face is too small to score, which is exactly the case where
    a viewer most wants to know how close it came.

    image_bytes must already be known to decode as an image — callers are
    responsible for distinguishing "not an image" from "no face in the
    image" (see _classify_fetch below); conflating the two produces the
    misleading `reject-no-face` observed live on Instagram/Facebook
    lookaside URLs, which actually return an HTML stub, not a photo.
    """
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None, 0, None, None  # not a prereject — goes through as reject-no-face

    faces = detector.detect(img)
    if not faces:
        return None, 0, None, None

    largest_px = max(_face_size_px(f) for f in faces)

    usable = [f for f in faces if quality_passes(f, min_face_px)[0]]
    if not usable:
        return None, len(faces), "reject-face-too-small", largest_px

    best = -1.0
    for f in usable:
        crop = align(img, f.kps5)
        emb = embedder.embed(crop)
        best = max(best, float(np.dot(emb.vec, probe_vec)))
    return best, len(usable), None, largest_px


@dataclass(frozen=True)
class _VariantOutcome:
    status: str  # "ok" | "blocked" | "fetch-failed" | "not-image" | "unsafe"
    url: str
    image_bytes: bytes | None = None
    decoded: np.ndarray | None = None
    attempted: tuple[str, ...] = ()
    diagnostics: FetchDiagnostics = field(default_factory=FetchDiagnostics)
    unsafe_cause: str | None = None
    unsafe_detail: str | None = None


def _classify_fetch(http: HttpCache, url: str) -> _VariantOutcome:
    """Fetches one candidate URL and classifies what we got, so a 200
    response carrying an HTML stub (Instagram/Facebook lookaside URLs,
    verified live 5 Sep 2026) is never conflated with "no face detected",
    and unsafe SSRF attempts are rejected cleanly.
    """
    try:
        assert_safe_url(url)
    except UnsafeUrlError as e:
        return _VariantOutcome(
            "unsafe",
            url,
            diagnostics=FetchDiagnostics(),
            unsafe_cause=e.cause,
            unsafe_detail=str(e),
        )

    data, diag, unsafe_err = _fetch_image(http, url)
    if unsafe_err is not None:
        if unsafe_err.cause == "not-an-image":
            if is_media_blocked(url):
                return _VariantOutcome("blocked", url, diagnostics=diag)
            return _VariantOutcome("not-image", url, diagnostics=diag)
        return _VariantOutcome(
            "unsafe",
            url,
            diagnostics=diag,
            unsafe_cause=unsafe_err.cause,
            unsafe_detail=str(unsafe_err),
        )

    if data is None:
        if is_media_blocked(url):
            return _VariantOutcome("blocked", url, diagnostics=diag)
        return _VariantOutcome("fetch-failed", url, diagnostics=diag)

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        # Fetch succeeded (2xx) but the bytes are not a decodable image —
        # e.g. Instagram/Facebook return a tiny text/html stub with a 200
        # status. This is a distinct, honest reject reason from
        # reject-no-face, which means "we saw a photo, no face in it".
        if is_media_blocked(url):
            return _VariantOutcome("blocked", url, image_bytes=data, diagnostics=diag)
        return _VariantOutcome("not-image", url, image_bytes=data, diagnostics=diag)

    diag = replace(diag, image_width=img.shape[1], image_height=img.shape[0])
    return _VariantOutcome("ok", url, image_bytes=data, decoded=img, diagnostics=diag)


def _resolve_candidate_image(http: HttpCache, cand: Candidate) -> _VariantOutcome | None:
    """Walks image_url + image_url_fallbacks (largest-first) and returns
    the first variant that decodes as an actual image, stopping early
    when a platform-blocked verdict is reached (retrying a different size
    of a domain that refuses programmatic access cannot help).
    """
    urls = [cand.image_url, *cand.image_url_fallbacks]
    urls = [u for u in urls if u]

    tried: list[str] = []
    last_non_ok: _VariantOutcome | None = None
    for url in urls:
        tried.append(url)
        outcome = _classify_fetch(http, url)
        outcome = replace(
            outcome,
            attempted=tuple(tried),
            diagnostics=replace(outcome.diagnostics, routes_tried=len(tried)),
        )
        if outcome.status == "ok":
            return outcome
        if outcome.status in ("blocked", "unsafe"):
            return outcome  # no point trying fallbacks if blocked or unsafe
        last_non_ok = outcome

    if last_non_ok is not None and last_non_ok.status in ("blocked", "unsafe"):
        return last_non_ok

    if not cand.page_url:
        return last_non_ok

    try:
        assert_safe_url(cand.page_url)
    except UnsafeUrlError as e:
        if last_non_ok is not None:
            return last_non_ok
        return _VariantOutcome(
            "unsafe",
            cand.page_url,
            attempted=tuple(tried) + (f"unsafe-page:{cand.page_url}",),
            unsafe_cause=e.cause,
            unsafe_detail=str(e),
        )

    cascade = resolve_page_to_image_url(http, cand.page_url)
    cascade_route_names = ",".join(cascade.routes_tried) or "none"
    if cascade.image_url:
        tried.append(f"{cascade.image_url} (via {cascade_route_names})")
        outcome = _classify_fetch(http, cascade.image_url)
        return replace(outcome, attempted=tuple(tried))

    # Cascade found nothing either. Record that routes WERE tried, even
    # though none of them produced a URL to fetch — this is what lets a
    # reject-fetch-failed / reject-no-image message say "resolver tried
    # opengraph,oembed" instead of implying nothing was ever attempted.
    resolver_note = (f"resolver:{cascade_route_names}",)
    if last_non_ok is not None:
        # There WAS an original image URL and it failed; keep url pointing
        # at that original URL (never the page URL — that produces the
        # exact "downloads HTML, misreports as no-face" bug this module's
        # docstring already documents once).
        return replace(last_non_ok, attempted=tuple(tried) + resolver_note)
    # No original image URL AND the cascade found nothing. url MUST stay
    # empty — never cand.page_url — so downstream's `if not
    # cand.image_url` (run_pipeline) correctly reports reject-no-image
    # rather than silently treating the page URL as an image URL.
    return _VariantOutcome("fetch-failed", "", attempted=tuple(tried) + resolver_note)


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
    expand_profiles: bool = True,
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
    with_urls = [c for c in all_candidates if c.image_url or c.image_url_fallbacks or c.page_url]
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
    candidate_diagnostics: dict[str, FetchDiagnostics] = {}

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
        if outcome is not None:
            candidate_diagnostics[cand.image_url] = outcome.diagnostics

        if outcome is not None and outcome.status == "unsafe":
            cause = outcome.unsafe_cause or "internal-address"
            detail = outcome.unsafe_detail or "SSRF/safety check failed"
            if cause == "not-an-image":
                prerejected.append(
                    (
                        cand,
                        "reject-not-an-image",
                        f"fetched {cand.image_url} successfully but it is not a "
                        "decodable image, so the face could not be verified",
                    )
                )
            else:
                prerejected.append(
                    (
                        cand,
                        "reject-unsafe-url",
                        f"unsafe URL ({cause}): {detail}",
                    )
                )
            continue

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
            # attempted may end with a "resolver:<routes>" marker appended
            # by _resolve_candidate_image when the size-variant walk was
            # exhausted and the page-resolver cascade (verify/resolver.py)
            # was also tried. Split that out so the message never calls a
            # resolver route a "size variant" — those are a genuinely
            # different recovery mechanism and conflating them in the
            # wording is exactly the kind of inaccurate-but-plausible
            # message R-24 forbids.
            size_variants = [a for a in attempted if not a.startswith("resolver:")]
            resolver_marker = next((a for a in attempted if a.startswith("resolver:")), None)

            parts = [f"could not fetch {size_variants[0] if size_variants else cand.image_url}"]
            if len(size_variants) > 1:
                parts.append(
                    f"tried {len(size_variants)} size variant(s), all failed "
                    f"(last: {size_variants[-1]})"
                )
            if resolver_marker:
                routes = resolver_marker.removeprefix("resolver:")
                parts.append(
                    f"page-resolver cascade also tried ({routes}), found nothing usable"
                    if routes and routes != "none"
                    else "page-resolver cascade found no applicable route"
                )
            reason = " — ".join(parts)
            prerejected.append((cand, "reject-fetch-failed", reason))
            continue

        score, faces_found, prereject_reason, largest_face_px = _score_one_candidate(
            detector, embedder, probe_vec, data, min_face_px
        )
        if outcome is not None:
            candidate_diagnostics[cand.image_url] = replace(
                outcome.diagnostics, faces_found=faces_found, largest_face_px=largest_face_px
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

    # T2.6 / R-28: Profile expansion, post-threshold and face-gated
    if expand_profiles and match.best is not None:
        from pipeline.search.expand import expand_verified_candidates

        verified_cands = [match.best.candidate] + [
            r.candidate for r in match.all_scored if r.decision == "corroborating"
        ]

        def _get_html(url: str) -> tuple[bool, bytes]:
            try:
                resp = http.get(url, timeout=5.0)
                return getattr(resp, "ok", False), getattr(resp, "content", b"")
            except Exception:
                return False, b""

        expanded = expand_verified_candidates(verified_cands, http_get_fn=_get_html, http=http)
        if expanded:
            for exp_cand in expanded:
                if exp_cand.origin == "linked" or not exp_cand.image_url:
                    prerejected.append(
                        (
                            exp_cand,
                            "linked-claim",
                            "claimed profile link on verified page (unscored)",
                        )
                    )
                else:
                    outcome = _resolve_candidate_image(http, exp_cand)
                    if outcome.image_bytes is not None:
                        images[outcome.url] = outcome.image_bytes
                        resolved_cand = replace(exp_cand, image_url=outcome.url)
                        score, faces_found, prereject_reason, largest_face_px = _score_one_candidate(
                            detector, embedder, probe_vec, outcome.image_bytes, min_face_px
                        )
                        if outcome is not None:
                            candidate_diagnostics[resolved_cand.image_url] = outcome.diagnostics
                        if prereject_reason:
                            prerejected.append(
                                (
                                    resolved_cand,
                                    "linked-claim",
                                    f"face verification unavailable ({prereject_reason}), preserved as linked claim",
                                )
                            )
                        else:
                            scored.append((resolved_cand, score, faces_found))
                    else:
                        prerejected.append(
                            (
                                exp_cand,
                                "linked-claim",
                                "media unavailable on expanded profile, preserved as linked claim",
                            )
                        )

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
        candidate_diagnostics=candidate_diagnostics,
    )
