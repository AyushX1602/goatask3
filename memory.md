# Memory — face-chain-verify

Living state file. Read this first when resuming work.

`phases.md` is the plan. **This is the state.** Update it at every phase boundary and whenever an assumption gets verified or a decision gets made.

**Last updated:** 5 September 2026
**Current phase:** Phase 2 — Provider interface + matcher
**Overall:** 2 of 14 phases complete

---

## 1. Where we are right now

Phase 0 complete and committed (`26744c4`). Starting Phase 1.

**Done**
- Deep research pass on all four layers, including a second pass specifically checking for a better architecture (`ANALYSIS.md`)
- Architecture chosen: Solution 4, scored 4.9 vs 4.2 / 3.8 / 2.0 for the alternatives
- Six planning documents written and slop-checked against the no-ai-slop skill (banned word `leverage` removed from `prd.md`)
- `.venv` created, all pinned deps installed cleanly on Windows / py312 — no build failures, confirming D-02 (skip `insightface`) and D-05 (skip FAISS) were the right calls
- Package tree scaffolded per `architecture.md` §6, module boundaries documented in each `__init__.py`
- `pipeline/config.py` — env loading, `MatchPolicy` (R-09 placeholder, clearly marked), commitment salt (R-01)
- `scripts/fetch_models.py` — all 3 models downloaded, sha256-verified, confirmed idempotent on re-run
- `pipeline/cli.py` stub — `python -m pipeline --help` and `version` both verified working
- `git init`, first commit `26744c4`

- Phase 1 complete and committed. `face/types.py`, `detect.py`, `align.py`, `embed.py` written. All 4 mandatory exit-criterion tests pass, plus 3 extra guard tests (canonical_kps ordering, multi-face sort, empty detection). Real measured numbers, not assumed:
  - same-person (obama1 vs obama2): cosine **0.7652**
  - different-person (miranda vs lacamoire): cosine **0.0645**
  - flip invariance: > 0.9 (test asserts and passes)
  - CPU timing: detect ~38 ms/img, embed ~48 ms/img (16-core CPU) — **Q5 resolved: stay on CPU**, no CUDA needed. A 2000-post crawl is ~3 min of inference.
  - A-04 resolved: `w600k_r50.onnx` has a dynamic batch axis (`input.1: [None, 3, 112, 112]`), confirmed via onnxruntime introspection, not assumed. Batched embedding path in `embed.py` is safe.
- Test fixtures sourced from `deepinsight/insightface` and `ageitgey/face_recognition` sample data (obama.jpg/obama2.jpg = same person, lin-manuel-miranda.png/alex-lacamoire.png = different people, t1.jpg = group photo). Stored under `tests/fixtures/`, **not gitignored** — these are small sample images from permissively-used public repos, not private data, so they are fine to commit for test reproducibility.

**Not done**
- No search providers, no matcher, no evidence, no chain code

**Next action:** Phase 2 — `search/base.py`, `orchestrator.py`, `verify/matcher.py`, `dedupe.py`, `allowlist.py`, `audit/run_log.py`, `cache/http_cache.py`.

---

## 2. Environment (verified 4 Sep 2026)

| Item | Value |
|---|---|
| Workspace | `d:\codes\hh goa face` |
| OS / shell | Windows, PowerShell — chain with `;` not `&&`, env vars are `$env:NAME` |
| Python | 3.12.10 at `C:\Users\ayush\AppData\Local\Programs\Python\Python312` |
| pip | 25.0.1 |
| venv | `.venv` created, **dependencies not yet installed** |
| git | 2.54.0.windows.1 |
| node | v24.15.0 (only needed if Hardhat is ever used — Foundry is the choice, so likely unused) |
| GPU | NVIDIA RTX 4050 Laptop + AMD Radeon 780M integrated |
| CPU | 16 logical processors |
| RAM | 15.2 GB |
| Free disk (D:) | 95.7 GB |

**GPU note:** CUDA is available in principle but onnxruntime-gpu needs matching CUDA + cuDNN, which is fiddly on Windows. Default to CPU. 16 cores should be ample for `buffalo_l`. Revisit only if Phase 1 timing is poor — that is open question Q5.

---

## 3. Decision log

Each entry: what, why, and what it costs us.

