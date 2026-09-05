# Architecture — face-chain-verify

**Companion docs:** [`prd.md`](./prd.md) (what and why) · [`design.md`](./design.md) (algorithms and signatures) · [`rules.md`](./rules.md) (invariants) · [`ANALYSIS.md`](./ANALYSIS.md) (why this shape won)

This document covers structure: components, boundaries, data flow, and stack decisions. It does not repeat algorithm detail — that lives in `design.md`.

---

## 1. System shape

```
                        ┌──────────────────────────────┐        ┌──────────────────────┐
                        │        cli.py (typer)        │        │  webapp/ (FastAPI)   │
                        │  scan  search  anchor        │        │  same pipeline calls │
                        │  verify  calibrate  run-all  │        │  static/index.html   │
                        └──────────────┬───────────────┘        └──────────┬───────────┘
                                       │                                   │
                                       └─────────────────┬─────────────────┘
      ┌────────────────────────────────┼────────────────────────────────┐
      ▼                                ▼                                ▼
┌───────────┐               ┌───────────────────┐            ┌──────────────────┐
│ STAGE 1   │               │ STAGE 2           │            │ STAGE 3          │
│ face      │               │ search + verify   │            │ evidence + chain │
└─────┬─────┘               └─────────┬─────────┘            └────────┬─────────┘
      │                               │                               │
      │ liveness.py  (3-state)        │ search/base.py (ABC)          │ evidence/
      │ detect.py    (YuNet)          │  ├─ web_detect.py  PRIMARY    │  canonical.py
      │ align.py     (5-pt affine)    │  │    ├ serpapi  Lens         │  bundle.py
      │ embed.py     (ArcFace 512-d)  │  │    └ gcv      WEB_DETECTION│  ipfs.py
      │ quality.py   (size gate)      │  └─ bluesky.py   keyless      │
      │                               │       fallback, closed corpus │ chain/
      │                               │                               │  evm.py
      │                               │ verify/                       │  ots.py
      │                               │  ├─ dedupe.py   (phash)       │  reverify.py
      │                               │  ├─ allowlist.py              │
      │                               │  ├─ matcher.py  (cosine)      │ contracts/
      │                               │  └─ calibrate.py (ROC)        │  FaceEvidence
      │                               │                               │  Registry.sol
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
INPUT (two first-class modes — D-19)
   webcam frame ──► liveness.py ──► LivenessResult{LIVE | SPOOF}
   uploaded file ─► liveness SKIPPED ──► LivenessResult{N/A, unverified}
        │
        ▼
   detect.py  (YuNet) ──────────► [DetectedFace{bbox, kps5, det_score}]
        │
        ├─ quality.py gate: reject faces < MIN_FACE_PX  (derived, see §2a)
        │
        └─ align.py ────────────► aligned 112×112 BGR crop
             └─ embed.py ───────► Embedding{vec(512,), L2-normalised}
                                        │
                                        │  probe
                                        ▼
                    ┌───────────────────────────────────────┐
                    │  WebDetectProvider        PRIMARY     │
                    │  one call -> ALL platforms Google      │
                    │  has indexed (proven: instagram,      │
                    │  facebook, youtube, x, reddit)        │
                    │    backend A: SerpApi google_lens     │
                    │    backend B: GCV WEB_DETECTION       │
                    └──────────────────┬────────────────────┘
                                       │
                    ┌──────────────────┴────────────────────┐
                    │  BlueskyProvider   keyless FALLBACK   │
                    │  closed corpus, labelled as such      │
                    └──────────────────┬────────────────────┘
                                       │  list[Candidate]
                                       ▼
                             dedupe.py  (perceptual hash)
                                       │
                                       ▼
                       allowlist.py  social-domain filter
                                       │
                                       ▼
                       download each candidate image (cached)
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │ SAME face core, re-applied           │
                    │ detect → quality gate → align → embed│
                    │ score ALL faces per image, take max  │
                    └──────────────────┬───────────────────┘
                                       │
                                       ▼
                          matcher.py  cosine vs probe
                          threshold + margin rule
                                       │
                    ┌──────────────────┴───────────────────┐
                    ▼                                      ▼
             MatchResult{best, runner_up,           audit/run_log.py
             all_scored, verdict}                   every reject + reason
                    │
                    ▼
             evidence/bundle.py ──► canonical.py ──► sha256/keccak256
                    │                                     │
                    ├──► ipfs.py ──► CID                  │
                    │                                     ▼
                    └──────────────────────► chain/evm.py  ──► tx hash
                                             chain/ots.py  ──► .ots proof
```

