# Design — face-chain-verify

frontend use vercel design.md for clean look and can also use colors form hhgoa.com use html for fe and backend you have decided ig



**Companion docs:** [`prd.md`](./prd.md) · [`architecture.md`](./architecture.md) · [`rules.md`](./rules.md) · [`phases.md`](./phases.md)

Algorithms, exact preprocessing, function signatures, and data schemas. Structure lives in `architecture.md`; this document is the implementation contract.

Confidence markers used below:
- **[V]** verified against documentation during research
- **[A]** assumed, must be confirmed live before the dependent phase starts (tracked in `memory.md`)

---

## 1. Face core

### 1.1 Shared types — `pipeline/face/types.py`

```python
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class DetectedFace:
    bbox: tuple[float, float, float, float]   # x1, y1, x2, y2 in source pixels
    kps5: np.ndarray                          # (5,2) float32, ORDERED, see 1.3
    det_score: float

@dataclass(frozen=True)
class Embedding:
    vec: np.ndarray            # (512,) float32, ALWAYS L2-normalised  (R-07)
    model: str                 # "w600k_r50"
    aligned_png_sha256: str    # provenance: which crop produced this

@dataclass(frozen=True)
class LivenessResult:
    passed: bool
    score: float
    label: str                 # "live" | "print" | "replay" | "unknown"
```

### 1.2 Detection — `detect.py`

Model: `face_detection_yunet_2023mar.onnx`, loaded through `cv2.FaceDetectorYN`. OpenCV performs anchor decoding and NMS internally, which is the entire reason for choosing it over SCRFD.

**[V]** YuNet returns one row per face with 15 values:

```
idx  0  1  2  3   4    5    6    7    8    9    10   11   12   13   14
     x  y  w  h   x_re y_re x_le y_le x_nt y_nt x_rcm y_rcm x_lcm y_lcm score
```

where `re`/`le` are eyes, `nt` is nose tip, `rcm`/`lcm` are mouth corners.

```python
def detect(bgr: np.ndarray,
           score_threshold: float = 0.85,
           nms_threshold: float = 0.3,
           top_k: int = 50) -> list[DetectedFace]
```

Behaviour:
- `setInputSize((w, h))` must be called whenever the frame size changes, or detection silently returns nothing.
- Returns faces sorted by `bbox` area, descending. The probe uses the largest face; candidate images check **all** faces (a group photo may contain the subject).
- Empty list is a valid result, not an error.

### 1.3 Landmark ordering — the subtle bug to avoid

The ArcFace template expects, by **image x-position**: left eye, right eye, nose, left mouth corner, right mouth corner. Naming conventions for "left" and "right" differ between libraries (subject's left vs viewer's left), and getting this backwards produces a horizontally mirrored alignment that silently degrades accuracy by a large margin without ever raising an error.

Do not trust the names. Order geometrically:

```python
def canonical_kps(raw: np.ndarray) -> np.ndarray:
    """raw: (5,2) as [eye_a, eye_b, nose, mouth_a, mouth_b] in any L/R convention."""
    eyes   = sorted([raw[0], raw[1]], key=lambda p: p[0])   # ascending x
    mouths = sorted([raw[3], raw[4]], key=lambda p: p[0])
    return np.array([eyes[0], eyes[1], raw[2], mouths[0], mouths[1]], dtype=np.float32)
```

Assert in a unit test that a horizontally flipped input produces an embedding with cosine > 0.9 against the unflipped one. If it does not, ordering is wrong.

### 1.4 Alignment — `align.py`

**[V]** Standard ArcFace 5-point destination template for a 112×112 output:

```python
ARCFACE_TEMPLATE_112 = np.array([
    [38.2946, 51.6963],   # left eye
    [73.5318, 51.5014],   # right eye
    [56.0252, 71.7366],   # nose tip
    [41.5493, 92.3655],   # left mouth corner
    [70.7299, 92.2041],   # right mouth corner
], dtype=np.float32)
```

```python
def align(bgr: np.ndarray, kps5: np.ndarray, size: int = 112) -> np.ndarray:
    """Similarity transform (rotation + uniform scale + translation) — NOT a
    full affine. Estimated with cv2.estimateAffinePartial2D, then warpAffine."""
```

Rules:
- Use `cv2.estimateAffinePartial2D(kps5, template, method=cv2.LMEDS)`. A full affine (`estimateAffine2D`) introduces shear and degrades ArcFace.
- For `size != 112`, scale the template by `size / 112.0`.
- `cv2.INTER_LINEAR`, `borderValue=0`.
- **Never embed an unaligned crop** (R-08). Alignment is not optional preprocessing; ArcFace was trained on this exact geometry.

### 1.5 Embedding — `embed.py`

Model: `w600k_r50.onnx` (ArcFace R50, trained on WebFace600K), from the `buffalo_l` pack.

**[V]** Preprocessing, matching InsightFace's own ONNX wrapper exactly:

| Step | Value |
|---|---|
| Input shape | `(1, 3, 112, 112)` NCHW float32 |
| Channel order | **RGB** — input crops are BGR, so swap |
| Scale | `1 / 127.5` |
| Mean subtracted | `127.5` per channel |
| Effective range | `[-1, 1]` |

Equivalent to `cv2.dnn.blobFromImage(crop, 1.0/127.5, (112,112), (127.5,127.5,127.5), swapRB=True)`.

```python
def embed(aligned_bgr: np.ndarray) -> Embedding:
    """Returns an L2-NORMALISED 512-d vector. Normalisation happens here, once,
    so every downstream consumer can assume it (R-07)."""
```

Because vectors are unit-length, cosine similarity is a plain dot product:

```python
def cosine(a: Embedding, b: Embedding) -> float:
    return float(np.dot(a.vec, b.vec))     # in [-1, 1]
```

**Batching:** for the Bluesky crawl, feed `(N, 3, 112, 112)` batches of 32. onnxruntime handles a dynamic batch axis for this model **[A]** — confirm in Phase 3 and fall back to a loop if the axis is fixed.

