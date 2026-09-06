# Architecture — face-chain-verify

**Companion docs:** [`prd.md`](./prd.md) (what and why) · [`design.md`](./design.md) (algorithms and signatures) · [`rules.md`](./rules.md) (invariants) · [`ANALYSIS.md`](./ANALYSIS.md) (why this shape won)

This document covers structure: components, boundaries, data flow, and stack decisions. It does not repeat algorithm detail — that lives in `design.md`.

---

## 1. System shape

**Revised 7 Sep 2026** to match the code as it actually stands (commit
`0a62f17`). The previous version of this diagram showed `chain/ots.py` and
`evidence/ipfs.py` as built modules and `c2pa.py` in the evidence layer —
none of those exist; OpenTimestamps, IPFS, and C2PA were all deferred (§9 in
`design.md`) and never implemented. It also showed SerpApi Lens as primary
with GCV second, which is the reverse of `pipeline/config.py`'s actual
`_resolve_backend()` ordering (GCV first for its ~10× larger free quota,
unless `SEARCH_LENS_UPLOAD=1`, in which case Lens goes first because the
upload makes it reachable). Both corrected below.

```
                        ┌──────────────────────────────┐        ┌──────────────────────┐
                        │        cli.py (typer)        │        │  webapp/ (FastAPI)   │
                        │  version scan search         │        │  same pipeline calls │
                        │  run-all anchor verify serve │        │  static/index.html   │
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
      │ liveness.py  (3-state)        │ search/base.py (Protocol)     │ evidence/
      │ detect.py    (YuNet)          │  ├─ web_detect.py  PRIMARY    │  canonical.py
      │ align.py     (5-pt affine)    │  │    ├ gcv      WEB_DETECTION│  bundle.py (v3)
      │ embed.py     (ArcFace 512-d)  │  │    │   (default first)     │  artifacts.py
      │ quality.py   (size gate)      │  │    └ serpapi  Lens         │  commitment.py
      │ headcrop.py  (search query    │  │        (needs a public URL │
      │   representation, gated on    │  │         — see uploader.py) │ chain/
      │   R-27; not the embedding)    │  ├─ uploader.py  (SerpApi     │  evm.py
      │                               │  │    direct upload (off by   │  reverify.py
      │                               │  │    default — see §4.2)    │  tamper.py
      │                               │  ├─ expand.py    (F3: 4-tier │
      │                               │  │    profile expansion,     │ contracts/
      │                               │  │    face/linked/conjecture)│  EvidenceRegistry.sol
      │                               │  ├─ serp_resolve.py (LinkedIn│
      │                               │  │    + Instagram owner via  │
      │                               │  │    Google SERP, not the   │
      │                               │  │    blocked platform)      │
      │                               │  └─ bluesky.py   keyless      │
      │                               │       fallback, closed corpus │
      │                               │                               │
      │                               │ verify/                       │
      │                               │  ├─ dedupe.py   (phash)       │
      │                               │  ├─ allowlist.py              │
      │                               │  ├─ resolver.py (OG/oEmbed    │
      │                               │  │   cascade, last-resort)    │
      │                               │  ├─ matcher.py  (cosine, the  │
      │                               │  │   ONLY accept/reject site) │
      │                               │  └─ pipeline_run.py (shared   │
      │                               │      loop, CLI + UI both call)│
      └───────────────┬───────────────┴───────────────┬───────────────┘
                      │                               │
                      ▼                               ▼
              ┌───────────────┐             ┌───────────────────┐
              │ audit/        │             │ cache/            │
              │ run_log.py    │             │ http_cache.py     │
              │ → runs/<id>/  │             │ urlguard.py (R-29)│
              └───────────────┘             │ → .cache/         │
                                             └───────────────────┘
```

Two cross-cutting concerns deliberately sit outside the three stages:

- **`audit/`** records every decision. It is what makes the search provably genuine, so it is not optional plumbing — it is a deliverable.
- **`cache/`** intercepts every outbound HTTP call. It protects the SerpApi free quota and makes runs reproducible offline (R-04), and — since 7 Sep 2026 — validates every candidate/page URL against SSRF before any fetch happens (`urlguard.py`, R-29): scheme, DNS resolution to a public IP, redirect-hop re-validation, streaming size cap, and real image magic bytes rather than a trusted `Content-Type`.