**The load-bearing property:** the box labelled "SAME face core, re-applied" is the same code that produced the probe embedding. Web detection returns *visual similarity*, which includes wrong people; our ArcFace re-verification is what converts that into a face match. That stage is the project's actual technical contribution, and it is why provider scores are never trusted (R-03).

## 2a. Quality gates — derived from measurement, not taste

Every number here came from `scripts/probe_accuracy.py` run against real fixtures. Recorded so they are defensible rather than arbitrary.

**Face size vs embedding stability** (same photo, progressively downscaled, cosine against full-res):

| face px | 232 | 174 | 119 | 87 | 61 | 43 | 31 | 20 |
|---|---|---|---|---|---|---|---|---|
| cosine | 1.000 | 0.972 | 0.971 | 0.970 | 0.962 | 0.954 | **0.868** | **0.767** |

Stable to ~43 px, then degrades sharply. **Gate: reject faces with min(width,height) < 50 px.** Below that the embedding is unreliable and will generate false scores.

**Blur vs embedding stability** (gaussian blur, Laplacian variance as the blur proxy):

| kernel | 1 | 3 | 7 | 13 | 21 | 31 |
|---|---|---|---|---|---|---|
| lap var | 499.7 | 36.2 | 7.9 | 3.6 | 2.3 | 1.8 |
| cosine | 1.000 | 0.984 | 0.980 | 0.974 | 0.949 | 0.904 |

ArcFace is far more blur-robust than expected — still 0.90 under heavy blur. **No blur gate needed.** Worth recording because the intuitive assumption was wrong.

**What a true non-match looks like** (9 confirmed different people vs one reference):

```
max 0.0738   mean -0.0241   std 0.0543   mean+4*std = 0.1930
same-person reference (obama1 vs obama2) = 0.7652
```

Two consequences:
1. The separation is wide. Non-matches cluster near zero; a true match sits near 0.77. The `0.42` placeholder threshold is conservative and safe, sitting far above the 4-sigma noise ceiling of 0.193.
2. **Anything in the 0.0–0.2 band is noise, not a weak match.** The UI must never present scores in that band as ranked suggestions — that was the cause of the "it showed me random female faces" report. See R-21.

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
| Vector search | **numpy brute-force dot product** | Only used by the keyless Bluesky fallback now. 512-d × 10k is 20 MB; a matmul is microseconds | FAISS — unnecessary. The primary path needs no index at all |
| Quality gate | **min face size 50 px**, no blur gate | Both derived from measurement, not intuition — see §2a | A blur gate: measured as unnecessary (0.90 cosine under heavy blur) |
| Liveness | Silent-Face anti-spoof ONNX, **3-state** | Meaningful on webcam capture; **not measurable on an uploaded file**, and must not claim otherwise (D-20) | Blink detection — multi-frame, more fragile on camera |
| **Primary search** | **Web detection** — SerpApi Google Lens now, Google Cloud Vision `WEB_DETECTION` as a second backend | **One call reaches every platform Google indexed.** Proven live: returned instagram, facebook, youtube, x, reddit post URLs. Free tiers: SerpApi ~100/mo (working today), GCV 1,000/mo | Per-platform API integration — see the rejected-approaches note below |
| Fallback search | Bluesky AT Protocol | Keyless, so the repo runs with no `.env`. **Closed corpus — labelled as a demo of the verifier, not as a web search** | — |
| Perceptual hash | `ImageHash` (phash) | Cheap dedupe of the same image served from many CDNs | — |
| CLI | `typer` | Type-hint driven, minimal boilerplate | argparse (verbose), click (more wiring) |
| Console output | `rich` | Tables and colour read well at 1080p. **A recording deliverable, not decoration** | plain print |
| Retries | `tenacity` | Declarative backoff for polite crawling | hand-rolled loops |
| Contract lang | Solidity + Foundry | Fast local Anvil chain, native fuzz tests | Hardhat — heavier Node toolchain for no gain here |
| Primary chain | Base Sepolia (84532) | Live faucet, ~2 s blocks, public explorer | Polygon Amoy — official faucet retired, public RPC deprecated. Ethereum Sepolia — mid-transition, support winding down |
| Second anchor | OpenTimestamps → Bitcoin mainnet | Free, no wallet, permanent, independent chain | — |
| Offline chain | Anvil | Demo cannot fail on RPC flake | Ganache — unmaintained |

