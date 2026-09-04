# Architecture — face-chain-verify

**Companion docs:** [`prd.md`](./prd.md) (what and why) · [`design.md`](./design.md) (algorithms and signatures) · [`rules.md`](./rules.md) (invariants) · [`ANALYSIS.md`](./ANALYSIS.md) (why this shape won)

This document covers structure: components, boundaries, data flow, and stack decisions. It does not repeat algorithm detail — that lives in `design.md`.

---

## 1. System shape

```
                        ┌──────────────────────────────┐
                        │        cli.py (typer)        │
                        │  scan  search  anchor        │
                        │  verify  calibrate  run-all  │
                        └──────────────┬───────────────┘
                                       │
      ┌────────────────────────────────┼────────────────────────────────┐
      ▼                                ▼                                ▼
┌───────────┐               ┌───────────────────┐            ┌──────────────────┐
│ STAGE 1   │               │ STAGE 2           │            │ STAGE 3          │
│ face      │               │ search + verify   │            │ evidence + chain │
└─────┬─────┘               └─────────┬─────────┘            └────────┬─────────┘
      │                               │                               │
      │ liveness.py                   │ search/base.py (ABC)          │ evidence/
      │ detect.py    (YuNet)          │  ├─ bluesky.py      no key    │  canonical.py
      │ align.py     (5-pt affine)    │  ├─ google_lens.py  SerpApi   │  bundle.py
      │ embed.py     (ArcFace 512-d)  │  ├─ bing_visual.py  Azure     │  ipfs.py
      │                               │  ├─ mastodon.py     no key    │
      │                               │  ├─ facecheck.py    opt-in    │ chain/
      │                               │  └─ search4faces.py opt-in    │  evm.py
      │                               │                               │  ots.py
      │                               │ verify/                       │  reverify.py
      │                               │  ├─ dedupe.py   (phash)       │
      │                               │  ├─ allowlist.py              │ contracts/
      │                               │  ├─ matcher.py  (cosine)      │  FaceEvidence
      │                               │  └─ calibrate.py (ROC)        │  Registry.sol
      │                               │                               │
      └───────────────┬───────────────┴───────────────┬───────────────┘
                      │                               │
                      ▼                               ▼
              ┌───────────────┐             ┌───────────────────┐
              │ audit/        │             │ cache/            │
              │ run_log.py    │             │ http_cache.py     │
              │ → runs/<id>/  │             │ → .cache/         │
              └───────────────┘             └───────────────────┘
```

Two cross-cutting concerns deliberately sit outside the three stages:

- **`audit/`** records every decision. It is what makes the search provably genuine, so it is not optional plumbing — it is a deliverable.
- **`cache/`** intercepts every outbound HTTP call. It protects the SerpApi free quota and makes runs reproducible offline. See `rules.md` R-04.

## 2. Data flow

```
webcam frame (BGR ndarray)
   └─ liveness.py ──────────────► LivenessResult{passed, score}
   └─ detect.py ────────────────► [DetectedFace{bbox, kps5, det_score}]
        └─ align.py ────────────► aligned 112×112 BGR crop
             └─ embed.py ───────► Embedding{vec(512,), l2_normalised=True}
                                        │
                                        │  probe
                                        ▼
                             ┌──────────────────────┐
                             │ SearchOrchestrator   │
                             │ parallel fan-out     │
                             └──────────┬───────────┘
                                        │
              ┌─────────────┬───────────┴──────┬─────────────┐
              ▼             ▼                  ▼             ▼
        BlueskyProvider  LensProvider   BingProvider   MastodonProvider
              │             │                  │             │
              └─────────────┴───────┬──────────┴─────────────┘
                                    │  list[Candidate]
                                    ▼
                          dedupe.py  (perceptual hash)
                                    │
                                    ▼
                    download each candidate image (cached)
                                    │
                                    ▼
                    ┌─────────────────────────────────┐
                    │ SAME face core, re-applied      │
                    │ detect → align → embed          │
                    └────────────────┬────────────────┘
                                     │
                                     ▼
                          matcher.py  cosine vs probe
                          threshold + margin rule
                          allowlist.py domain filter
                                     │
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
             MatchResult{best, runner_up,      audit/run_log.py
             all_scored, decision}             every reject + reason
                    │
                    ▼
             evidence/bundle.py ──► canonical.py ──► sha256/keccak256
                    │                                     │
                    ├──► ipfs.py ──► CID                  │
                    │                                     ▼
                    └──────────────────────► chain/evm.py  ──► tx hash
                                             chain/ots.py  ──► .ots proof
```

**The load-bearing property:** the box labelled "SAME face core, re-applied" is the same code that produced the probe embedding. One implementation, used for probe encoding, candidate verification, and corpus indexing. That is what lets Google Lens (an image-similarity engine) and Bluesky (a raw post firehose) feed one decision function.

## 3. Component boundaries