| # | Decision | Rationale | Trade-off accepted |
|---|---|---|---|
| D-01 | Solution 4 from `ANALYSIS.md` | Highest weighted score against the brief's actual grading | Largest scope of the four options |
| D-02 | **Do not use the `insightface` pip package** | Builds Cython extensions; commonly fails on Windows without VS Build Tools | Must write alignment and preprocessing ourselves. Upside: we own and can show that code |
| D-03 | **OpenCV YuNet** for detection, not SCRFD | Ships in opencv, returns bbox + 5 landmarks, all anchor decoding internal | Slightly lower recall than `det_10g`. Upgrade path documented |
| D-04 | ArcFace `w600k_r50.onnx` via onnxruntime for embedding | SOTA-class 512-d, CPU-viable, documented preprocessing | Model download required |
| D-05 | **numpy brute-force, not FAISS** | 512-d × 10k = 20 MB; matmul is sub-millisecond. One less wheel to break | Would need FAISS above ~10⁵ vectors. One-line swap |
| D-06 | **Bluesky before Google Lens** | Zero API keys, zero quota, zero signup → working pipeline on Day 1 | Closed-corpus search alone would not satisfy "search the web"; Lens is still required |
| D-07 | Blockchain deprioritised to Day 3 | Owner's call: judges want the pipeline, not deployment | Less time for the chain layer. Acceptable — it is the easy part |
| D-08 | No UI, no deployment | Brief says no website required | None |
| D-09 | Base Sepolia primary, Anvil fallback | Live faucet, ~2 s blocks, public explorer. Anvil means the demo cannot fail | Testnet has no economic finality — disclosed in README |
| D-10 | Avoid Polygon Amoy | Official faucet retired, public RPC endpoints deprecated | None, strictly worse option |
| D-11 | Avoid Ethereum Sepolia as primary | Mid-transition: Glamsterdam activation ~6 Oct 2026, support winding down | None |
| D-12 | Custom contract, not EAS | Shows contract authorship; EAS cited as the production path in README | Slightly more code than an EAS attestation |
| D-13 | **Reject PimEyes / Yandex scraping** | ToS violation + Yandex returns captcha pages as HTTP 200 → silent mid-demo failure | Lower open-web recall than a true face index. Correct call regardless |
| D-14 | FaceCheck.ID and Search4Faces disabled by default | Crypto-only prepayment / unverified freshness. A judge must be able to run the repo | Lower recall on the opt-in paths |
| D-15 | Salted commitment, never raw embeddings on chain | GDPR Art. 9 / DPDP Act; immutable ledger + biometric template is un-deletable | Cannot compare faces directly from chain data. That is the point |
| D-16 | `rich` console output treated as a deliverable | The recording *is* the submission; legibility at 1080p matters | Minor extra work |

---

## 4. Assumptions still to verify

Tracked from `design.md`'s `[A]` markers. **Verify before starting the dependent phase.**

| ID | Assumption | Blocks | Status |
|---|---|---|---|
| A-01 | Bluesky Jetstream (`wss://jetstream2.us-east.bsky.network/subscribe`) accepts a connection and emits post JSON | Phase 3 | unverified — fallback is public AppView polling |
| A-02 | Image URL pattern `https://cdn.bsky.app/img/feed_thumbnail/plain/{did}/{cid}@jpeg` resolves | Phase 3 | unverified — test against one real post first |
| A-03 | SerpApi Google Lens requires a publicly reachable image URL and cannot take raw bytes | Phase 5 | unverified — **largest unknown in the project** |
| A-04 | `w600k_r50.onnx` has a dynamic batch axis, so batches of 32 work | Phase 3 | unverified — fall back to a loop if fixed |
| A-05 | CPU inference is fast enough for a 2000-post crawl | Phase 3 | unverified — measure in Phase 1 |
| A-06 | Demo subject for the open-web path is indexed well enough for Lens | Phase 5 | unresolved, ties to Q1 |

### Verified facts worth not re-researching

| Fact | Source checked |
|---|---|
| YuNet output row is 15 values: `x y w h` + 5 landmark pairs + score | opencv_zoo |
| ArcFace 112×112 5-point template coordinates | InsightFace |
| ArcFace preprocessing: RGB, scale `1/127.5`, mean `127.5`, NCHW | InsightFace ONNX wrapper |
| Bluesky public reads need no auth via `public.api.bsky.app` | Bluesky docs |
| `app.bsky.feed.searchPosts` needs an app password — **we do not need it**, we filter on "has image" | Bluesky docs |
| Mastodon full-text search needs auth + ElasticSearch; use public timelines instead | Mastodon docs |
| SerpApi free tier ≈ 100 searches/month | SerpApi |
| Base Sepolia faucet: up to 0.1 test ETH / 24 h | Base docs |
| Polygon official faucet retired; public RPC deprecated | Polygon docs + forum |
| OpenTimestamps: free, no wallet, no API key; calendar servers pay Bitcoin fees; batched so fresh stamps are PENDING for ~1 h+ | opentimestamps.org |
| Pinata free tier: 1 GB + dedicated gateway | Pinata |
| Yandex returns captcha pages with HTTP 200 → silent scraper failure | scraping write-ups |