### Rejected: a per-platform API layer

An earlier revision of this document proposed a second search layer that resolved an identity string to accounts on each platform (`app.bsky.actor.searchActors`, GitHub user search, YouTube channel search, Mastodon account search) and then verified their images. It was **built as a probe, validated, and then deliberately dropped.**

It worked — `scripts/probe_identity_route.py` scored 14 real images from `barackobama.bsky.social`, returning 10 above threshold with a best of 0.7506 and clean separation (next non-Obama face at 0.166). The plumbing is sound and the probe is kept for reference.

It was dropped because it solves a problem web detection already solves, at much higher cost:

| | Web detection | Per-platform APIs |
|---|---|---|
| Platform reach | Every platform Google indexed — **proven: instagram, facebook, youtube, x, reddit** | Only platforms with a free search API |
| X / Twitter | reachable via indexed links | **impossible** — no free tier since Feb 2026, pay-per-use at $0.005/read |
| Instagram / Facebook / TikTok | reachable via indexed links | **impossible** — no public search API |
| Integrations to build and maintain | 1 | 1 per platform |
| Depends on an identity string being extracted correctly | no | yes — an extra failure mode |

The one genuine advantage the platform route had: it fetches images from the platform's own CDN at verification time, whereas web detection verifies against Google's cached thumbnail. That is a real difference in evidence integrity, and it is handled by recording *exactly what was fetched and hashed* in the evidence bundle rather than by adding a whole subsystem.

## 5a. Local demo UI (amended 5 Sep 2026, prd.md G8/S13)

A thin FastAPI app under `webapp/` that calls the exact same `pipeline.*` functions the CLI calls — `face/`, `search/`, `verify/`. No duplicate logic, no separate scoring path. Its only job is to make the run legible on a screen for the recording.

```
webapp/
├─ server.py          FastAPI app, 3 endpoints, in-memory run state
├─ static/
│   ├─ index.html      webcam capture (getUserMedia) + candidate table
│   └─ app.js          fetch() calls to the endpoints below, no framework
```

| Endpoint | Calls | Returns |
|---|---|---|
| `POST /api/scan` | `face.detect`, `face.quality`, `face.align`, `face.embed`, `face.liveness` | aligned crop (base64 PNG), liveness verdict (3-state), face pixel size, embedding provenance hash (never the vector — R-01) |
| `POST /api/upload` | same as `/api/scan` but liveness reported `N/A` (D-20) | same shape as `/api/scan` |
| `POST /api/search/{run_id}` | `search.orchestrator.gather`, `verify.matcher` | ranked candidate list: score, source, domain, decision, reason — the same fields as the `rich` CLI table |
| `GET /api/run/{id}` | reads `runs/<id>/` | full run for redisplay |

Runs on `127.0.0.1` only, no auth, single local user — acceptable for a local demo tool, called out explicitly in the README as not suitable for any network-exposed deployment. It is not a second implementation to maintain: if `orchestrator.gather` changes shape, the UI breaks loudly (a Python import error) rather than silently drifting from the CLI.

**UI presentation rules (R-21), driven by the §2a measurements:**