### 1.6 Liveness — `liveness.py`

Silent-Face anti-spoof, ONNX. Input is a **loosely** cropped face — a 2.7× expansion of the detection box, not the tight aligned crop. Output is a 3-class softmax; class 1 is live.

```python
def check(bgr: np.ndarray, face: DetectedFace,
          threshold: float = 0.7) -> LivenessResult
```

**Three states, never two (D-20).** An anti-spoof model detects print and
replay artifacts in a *camera capture*. A clean uploaded JPEG of a real
person will typically score "live" while proving nothing about physical
presence, so reporting `LIVE` for an upload would be a false assurance.

| Input | `passed` | `label` | UI |
|---|---|---|---|
| webcam, score ≥ threshold | `True` | `live` | `LIVE`, green |
| webcam, score < threshold | `False` | `spoof` | `SPOOF`, red, blocks the run |
| **uploaded file** | `True` | **`not_applicable`** | `N/A — provenance unverified`, neutral grey |

`passed=True` on the upload path means "does not block", not "verified live".
Never render the upload state in the same visual style as a real `live` pass.

Liveness is likewise skipped for candidate images downloaded from the web —
they are photographs of photographs by definition.

Degradation: if the model file is absent, return
`LivenessResult(True, 0.0, "unknown")` and log a warning. Missing liveness
must not break the pipeline.

### 1.7 Quality gate — `face/quality.py`

Derived from measurement (`architecture.md` §2a), not intuition.

```python
MIN_FACE_PX = 50   # config-overridable via MIN_FACE_PX

def passes(face: DetectedFace) -> tuple[bool, str]:
    """Returns (ok, reason). Faces below MIN_FACE_PX produce unreliable
    embeddings: measured cosine-vs-reference falls to 0.868 at 31px and
    0.767 at 20px, versus 0.954 at 43px."""
```

Applied in two places, using the same function:
- **probe**: a too-small face is a hard error with a clear message ("move closer to the camera" / "supply a higher-resolution image")
- **candidates**: a too-small face is `reject-face-too-small`, logged with its pixel size, never silently dropped

No blur gate: measured as unnecessary (cosine still 0.904 under heavy blur).

### 1.8 Head crop as a search query — `face/headcrop.py`  ·  built, disabled by default (R-27)

Distinct from everything above: this crop is never an input to `embed()`.
It exists purely as an alternate **search query representation**, built
after a competitor measured a probe in distinctive clothing returning
almost entirely garment listings (the reverse-image engine locked onto the
outfit, not the face).

```python
def create_head_crop(bgr: np.ndarray, face: DetectedFace,
                      target_size: int = 512) -> np.ndarray
```

- Bounding box grown asymmetrically: 55% up, 30% side, 18% down. Growing
  downward mostly adds collar and shoulders — exactly the clothing this
  exists to exclude.
- Padded to square, soft elliptical mask, composited onto neutral mid-grey
  `(128,128,128)` — deliberately not white, since a white background biases
  reverse-image engines toward catalogue/stock imagery.

Per R-27, a new search representation ships only if measured to beat the
current one on our own fixtures. `use_head_crop` therefore defaults to
`False` in `search/image_prep.py::prepare_search_image()` until
`scripts/probe_query_representation.py` has been run against real fixtures
and the result — win or lose — is recorded in `memory.md`. As of this
writing that measurement has not yet been executed; do not assume the flag
should be flipped without running it.

---

## 2. Search layer

### 2.1 Orchestrator — `search/orchestrator.py`

```python
def gather(aligned_png: bytes, probe: Embedding,
           providers: list[SearchProvider],
           timeout_s: float = 45.0) -> tuple[list[Candidate], list[ProviderReport]]
```

- `ThreadPoolExecutor`, one worker per provider. Providers are IO-bound.
- Providers where `available()` is False are skipped without error (enables the zero-key quickstart).
- A provider that raises is recorded in its `ProviderReport` and contributes zero candidates. **No provider can abort a run** (R-14).
- Per-provider timeout, so one slow source cannot stall the demo.

### 2.1a Web detection provider — `search/web_detect.py`  ·  **PRIMARY**

**Revised 7 Sep 2026.** Everything marked **[A]** below is now resolved and
live, not assumed — this section previously described SerpApi as the sole
proven-live backend with GCV as an unexercised assumption, which was true in
early September but is no longer the state of the code.

One call reaches every platform Google has indexed. This replaces the
per-platform integration layer entirely (`architecture.md` §5, "Rejected").

Two interchangeable backends behind one provider, both **[V] proven live**:

| Backend | Endpoint | Free tier | Needs a public URL? |
|---|---|---|---|
| `gcv` (default first) | `vision.googleapis.com/v1/images:annotate`, feature `WEB_DETECTION` | ~1,000/mo | No — accepts raw base64 |
| `serpapi` | `serpapi.com/search?engine=google_lens&url=<img>` **or** `&image_id=<id>` | ~100/mo | With a URL, yes; with `SEARCH_LENS_UPLOAD=1`, the head crop is uploaded directly to SerpApi (`POST /image`) — no public host involved (§2.1c) |

**Backend selection is no longer "pick one and commit."** `_resolve_backend()`
returns an *ordered list*; `search()` tries the first, and — only if the
result carries no real identity signal (§2.1b) — escalates to the second and
merges. This replaced a real, measured defect: two live runs
(`runs/2026-09-05T22-52-19Z`, `runs/2026-09-05T22-53-02Z`) showed GCV
returning 20/20 candidates that were all `visuallySimilarImages` lookalikes
on unrelated university faculty pages, with SerpApi Lens never running at
all because the two backends were mutually exclusive at construction time.

**[V] Verified SerpApi response shape** (`scripts/probe_lens.py`, cached to
`.cache/lens_probe/`). Measured on one real face photo:

```
visual_matches:   59      organic_results: 9      related_content: 1
related_content[0].query == "Barack Obama"     <- identity signal
11 of 68 links on social domains: facebook, instagram, youtube, x, reddit
```

Fields we consume:

| Field | Use |
|---|---|
| `visual_matches[].link` | `Candidate.page_url` |
| `visual_matches[].image` (preferred) / `.thumbnail` (fallback) | `Candidate.image_url` — the full-resolution field is preferred over the thumbnail because small thumbnails routinely fail the 50px face-quality gate; which field won is recorded in `raw["image_field_used"]` |
| `visual_matches[].title` | audit log context, and a secondary identity signal |
| `organic_results[].link` / `.title` | additional candidates |
| `related_content[].query` | recorded as the identity signal Google inferred. **Context only — never used in the accept decision** (R-03) |

GCV backend maps to the same `Candidate` shape from `webDetection.pagesWithMatchingImages`, `.fullMatchingImages`, `.partialMatchingImages`, `.visuallySimilarImages`, with `webEntities[].description` as the identity signal, and `match_kind` set to `"full"`/`"partial"`/`"similar"`/`"page"` accordingly (see `search/base.py`'s `Candidate.match_kind`).

**Known gotchas, resolved and current.**
- GCV `WEB_DETECTION` sometimes returns only `webEntities`/`bestGuessLabels` with the image-URL arrays absent. Handled as a valid zero-candidate result, never as an error.
- Roughly 16% of returned links were on social domains in the one measured sample. Expect most candidates to be rejected by the allowlist — that is normal and must be visible in the audit log, not hidden.
- GCV alone can return real candidates that carry no genuine identity signal at all (see §2.1b) — this is the case escalation exists to catch.

**Evidence-integrity note.** We verify the face against the bytes served at
fetch time, not necessarily the live image on the platform. The evidence
bundle records the exact `image_url` fetched, its `sha256` and `phash`, and
`match.verified_against` (`"platform_origin"` | `"search_engine_cache"` |
`"platform_api"` | `"platform_embed"` | `"unknown"`), derived from the
fetched URL's host rather than trusted as a caller-supplied default (R-24).
This is a real, disclosed limitation, not papered over.

### 2.1b Quality-triggered backend escalation

```python
GENERIC_ENTITIES = frozenset({
    "human", "person", "face", "photograph", "portrait",
    "chin", "forehead", "head", "smile", "hair", "eyebrow",
})

def result_carries_identity_signal(
    candidates: list[Candidate], identity_signals: list[str],
) -> bool:
    """True iff EITHER: a candidate on an allowlisted domain has
    match_kind in {"full","partial"}; OR a candidate on an allowlisted
    domain has a retrievable image_url with content_kind in {post,profile};
    OR at least one identity_signal (case-insensitive) is not in
    GENERIC_ENTITIES and has length >= 3."""
```

Deliberately **never** triggers on candidate count — that specific bug was
already fixed once before (see `memory.md` §3d), because 20 useless
candidates can look exactly like success to a count-based check. The escalation
decision is entirely about whether the provider actually claimed to
recognise something, not how many things it returned.

Backend ordering in `auto` mode depends on `SEARCH_LENS_UPLOAD` (§2.1c):
`["gcv", "serpapi"]` when it's off (the default — GCV's larger quota wins the
tie, D-28), `["serpapi", "gcv"]` when it's on (Lens becomes reachable — via
the direct head-crop upload — and often answers a more specific question
than GCV's visual-similarity fallback).

### 2.1c Public image hosting for Lens — `search/uploader.py`  ·  **off by default**

SerpApi's `google_lens` engine is documented around an image URL, but it also
exposes a direct upload endpoint — `POST https://serpapi.com/image` →
`{"image_id": ...}` — which `engine=google_lens` accepts as `image_id=`
(verified live; limits at `serpapi.com/image-api`: JPG/PNG/WebP, 500 KB max).
This module is the narrowest possible version of that requirement — and it
needs no public image host at all:

```python
def upload_crop_to_serpapi(
    jpeg_bytes: bytes, http: HttpCache | None = None, *, is_head_crop: bool,
) -> str:
    """Uploads the head crop directly to SerpApi (POST /image) and returns
    the image_id for engine=google_lens (held ~10 min on SerpApi's side —
    never a public image host). is_head_crop is keyword-only and
    STRUCTURALLY enforced: raises ValueError if False. Uploading the
    original probe photo through this function is a hard error, not a
    comment someone can ignore."""
```

Gated by `SEARCH_LENS_UPLOAD` (default `0`). A fresh clone with no `.env`
changes never uploads anything. Only `headcrop.py`'s output — the
background-removed head crop, never the full photograph — may be passed in.
The earlier variant hosted the crop on imgbb with a 5-minute expiry; it is
retired (7 Sep 2026) — one fewer API key, one fewer third-party host
touching biometric data, same escalation behaviour.

### 2.2 Bluesky provider — `search/bluesky.py`  ·  **KEYLESS FALLBACK ONLY**

Retained so the repo runs with no `.env` at all, and to demonstrate the
verifier when no key is configured. **It searches a corpus we build
ourselves, so it is not an answer to "search the web"** and the UI must
label any Bluesky-only run as a degraded, closed-corpus run
(`architecture.md` §9).

Two-phase: ingest, then query.

**Ingest.** Two possible sources, in preference order:

1. **Jetstream** **[A]** — `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post`. Emits plain JSON post events, no CBOR/CAR decoding. Strongly preferred if reachable.
2. **Public AppView polling** **[V]** — `https://public.api.bsky.app`, unauthenticated for most read endpoints. Walk `app.bsky.feed.getAuthorFeed` over a seed set of handles, and `app.bsky.feed.getFeed` for public feed generators. Slower, but no websocket dependency.

Note **[V]**: `app.bsky.feed.searchPosts` reportedly requires an app password. Keyword search is **not** needed — we filter by "has image", so avoid that endpoint entirely.