**Not built, and not implied to be:** IPFS pinning, OpenTimestamps, C2PA manifests. All three were evaluated and deliberately deferred (`design.md` §9) rather than half-implemented. `chain/evm.py` writes an empty string for the on-chain CID field rather than fabricating one.

## 2. Data flow

**Revised 7 Sep 2026.** The previous diagram ended at `ipfs.py ──► CID` and
`chain/ots.py ──► .ots proof`, neither of which exists. It also stopped at
`matcher.py`'s verdict without showing the F3 profile-expansion stage that
now runs after a MATCH, or the F1 backend-escalation logic that decides
which search backend actually runs. Both are real, tested, and load-bearing
for the current LinkedIn/cross-platform behaviour — see §9.3 in
`docs/architecture-deep-dive.md` for the investigation that added them.

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
                                        │  probe (never leaves this stage
                                        │  in reversible form — R-01)
                                        ▼
                    ┌───────────────────────────────────────────┐
                    │  image_prep.py — one or two representations│
                    │    original photo (D-34: NOT the 112px    │
                    │      aligned crop — GCV/Lens need context) │
                    │    + head crop (headcrop.py), IF            │
                    │      use_head_crop=True (default False,     │
                    │      pending the R-27 measurement — §4.3)   │
                    └──────────────────┬──────────────────────────┘
                                       │
                    ┌──────────────────▼────────────────────────┐
                    │  WebDetectProvider        PRIMARY          │
                    │  quality-triggered ESCALATION (F1/F2):     │
                    │  tries backend #1; if result_carries_      │
                    │  identity_signal() is False (no full/      │
                    │  partial match_kind, no allowlisted URL,   │
                    │  only generic entities), tries backend #2  │
                    │  and merges — NEVER escalates on candidate │
                    │  count alone (that already caused a real   │
                    │  bug once, see memory.md §3d)               │
                    │    backend gcv:     WEB_DETECTION, raw     │
                    │                     bytes, default first   │
                    │    backend serpapi: google_lens, needs a   │
                    │                     public URL — goes first│
                    │                     only if                │
                    │                     SEARCH_LENS_UPLOAD=1   │
                    └──────────────────┬──────────────────────────┘
                                       │
                    ┌──────────────────┴────────────────────┐
                    │  BlueskyProvider   keyless FALLBACK   │
                    │  closed corpus, labelled as such      │
                    └──────────────────┬────────────────────┘
                                       │  list[Candidate]
                                       ▼
                       cache/urlguard.py — SSRF check on every
                       candidate/page URL BEFORE fetch (R-29)
                                       │
                                       ▼
                             dedupe.py  (perceptual hash)
                                       │
                                       ▼
                       allowlist.py  social-domain filter
                                       │
                                       ▼
                       download each candidate image (cached)
                       verify/resolver.py: last-resort OpenGraph/
                       oEmbed/Reddit .json cascade if the direct
                       fetch fails (additive only — R-23)
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
                          threshold + margin rule (R-03: the
                          ONLY place accept/reject is decided)
                                       │
                    ┌──────────────────┴───────────────────┐
                    ▼                                      ▼
             MatchResult{best, runner_up,           audit/run_log.py
             all_scored, verdict}                   every reject + reason
                    │
                    │  IF verdict == MATCH:
                    ▼
             search/expand.py (F3) — profile expansion,
             gated strictly on an already-accepted match
               ├─ origin="face"       (re-scored, counts as a match)
               ├─ origin="linked"     (claim on the verified page,
               │                       unscored — linked-claim)
               └─ origin="conjecture" (same-handle guess, unscored,
                                       weaker than linked — conjecture-claim)
             serp_resolve.py — LinkedIn/Instagram owner via Google
             SERP, since the platform itself blocks direct fetch
                    │
                    ▼
             evidence/bundle.py ──► canonical.py ──► sha256/keccak256
                    │
                    └──────────────────────► chain/evm.py  ──► real tx hash,
                                              real block, real eth_call