1. Two input tabs: **Webcam** and **Upload image**. Both first-class (D-19). Upload is not a fallback — it is the only way to probe a public figure, which the web-detection path needs.
2. Liveness renders three distinct states, never two: `LIVE` (green), `SPOOF` (red, blocks the run), `N/A — provenance unverified` (neutral grey, uploads only).
3. On `NO_MATCH`, the verdict is the headline. The candidate list **collapses behind a "show diagnostics" toggle** and is captioned as rejected candidates. Scores in the 0.0–0.2 noise band must never be presented as ranked suggestions.
4. Every row shows the reject reason, and the threshold and margin in force are always visible so a score can be read against them.

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
│   ├─ face/        liveness.py  detect.py  align.py  embed.py  quality.py  types.py
│   ├─ search/      base.py  orchestrator.py  image_prep.py
│   │               web_detect.py       # PRIMARY: gcv (primary) + serpapi backends
│   │               media_urls.py       # G1/G1.1: platform CDN size-variant + URL
│   │                                   #   derivation knowledge (X/Twitter dual scheme,
│   │                                   #   YouTube thumbnail tiers). R-23: any rewrite
│   │                                   #   must keep the provider's original URL in the
│   │                                   #   resulting fallback chain, never drop it.
│   │               bluesky.py          # keyless fallback, closed corpus
│   ├─ verify/      dedupe.py  allowlist.py  matcher.py  calibrate.py  pipeline_run.py
│   ├─ evidence/    canonical.py  commitment.py  bundle.py  c2pa.py  ipfs.py
│   │               # bundle.py schema v2 (5 Sep 2026): post.content_kind,
│   │               # match.image_phash, match.verified_against. See design.md 4.2.
│   ├─ chain/       evm.py  ots.py  reverify.py  abi/
│   ├─ audit/       run_log.py
│   └─ cache/       http_cache.py
├─ contracts/
│   ├─ src/EvidenceRegistry.sol         # deployed live to Anvil, F8/F9 (see phases.md)
│   ├─ test/EvidenceRegistry.t.sol
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
| `SERPAPI_KEY` | none | SerpApi web-detection backend reports `available() == False`, skipped silently |
| `GCV_API_KEY` | none | Google Cloud Vision `WEB_DETECTION` backend skipped. Either backend alone is sufficient |
| `WEB_DETECT_BACKEND` | `auto` | `auto` uses whichever key is present, preferring `gcv` for its larger quota. Force with `serpapi` or `gcv` |
| `MIN_FACE_PX` | `50` | quality gate, derived in §2a. Faces smaller than this are rejected, not scored |
| `BLUESKY_CRAWL_LIMIT` | `300` | fallback-provider corpus size. Lowered from 2000: measured ~86 faces per 200 posts at ~27 s, and the fallback is a demo aid, not the primary path |
| `MATCH_THRESHOLD` | from `calibration/threshold.json` | never hardcoded inline (R-09) |
| `MATCH_MARGIN` | from `calibration/threshold.json` | — |
| `EVM_RPC_URL` | `http://127.0.0.1:8545` | falls back to local Anvil |
| `EVM_CHAIN` | `anvil` | `base-sepolia` to go public |
| `EVM_PRIVATE_KEY` | none | anchoring disabled, pipeline still runs |
| `PINATA_JWT` | none | IPFS skipped, bundle stays local |
| `HTTP_CACHE` | `1` | set `0` to force live fetches. **Leave on** — it is what protects the SerpApi quota (R-04) |

## 9. Failure isolation

The demo must survive any single external dependency failing. How each is contained:

| Dependency | If it fails | Containment |
|---|---|---|
| SerpApi quota exhausted | that backend returns `[]` | Switch to the GCV backend (1,000/mo vs 100/mo). Cached responses still replay offline |
| Both web-detection backends down | primary path returns `[]` | Bluesky fallback still demonstrates the verifier, but **this is a degraded run and the UI must say so** — it is no longer a web search |
| Bluesky API | fallback returns `[]` | Primary path is unaffected |
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