**Image URL construction [A]** — verify against one live post in Phase 3:

```
https://cdn.bsky.app/img/feed_thumbnail/plain/{did}/{blob_cid}@jpeg
```

**Permalink construction [V]:** from an AT-URI `at://{did}/app.bsky.feed.post/{rkey}` →
`https://bsky.app/profile/{handle_or_did}/post/{rkey}`

**Index.** In-memory, numpy:

```python
class FaceIndex:
    vecs: np.ndarray            # (N, 512) float32, L2-normalised, C-contiguous
    meta: list[PostRef]         # parallel array, N entries

    def query(self, probe: Embedding, k: int = 20) -> list[tuple[int, float]]:
        scores = self.vecs @ probe.vec      # (N,) — this is the whole search
        idx = np.argpartition(-scores, k)[:k]
        return sorted(((int(i), float(scores[i])) for i in idx),
                      key=lambda t: -t[1])
```

At N = 10,000 this is a 20 MB matmul: sub-millisecond. FAISS is unnecessary below ~10⁵ (see `architecture.md` §5).

Persist the index to `.cache/bluesky_index.npz` so repeated demo runs do not re-crawl, but **always print the crawl timestamp and post count** — recency is the proof of a live search.

Politeness (R-12): capped concurrency, descriptive User-Agent with a contact URL, `tenacity` exponential backoff, hard cap from `BLUESKY_CRAWL_LIMIT`.

### 2.3 (superseded)

The former "Google Lens provider" section is now §2.1a `web_detect.py`, which
covers both the SerpApi and Google Cloud Vision backends behind one provider.

### 2.4 Dedupe — `verify/dedupe.py`

The same image is served from many CDNs at many sizes. Deduplicate before spending inference on it.

```python
def dedupe(cands: list[Candidate]) -> list[Candidate]:
    """Key on (normalised URL) first, then perceptual hash of fetched bytes.
    phash Hamming distance <= 6 counts as the same image."""
```

Keeps the first occurrence, records collapsed duplicates in the audit log.

### 2.5 Allowlist — `verify/allowlist.py`

A match only counts if it sits on a public social platform. Also an ethics control (R-06).

```python
SOCIAL_ALLOW = {
    "bsky.app", "mastodon.social", "x.com", "twitter.com",
    "instagram.com", "facebook.com", "linkedin.com",
    "reddit.com", "youtube.com", "tiktok.com", "threads.net", "github.com",
}
DENY = {  # never surfaced, regardless of match score
    # dating platforms, mugshot aggregators, adult platforms
}
```

Registrable-domain comparison, not substring matching, so `evil-x.com.attacker.net` cannot pass. Off-allowlist candidates are still scored and logged — they simply cannot become the reported match. Showing them scored-then-excluded strengthens the audit trail.

Deliberately includes CDN hosts alongside platform www domains
(`pbs.twimg.com`, `licdn.com`, `preview.redd.it`) — excluding them was
measured to discard the highest-scoring verifiable evidence in real runs
(D-36). `content_kind()` in the same module classifies a matched URL as
`"post"` | `"profile"` | `"unknown"`, reporting-only per R-03.

### 2.6 Profile expansion — `search/expand.py`, `search/serp_resolve.py`  ·  **F3, added 7 Sep 2026**

Runs only after `matcher.py` has already accepted a MATCH — expansion never
gates or influences the accept decision, it only runs after one has already
been made. From the accepted candidate(s), it discovers further candidates
by three routes, each producing a distinct, honestly-labelled origin:

| origin | route | re-scored? | decision if unscored |
|---|---|---|---|
| `face` | outbound link / SERP result WITH a retrievable image | yes — re-enters `matcher.py` exactly like any primary candidate (R-28) | n/a — it is scored |
| `linked` | an explicit claim published on the already-verified page, or a SERP-recovered profile with no thumbnail | no | `linked-claim` |
| `conjecture` | a same-handle structural guess (`derive_profile_urls()` — "if the verified handle is `alice`, also try `x.com/alice`") | no | `conjecture-claim` |

`linked` and `conjecture` are deliberately different tiers, not one merged
"unscored" bucket: a `linked` claim was actually published by the verified
party, while `conjecture` is a coincidence-until-proven guess about a
same-named account elsewhere. Neither is ever counted as a match or shown
with a score (R-28).

LinkedIn and Instagram specifically go through `serp_resolve.py` rather than
being fetched directly, since both platforms block programmatic access to
their own pages (`linkedin.com/in/...` returns HTTP 999; Instagram's public
HTML is a login shell). `serp_resolve.py` runs a targeted Google search
(`site:linkedin.com/in "{handle}"`) via SerpApi and reads the profile photo
straight from Google's own SERP `thumbnail` field when one is present —
never requesting the blocked platform itself. Capped at
`EXPAND_SERP_MAX_CALLS` (default 1) per run to protect quota.

---

## 3. Matching

### 3.1 Decision rule — `verify/matcher.py`

```python
@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float | None          # None if no face found in the candidate image
    faces_found: int
    decision: str                # "ACCEPT" | "reject-<reason>"
    reason: str

@dataclass(frozen=True)
class MatchResult:
    best: ScoredCandidate | None
    runner_up: ScoredCandidate | None
    all_scored: list[ScoredCandidate]      # every candidate, for the audit log
    threshold: float
    margin_required: float
    verdict: str                 # "MATCH" | "NO_MATCH"
```

Accept only if **both** hold:

```
best.score >= MATCH_THRESHOLD
best.score - runner_up.score >= MATCH_MARGIN        # runner_up = highest scoring
                                                    # candidate from a DIFFERENT page
```

The margin rule is what kills lookalike false positives, and it is easy to narrate on camera. Runner-up must come from a different `page_url`, otherwise multiple images of the true subject on one page would suppress a correct match.