```

**The load-bearing property:** the box labelled "SAME face core, re-applied" is the same code that produced the probe embedding. Web detection returns *visual similarity*, which includes wrong people; our ArcFace re-verification is what converts that into a face match. That stage is the project's actual technical contribution, and it is why provider scores are never trusted (R-03). Profile expansion inherits this property exactly: an `origin="face"` expansion candidate is re-scored by this same stage, never trusted on the strength of a matching handle alone (R-28).

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
| `search/` | `Candidate`, HTTP, provider APIs, SERP resolution, expansion | face models, cosine scoring, chains |
| `verify/` | embeddings, candidates, thresholds | provider internals, HTTP, chains |
| `evidence/` | match results, canonical JSON, artifact digests | face models, providers, chain RPC |
| `chain/` | 32-byte hashes, RPC, tamper mutation of scratch copies | faces, providers, images |
| `audit/` | everything, write-only | nothing calls back into it |
| `cache/` | raw HTTP bytes, URL safety (SSRF) | face models, scoring decisions |

Three rules that follow from this table and are enforced in `rules.md`:

- A provider **never** scores a face. It returns candidates. Scoring is `verify/matcher.py` alone — including candidates discovered by `search/expand.py`'s profile expansion, which re-enters this exact stage rather than trusting a matching handle (R-03, R-28).
- `chain/` never sees an image or an embedding. It receives hashes. This is how R-01 (no biometrics on chain) is enforced structurally rather than by discipline.
- `cache/urlguard.py` validates every outbound URL before `cache/http_cache.py` fetches it (R-29) — a search-layer concern implemented in the cache layer, since it is the one place every external fetch is guaranteed to pass through.

## 4. The provider interface

Adding a search source must never require touching the face core, the matcher, or the chain layer. Everything hangs off one small contract, read directly from `pipeline/search/base.py` (grown considerably since the fields below were first written — this is the real current shape, not the original design sketch):

```python
@dataclass(frozen=True)
class Candidate:
    image_url: str                     # candidate image to download and verify
    page_url: str                      # the post/page it appeared on
    source: str                        # provider name, for the audit log
    provider_score: float | None = None  # recorded, NEVER used to decide  (R-03)
    raw: dict = field(default_factory=dict)     # untouched provider response
    post_meta: dict | None = None      # author/text/timestamp if provider supplies it
    image_url_fallbacks: tuple[str, ...] = ()    # additive size-variant chain (R-23) —
                                                  # the provider's original URL is always
                                                  # a member, never replaced
    match_kind: str = "unknown"        # provider's own image-identity claim:
                                        # "full" | "partial" | "similar" | "page" | "unknown"
                                        # — diagnostic only, R-03
    origin: str = "face"               # "face" | "linked" | "conjecture" (R-28, F3) —
                                        # only "face" candidates are ever scored/counted

class SearchProvider(Protocol):
    name: str
    requires_credentials: bool
    def available(self) -> bool: ...
    def search(self, search_image_bytes: bytes, probe_vec) -> list[Candidate]: ...

@dataclass(frozen=True)
class ProviderReport:
    """One per provider PER ATTEMPT — a backend that gets escalated past
    (F1/F2) produces two reports in one run, both retained, never one
    overwriting the other."""
    name: str
    available: bool
    attempted: bool
    latency_ms: float | None = None
    candidates_returned: int = 0
    error: str | None = None
```

`available()` lets the orchestrator skip unconfigured providers silently, which is what makes the zero-API-key quickstart work (S10 in `prd.md`).

`origin` is the field that makes profile expansion (§4.4 below) honest: it is impossible for an expansion-discovered candidate to be counted as a match without also being `origin="face"`, which itself only happens after re-scoring — see `search/expand.py` and R-28.

## 4.1 Backend escalation — quality-triggered, never count-triggered

`web_detect.py`'s `WebDetectProvider` no longer picks one backend and commits to it for the whole run. It tries a backend, checks whether the result actually carries an identity signal, and only escalates to the second backend if it doesn't:

```python
def result_carries_identity_signal(candidates, identity_signals) -> bool:
    """True iff at least one candidate has match_kind in {"full","partial"},
    OR at least one candidate sits on an allowlisted social domain with a
    retrievable image, OR at least one identity_signal is not in
    GENERIC_ENTITIES (a stop-list of {"human","person","face","photograph",
    "portrait","chin","forehead","head","smile","hair","eyebrow"} — generic
    vision-API labels that carry no identity information)."""