---

## 5. Open questions for the owner

| # | Question | Blocks | Status |
|---|---|---|---|
| Q1 | Who is the demo subject for the open-web path? Needs heavy indexed presence | Phase 5 | open |
| Q2 | Do we control a Bluesky account for the fallback demo? | Phase 3 | open |
| Q3 | Team size — can the contract track (Phase 10) run in parallel? | Phase 10 | open |
| Q4 | Recording tool chosen and legibility tested at 1080p? | Phase 13 | open |
| Q5 | CUDA for onnxruntime, or stay CPU? | Phase 3 | decide after Phase 1 timing |

---

## 6. Gotchas discovered

Things that will silently break. Re-read before touching the relevant code.

1. **Landmark ordering.** "Left eye" means different things in different libraries. Order geometrically by x-position, never by name. A mirrored alignment degrades accuracy badly and raises no error. Guard: the flip test in Phase 1.
2. **`setInputSize` on YuNet.** Must be called whenever frame dimensions change, or detection silently returns zero faces.
3. **Similarity transform, not full affine.** `estimateAffinePartial2D`, not `estimateAffine2D`. A full affine introduces shear and degrades ArcFace.
4. **Canonicalisation drift is the classic demo killer.** No floats in hashed structures, ever. Freeze the schema at the end of Phase 9.
5. **SerpApi quota is tiny.** Wire the cache before the first live call, not after. ~100 searches/month disappears in one debugging session.
6. **OTS confirmation is slow.** Stamp a bundle on Day 3 *morning* so a CONFIRMED Bitcoin proof exists for the recording. A live stamp will read PENDING.
7. **Model download on camera.** Run `scripts/fetch_models.py` and `scripts/warmup.py` before recording, or eat 40 seconds of dead air.
8. **PowerShell, not bash.** `;` not `&&`. `$env:NAME` not `%NAME%`.

---

## 7. Files written so far

| File | Purpose |
|---|---|
| `ANALYSIS.md` | Decision record: options considered, why Solution 4 won, what was rejected and why |
| `prd.md` | Requirements, goals, non-goals, success criteria, traceability, limitations |
| `architecture.md` | Components, boundaries, data flow, stack decisions, layout, config, failure isolation |
| `design.md` | Algorithms, exact preprocessing, signatures, schemas, tests |
| `rules.md` | 16 hard invariants + conventions + cut order |
| `phases.md` | 14 phases across 3 days with executable exit criteria |
| `memory.md` | This file — live state |

No source code yet.

---

## 8. Session log

### Session 2 — 5 Sep 2026
- Ran the `no-ai-slop` skill against all planning docs: 0 em-dash issues (remaining ones are table-cell separators, not rhythm crutches), 1 banned word (`leverage`) found and fixed in `prd.md`
- Completed Phase 0: installed deps (no build failures on Windows py312 — validates D-02/D-05), scaffolded `pipeline/` package tree with boundary docstrings, wrote `config.py`, wrote and **ran** `fetch_models.py` against real URLs (found via GitHub/HF API lookups, not guessed) — all 3 models verified by sha256, confirmed idempotent
- `git init`, first commit
- Model URLs/hashes now known-good, recorded in `fetch_models.py` itself as the source of truth — no need to re-derive them

### Session 1 — 4 Sep 2026
- Analysed the brief; identified that stage 2 (search) is the whole project and that "re-verify" is the graded part of stage 3
- Researched face libraries, four search strategies, the 2026 testnet landscape, and the provenance/attestation ecosystem
- Second research pass specifically hunting for a better approach. **Result: no better option on the search axis** — PimEyes scraping, Yandex scraping, and Search4Faces all rejected with reasons. **Found a genuinely better trust layer** — OpenTimestamps gives free Bitcoin *mainnet* anchoring, plus EAS and C2PA as options
- Wrote `ANALYSIS.md` (678 lines)
- Owner set the constraints: 3 days, search first, blockchain secondary, no UI, no deployment
- Revised the build order: Bluesky before Google Lens, contract work moved to Day 3
- Verified the environment; created `.venv`
- Dependency install started and **interrupted** — must be re-run
- Wrote the six planning documents

**Next session starts here:** Phase 0, install dependencies. Then Phase 1, the face core, and do not proceed past it until all four exit criteria are observed.
