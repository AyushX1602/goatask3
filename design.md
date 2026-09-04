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

Applies to webcam input only. Candidate images downloaded from the web are already photographs of photographs by definition, so liveness is skipped there — an important asymmetry to state in the README.

Degradation: if the model file is absent, return `LivenessResult(True, 0.0, "unknown")` and log a warning. Missing liveness must not break the pipeline.

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

### 2.2 Bluesky provider — `search/bluesky.py`

The zero-API-key path, and Day 1's target. Two-phase: ingest, then query.

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

### 2.3 Google Lens provider — `search/google_lens.py`

**[V]** SerpApi, `engine=google_lens`. Free tier is roughly 100 searches/month, so caching is mandatory (R-04).

SerpApi needs a publicly reachable image URL, it cannot accept raw bytes **[A]**. Options, in order:
1. Upload the aligned crop to a temporary public host and pass its URL.
2. Use the SerpApi endpoint that accepts an uploaded file if one is available at build time.

Resolve this in Phase 5; it is the main unknown in the open-web path.

Parse `visual_matches[]` → `Candidate(image_url=thumbnail_or_original, page_url=link, source="google_lens", provider_score=None, raw=item)`.

`provider_score` is left `None` deliberately. Lens gives no similarity number, and we would not use it if it did (R-03).

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
    "reddit.com", "youtube.com", "tiktok.com", "threads.net",
}
DENY = {  # never surfaced, regardless of match score
    # dating platforms, mugshot aggregators, adult platforms
}
```

Registrable-domain comparison, not substring matching, so `evil-x.com.attacker.net` cannot pass. Off-allowlist candidates are still scored and logged — they simply cannot become the reported match. Showing them scored-then-excluded strengthens the audit trail.

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

Rejection reasons, all logged verbatim:

| Reason | Meaning |
|---|---|
| `reject-no-face` | no face detected in the candidate image |
| `reject-below-threshold` | scored under the calibrated threshold |
| `reject-margin` | above threshold but too close to the runner-up |
| `reject-domain` | not on the social allowlist |
| `reject-duplicate` | collapsed by phash |
| `reject-fetch-failed` | image could not be downloaded |

When a candidate image contains several faces, score all of them and keep the maximum.

### 3.2 Calibration — `verify/calibrate.py`

Thresholds are **derived, never hardcoded** (R-09).

1. Build `calibration/pairs/` — roughly 100 positive and 100 negative pairs from public face datasets or self-collected consented images.
2. Sweep the threshold, compute TPR/FPR, write `roc.png`.
3. Choose the operating point at a **declared** false-match rate (target: FMR ≤ 1%).
4. Emit `calibration/threshold.json`:

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

Expect the threshold to land in 0.35–0.45 for this model. **Derive it anyway** — the committed ROC is the evidence, and the number alone is not.

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

### 4.2 Bundle schema v1

```jsonc
{
  "schema_version": 1,
  "probe": {
    "face_commitment": "0x…",       // salted hash — NEVER the embedding (R-01)
    "liveness_passed": true,
    "liveness_label": "live",
    "captured_at": 1757000000,
    "aligned_sha256": "…"
  },
  "match": {
    "page_url": "https://bsky.app/profile/…/post/…",
    "image_url": "https://cdn.bsky.app/…",
    "image_sha256": "…",
    "provider": "bluesky",
    "score_bps": 6842,               // 0.6842 cosine
    "margin_bps": 3910,
    "threshold_bps": 4200
  },
  "post": {
    "platform": "bluesky",
    "author_handle": "…",
    "author_display": "…",
    "author_did": "…",
    "text": "…",
    "published_at": 1756900000,
    "permalink": "https://bsky.app/…"
  },
  "run": {
    "run_id": "2026-09-05T14-22-08Z",
    "providers_queried": ["bluesky", "google_lens"],
    "candidates_examined": 12,
    "candidates_rejected": 11,
    "corpus_size": 2000,
    "corpus_crawled_at": 1757000000,
    "pipeline_version": "0.1.0",
    "model": "w600k_r50"
  }
}
```

### 4.3 Face commitment

```python
def face_commitment(emb: Embedding, salt: bytes) -> bytes:
    q = np.round(emb.vec * 1000).astype(np.int16)   # deterministic quantisation
    return keccak256(salt + q.tobytes())