Rejection reasons, all logged verbatim (extended 7 Sep 2026 with `reject-unsafe-url` and the two expansion decision states, which are not rejections but are reported the same way — never silently dropped):

| Reason / decision | Meaning |
|---|---|
| `reject-no-face` | no face detected in the candidate image |
| `reject-face-too-small` | face below `MIN_FACE_PX`; embedding would be unreliable (§1.7) |
| `reject-below-threshold` | scored under the calibrated threshold |
| `reject-margin` | above threshold but too close to the runner-up |
| `reject-domain` | not on the social allowlist |
| `reject-duplicate` | collapsed by phash |
| `reject-fetch-failed` | image could not be downloaded |
| `reject-unsafe-url` | URL failed SSRF validation before any fetch was attempted — non-https scheme, internal/private/loopback address, unresolvable host, size cap exceeded, or magic-bytes mismatch (R-29) |
| `linked-claim` | not a rejection — an `origin="linked"` expansion candidate, unscored by design, never counted as a match (R-28) |
| `conjecture-claim` | not a rejection — an `origin="conjecture"` expansion candidate, unscored and weaker than `linked-claim` (R-28) |

**Score-band semantics**, from the §2a measurements. These are presentation
rules, not extra thresholds — the accept decision remains threshold + margin.

| Band | Meaning | UI treatment |
|---|---|---|
| ≥ threshold (0.42 provisional) | candidate match | full row, highlighted if ACCEPT |
| 0.20 – threshold | weak similarity, rejected | shown in diagnostics, plainly marked rejected |
| **0.00 – 0.20** | **statistical noise** — measured non-match ceiling was 0.074, mean+4σ = 0.193 | **never presented as a ranked suggestion** (R-21) |
| < 0.00 | unrelated | diagnostics only |

When a candidate image contains several faces, score all of them and keep the maximum.

### 3.2 Calibration — current state, corrected 7 Sep 2026

**This section previously described a benchmark-scale ROC calibration
(`verify/calibrate.py`, ~100/~100 pairs, a committed `roc.png`) as though it
were built. It is not.** `verify/calibrate.py` does not exist as a module in
the current tree, and no ROC sweep has been run. Documenting a process this
confidently while the code doesn't do it is exactly the doc-vs-code defect
category Tier 0 was created to remove — corrected here rather than left.

What is actually true, read from `pipeline/config.py::load_match_policy()`:

- `calibration/threshold.json` **is** the source of truth when present, and
  `MATCH_THRESHOLD`/`MATCH_MARGIN` are never a literal in pipeline code
  (R-09 holds).
- If that file does not exist, `load_match_policy()` returns a clearly
  marked placeholder: `threshold=0.42, margin=0.08, model="w600k_r50",
  target_fmr=0.01, is_placeholder=True`. The placeholder is not a guess —
  it is a documented default set from real measurement
  (`architecture.md` §2a: same-person cosine 0.7652, non-match ceiling
  0.1385 across 41 live-harvested negatives), and it is honestly labelled
  as provisional rather than presented as a benchmark result.
- A full benchmark-scale calibration (positive/negative pairs, a swept ROC
  curve, a declared target FMR) was evaluated and explicitly skipped — see
  `memory.md` D-29 — as out of proportion for this task's scope, not
  forgotten. `prd.md`'s known-limitations §10 item 6 discloses this
  directly: "The similarity threshold is calibrated on a small labelled
  pair set, not a benchmark-scale evaluation."

If a full calibration pass is ever built, this is its target shape (kept as
a forward design, not a claim about current code):

```json
{
  "model": "w600k_r50",
  "threshold": 0.42,
  "margin": 0.08,
  "target_fmr": 0.01,
  "measured_fmr": 0.0092,
  "measured_tpr": 0.94,
  "n_positive_pairs": 104,
  "n_negative_pairs": 118,
  "calibrated_at": "2026-09-05T00:00:00Z"
}
```

---

## 4. Evidence

### 4.1 Canonicalisation — `evidence/canonical.py`

Where hash-anchoring demos die. If serialisation drifts between anchor and verify, the hash changes and verification fails on stage.

Hard rules (R-02):
- RFC 8785 JCS, or equivalently: UTF-8, lexicographically sorted keys, no insignificant whitespace, `\n` never literal.
- **No floats anywhere.** Similarity → integer basis points. Timestamps → integer Unix seconds.
- `schema_version` mandatory.
- Frozen at the end of Phase 9. Any later change requires a version bump.

```python
def canonical_bytes(obj: dict) -> bytes
def evidence_hash(obj: dict) -> bytes           # keccak256(canonical_bytes)
def sha256_hex(data: bytes) -> str
```

CI test (R-13): `canonical_bytes(json.loads(canonical_bytes(x))) == canonical_bytes(x)`.

### 4.2 Bundle schema — now v3, updated 7 Sep 2026

**v3** adds one thing on top of v2: an explicit `artifacts[]` manifest
(§5.6), so `chain/reverify.py` can iterate a declared list of files-and-digests
instead of hardcoding `match_image.jpg` by convention. This is what makes
R-25 (re-verification recomputes every digest from disk, never reuses a
stored one) mechanically possible — see `design.md` §5.4 and
`architecture-deep-dive.md` §6 for the defect this closed.

v2 (5 Sep 2026, retained below for history) added three fields in one bump:
`post.content_kind`, `match.image_phash`, `match.verified_against`. That bump
also closed a real defect: `match.image_sha256` was previously always `""`
because no provider ever populated `post_meta["image_sha256"]`, and
`chain/evm.py` silently zero-filled the resulting on-chain `imageHash`.
`build_evidence()` now takes the actual scored image bytes as a required
argument (no fallback) and computes both hashes from them directly.

Current shape, read directly from `pipeline/evidence/bundle.py`
(`SCHEMA_VERSION = 3`):