| Layer | Knows about | Must not know about |
|---|---|---|
| `face/` | numpy arrays, ONNX sessions | providers, HTTP, chains, evidence |
| `search/` | `Candidate`, HTTP, provider APIs | face models, cosine scoring, chains |
| `verify/` | embeddings, candidates, thresholds | provider internals, HTTP, chains |
| `evidence/` | match results, canonical JSON, IPFS | face models, providers, chain RPC |
| `chain/` | 32-byte hashes, CIDs, RPC | faces, providers, images |
| `audit/` | everything, write-only | nothing calls back into it |

Two rules that follow from this table and are enforced in `rules.md`:

- A provider **never** scores a face. It returns candidates. Scoring is `verify/matcher.py` alone. This is why provider-supplied scores are recorded but never trusted (R-03).
- `chain/` never sees an image or an embedding. It receives hashes. This is how R-01 (no biometrics on chain) is enforced structurally rather than by discipline.

## 4. The provider interface

Adding a search source must never require touching the face core, the matcher, or the chain layer. Everything hangs off one small contract:

```python
@dataclass(frozen=True)
class Candidate:
    image_url: str                  # candidate image to download and verify
    page_url: str                   # the post/page it appeared on
    source: str                     # provider name, for the audit log
    provider_score: float | None    # recorded, NEVER used to decide  (R-03)
    raw: dict                       # untouched provider response, for the audit log
    post_meta: dict | None = None   # author/text/timestamp if provider supplies it

class SearchProvider(Protocol):
    name: str
    requires_credentials: bool
    def available(self) -> bool: ...
    def search(self, aligned_face_png: bytes,
               probe: Embedding) -> list[Candidate]: ...
```

`available()` lets the orchestrator skip unconfigured providers silently, which is what makes the zero-API-key quickstart work (S10 in `prd.md`).

## 5. Stack decisions

| Concern | Choice | Rationale | Rejected |
|---|---|---|---|
| Language | Python 3.12 | Face models, HTTP, and crypto libs all present | — |
| Face detection | **OpenCV YuNet** (`cv2.FaceDetectorYN`) | Ships in opencv, returns bbox + 5 landmarks, all post-processing internal. Zero extra decode code | SCRFD `det_10g` — better accuracy, needs ~80 lines of anchor decoding. Kept as a Phase-7 optional upgrade |
| Face embedding | **ArcFace `w600k_r50.onnx`** via onnxruntime | SOTA-class, 512-d, CPU-viable, well-documented preprocessing | FaceNet (weaker separation), dlib 128-d (2017-era, will false-match on camera) |
| Model runtime | `onnxruntime` CPU | One wheel, no compiler, no CUDA version matching | `insightface` pip package — builds Cython extensions, commonly fails on Windows without VS Build Tools. **Primary reason for the ONNX-direct approach** |
| Vector search | **numpy brute-force dot product** | 512-d × 10k vectors is 20 MB; a matmul is microseconds. One less wheel | FAISS — one-line swap if the corpus exceeds ~10⁵. Not needed at our scale |
| Liveness | Silent-Face anti-spoof ONNX | Small, CPU-fast, turns "upload a JPEG" into "face scan" | Blink detection — needs multi-frame logic, more fragile on camera |
| Primary search | Google Lens via SerpApi | Genuine open web, free tier ~100/mo, stable JSON | Yandex scraping — captcha pages returned as HTTP 200, silent failure. PimEyes scraping — ToS violation, tooling broken |
| Fallback search | Bluesky AT Protocol | No auth for public reads, live real posts, no quota | Twitter/X API — paid. Instagram — hostile to automation |
| Perceptual hash | `ImageHash` (phash) | Cheap dedupe of the same image served from many CDNs | — |
| CLI | `typer` | Type-hint driven, minimal boilerplate | argparse (verbose), click (more wiring) |
| Console output | `rich` | Tables and colour read well at 1080p. **A recording deliverable, not decoration** | plain print |
| Retries | `tenacity` | Declarative backoff for polite crawling | hand-rolled loops |
| Contract lang | Solidity + Foundry | Fast local Anvil chain, native fuzz tests | Hardhat — heavier Node toolchain for no gain here |
| Primary chain | Base Sepolia (84532) | Live faucet, ~2 s blocks, public explorer | Polygon Amoy — official faucet retired, public RPC deprecated. Ethereum Sepolia — mid-transition, support winding down |
| Second anchor | OpenTimestamps → Bitcoin mainnet | Free, no wallet, permanent, independent chain | — |
| Offline chain | Anvil | Demo cannot fail on RPC flake | Ganache — unmaintained |

## 6. Directory layout