```

The salt is generated once, stored in `.env` (never committed), and required to reproduce the commitment. This is the mechanism behind R-01: the record proves "the same face produced this" without publishing anything biometric. Quantisation makes it reproducible across runs despite float noise.

---

## 5. Chain layer

### 5.1 Contract — `contracts/src/FaceEvidenceRegistry.sol`

Full source in `ANALYSIS.md` §6.3. Summary:

```solidity
struct Record {
    bytes32 faceCommitment;  // salted hash, no biometric content
    bytes32 imageHash;
    bytes32 postHash;
    string  cid;
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
def anchor(bundle: dict, cid: str | None) -> AnchorReceipt
def read_record(evidence_hash: bytes) -> Record | None
```

`EVM_CHAIN` selects `anvil` (default) or `base-sepolia` (84532). Identical code path — that is the point (R-15). `AnchorReceipt` carries tx hash, chain id, block number, contract address, and gas used, and is written to `runs/<id>/anchor.json`.

### 5.3 OpenTimestamps — `chain/ots.py`

```python
def stamp(path: Path) -> Path          # writes <path>.ots
def verify_ots(path: Path) -> OtsResult   # PENDING | CONFIRMED(block, utc_time)
```

**[V]** Free, no wallet, no API key; public calendar servers aggregate requests and pay Bitcoin fees. Batched, so a fresh stamp is `PENDING` for an hour or more.

Demo consequence: stamp a bundle the **day before** the recording so a `CONFIRMED` proof exists on camera, and stamp the live one to show `PENDING` while explaining batching. Do not attempt to show a live confirmation.

### 5.4 Verifier — `chain/reverify.py`

Four independent checks, each pass/fail on its own:

| # | Check | Failure meaning |
|---|---|---|
| 1 | Recomputed keccak256 equals the stored commitment | bundle was modified |
| 2 | C2PA manifest signature valid (if present) | manifest tampered |
| 3 | On-chain record exists and every field matches | not anchored, or fields altered |
| 4 | OTS proof valid against a Bitcoin block | timestamp proof broken |

```python
def reverify(bundle_path: Path) -> ReverifyReport   # per-check results + overall
```

Exit code 0 only when every attempted check passes. Skipped checks (no CID, no `.ots`, no C2PA) are reported as `SKIPPED`, never silently as pass.

The tamper demo — change one character, re-run, see check 1 fail — is a **required deliverable**, not a nice-to-have (S9 in `prd.md`).

---

## 6. Audit log

`runs/<id>/audit.json`. This artifact is the answer to "prove it isn't hardcoded", so it is a first-class output.

```jsonc
{
  "run_id": "…",
  "started_at": 1757000000,
  "probe": { "liveness": {...}, "det_score": 0.98, "bbox": [...] },
  "providers": [
    { "name": "bluesky", "available": true, "latency_ms": 8421,
      "corpus_size": 2000, "corpus_crawled_at": 1757000000,
      "candidates_returned": 8, "error": null },
    { "name": "google_lens", "available": true, "latency_ms": 3120,
      "http_status": 200, "cache_hit": false,
      "candidates_returned": 14, "error": null,
      "raw_response_path": "runs/…/raw/google_lens.json" }
  ],
  "candidates": [
    { "rank": 0, "page_url": "…", "image_url": "…", "source": "bluesky",
      "faces_found": 1, "score_bps": 6842, "decision": "ACCEPT", "reason": "…" },
    { "rank": 1, "page_url": "…", "score_bps": 3112,
      "decision": "reject-below-threshold", "reason": "…" }
  ],
  "verdict": "MATCH",
  "threshold_bps": 4200,
  "margin_bps_required": 800
}
```

Raw provider responses are written verbatim to `runs/<id>/raw/`. Unedited third-party JSON is hard to fabricate, which is exactly why it is committed.

---

## 7. CLI surface — `pipeline/cli.py`

```
python -m pipeline scan      [--webcam | --image PATH] [--out DIR]
python -m pipeline search    --probe DIR [--providers bluesky,google_lens]
python -m pipeline anchor    --run DIR [--chain anvil|base-sepolia]
python -m pipeline verify    --bundle PATH
python -m pipeline calibrate --pairs calibration/pairs
python -m pipeline run-all   [--webcam | --image PATH] [--dry-run]
python -m pipeline crawl     --limit N          # refresh Bluesky index only
```

Output is `rich` tables and panels — a recording deliverable, not decoration (`architecture.md` §5). `--dry-run` executes everything and anchors nothing (R-06).

---

## 8. Testing

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
| tamper → check 1 fails | verifier actually verifies |
| no float appears in canonical output | R-02 |

---

## 9. Deferred

Recorded so they are visibly choices, not oversights. Each becomes a README "future work" line.

| Item | Why deferred |
|---|---|
| SCRFD `det_10g` detector | YuNet is sufficient; upgrade only if detection recall disappoints |
| FAISS | unnecessary below ~10⁵ vectors |
| C2PA manifest | needs cert setup; first thing cut (`phases.md`) |
| EAS attestations | custom contract already satisfies the brief |
| zk proof of `cosine ≥ τ` | genuinely interesting, not a 3-day task |
| Multi-frame liveness | single-frame passive model is enough |
| CUDA execution provider | decide after Phase 1 timing |