```jsonc
{
  "schema_version": 3,
  "artifacts": [
    {"path": "match_image.jpg", "sha256": "…", "phash": "aabbccdd11223344"}
  ],
  "probe": {
    "face_commitment": "0x…",       // salted hash — NEVER the embedding (R-01)
    "liveness_passed": true,
    "liveness_label": "live",
    "captured_at": 1757000000,
    "aligned_sha256": "…"
  },
  "match": {
    "page_url": "https://x.com/…/status/…",
    "image_url": "https://pbs.twimg.com/…",
    "image_sha256": "…",             // sha256 of the ACTUAL bytes scored — required, never empty
    "image_phash": "aabbccdd11223344", // 64-bit perceptual hash, 16 hex chars, same
                                        // imagehash/Hamming convention as verify/dedupe.py
    "verified_against": "search_engine_cache", // | "platform_origin" | "platform_api"
                                                // | "platform_embed" | "unknown"
    "match_kind": "similar",         // provider's own image-identity claim, R-03 diagnostic only
    "provider": "gcv_web_detection",
    "score_bps": 9806,               // 0.9806 cosine
    "margin_bps": 3910,
    "threshold_bps": 4200
  },
  "post": {
    "platform": "x",
    "content_kind": "post",          // "post" | "profile" | "unknown" — never null.
                                      // See verify/allowlist.content_kind. Reporting only
                                      // (R-03): never affects the accept decision.
    "author_handle": "…",
    "author_display": "…",
    "text": "…",
    "published_at": 1756900000,
    "permalink": "https://x.com/…",
    "metadata_source": null          // "bluesky_appview" when the provider supplied post
                                      // metadata; honestly null otherwise (GCV/SerpApi don't)
  },
  "run": {
    "run_id": "2026-09-05T18-07-40Z",
    "providers_queried": ["gcv_web_detection"],
    "candidates_examined": 21,
    "candidates_rejected": 20,
    "degraded_closed_corpus": false,
    "identity_signals": ["Shah Rukh Khan"],  // context only, R-03
    "pipeline_version": "0.1.0",
    "model": "w600k_r50"
  }
}
```

A v1 or v2 bundle (missing `artifacts[]`, or missing v2's three fields
entirely) remains a valid, hashable structure forever — old evidence is
never invalidated by a later schema version existing, and
`extract_artifact_manifest()` falls back to the known `match_image.jpg`
mapping when `artifacts[]` is absent, so both pre-v3 committed sample runs
keep verifying without being regenerated (`tests/test_evidence_v2.py`,
`tests/test_evidence.py` pin this).

### 4.3 Face commitment

```python
def face_commitment(emb: Embedding, salt: bytes) -> bytes:
    q = np.round(emb.vec * 1000).astype(np.int16)   # deterministic quantisation
    return keccak256(salt + q.tobytes())
```

The salt is generated once, stored in `.env` (never committed), and required to reproduce the commitment. This is the mechanism behind R-01: the record proves "the same face produced this" without publishing anything biometric. Quantisation makes it reproducible across runs despite float noise.

---

## 5. Chain layer

### 5.1 Contract — `contracts/src/EvidenceRegistry.sol`

**Corrected 7 Sep 2026** — this section previously named the contract
`FaceEvidenceRegistry.sol`; the file actually deployed and tested is
`EvidenceRegistry.sol`. Full source in `docs/ANALYSIS.md` §6.3. Summary,
read against the real contract:

```solidity
struct Record {
    bytes32 faceCommitment; // keccak256(salt || quantised embedding) — never the raw vector
    bytes32 imageHash;      // sha256 of the matched candidate image bytes
    bytes32 postHash;
    string  cid;             // written as "" — IPFS pinning was never built, §9
    uint32  scoreBps;
    uint64  anchoredAt;
    address submitter;
}
mapping(bytes32 => Record) private _records;   // key = evidenceHash

function anchor(...) external;                                    // reverts on re-anchor
function verify(bytes32) external view returns (bool, Record memory);
event EvidenceAnchored(bytes32 indexed evidenceHash,
                       bytes32 indexed faceCommitment,
                       string cid, uint32 scoreBps);
```

Foundry tests required before any deploy: anchor-then-verify round trip, double-anchor reverts, unknown hash returns `exists == false`, event fields correct.

### 5.2 EVM client — `chain/evm.py`

```python
class EvmClient:
    def anchor(self, bundle: EvidenceBundle) -> AnchorReceipt: ...
    def verify(self, evidence_hash_hex: str) -> VerifyResult: ...
```

Receives **only hashes and the CID string** from the bundle — never the bundle's raw data dict, and never a face embedding (`architecture.md` §3 boundary rule; this is how R-01 is structural rather than a matter of discipline).

`EVM_CHAIN` selects `anvil` (default, D-27/D-43) or `base-sepolia` (84532, opt-in bonus only). Identical code path — that is the point (R-15). Anvil's well-known default private key is used automatically when `EVM_CHAIN=anvil` and no `EVM_PRIVATE_KEY` is set — deliberately **not** read from `.env`, so it can never be mistaken for a real secret; any other chain must supply a real key or `EvmClient.__init__` raises. `AnchorReceipt` carries tx hash, chain id, block number, contract address, and gas used, and is written to `runs/<id>/anchor.json`.

### 5.3 (removed — never built)

**Removed 7 Sep 2026.** This section previously described `chain/ots.py`
(OpenTimestamps → Bitcoin) as a built module with a working `stamp()`/
`verify_ots()` API. It was evaluated, deferred (§9 below), and never
implemented — no such file exists in `pipeline/chain/`. The section is
removed rather than left describing code that isn't there.

### 5.4 Verifier — `chain/reverify.py`

**Revised 7 Sep 2026 (Tier 2).** The previous version of this section listed
four checks, two of which (C2PA manifest, OpenTimestamps proof) were never
built and are deferred (§9) — a doc-vs-code overclaim, now removed. It also
described the integrity check in a way that concealed a real defect: see R-25.

Three independent checks. Each is pass/fail on its own, and each has a
distinct failure meaning:

| # | Check | Source of truth | Failure verdict |
|---|---|---|---|
| 1 | **Artifact digests** — recompute `sha256`/`phash` of every file the bundle references and compare against the digest recorded in the bundle | the files on disk | `ARTIFACT_MISMATCH`, or `ARTIFACT_MISSING` |
| 2 | **Bundle integrity** — canonicalise the *rebuilt* bundle and compare keccak256 against the hash actually anchored (`anchor.json`) | `anchor.json` | `BUNDLE_MODIFIED` |
| 3 | **On-chain record** — the contract holds that exact hash, and its fields match | an `eth_call` | `NOT_ANCHORED` |

Check 1 is the one that was missing. Ordering matters: artifacts are checked
*first*, because an artifact swap is a different and more interesting attack
than a JSON edit, and reporting it as a generic "tampered" would lose that
information.

```python
def rebuild_from_artifacts(run_dir: Path) -> tuple[dict, list[ArtifactCheck]]
    """Load the stored bundle, then OVERWRITE every recorded digest with one
    recomputed from the corresponding file on disk. Returns the rebuilt
    structure plus a per-artifact pass/fail list.

    R-25: no stored digest is ever carried through. This is the function the
    whole re-verification claim rests on."""

def reverify_bundle(run_dir: Path, ...) -> ReverifyReport
```

Local disk plus one `eth_call`. **No hosted URL is ever re-fetched during
verification** — not the candidate's platform image (it may 403, expire, or
be deleted) and not a search-engine cache URL (those expire by design).
Verification must keep working indefinitely and offline, with only the RPC
reachable. This is deliberate and is why `match.verified_against` records
what was scored at anchor time as a separate question from what the verifier
checked.

Exit code 0 only when every attempted check passes.

#### Verdict states

| Verdict | Meaning | Exit |
|---|---|---|
| `PASS` | all three checks pass | 0 |
| `ARTIFACT_MISMATCH` | a file's bytes no longer hash to the recorded digest | 1 |
| `ARTIFACT_MISSING` | the bundle references a file that is not on disk | 1 |
| `BUNDLE_MODIFIED` | rebuilt bundle hash ≠ the anchored hash | 1 |
| `NOT_ANCHORED` | hash is self-consistent but was never anchored on this chain | 1 |
| `ERROR` | unreadable file, malformed JSON, chain unreachable | 1 |

`TAMPERED` is retired as a verdict string: it conflated an artifact swap with
a bundle edit. Both are tampering; they are not the same tampering, and R-24's
principle (a reason must describe what actually happened) applies to verifier
output exactly as it applies to reject reasons.

### 5.5 Tamper demonstration — three modes

The tamper demo is a required deliverable (S9 in `prd.md`), and one button
that flips one field demonstrates less than it appears to. Editing a field
*inside* the bundle and observing the hash change proves keccak256 is
deterministic — it does not prove the bundle commits to the evidence.

Three modes, each a different attack, each producing a different verdict:

| Mode | What it mutates | Proves | Expected verdict |
|---|---|---|---|
| `swap-artifact` | flips one bit in `match_image.jpg` | the bundle genuinely commits to the image bytes, not just to itself | `ARTIFACT_MISMATCH` |
| `edit-bundle` | `match.score_bps` 9806 → 9999 | the on-chain hash pins the bundle's contents | `BUNDLE_MODIFIED` |
| `forge-bundle` | builds a fresh, internally consistent, correctly-hashed bundle | consistency is not provenance — a valid hash that was never anchored is worthless | `NOT_ANCHORED` |

Every mode operates on a **copied run directory in a temp path**. The real
`runs/<id>/` is never written to, and a test asserts every file is
byte-identical before and after. `swap-artifact` mutates the *source file* and
lets the recomputed digest propagate up through `rebuild_from_artifacts` —
mutating the digest field directly would be self-referential and a reviewer
reading the code would rightly discount it.

### 5.6 Artifact manifest

For check 1 to be complete, the bundle must say which files it commits to.
Schema v2 already records `match.image_sha256` and `match.image_phash` for
`match_image.jpg`; v3 makes the mapping explicit rather than implied by
convention, so the verifier iterates a list instead of hardcoding filenames:

```jsonc
"artifacts": [
  {"path": "match_image.jpg", "sha256": "…", "phash": "a3f0c1…"}
]
```

Additive and backward-compatible: a v2 bundle with no `artifacts` key falls
back to the known `match_image.jpg` mapping, so the two committed sample runs
keep verifying without being regenerated or hand-edited.

---

## 6. Audit log

**Corrected 7 Sep 2026.** The shape below previously showed a
`runs/<id>/raw/` directory of verbatim third-party JSON responses; no such
directory is written by the current code. Raw provider payloads are folded
into each candidate's `raw: dict` field inside `audit.json` itself
(`search/base.py::Candidate.raw`) rather than as separate files — same
anti-fabrication property (an unedited provider response is hard to
fabricate), different storage shape.

`runs/<id>/audit.json`. This artifact is the answer to "prove it isn't hardcoded", so it is a first-class output. Actual shape, read from `pipeline/audit/run_log.py::build_audit()`:

```jsonc
{
  "run_id": "2026-09-05T18-07-40Z",
  "started_at": 1757000000.0,
  "liveness": { "passed": true, "score": null, "label": "not_applicable" },
  "providers": [
    { "name": "web_detect", "available": true, "attempted": true,
      "latency_ms": 8.4, "candidates_returned": 20, "error": null }
  ],
  "candidates": [
    { "rank": 0, "page_url": "…", "image_url": "…", "source": "gcv_web_detection",
      "origin": "face", "faces_found": 1, "score": 0.9806,
      "decision": "ACCEPT", "reason": "…", "match_kind": "similar",
      "diagnostics": { "http_status": 200, "content_type": "image/jpeg",
        "content_bytes": 41203, "image_width": 640, "image_height": 640,
        "faces_found": 1, "largest_face_px": 512, "routes_tried": 1 } }
  ],
  "verdict": "MATCH",
  "threshold": 0.42,
  "margin_required": 0.08,
  "cache": { "web_detect": {"hits": 17, "misses": 8}, "aggregate": {"hits": 17, "misses": 8} },
  "identity_signals": ["Shah Rukh Khan"],
  "degraded_closed_corpus": false
}
```