```

This exists because a naive "did we get candidates back" check was already
proven wrong once: a GCV response can return 20 candidates that are all
`visuallySimilarImages` lookalikes with `identity_signals=["Human"]` — 20
results, zero actual signal. Escalating on count alone would never trigger in
that case, which is exactly the failure this function closes.

Backend order depends on `SEARCH_LENS_UPLOAD`: `gcv` first when it's off
(the default — GCV takes raw bytes and has ~10× the free quota, D-28); `serpapi`
first when it's on (Lens is now reachable via `uploader.py` and answers a
different, often more useful, question — "where does this exact image
appear" rather than "what looks similar").

## 4.2 Uploader — the narrowest version of a hop every Lens-based competitor needs

SerpApi's `google_lens` engine is documented around an image URL, but it also
exposes a direct upload endpoint — `POST https://serpapi.com/image` returns
an `image_id` that `engine=google_lens` accepts as `image_id=` (verified
live: the endpoint answers 401 without a key, and the limits are documented
at `serpapi.com/image-api`: JPG/PNG/WebP, 500 KB max). Discovered in the
hhgoa-provenance review; adopted 7 Sep 2026, **replacing the earlier imgbb
hop** (retained as a historical record in `docs/memory.md`).

That changes the trade this module makes, in our favour:

- Uploads **only the background-removed head crop** (`headcrop.py`'s
  output), never the original photograph. This is structurally enforced —
  `upload_crop_to_serpapi()` takes a keyword-only `is_head_crop: bool` and
  **raises `ValueError` if it is `False`**, so passing the original photo
  through this function is a hard error, not a comment someone can ignore.
- The crop is held by SerpApi for ~10 minutes and is never placed on a
  public image host. The earlier imgbb design published a (masked) head
  crop to a third-party host; that disclosure is gone, not softened.
- Gated by `SEARCH_LENS_UPLOAD` (default `0`). A fresh clone with no `.env`
  changes never uploads anything — sending the head crop to SerpApi is a
  disclosure the user opts into explicitly.
- Upload failure degrades to a recorded skip reason, never a crash; the GCV
  path is unaffected (R-14).

