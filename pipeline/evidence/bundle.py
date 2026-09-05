"""Evidence bundle assembly. See design.md 4.2.

Produces the exact structure that gets canonicalised and hashed. This is
the ONLY place a bundle is constructed — a future chain/ module must never
build its own dict, or drift between "what we anchored" and "what we
verify against" becomes possible (the classic hash-anchoring demo failure,
design.md 4.1).

schema_version is frozen once this lands in a committed sample run
(rules.md R-02). Any structural change after that requires a version bump,
not an in-place edit.

**v2 (5 Sep 2026)** — three additions, bumped together deliberately BEFORE
any sample run under G3 is committed, since a second bump after that would
mean regenerating every committed bundle and its anchored on-chain hash:

  - `post.content_kind` — "post" | "profile" | "unknown", never null. The
    brief asks for a "social media post"; a profile page is not one. See
    verify/allowlist.content_kind for why profiles are accepted (rather
    than dropped) and reported honestly instead of overclaimed.
  - `match.image_phash` — 64-bit perceptual hash (16 hex chars) of the
    SAME bytes hashed into image_sha256, using the identical `imagehash`
    library and Hamming-distance convention as verify/dedupe.py, so a
    later re-fetch check's "perceptually identical" verdict means the
    same thing here as it does during deduplication.
  - `match.verified_against` — "platform_origin" | "platform_api" |
    "platform_embed" | "search_engine_cache" | "unknown". Computed here
    from the HOST of the exact URL that was fetched
    (`best.candidate.image_url`), not accepted as a caller-supplied
    default — a default that nobody re-derives from the real URL is
    exactly the kind of value R-24 exists to forbid (it looks like data
    but isn't checked against anything). See `_classify_verified_against`.
  - `match.match_kind` — mirrors `Candidate.match_kind` ("full" | "partial"
    | "similar" | "page" | "unknown"), the provider's own claim about how
    confident it is this is the SAME image. R-03: diagnostic only, never
    part of the accept decision, which has already happened by the time
    this bundle is built.
  - `post.metadata_source` — "bluesky_appview" when post_meta came from
    the Bluesky provider, else null. Other providers (GCV/SerpApi) supply
    no post metadata today, so this is honestly null rather than guessed.

  Fixes a real defect found in every previously anchored run: this module
  used to read `post_meta.get("image_sha256", "")`, but no provider ever
  wrote that key, so `match.image_sha256` was ALWAYS the empty string, and
  `chain/evm.py` silently zero-filled it on-chain (`imageHash` = 64 zeros
  in every anchored record). `image_bytes` is now a required, explicit
  argument to build_evidence() — the bytes that were actually scored, with
  no fallback and no default. Passing empty/None bytes raises rather than
  producing a bundle with a meaningless hash field.
"""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import imagehash
from PIL import Image

from pipeline.evidence.canonical import canonical_bytes, evidence_hash_hex
from pipeline.evidence.commitment import face_commitment_hex
from pipeline.face.types import Embedding, LivenessResult
from pipeline.verify.allowlist import content_kind
from pipeline.verify.matcher import MatchResult

SCHEMA_VERSION = 3

# match.verified_against — every value that can be emitted. Kept as a
# frozenset so a future addition to _classify_verified_against is forced
# to also update this set, and the "unknown" test below stays meaningful.
VERIFIED_AGAINST_VALUES = frozenset(
    {"platform_origin", "platform_api", "platform_embed", "search_engine_cache", "unknown"}
)

# Hosts through which Google serves ITS OWN cached copy of an image rather
# than the platform's origin. Suffix-matched against the fetched URL's
# hostname.
_SEARCH_ENGINE_CACHE_HOST_SUFFIXES = (
    "gstatic.com",       # encrypted-tbn*.gstatic.com and others
    "googleusercontent.com",  # lh*.googleusercontent.com
)