Every rejected candidate carries real measured `diagnostics` (HTTP status,
content-type, byte count, image dimensions, faces found, largest face
pixel size, routes tried) rather than a bare dash or a fabricated
confidence number — a structurally-unscoreable row (e.g. a fetch that never
returned bytes) is reported as exactly that, never invented (R-24).

---

## 7. CLI surface — `pipeline/cli.py`

**Corrected 7 Sep 2026.** The command list below was aspirational —
`scan`/`search`/`run-all`/`anchor`/`verify` did not exist as real
subcommands when this section was first written, and `crawl`/`calibrate`
were never built at all. T2.7 landed the real CLI; this now reflects the
actual `typer` app.

```
python -m pipeline version
python -m pipeline scan     IMAGE_PATH [--run-id ID] [--public-image-url URL]
python -m pipeline search   RUN_ID [--public-image-url URL]
python -m pipeline run-all  IMAGE_PATH [--public-image-url URL] [--anchor/--no-anchor]
python -m pipeline anchor   RUN_ID
python -m pipeline verify   RUN_ID [--tamper swap-artifact|edit-bundle|forge-bundle]
python -m pipeline serve    [--host HOST] [--port PORT]
```

`crawl` and `calibrate` as standalone commands were never built — the
Bluesky crawl happens inline inside `search`/`run-all`, and no calibration
sweep exists yet (§3.2). Output is `rich` tables and panels — a recording
deliverable, not decoration (`architecture.md` §5).

Structured exit codes, shared across every command:

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | success |
| 1 | `EXIT_VERIFICATION_MISMATCH` | `verify` reported anything other than `PASS`, or a run directory was missing/malformed |
| 2 | `EXIT_NO_FACE` | no usable face detected or the quality gate rejected it |
| 3 | `EXIT_PROVIDER_ERROR` | every search provider failed or was unavailable |
| 4 | `EXIT_NO_MATCH` | search completed but the verdict was not `MATCH` |
| 5 | `EXIT_CHAIN_ERROR` | anchor or verify hit a chain/RPC failure |

Covered by `tests/test_cli_exit_codes.py`.

---

## 8. Testing

**Extended 7 Sep 2026** with the categories added by Tier 2. 290 tests pass
as of commit `0a62f17` (up from the 10 conceptual categories originally
listed here, which undercounted the real suite considerably).

| Test | Guards against |
|---|---|
| flipped-input embedding cosine > 0.9 | landmark ordering bug (§1.3) |
| same-person pair scores above threshold | broken preprocessing |
| different-person pair scores below threshold | broken preprocessing |
| canonical round-trip byte equality | canonicalisation drift (§4.1) |
| every embedding has `‖v‖ ≈ 1` | missing normalisation (R-07) |
| provider raising → run still completes | orchestrator isolation (R-14) |
| Foundry: anchor → verify round trip | contract correctness |
| Foundry: double anchor reverts | replay protection |
| no float appears in canonical output | R-02 |
| swapped `match_image.jpg` → `ARTIFACT_MISMATCH`, not `PASS` | R-25's defect, once real |
| three tamper modes → three distinct, correct verdicts | tamper demo proving less than one PASS/FAIL toggle would |
| the real `runs/<id>/` is byte-identical before/after every tamper mode | tamper demo ever touching real evidence |
| crafted `"><script>`/quote-break/`javascript:` candidate renders as inert text | XSS (R-26) |
| candidate URL pointing at `127.0.0.1`/`169.254.169.254`/etc. is rejected before fetch | SSRF (R-29) |
| escalation triggers on `result_carries_identity_signal()`, never on candidate count | the exact bug already found once (§3d in `memory.md`) |
| expanded candidate below threshold rejected exactly like a primary candidate | R-28's face-gating actually holding |
| `linked`/`conjecture` candidates never appear in the match count | R-28 |
| CLI exit codes match the table in §7 for each real failure mode | T2.7 |

---

## 9. Deferred

**Reconciled 7 Sep 2026** against what has actually shipped since this
section was written. Several items below moved from "deferred" to "built" —
those are struck through and cross-referenced. Everything else remains
genuinely deferred.

| Item | Status |
|---|---|
| SCRFD `det_10g` detector | still deferred — YuNet remains sufficient |
| FAISS | still deferred — unnecessary below ~10⁵ vectors |
| C2PA manifest | still deferred — needs cert setup, out of proportion for this task |
| OpenTimestamps (`chain/ots.py`) | still deferred — evaluated, never built (§5.3) |
| IPFS pinning | still deferred — `chain/evm.py` writes an empty CID string |
| EAS attestations | still deferred — the custom contract already satisfies the brief |
| zk proof of `cosine ≥ τ` | still deferred — genuinely interesting, out of scope |
| Multi-frame liveness | still deferred — single-frame passive model is enough |
| CUDA execution provider | still deferred — CPU inference has been fast enough throughout |
| Benchmark-scale ROC calibration | still deferred — see §3.2's correction; the threshold is a documented, measurement-derived placeholder, not a swept ROC |
| ~~Real CLI subcommands~~ | **built — T2.7, §7 above** |
| ~~Artifact-level re-verification~~ | **built — R-25, §5.4, closing a real defect** |
| ~~Cross-platform profile expansion~~ | **built — F3, §2.6** |
| ~~SSRF hardening on candidate fetches~~ | **built — R-29** |
| ~~Head-crop search representation~~ | **built, but currently disabled by default pending the R-27 measurement** — see §1.7 and `scripts/probe_query_representation.py` |