```
face-chain-verify/
├─ ANALYSIS.md  prd.md  architecture.md  design.md  rules.md  phases.md  memory.md
├─ README.md
├─ .env.example  .gitignore  requirements.txt  pyproject.toml
├─ models/                        # gitignored, fetched by scripts/fetch_models.py
│   ├─ face_detection_yunet_2023mar.onnx
│   ├─ w600k_r50.onnx
│   └─ anti_spoof.onnx
├─ scripts/
│   ├─ fetch_models.py            # idempotent model download + sha256 check
│   └─ warmup.py                  # pre-load sessions before recording
├─ pipeline/
│   ├─ __init__.py  config.py  cli.py
│   ├─ face/        liveness.py  detect.py  align.py  embed.py  types.py
│   ├─ search/      base.py  orchestrator.py  bluesky.py  google_lens.py
│   │               bing_visual.py  mastodon.py  facecheck.py  search4faces.py
│   ├─ verify/      dedupe.py  allowlist.py  matcher.py  calibrate.py
│   ├─ evidence/    canonical.py  bundle.py  c2pa.py  ipfs.py
│   ├─ chain/       evm.py  ots.py  reverify.py  abi/
│   ├─ audit/       run_log.py
│   └─ cache/       http_cache.py
├─ contracts/
│   ├─ src/FaceEvidenceRegistry.sol
│   ├─ test/FaceEvidenceRegistry.t.sol
│   └─ script/Deploy.s.sol
├─ calibration/    pairs/  roc.png  threshold.json
├─ runs/           <run-id>/  evidence.json  audit.json  candidates/  probe.png
├─ .cache/         gitignored HTTP cache
└─ tests/
```

## 7. Runtime artifacts

Every invocation of `run-all` produces one directory under `runs/`. This directory **is** the evidence, and selected runs are committed to the repo as proof of non-fabrication.

```
runs/2026-09-05T14-22-08Z/
├─ probe.png              aligned 112×112 crop actually used
├─ evidence.json          canonical bundle, the thing that gets hashed
├─ audit.json             every provider request, status, latency, candidate, decision
├─ candidates/
│   ├─ 00_score-0.6842_ACCEPT.jpg
│   ├─ 01_score-0.3112_reject-below-threshold.jpg
│   └─ ...
├─ anchor.json            tx hash, chain id, block, contract address, CID
└─ evidence.json.ots      OpenTimestamps proof
```

Filenames encode the score and the decision so the directory is self-documenting when opened on camera.

## 8. Configuration

Single source of truth in `pipeline/config.py`, loaded from `.env` with defaults that work with **no** `.env` present — that is what makes the zero-key quickstart possible.

| Var | Default | Effect if unset |
|---|---|---|
| `SERPAPI_KEY` | none | Lens provider reports `available() == False`, skipped silently |
| `AZURE_VISION_KEY` | none | Bing provider skipped |
| `BLUESKY_CRAWL_LIMIT` | `2000` | posts to ingest per run |
| `MATCH_THRESHOLD` | from `calibration/threshold.json` | never hardcoded inline (R-09) |
| `MATCH_MARGIN` | from `calibration/threshold.json` | — |
| `EVM_RPC_URL` | `http://127.0.0.1:8545` | falls back to local Anvil |
| `EVM_CHAIN` | `anvil` | `base-sepolia` to go public |
| `PRIVATE_KEY` | none | anchoring disabled, pipeline still runs |
| `PINATA_JWT` | none | IPFS skipped, bundle stays local |
| `FACECHECK_KEY` | none | provider disabled (default) |
| `HTTP_CACHE` | `1` | set `0` to force live fetches |

## 9. Failure isolation

The demo must survive any single external dependency failing. How each is contained:

| Dependency | If it fails | Containment |
|---|---|---|
| SerpApi (quota/outage) | Lens provider returns `[]` | Bluesky provider still produces a match |
| Bluesky API | provider returns `[]` | Lens still produces a match |
| Base Sepolia RPC | anchor step errors | `EVM_CHAIN=anvil` fallback, identical code path |
| Pinata | no CID | bundle stays local; hash and chain record unaffected |
| OTS calendars | no `.ots` proof | EVM anchor is independent |
| Model download | startup fails | `scripts/fetch_models.py` is idempotent, run before recording |

The orchestrator treats a provider raising an exception as a provider returning zero candidates, logs it to the audit trail, and continues. No single provider can abort a run.

## 10. Extension points

Where future work plugs in without restructuring:

- **New search source** → implement `SearchProvider`, register in `orchestrator.py`. Nothing else changes.
- **Better detector** → swap `detect.py` internals; `DetectedFace` contract is stable.
- **GPU** → change the onnxruntime provider list in `embed.py`. Everything downstream is unaffected.
- **FAISS** → replace the matmul in `bluesky.py`'s index; `matcher.py` untouched.
- **EAS instead of custom contract** → new module beside `evm.py`; `reverify.py` gains a fifth check.
- **zk threshold proof** → prove `cosine ≥ τ` without revealing the embedding. Noted as future work in the README; out of scope for 3 days.