Why the imgbb variant was retired: one fewer API key, one fewer third-party
host touching biometric data, one fewer service that can fail
mid-recording — same escalation behaviour.

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
| **Primary chain** | **Anvil** (local, chain id 31337) — reaffirmed D-27/D-43 | The brief explicitly permits a local/simulated chain. A judge clones the repo and runs the full anchor→verify→tamper cycle with zero account, zero faucet, zero funds, and the required key (Anvil's well-known default account) is deliberately never read from `.env` so it can never be mistaken for a real secret | Base Sepolia — kept as a documented opt-in bonus (`EVM_CHAIN=base-sepolia`, R-15: identical code path) but never on the recording's critical path; a dry faucet or RPC flake must not be able to break a take |
| ~~Second anchor~~ | ~~OpenTimestamps~~ | **Deferred, never built** — `chain/ots.py` does not exist. Removed from this table 7 Sep 2026 after it was found still listed here despite `design.md` §9 correctly marking it deferred | — |
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

**Revised 7 Sep 2026** — the endpoint table below previously listed 4
endpoints and was missing 5 real ones added by G4 and Tier 2 (anchor, verify,
tamper, and the Evidence Explorer's run-listing endpoints).

| Endpoint | Calls | Returns |
|---|---|---|
| `GET /` | — | `static/index.html` |
| `POST /api/scan` | `face.detect`, `face.quality`, `face.align`, `face.embed`, `face.liveness` | aligned crop (base64 PNG), liveness verdict (3-state), face pixel size, embedding provenance hash (never the vector — R-01) |
| `POST /api/upload` | same face core as `/api/scan`; liveness reported `N/A — provenance unverified` (D-20, R-22) | same shape as `/api/scan` |
| `POST /api/search/{run_id}` | `verify.pipeline_run.run_pipeline` — the ONE shared verification loop the CLI's `search`/`run-all` also call | full `MatchResult` including F3 expansion candidates, verdict, threshold/margin in force |
| `POST /api/anchor/{run_id}` | `chain.evm.EvmClient.anchor` | tx hash, chain id, block number, evidence hash |
| `POST /api/verify/{run_id}` | `chain.reverify.reverify_bundle` | one of `PASS`/`ARTIFACT_MISMATCH`/`ARTIFACT_MISSING`/`BUNDLE_MODIFIED`/`NOT_ANCHORED`/`ERROR`, plus per-artifact check detail |
| `POST /api/tamper/{run_id}?mode=…` | `chain.tamper.tamper_run` on a **scratch copy only** — never `runs/<id>/` itself | the resulting verdict for the chosen mode (`swap-artifact` / `edit-bundle` / `forge-bundle`) |
| `GET /api/runs` | reads `runs/` | summary of every run present: verdict, score, platform, anchored status, schema version — the Evidence Explorer panel's data source |
| `GET /api/run/{run_id}` | reads `runs/<run_id>/` | full bundle, anchor record, audit log, and on-disk artifact list for one run |

Runs on `127.0.0.1` only, no auth, single local user — acceptable for a local demo tool, called out explicitly in the README as not suitable for any network-exposed deployment. It is not a second implementation to maintain: if `pipeline_run.run_pipeline`'s shape changes, the UI breaks loudly (a Python import error) rather than silently drifting from the CLI, since both call the exact same function.

**UI presentation rules (R-21), driven by the §2a measurements:**

1. Two input tabs: **Webcam** and **Upload image**. Both first-class (D-19). Upload is not a fallback — it is the only way to probe a public figure, which the web-detection path needs.
2. Liveness renders three distinct states, never two: `LIVE` (green), `SPOOF` (red, blocks the run), `N/A — provenance unverified` (neutral grey, uploads only).
3. On `NO_MATCH`, the verdict is the headline. The candidate list **collapses behind a "show diagnostics" toggle** and is captioned as rejected candidates. Scores in the 0.0–0.2 noise band must never be presented as ranked suggestions.
4. Every row shows the reject reason, and the threshold and margin in force are always visible so a score can be read against them.
5. **Added 7 Sep 2026 (F3):** a `MATCH` run's candidate table renders **four** visually distinct row states, not two: `ACCEPT`/`corroborating` (scored, real face matches) render normally; `linked-claim` rows render with a subtle amber background; `conjecture-claim` rows render with a subtle violet background. Both unscored tiers show their basis text (e.g. "claimed profile link on verified page (unscored)") where a score would otherwise appear — never a bare dash, and never a fabricated number. Neither tier is counted in the match-count summary line.
6. Every externally-sourced string (candidate `page_url`, `source`, reason text) is escaped before reaching the DOM, and a `page_url` must validate as `http`/`https` before becoming an `href` — otherwise it renders as inert text (R-26).
7. An always-visible **Evidence Explorer** panel lists every run under `runs/` and lets a judge verify or run any of the three tamper modes against a past run without performing a new scan first — the chain buttons are no longer gated on a fresh in-session `MATCH` (§5b, D-42).

## 5b. Verification is a separate trust domain (Tier 2, 7 Sep 2026)

Added after a source-level review of three competitor repos found a real hole
in ours (`memory.md` §3w, rule R-25). The boundary is worth stating explicitly
because it was violated by omission rather than by a bad decision.

**The anchor path and the verify path must not share a source of truth.**

```
ANCHOR TIME                          VERIFY TIME
-----------                          -----------
image bytes  --sha256-->  digest     file on disk --sha256--> digest'
                            |                                   |
                     bundle[digest]                             |
                            |                     bundle[digest] vs digest'
                        keccak256                        (must be compared)
                            |                                   |
                     anchor.json + chain            rebuilt bundle -> keccak256
                                                            |
                                                     vs anchor.json + chain
```

The defect was that `reverify.py` recomputed the keccak of `evidence.json` and
compared it to `anchor.json` — both sides ultimately deriving from the stored
bundle. The recorded `image_sha256` was never compared against the bytes on
disk, so an artifact swap passed. Structural fix:

- `evidence/artifacts.py` owns the artifact list and per-file digest checks.
- `rebuild_from_artifacts()` is the **only** function permitted to construct
  the structure that gets verified, and it recomputes every digest from disk.
  No caller may hand it a digest.
- `chain/reverify.py` consumes that output. It has no path that reads a digest
  out of the stored bundle and treats it as verified.

Verification reads **local disk plus one `eth_call`, and never re-fetches a
hosted URL.** A candidate's platform image may 403, expire, or be deleted, and
search-engine cache URLs expire by design; a verifier that depends on them
would report tampering on healthy evidence. What was fetched at anchor time
(`match.verified_against`) and what the verifier checked are deliberately
separate questions.

`chain/tamper.py` sits in the same domain and is the adversary: it operates
only on a copied run directory in a temp path, mutates **source artifacts**
rather than digest fields, and is covered by a test asserting the real
`runs/<id>/` is byte-identical before and after.

## 5c. Untrusted input reaches the DOM (Tier 2)

Candidate `page_url`, `source`, provider titles, and reason text all originate
outside this process. Treating them as trusted in the UI was a real defect
(R-26): they were interpolated raw into an `href` attribute and into table
cells while `escapeHtml()` sat unused in the same file.

Boundary: **`webapp/static/app.js` treats every field of a candidate as
hostile.** External values are escaped or set via `textContent`/`setAttribute`,
and a `page_url` must validate as `http`/`https` before it may become an
`href` — otherwise it renders as inert text. Covered by `tests/test_web_xss.py`,
a test category that did not previously exist.

## 6. Directory layout

**Revised 7 Sep 2026** to match the actual tree (`docs/` now holds every
planning document, and the following pipeline modules were added by the
Tier 2 hardening pass and did not exist when this section was last written:
`evidence/artifacts.py`, `chain/tamper.py`, `search/expand.py`,
`search/serp_resolve.py`, `search/uploader.py`, `cache/urlguard.py`,
`face/headcrop.py`, `verify/resolver.py`). `evidence/c2pa.py` and
`chain/ots.py`, previously shown below, were never built.

```
face-chain-verify/
├─ docs/
│   ├─ prd.md  architecture.md  design.md  rules.md  phases.md  memory.md
│   ├─ ANALYSIS.md
│   ├─ architecture-deep-dive.md   # long-form companion to this file;
│   │                               #   source-level competitor analysis lives here
│   └─ recording-beat-sheet.md
├─ README.md
├─ .env.example  .gitignore  requirements.txt  pyproject.toml
├─ models/                        # gitignored, fetched by scripts/fetch_models.py
│   ├─ face_detection_yunet_2023mar.onnx
│   ├─ w600k_r50.onnx
│   └─ anti_spoof.onnx
├─ scripts/
│   ├─ fetch_models.py            # idempotent model download + sha256 check
│   ├─ warmup.py                  # pre-load sessions + readiness check before recording
│   └─ probe_query_representation.py  # R-27 measurement: original vs head-crop vs merged
├─ pipeline/
│   ├─ __init__.py  config.py  cli.py
│   ├─ face/        liveness.py  detect.py  align.py  embed.py  quality.py  types.py
│   │               headcrop.py         # T2.5: search-query representation only,
│   │                                   #   never the embedding input (R-08)
│   ├─ search/      base.py  orchestrator.py  image_prep.py
│   │               web_detect.py       # PRIMARY: gcv + serpapi backends, quality-
│   │                                   #   triggered escalation between them (F1/F2)
│   │               uploader.py         # F1a-rev: head-crop-only DIRECT upload to
│   │                                   #   SerpApi for Lens (no public host),
│   │                                   #   off by default (SEARCH_LENS_UPLOAD)
│   │               expand.py           # F3: post-threshold profile expansion,
│   │                                   #   face/linked/conjecture origin tiers (R-28)
│   │               serp_resolve.py     # LinkedIn/Instagram resolution via Google SERP
│   │                                   #   instead of requesting the blocked platform
│   │               media_urls.py       # G1/G1.1: platform CDN size-variant + URL
│   │                                   #   derivation knowledge (X/Twitter dual scheme,
│   │                                   #   YouTube thumbnail tiers). R-23: any rewrite
│   │                                   #   must keep the provider's original URL in the
│   │                                   #   resulting fallback chain, never drop it.
│   │               bluesky.py          # keyless fallback, closed corpus
│   ├─ verify/      dedupe.py  allowlist.py  matcher.py  pipeline_run.py
│   │               resolver.py         # last-resort OpenGraph/oEmbed/Reddit .json
│   │                                   #   cascade, additive only (R-23)
│   ├─ evidence/    canonical.py  commitment.py  bundle.py  artifacts.py
│   │               # bundle.py schema v3 (7 Sep 2026): additive artifacts[] manifest.
│   │               # artifacts.py: rebuild_from_artifacts(), the function the whole
│   │               # re-verification claim rests on (R-25). See design.md 4.2/5.6.
│   ├─ chain/       evm.py  reverify.py  tamper.py
│   ├─ audit/       run_log.py
│   └─ cache/       http_cache.py  urlguard.py   # urlguard.py: SSRF hardening (R-29)
├─ contracts/
│   ├─ src/EvidenceRegistry.sol         # deployed live to Anvil, F8/F9 (see phases.md)
│   ├─ test/EvidenceRegistry.t.sol
│   └─ script/Deploy.s.sol
├─ calibration/    pairs/  roc.png  threshold.json  negatives_harvested.json
├─ runs/           <run-id>/  evidence.json  audit.json  match_image.<ext>  probe.png
├─ .cache/         gitignored HTTP cache
├─ webapp/         server.py  static/index.html  static/app.js
└─ tests/
```

**Deliberately absent, not merely unmentioned:** `evidence/ipfs.py`,
`evidence/c2pa.py`, `chain/ots.py`. These were evaluated and deferred
(`design.md` §9), never half-built. `chain/evm.py`'s `cid` field is written
as an empty string, not a fabricated placeholder.

## 7. Runtime artifacts

Every invocation of `run-all` produces one directory under `runs/`. This directory **is** the evidence, and selected runs are committed to the repo as proof of non-fabrication.

```
runs/2026-09-05T18-07-40Z/
├─ probe.jpg              the original photo submitted (D-34: NOT the aligned crop)
├─ evidence.json          canonical bundle, schema v3, the thing that gets hashed
├─ audit.json             every provider request, status, latency, candidate, decision
├─ match_image.jpg        the ACTUAL bytes fetched and scored for the winning candidate —
│                         real=verify time recomputes sha256/phash from THIS file (R-25)
└─ anchor.json            tx hash, chain id, block, contract address, evidence_hash
```

Filenames encode enough of the run's shape to be self-documenting when opened
on camera. `evidence.json`'s `artifacts[]` array names every file this
run's re-verification depends on — see `design.md` §5.6.

## 8. Configuration

Single source of truth in `pipeline/config.py`, loaded from `.env` with defaults that work with **no** `.env` present — that is what makes the zero-key quickstart possible. Table below is read directly from the current `Config` dataclass, not from an earlier design sketch.

| Var | Default | Effect if unset / left default |
|---|---|---|
| `SERPAPI_KEY` | none | SerpApi Lens backend reports `available() == False`, skipped silently |
| `GCV_API_KEY` | none | Google Cloud Vision `WEB_DETECTION` backend skipped. Either key alone is sufficient |
| `WEB_DETECT_BACKEND` | `auto` | `auto` orders backends by `SEARCH_LENS_UPLOAD` (see §4.1). Force with `serpapi` or `gcv` |
| `WEB_DETECT_ESCALATE` | `1` | set `0` to disable quality-triggered escalation and use only the first backend |
| `SEARCH_LENS_UPLOAD` | `0` | set `1` to enable `uploader.py`'s head-crop-only direct upload to SerpApi (`POST /image` → `image_id`) and let SerpApi Lens go first in `auto` ordering. The crop is held by SerpApi ~10 min, never a public image host. **Off by default on every fresh clone** |
| `EXPAND_SERP_MAX_CALLS` | `1` | caps how many Google SERP calls `expand.py`/`serp_resolve.py` may make per run, to protect SerpApi quota |
| `MIN_FACE_PX` | `50` | quality gate, derived in §2a. Faces smaller than this are rejected, not scored |
| `BLUESKY_CRAWL_LIMIT` | `300` | fallback-provider corpus size. Lowered from 2000: measured ~86 faces per 200 posts at ~27 s, and the fallback is a demo aid, not the primary path |
| `MATCH_THRESHOLD` | from `calibration/threshold.json` | never hardcoded inline (R-09) |
| `MATCH_MARGIN` | from `calibration/threshold.json` | — |
| `EVM_RPC_URL` | `http://127.0.0.1:8545` | falls back to local Anvil |
| `EVM_CHAIN` | `anvil` | `base-sepolia` to go public — opt-in bonus only, D-43 |
| `EVM_PRIVATE_KEY` | none on non-Anvil; Anvil's well-known default key on `anvil` | anchoring disabled on any chain but Anvil until a real key is supplied |
| `EVM_CONTRACT_ADDRESS` | none | must be set once the contract is deployed to the target chain |
| `HTTP_CACHE` | `1` | set `0` to force live fetches. **Leave on during development** — it is what protects the SerpApi quota (R-04); flip to `0` only for the actual recording take (I-10) |

**Removed from this table 7 Sep 2026:** `PINATA_JWT` — IPFS pinning was never built (§6), so this variable does not exist in `Config`.

## 9. Failure isolation

The demo must survive any single external dependency failing. How each is contained:

| Dependency | If it fails | Containment |
|---|---|---|
| First web-detection backend returns no real signal | `result_carries_identity_signal()` is False | Escalates to the second backend automatically (F1/F2), merges results |
| Both web-detection backends down | primary path returns `[]` | Bluesky fallback still demonstrates the verifier, but **this is a degraded run and the UI must say so** — it is no longer a web search |
| Bluesky API | fallback returns `[]` | Primary path is unaffected |
| Base Sepolia RPC (opt-in only) | anchor step errors | `EVM_CHAIN=anvil` is the default and is never depended on live — see D-43 |
| A candidate URL points at a private/internal address | `urlguard.assert_safe_url` raises before any fetch | Logged as `reject-unsafe-url` with the specific cause (R-29); the run continues |
| A profile expansion SERP call fails | `expand.py`/`serp_resolve.py` return `[]` | Enrichment only — the already-accepted MATCH from the primary search is unaffected (R-14) |
| Model download | startup fails | `scripts/fetch_models.py` is idempotent, run before recording |

The orchestrator treats a provider raising an exception as a provider returning zero candidates, logs it to the audit trail, and continues. No single provider can abort a run.

## 10. Extension points

Where future work plugs in without restructuring:

- **New search source** → implement `SearchProvider`, register in `orchestrator.py`. Nothing else changes.
- **New expansion target platform** → add a pattern to `search/expand.py` or a resolver to `search/serp_resolve.py`; the `origin` taxonomy and R-28's face-gating apply automatically to whatever it discovers.
- **Better detector** → swap `detect.py` internals; `DetectedFace` contract is stable.
- **GPU** → change the onnxruntime provider list in `embed.py`. Everything downstream is unaffected.
- **FAISS** → replace the matmul in `bluesky.py`'s index; `matcher.py` untouched.
- **EAS instead of custom contract** → new module beside `evm.py`; `reverify.py`'s artifact/bundle/on-chain checks are independent of which contract backs the third one.
- **zk threshold proof** → prove `cosine ≥ τ` without revealing the embedding. Noted as future work; out of scope for this task.