def _classify_verified_against(image_url: str) -> str:
    """Derives verified_against from the HOST of the exact URL that was
    fetched and scored, rather than trusting a caller-supplied label. A
    label nobody re-derives from the real URL is exactly the kind of
    value R-24 exists to forbid — it reads as data but was never checked.

    Only "search_engine_cache" vs "platform_origin" are distinguishable
    from the URL alone. "platform_api" / "platform_embed" require the
    CALLER to know how the bytes were obtained (e.g. an oEmbed thumbnail
    vs a raw CDN fetch) — see the `verified_against` override parameter
    on build_evidence(), which callers use for those two cases. This
    function is the DEFAULT when no override is supplied.
    """
    host = (urlparse(image_url).hostname or "").lower()
    if not host:
        return "unknown"
    if any(host == suf or host.endswith("." + suf) for suf in _SEARCH_ENGINE_CACHE_HOST_SUFFIXES):
        return "search_engine_cache"
    return "platform_origin"


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
    image_bytes: bytes | None,
    verified_against: str | None = None,
    captured_at: int | None = None,
) -> EvidenceBundle:
    """Builds and hashes an evidence bundle for an ACCEPTed match.

    Raises ValueError if match.verdict != "MATCH" — there is nothing to
    anchor for a NO_MATCH run (rules.md R-16: NO_MATCH is a valid outcome,
    but it produces no evidence bundle, only an audit log entry).

    image_bytes: the ACTUAL bytes that were fetched and scored for the
    winning candidate (see verify/pipeline_run.py's PipelineResult.
    best_image_bytes). Required and must be non-empty — there is no
    fallback. A match with no provable image bytes is a bug upstream, and
    it must fail loudly here rather than anchor a record whose headline
    hash field is meaningless (see the module docstring for the defect
    this replaced: every previously anchored run had image_sha256 == "").

    verified_against: optional override. When omitted (the normal case),
    it is DERIVED from the host of best.candidate.image_url via
    _classify_verified_against — never a hardcoded default, since an
    unverified default is exactly the kind of value R-24 forbids. Pass an
    explicit value only when the caller has out-of-band knowledge the URL
    alone can't provide: "platform_api" (bytes came from an official
    API/oEmbed thumbnail) or "platform_embed" (a public embed surface).
    Must be one of bundle.VERIFIED_AGAINST_VALUES if supplied.
    """
    if match.verdict != "MATCH" or match.best is None:
        raise ValueError(
            "build_evidence requires an accepted match; NO_MATCH runs are "
            "recorded in the audit log only (R-16), never as an evidence bundle"
        )

    if not image_bytes:
        raise ValueError(
            "build_evidence requires non-empty image_bytes — the bytes actually "
            "scored for the winning candidate. No fallback exists deliberately: "
            "a bundle whose image_sha256 cannot be computed must not be built, "
            "let alone anchored (see the module docstring)."
        )

    if verified_against is not None and verified_against not in VERIFIED_AGAINST_VALUES:
        raise ValueError(
            f"verified_against={verified_against!r} is not one of {sorted(VERIFIED_AGAINST_VALUES)}"
        )

    best = match.best
    runner_up_score = match.runner_up.score if match.runner_up else None
    margin = best.score - (runner_up_score if runner_up_score is not None else -1.0)

    post_meta = best.candidate.post_meta or {}

    resolved_verified_against = verified_against or _classify_verified_against(best.candidate.image_url)

    # Bluesky is the only provider that populates post_meta today (GCV and
    # SerpApi supply none). Honestly null rather than guessed for anything
    # else — see the module docstring's v2 changelog entry.
    metadata_source = "bluesky_appview" if best.candidate.source == "bluesky" and post_meta else None

    ext = detect_image_extension(image_bytes)
    img_sha = image_sha256(image_bytes)
    img_phash = image_phash(image_bytes)

    data = {
        "schema_version": SCHEMA_VERSION,
        "artifacts": [
            {
                "path": f"match_image{ext}",
                "sha256": img_sha,
                "phash": img_phash,
            }
        ],
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
            "image_sha256": img_sha,
            "image_phash": img_phash,
            "verified_against": resolved_verified_against,
            "match_kind": best.candidate.match_kind or "unknown",
            "provider": best.candidate.source,
            "score_bps": _score_bps(best.score),
            "margin_bps": _score_bps(margin),
            "threshold_bps": _score_bps(match.threshold),
        },
        "post": {
            "platform": post_meta.get("platform", best.candidate.source),
            "content_kind": content_kind(best.candidate.page_url, best.candidate.image_url),
            "author_handle": post_meta.get("author_handle", ""),
            "author_display": post_meta.get("author_display", ""),
            "text": post_meta.get("text", ""),
            "published_at": _to_unix_seconds(post_meta.get("published_at")),
            "permalink": post_meta.get("permalink", best.candidate.page_url),
            "metadata_source": metadata_source,
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


def detect_image_extension(image_bytes: bytes) -> str:
    """Sniffs the real format of image_bytes via PIL rather than trusting
    a URL's extension (URLs frequently lie: a JPEG served from a path
    ending in no extension at all, or a signed CDN URL with none). Used
    when saving runs/<id>/match_image.<ext> so the run directory is
    self-contained (T0.2). Falls back to ".jpg" if PIL cannot identify
    the format — matches build_evidence's own tolerance, since a failure
    here must never block writing the rest of the run.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        fmt = (img.format or "JPEG").lower()
        return {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}.get(fmt, f".{fmt}")
    except Exception:
        return ".jpg"


def image_phash(image_bytes: bytes) -> str:
    """64-bit perceptual hash, 16 hex characters, using the SAME library
    and convention as verify/dedupe.py's max_hamming comparison, so a
    future re-fetch check's "perceptually identical" verdict (Hamming
    distance <= dedupe's max_hamming) means the same thing here as it
    does during candidate deduplication.

    Raises ValueError if the bytes are not decodable as an image — this
    must never silently return an empty or placeholder hash, per the same
    reasoning as image_sha256. By the time this is called from
    build_evidence(), the bytes have already been through cv2.imdecode
    successfully upstream (verify/pipeline_run.py), so a failure here
    would mean OpenCV and Pillow disagree on decodability, which is worth
    surfacing loudly rather than masking.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return str(imagehash.phash(img))
    except Exception as e:
        raise ValueError(f"image_bytes could not be perceptually hashed: {e}") from e
