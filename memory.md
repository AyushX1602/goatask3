# Memory — face-chain-verify

Living state file. Read this first when resuming work.

`phases.md` is the plan. **This is the state.** Update it at every phase boundary and whenever an assumption gets verified or a decision gets made.

**Last updated:** 5 September 2026 — Tier 1 ~two-thirds done (verdicts, GitHub allowlist, match_kind, consent quarantine); re-fetch check + headline selection remain
**Current phase:** G-SERIES (see `phases.md`). F1–F9 complete, G1 complete, G1.1 complete, Tier 0 complete, Tier 1 partial.
**Overall:** **All three brief requirements are met and observed live against the real running Anvil chain, and against a genuine non-celebrity face-only match (not a public figure — the strongest anti-hardcode evidence gathered so far).** 167 Python tests + 8 Foundry tests passing.

> **Read §3f–3o at the bottom of this file first** — they carry the current
> state. Sections 1–3e above are historical and describe a plan that has since
> been superseded four times (D-25..D-33 scope reset, then the G-series, then
> Tier 0, then Tier 1). In particular: the blockchain phase is no longer
> "stopped", `media_urls.py` now handles two twimg schemes plus GitHub
> derivation, schema is v2, `github.com` is now on the allowlist under a
> written principle (R-06), the matcher has 4 verdicts not 2, and the biggest
> open items are the repo still has **no root `README.md`** (required by the
> brief), a small remainder of Tier 1 (re-fetch check, headline selection),
> and **two sensitive test runs sit redacted in `calibration/quarantine/`
> (gitignored) and must never be committed, referenced, or recorded with —
> see §3m for why.**

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
- Phase 2 core built: `search/base.py` (Candidate, ProviderReport, SearchProvider protocol), `search/orchestrator.py` (parallel fan-out, R-14 isolation), `verify/matcher.py` (score_candidates — the one place accept/reject is decided, R-03), `verify/allowlist.py`, `verify/dedupe.py`, `audit/run_log.py`.
- `pipeline/face/liveness.py` built (MiniFASNetV2 ONNX). Preprocessing confirmed against the reference repo's own inference code (raw HWC->CHW, no normalisation, class index 1 = live, scale 2.7) — not guessed. Tested live: real photo scores 0.9999 live.
- `pipeline/search/bluesky.py` built and proven live against the real API (not mocked):
  - `getAuthorFeed`/`getFeed` need no auth; `searchPosts` returns 403 unauthenticated (confirmed live) — this is why the provider walks feed generators instead of using search.
  - Image embeds return ready-made `thumb`/`fullsize` CDN URLs — better than the A-02 assumption in `design.md`, no manual URL construction needed.
  - First version fetched images serially and **timed out at 90s for a 150-post crawl** (~1s/image, no error, just slow). Root-caused via direct timing, not guessed, then fixed with a `ThreadPoolExecutor(max_workers=16)` for image fetches specifically (feed pagination stays sequential — cheap, ~3s/50 posts). Result: 200 posts / 86 faces indexed in 27s.
- **Amended scope (owner request, this session):** a local demo UI is now in scope (`prd.md` G8/S13, `architecture.md` 5a, `phases.md` Phase 4a) — a thin FastAPI + plain HTML/JS layer for judges, calling `pipeline.face`/`pipeline.search` directly with zero duplicate logic. Owner's explicit instruction: **build the scanning interface, then stop before any blockchain work.**
- Built `webapp/server.py` (`/api/scan`, `/api/search/{run_id}`) and `webapp/static/{index.html,app.js}`. Wired `python -m pipeline serve`. Tested live end to end, not just started:
  - `/api/scan` on a real photo: face found, liveness 0.9999 live, det_score 0.945, aligned crop returned as base64 PNG.
  - `/api/search/{run_id}`: triggered a real 300-post Bluesky crawl (107 faces indexed), re-verified all 20 candidates with our own face core, correctly returned `NO_MATCH` (probe was Obama, not in this small random sample) — the honest-failure path (R-16) firing correctly, not a bug.
  - Confirmed `runs/<id>/audit.json` written with real candidate scores and the placeholder threshold (0.42, `is_placeholder=True` until Phase 7 calibrates).
  - Full request/response cycle for both endpoints exercised via direct HTTP calls (PowerShell's `Invoke-RestMethod` doesn't support multipart on 5.1, used Python `requests` instead) — server logs confirm 200 OK on all four calls made.

**Not done**
- No Google Lens / Bing / Mastodon providers (Phase 5-6, after the stop point but not yet started)
- No calibration yet — `MatchPolicy` is still the Phase 2 placeholder (`threshold=0.42, is_placeholder=True`). The demo UI is currently running on an undemonstrated threshold; Phase 7 must replace this before the accept/reject numbers mean anything beyond "plausible."
- No standalone `python -m pipeline crawl`/`search`/`run-all` CLI commands (Phase 3-4 as originally scoped) — the demo UI's `server.py` inlines a minimal version of that loop directly rather than calling a finished CLI pipeline function. This works today but is technical debt: if Phase 3/4 are built properly later, `server.py`'s inlined crawl-and-score block should be refactored to call that shared function instead of duplicating it.
- No evidence bundle, no chain code, no C2PA, no OpenTimestamps (Phases 9-11) — **deliberately stopped here per owner instruction.**

## 3c. F4-F6 complete — REAL MATCH achieved end to end, live

5 Sep 2026. Built the shared verification loop and wired it through the UI, per the FINAL PLAN in phases.md.

**F4 `verify/pipeline_run.py`** — the single shared candidate-verification loop both the CLI and UI call. Fetches every candidate concurrently, applies the quality gate (F2), scores with our own ArcFace, dedupes, and hands off to `matcher.score_candidates`. `webapp/server.py`'s previous inlined duplicate loop (flagged as debt back in the Phase 2/3 session) is now deleted — one implementation, matching architecture.md 5a.

**Real bug found and fixed during live testing, not assumed:** YuNet's detection confidence degrades on very large images. The approved demo subject's Wikimedia portrait (3356x2687) scored 0.63-0.77 for real faces — all below the 0.85 default threshold — while the same faces scored 0.94 on a 0.4x downscale. Fixed in `face/detect.py`: detection now runs on a copy capped at `max_detect_side=1600`, with bbox/landmarks rescaled back to source coordinates. Verified the rescale is numerically correct, not just plausible: the resulting embedding still scored 0.9691 cosine against a known-same-person fixture. Regression tests in `tests/test_detect_large_image.py`.

**D-31 partially resolved without S3.** Threading a `public_image_url` through `run_pipeline` -> `orchestrator.gather` -> `WebDetectProvider.search()` (inspected via signature so Bluesky, which doesn't accept the kwarg, is unaffected) means the SerpApi backend works today for any probe with a known public URL — including the approved demo subject's own Wikimedia portrait. Full S3 presigning for arbitrary local files remains future work, tracked as before.

**F5/F6 done together in the UI:** upload tab (D-19) alongside webcam, an optional public-URL field, 3-state liveness rendering (`LIVE`/`SPOOF`/`N/A — provenance unverified`, never conflating upload with a real pass — R-22), and the R-21 presentation fix: NO_MATCH is now the headline with candidates collapsed behind a "show diagnostics" toggle, captioned as rejected rather than ranked.

**Live result, through the actual running server, with the owner's real SerpApi key:**

```
scan:   face_found=True, det_score=0.933 (post-fix; was 0 before)
search: verdict=MATCH, 47 candidates examined, elapsed 5.2s
ACCEPT: https://www.instagram.com/p/Dc5KuZdDZiX/
        score 0.7685 (threshold 0.42, margin 1.7685)
breakdown: 25 reject-face-too-small, 18 reject-domain, 3 reject-no-face, 1 ACCEPT
```

This is the first real, live, end-to-end MATCH against an actual social post, through the actual server, with no mocking. It directly satisfies prd.md S14/S15/S18 and the brief's core "genuine search, not hardcoded" requirement — 44 of 47 candidates were rejected with individual, honest reasons, and the one accepted result is a real Instagram post with a defensible score.

**46 tests passing** (43 pre-existing + 3 new detect-large-image regression tests).

**Still open:** GCV key (D-30, unblocked, not yet provided), full S3 presigning for arbitrary uploaded files without a known public URL, Foundry/Anvil installation for F8.

---

## 3d. F7 complete — evidence bundle proven live, then STOPPED per explicit instruction

5 Sep 2026. Owner said "continue, stop before blockchain phase" — F7 built and verified live, then stopped exactly at the F7/F8 boundary as instructed.

**Installed `eth-hash[pycryptodome]`** for a real EVM-compatible keccak256 (verified it differs from SHA3-256 and from SHA-256 — a common mistake, tested explicitly in `test_keccak256_differs_from_sha256`).

**`evidence/canonical.py`** — `canonical_bytes` walks the structure and *raises* `NonCanonicalValueError` on any float, rather than silently coercing one. That distinction matters: silent coercion is exactly the kind of drift that breaks hash reproducibility without anyone noticing until a future `verify()` call fails. `bool` explicitly confirmed not to trip the float check (it's an `int` subclass in Python).

**`evidence/commitment.py`** — `face_commitment()` never touches anything but `keccak256(salt || quantised_vector)`. Tested that it: (a) reproduces identically given the same salt, (b) differs with a different salt, (c) **absorbs float noise** — two embeddings differing by 1e-6 per element (the kind of noise a re-encode or re-alignment would introduce) still produce an identical commitment, which is the entire point of quantising rather than hashing the raw float32 vector.

**`evidence/bundle.py`** — `build_evidence()` is the *only* place a bundle gets constructed, specifically so a future chain module can't build its own dict and drift from what gets hashed. Refuses to build a bundle for a `NO_MATCH` (R-16: that's an audit-log outcome, never an evidence bundle). Upload probes are forced to `liveness_label: not_applicable` regardless of what the liveness model itself reported (R-22), tested explicitly.

**Live proof, not just unit tests.** Re-ran the same Obama-portrait MATCH from the F4-F6 session through the actual running server:

```
verdict: MATCH
evidence_hash: 0xef13f0eaee8ada50228a5b0a079deb725364698d1828488715ac41cc56a11eb4
```

Then independently reloaded the persisted `runs/<id>/evidence.json`, re-parsed it, re-canonicalised it, and recomputed the hash from scratch in a separate process:

```
stored bytes match canonical_bytes(parsed): True
recomputed hash: 0xef13f0eaee8ada50228a5b0a079deb725364698d1828488715ac41cc56a11eb4   <- identical
```

That is the exact property a future `anchor()`/`verify()` pair depends on, proven at the hash layer with a real bundle, before any chain code exists.

**66 tests passing** (46 prior + 20 new evidence tests).

**STOPPED HERE, exactly at F7/F8, per explicit owner instruction: "stop before blockchain phase."** F8 (Foundry contract + Anvil deploy) not started. Foundry (`anvil`/`forge`/`cast`) confirmed still not installed — will need to be set up when blockchain work is authorised.

---

## 3e. Deep review pass — 1 real bug found and fixed, cleanup done

5 Sep 2026, on owner request ("do a deep check now and fix errors if any"). Ran pyflakes, re-read every non-trivial module line by line, and cross-checked code against the documented behaviour in architecture.md rather than just re-running existing tests (which were all passing and would not have caught this).

**Real bug found: the documented fallback behaviour did not exist in code.** `architecture.md` §9 states plainly: *"Both web-detection backends down -> Bluesky fallback still demonstrates the verifier, but this is a degraded run and the UI must say so."* `webapp/server.py`'s `search()` endpoint decided whether to use the primary path purely via `_web_detect.available()` — which only checks whether an API key exists, not whether a search will *succeed*. Concretely: SerpApi without a `public_image_url` raises internally (by design, `orchestrator.gather` correctly catches it per R-14), and that surfaced as the primary path silently returning zero candidates and `NO_MATCH`, with `degraded_closed_corpus=False` — i.e. the UI reported "we searched the web and found nothing" when the web search never actually ran.

Verified this precisely before fixing it:
```
available (key set): True
p.search(...) -> ValueError: serpapi backend needs a publicly reachable image URL
```

**Fix:** `search()` now runs the primary provider, then checks its actual `provider_reports` (did any provider return >0 candidates), and falls back to Bluesky if not — merging both attempts' `ProviderReport`s into one audit trail rather than silently swapping one for the other. Verified against the real running server with the exact failure scenario (SerpApi key present, no public URL): before the fix, `degraded=False, candidates=0`; after, `degraded=True, crawl_size=155, candidates=18`, and `audit.json` shows both the failed `web_detect` attempt (with its real error message) and the successful `bluesky` fallback.

Added `tests/test_server_fallback.py` — 3 tests through FastAPI's real `TestClient` (not a stub), including one that asserts the audit trail contains both provider attempts by name.

**Cleanup, no behavioural change:**
- Removed 4 pyflakes-flagged unused imports (`numpy` in `matcher.py`, `time` in `pipeline_run.py`, `sha256_hex` re-export in `bundle.py`) and one unused local (`biggest_ok` in `pipeline_run.py`).
- Removed dead config: `bluesky_seed_handles` was parsed from `BLUESKY_SEED_HANDLES` but nothing ever read it (the scoped-crawl idea it supported was cancelled by D-21). Removed the field from `Config`, and the env var from both `.env` and `.env.example`.
- `BlueskyProvider` default `crawl_limit` in code (2000) never matched the documented/actual value (300) used everywhere it's actually instantiated; changed the dataclass default itself to 300 so config and code agree, and simplified `server.py`'s now-redundant `or 300` fallback.

**69 tests passing** (66 prior + 3 new regression tests). No blockchain code touched, per the standing "stop before blockchain phase" instruction.

---

**Next action.** F1-F7 complete and deep-reviewed. Awaiting explicit go-ahead for F8 (blockchain: Foundry contract + Anvil deploy + tamper demo).

### What the owner should check in this revision

- `architecture.md` §2 (revised data flow), §2a (measured quality gates), §5 stack table + the "Rejected: per-platform API layer" note
- `design.md` §1.6 (3-state liveness), §1.7 (quality gate), §2.1a (web detection provider), §3.1 score-band table
- `prd.md` G9-G11, S14-S18, and the rewritten limitations list
- `rules.md` R-21, R-22, and the revised cut order
- `phases.md` — the whole REVISED PLAN block, and Phase 3b being cancelled
- `memory.md` D-21..D-24 and §3a (all measured numbers)

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
| **D-17** | **Commercial face-search APIs removed entirely** (superseded D-14) | Owner checked: not free. Confirmed by research — the 2026 market gates **source URLs** behind paid tiers (FaceSeek: 10 tokens/search; FaceSearchAI: "source URLs unlock on any paid plan"). A source URL is the one field we need, so even a free tier is useless here. FaceCheck.ID is ~$0.30/search, crypto-only. | Reduces open-web redundancy |
| **D-18** | **Azure / Bing Visual Search provider removed** | **Microsoft retired the Bing Search APIs on 11 Aug 2025**, explicitly including Bing Visual Search. Decommissioned, no new signups — it has not existed for over a year. `bing_visual.py` was planned against a dead product. Azure AI Vision (F0, 5,000 tx/mo free) still exists but does image *analysis* (tagging/OCR/object detection), **not** reverse image search, so it cannot answer "where else does this face appear" — wrong tool, free tier irrelevant. | **Google Lens via SerpApi is now the ONLY open-web provider. Zero redundancy on the brief's core requirement.** See §4a. |
| **D-19** | **Image upload added as a first-class input mode, alongside webcam** | The PS says "Detect and encode a face from an **input image**" — it never requires a live person. Liveness was our own addition, not a requirement. More importantly, upload is **necessary**: the Google Lens route only works on a public figure with heavy web presence, and you cannot produce a public figure via webcam. Without upload the entire open-web path is untestable. | Liveness becomes inapplicable on the upload path and must not claim otherwise — see D-20 |
| **D-20** | **Liveness gets three states, not two** | An anti-spoof model detects print/replay artifacts in a *camera capture*. A clean uploaded JPEG of a real person will likely score "live" while proving nothing about physical presence. Displaying `LIVE` for an upload would be a **false assurance**. | `webcam+pass -> LIVE`, `webcam+fail -> SPOOF (blocks)`, `upload -> N/A, provenance unverified` (neutral, never green). Mirrors the asymmetry already documented for candidate images in design.md 1.6. |
| **D-21** | **Web detection is the single primary search path. The per-platform API layer is dropped.** | Proven live: one Lens call on one face returned links on instagram, facebook, youtube, x AND reddit (11 of 68 links social). Google already crawled those platforms, so we inherit the reach for one integration. The per-platform alternative was built as a probe and worked (best 0.7506 on a real Bluesky post) but covers strictly fewer platforms — X/Instagram/Facebook/TikTok have **no free search API at all** — for N× the integration cost, and adds an identity-extraction failure mode. | Verification happens against the search engine's cached thumbnail rather than the platform's live image. Disclosed in prd.md limitation 3 and recorded per-candidate in the evidence bundle. |
| **D-22** | **Google Cloud Vision `WEB_DETECTION` added as a second backend behind the same provider** | Official first-party Google API, **1,000 free units/month vs SerpApi's ~100**. Same job, same `Candidate` output. Gives quota headroom and removes single-vendor risk. | Requires a GCP account (card for verification, not charged in free tier). SerpApi stays the default because it is already working and needs nothing from the owner. |
| **D-23** | **Quality gates set from measurement: min face 50 px, no blur gate** | Measured embedding stability directly (`scripts/probe_accuracy.py`): cosine self-similarity holds ≥0.95 down to 43 px then collapses (0.868 @ 31 px, 0.767 @ 20 px). Blur was the opposite of expected — still 0.904 under a 31-px gaussian kernel, so a blur gate would add cost for nothing. | Very small faces in group photos are rejected rather than scored. Correct: their embeddings are unreliable. |
| **D-24** | **Score-band presentation rules (R-21)** | The "engine showed me random female faces" report was **not an engine fault**. Those scores were 0.157/0.132/0.111; measured true non-match ceiling is 0.074 with mean+4σ = 0.193, versus 0.765 same-person. The engine correctly returned `NO_MATCH`. The UI listed 20 nearest neighbours as a ranked table, which reads as suggestions. | Fixing presentation, not the model. Noise-band scores never render as ranked suggestions. |

---

## 3b. SCOPE RESET — 5 Sep 2026, approved by owner

An external review flagged that this is **HH Goa 2026 Shortlisting Task 3** — a screening gate, not a hackathon final. The plan had been built for a 3-day final: 14 phases, 7 cross-referenced docs, a decision log to D-24, ROC calibration, dual-chain anchoring, C2PA. That is misallocated for a task whose literal ask is four bullets. Several corrections were accepted; one recommendation was rejected with reasons. All decisions below are **owner-approved**.

| # | Decision | Rationale | Trade-off |
|---|---|---|---|
| **D-25** | **Keep local ArcFace. Reject the proposed AWS Rekognition swap.** | Two concrete reasons — note that "our model is more explainable" is **not** one of them, because `w600k_r50` is a pretrained checkpoint we did not train, so it is the same category of trust as an API call. The real reasons: **(1) Reproducibility with no third-party account** — Rekognition would be unavoidable on *every* run, so a judge cloning the repo would need their own AWS account for Stage 1 *plus* a GCV/SerpApi key for Stage 2. Two paid-cloud gates instead of one. Local ArcFace means Stage 1 runs for anyone, no account. **(2) Single-take recording risk** — `CompareFaces` is one call per candidate; a 25-candidate run is 25 round trips against low default per-account TPS quotas, and raising them is a support ticket, not a toggle. Local is ~48 ms/embed, measured, offline. | Face stage stays our own code to maintain. Already built, tested, and committed, so this costs nothing now. |
| **D-26** | **Salted face commitment stays; R-01 unchanged** | The review's "Rekognition means nothing biometric to protect" claim was withdrawn as stated backwards: sending a face to Amazon is a *larger* privacy surface than computing an embedding locally and never transmitting the image. "We never send your face anywhere" is the stronger claim. We have a real embedding, so R-01 applies exactly as designed. | None |
| **D-27** | **Anvil (local Foundry chain) is the required chain. Base Sepolia is an optional bonus.** | The brief says in plain text "or a **local/simulated chain**". D-09..D-11 spent real effort choosing a testnet to solve a problem the brief had already waived. Anvil has zero faucet, RPC, or network-deprecation risk — the demo cannot fail on something outside our control. | No public explorer link unless the bonus gets built. Worth ~30 min if time remains, purely for the on-camera visual. |
| **D-28** | **GCV `WEB_DETECTION` is the primary search backend; SerpApi Lens is secondary.** Reverses the previous ordering. | Two decisive facts my own D-22 recorded and my phase ordering then ignored (sunk-cost reasoning, since SerpApi already worked): **1,000/mo free vs ~100/mo**, and **GCV accepts raw base64**, which resolves A-03 outright. | Needs a GCP project with billing attached (free tier does not charge). Mitigated by D-30. |
| **D-29** | **Cut C2PA, OpenTimestamps, dual-chain anchoring, and full ROC calibration from scope.** | None are required by the brief. Ship `0.42` as a **documented, measured default** justified by the observed 0.765-vs-0.074 separation, disclosed in the README as a default rather than a benchmark-calibrated figure. | Weaker than a published ROC. Honest disclosure covers it at this project's scale. |
| **D-30** | **Build GCV against mocked/cached fixtures now; do not block on the billing-card decision.** | The request/response shapes are known from the docs. Wiring a real key later is a config change, not a rewrite. If the owner declines to attach a card, SerpApi runs primary instead — the orchestrator does not care which provider is first as long as one works. | GCV path stays unverified against the live API until a key exists. Tracked as A-08. |
| **D-31** | **AWS is scoped to S3 presigned URLs only** | Legitimate right-sized use: SerpApi requires a publicly reachable image URL, and a short-expiry presigned GET (~600 s) solves that without leaving a copy of anyone's face permanently public. Plumbing, never the graded decision. | Only needed if the SerpApi secondary provider gets built. |
| **D-32** | **Liveness code is kept but gets no further investment** | Already built and working (measured 0.9999 on a real photo). The brief never required it — it says "input image". Deleting working code saves nothing; extending it costs hours for no graded benefit. | Stays as a minor differentiator, mentioned in the README, not featured in the demo. |
| **D-33** | **Seven planning docs collapse to one README front door; the rest move to `docs/`** | A screening reviewer skimming the repo wants the four things the brief asks for, not a design-doc suite to navigate. | The detail survives in `docs/` as appendices. |

**Demo subject approved:** the public-domain Wikimedia portrait of Barack Obama, already used in the Lens probe. Public figure, maximal indexed footprint, so the search step is demonstrably genuine rather than a coin flip. Consistent with the "consented self-search, not surveillance" ethics position.

**Resolved by D-28:** ~~A-03~~ — GCV accepts raw base64, so there is no public-URL problem on the primary path. It only returns if SerpApi is built, and D-31 handles it.

---

## 3a. Measured research evidence

Numbers below are from probes actually executed, not estimates. Scripts kept in `scripts/` so they can be re-run.

### Web detection reach — `scripts/probe_lens.py` (1 SerpApi credit, response cached)

Probe: one public-figure face photo via `engine=google_lens`.

```
visual_matches:  59        organic_results: 9        related_content: 1
related_content[0].query = "Barack Obama"        <- clean identity signal
social links: 11 of 68  ->  facebook, instagram, youtube, x, reddit
example real post URLs returned:
  instagram.com/p/Dc1RWQ9DhXe/
  reddit.com/r/Presidents/comments/1vewo5f/...
  x.com/SLOTUS/status/2093690076098130142
```

Also confirmed: results contain clear noise (an unrelated Trump video, an unrelated Usha Vance post). **This is why local re-verification is mandatory** — it is the difference between a search wrapper and a face-match pipeline.

### Per-platform route — `scripts/probe_identity_route.py` (validated, then descoped per D-21)

```
searchActors("Barack Obama")   -> 4 real accounts, genuine one ranked first
getAuthorFeed                  -> 19 images across 60 posts
our ArcFace scored 14 images   -> 10 above threshold
best 0.7506  on a real post dated 2026-06-18
separation: 0.5187 -> then a cliff to 0.1664, 0.0638, 0.0175, -0.0831
           (the low scores are OTHER people in the same group photos)
```

Plumbing works and is kept for reference. Not the primary path.

### Accuracy levers — `scripts/probe_accuracy.py`

Face size vs self-similarity (same photo downscaled):

| face px | 232 | 174 | 119 | 87 | 61 | 43 | 31 | 20 |
|---|---|---|---|---|---|---|---|---|
| cosine | 1.000 | 0.972 | 0.971 | 0.970 | 0.962 | 0.954 | 0.868 | 0.767 |

Blur vs self-similarity: 0.984 @ k=3, 0.974 @ k=13, **0.904 @ k=31**. Far more robust than assumed; no gate needed.

True non-match distribution (9 confirmed different people):

```
max 0.0738   mean -0.0241   std 0.0543   mean+4*std = 0.1930
same-person reference = 0.7652
```

### API availability (verified 5 Sep 2026)

| Service | Free tier | Note |
|---|---|---|
| SerpApi Google Lens | ~100/mo | working today, key present |
| Google Cloud Vision `WEB_DETECTION` | 1,000/mo | official; needs GCP account |
| Bluesky AT Protocol | unlimited, no auth | `searchActors`, `getAuthorFeed`, `getFeed` all confirmed open; `searchPosts` returns **403** |
| Mastodon | no auth | account search works with a User-Agent header; `only_media` on public timeline returned **422** |
| GitHub REST | 60/hr anon, 5,000/hr with free PAT | user search + avatars public |
| **X / Twitter** | **none** | pay-per-use since Feb 2026, ~$0.005/post read |
| Instagram / Facebook / TikTok / LinkedIn | none | no public search API — indexed links only |
| Bing Visual Search | **dead** | retired by Microsoft 11 Aug 2025 |
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
| ~~A-07~~ | ~~Lens results include social-media pages~~ | — | **RESOLVED 5 Sep 2026 — confirmed true.** 11 of 68 links were on social domains (facebook, instagram, youtube, x, reddit) with real post URLs. See §3a. |
| ~~A-04~~ | ~~dynamic batch axis~~ | — | RESOLVED: `input.1: [None,3,112,112]`, confirmed via onnxruntime introspection |
| ~~A-01 / A-02~~ | ~~Bluesky endpoints and image URLs~~ | — | RESOLVED live; API returns ready-made `thumb`/`fullsize` URLs |
| **A-03** | **How to submit a local probe image to SerpApi** — it needs a publicly reachable URL, and our probe is a local webcam frame or uploaded file | **Phase 5a** | **UNRESOLVED. Now the last significant unknown, and it gates the critical phase.** Options: temporary public upload host, or a SerpApi file-upload endpoint if one exists. The GCV backend takes raw base64 and sidesteps this entirely, which is a further argument for D-22. |
| **A-08** | GCV `WEB_DETECTION` returns image-URL arrays, not just `webEntities` | Phase 5f | Unverified. Known failure mode in the wild — treat empty arrays as a valid zero-candidate result |

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

## 5a. RESOLVED — the MATCH-demo gap

Recorded 5 Sep 2026, **resolved the same day by D-21**. Kept because the diagnosis is still the clearest explanation of a confusing symptom.

**Resolution:** web detection returns real social posts across many platforms without us choosing where to look, so a MATCH on a public figure is reachable through the ordinary path. The "scoped crawl / seed handles" workaround (old Phase 3b) is cancelled, along with the honesty problem it carried.

**Symptom.** A real webcam scan produced `det_score 0.951`, `liveness LIVE 0.991`, verdict `NO_MATCH`. All three are correct and mutually consistent — they measure different things:

| Number | Question it answers | Verdict |
|---|---|---|
| `det_score 0.951` | "Is there a face in this frame?" | correct, high |
| `liveness 0.991` | "Is it a live face, not a printed photo?" | correct, live |
| `NO_MATCH` | "Does this face appear in the corpus we searched?" | correct — it genuinely does not |

The owner's face is not in a random 300-post crawl of Bluesky's `whats-hot` feed, so `NO_MATCH` is the honest, correct output (R-16 treats this as a first-class outcome).

**The actual problem.** With the corpus sourced only from a random public feed, the pipeline will return `NO_MATCH` for *every* face we can actually test with. There is currently **no planned path to demonstrate a successful MATCH on camera.** A recording that only ever shows "no match" does not demonstrate a working pipeline. The plan needs both outcomes.

**Root cause in code.** Three related gaps, all real:
1. `config.bluesky_seed_handles` exists and parses `BLUESKY_SEED_HANDLES`, but **nothing reads it.** Dead config.
2. `bluesky.py` only calls `getFeed` (public feed generators). `getAuthorFeed` is named in a docstring but never invoked — so there is no way to crawl a *specific account*.
3. `webapp/server.py` hardcodes `BlueskyProvider(crawl_limit=300)`, ignoring `config.bluesky_crawl_limit` (2000).

**Actual fix (D-21).** Probe a **public figure via an uploaded image**, and web detection returns real social posts we then verify locally. Nothing is scoped, seeded, or hand-picked: the platforms come from Google's index, the candidates come from its response, and the winner comes from our own ArcFace score. This is why image upload became a required input mode (D-19) rather than a convenience — you cannot put a public figure in front of your webcam.

The demo therefore shows both outcomes from one code path: a public-figure probe gives `MATCH` with a real post URL, and the owner's own face gives a correct `NO_MATCH`.

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

---

## 3f. F8/F9 complete — blockchain live, tamper demo observed

5 Sep 2026, on explicit owner go-ahead ("start coding now blockchain part").

**Foundry installed via precompiled win32 zip, not `foundryup`.** The official
installer is a bash script; using it would have meant WSL. The release zip
gives native PowerShell `forge`/`anvil`/`cast` with no POSIX shell — consistent
with the Windows-first rule.

**`contracts/src/EvidenceRegistry.sol`** deployed to a live local Anvil chain at
`0x5FbDB2315678afecb367f032d93F642f64180aa3` (the deterministic first-deploy
address on a fresh Anvil — both competitor repos landed on the same one).
8 Foundry tests green.

**`pipeline/chain/evm.py` + `pipeline/chain/reverify.py`**, CLI `anchor` / `verify`.

Anvil's default private key is auto-used **only** when `chain=anvil`. Any other
chain requires an explicit `EVM_PRIVATE_KEY` — never a silent fallback to a
well-known key.

`TAMPERED` vs `NOT_ANCHORED` are distinguished via an explicit `expected_hash`
read from `anchor.json`. Without it, "this hash isn't on chain" is ambiguous
between "never anchored" and "anchored then edited". A failing test caught this
distinction being collapsed.

**Observed live, end to end:**

```
SRK match 0.9618
  -> python -m pipeline anchor 2026-09-05T10-44-21Z
     tx=0xef23bf9f...  block 11
  -> python -m pipeline verify  ->  PASS   exit 0
  -> edited ONE character in evidence.json
  -> python -m pipeline verify  ->  TAMPERED  exit 1
  -> restored  ->  PASS  exit 0
```

That is brief requirement 3 satisfied verbatim: "demonstrate re-verifying the
data against the on-chain record."

---

## 3g. G1 — measured recall recovery, then a self-inflicted regression

5 Sep 2026. Owner asked whether competitor repos fetch Instagram/X, supplying
two links. Investigating that turned into the largest recall finding so far.

### What the competitor repos actually do

Both were read directly (READMEs fetched, not assumed).

`ivocreates/facechain` — Next.js + Express + `face-api.js` (128-d), GCV/Lens/Bing
reverse search, scores all candidates, Sepolia or Anvil. Its own limitations
section concedes candidate images may be behind auth or rate-limited and the
pipeline reports rather than guesses. **It does not fetch Instagram either.** It
offers a manual "paste a content URL" fallback instead.

`SAJITH07N/facechain` — Python + `face_recognition`/dlib (128-d), SerpApi Lens
(free tier stated as 250/mo, not ~100), Sepolia via Remix + MetaMask. Its
limitations section independently confirms our finding **and explains the
mechanism**: Instagram blocks most search-engine crawlers via `robots.txt`, so
Lens cannot surface individual Instagram posts by content, while X/Twitter and
LinkedIn are considerably more crawlable. It also flags exactly our thumbnail
bug — platforms serve heavily cropped, low-resolution thumbnails that can fail
face detection even when the underlying post is a genuine match.

So the Meta/TikTok wall is real and independently confirmed by two other teams.
Content rephrased for licensing compliance; see the repos for originals.

### But X was recoverable, and we were throwing it away

`scripts/probe_size_variants.py` measured the exact URL that a live SRK run had
rejected as `reject-face-too-small`:

| `?name=` | resolution | face | outcome |
|---|---|---|---|
| `thumb` | 150x150 | 32px | rejected (what we fetched) |
| `small` | 680x680 | 149px | passes |
| `medium`/`large`/`orig` | 1080x1080 | 235px | passes |

A face **was** detected. It was 32px only because `thumb` is X's 150px variant.
One query parameter was discarding an entire verifiable platform.

YouTube, same probe: `maxresdefault.jpg` 404s for some videos (`e2uRUMgozFQ`),
where `sddefault` (face 75px) and `hqdefault` (face 54px) both work. But for the
Short `V8UwSQAPDxs`, **only** `maxresdefault` clears the gate (68px) while
`sddefault` gives 49px and `hqdefault` 36px. So the fallback rule cannot be
"first that fetches" — it must be "first that fetches **and** clears the gate",
which needs the detector, not just a URL rewrite.

### G1 shipped, and it worked

Applied at **parse** time, not fetch time, because `evidence/bundle.py` records
`candidate.image_url` verbatim into the hashed structure — rewriting at fetch
time would let the on-chain record cite a URL different from the one actually
scored. New `pipeline/search/media_urls.py` owns all CDN size knowledge;
`Candidate` gained `image_url_fallbacks`; `pipeline_run.py` walks the chain and
rewrites `image_url` to the variant that won.

Verified live against the real SRK photo through the running server:

```
X   pbs.twimg.com ?name=orig   ->  ACCEPT 0.9806   (was: discarded entirely)
YT  e2uRUMgozFQ  -> sddefault  ->  corroborating 0.6037  (was: reject-fetch-failed)
YT  /shorts/V8UwSQAPDxs        ->  scored 0.2795, honest reject-below-threshold
                                   (was: reject-no-image)
11 Meta/TikTok rows            ->  reject-platform-blocked  (was: reject-no-face)
```

X went from silently discarded to the single best match in the run, beating the
previous YouTube-only accept of 0.9618. 122 tests passing, pyflakes clean.

### Then it broke a real run — the G1 regression

Owner tested a **non-celebrity** X profile and got a false `NO_MATCH`. Root
cause verified by direct HTTP probe, not guessed:

```
_400x400.jpg              -> 200, 14046 bytes   <- what GCV actually returned
_400x400.jpg?name=orig    -> 404                <- what our rewrite produced
_400x400.jpg?name=thumb   -> 404                <- last variant tried, hence the message
```

`pbs.twimg.com` runs **two mutually exclusive sizing schemes on one domain**:

| path | scheme | largest → smallest | `?name=` |
|---|---|---|---|
| `/media/` | query param | `orig` 122KB → `thumb` 9KB | required |
| `/profile_images/` | filename suffix | bare `.jpg` 18136 B → `_400x400` 14046 → `_200x200` 6000 → `_bigger` 2439 → `_normal` 1807 → `_mini` 1451 | **404s** |

Note the largest profile variant is the **bare filename**, larger than
`_400x400`. All figures measured live.

GCV handed us a working URL and our "recall fix" turned it into five 404s. A
recall optimisation made recall strictly *worse*, silently, via
`reject-fetch-failed`. Two rules were written from this:

- **R-23** — URL rewriting is additive-only; the provider's original URL must
  survive in the fallback chain. Worst case of any future rewrite becomes "no
  better than before" rather than "worse". Guarded by a generic property test so
  new platform schemes inherit it.
- **R-24** — a reject reason must describe what actually happened. The failure
  message named only the *last* (smallest) variant tried, making a working fix
  look like it had never run.

### Honest standing vs the two repos

Ahead: ArcFace 512-d vs both repos' 128-d; every candidate re-verified locally
(SAJITH07N only face-checks the search engine's top result); a real reject
taxonomy; canonical float-free JSON + salted commitment + the URL-actually-
verified guarantee; margin measured against best non-match rather than runner-up
(both repos would fail the four-genuine-matches case); size-variant recovery at
all, which neither attempts.

Not ahead: no recovery path when every fetch fails (ivocreates' manual URL paste
would have rescued the failed run — rejected anyway, see D-38); tamper demo is
CLI-only where SAJITH07N's runs inline (G1.3 closes this); local Anvil vs public
Sepolia, which the brief explicitly permits.

---

## 3h. Decision log continued (D-34..D-38)

| # | Decision | Rationale | Trade-off accepted |
|---|---|---|---|
| **D-34** | **Send the ORIGINAL photo to search, not the 112px aligned crop** | Measured on Obama: crop → 9 webEntities / 0 fullMatchingImages / 85 candidates; original → 11 / 40 / 130. On SRK the crop produced **zero** webEntities (unrecognised) while the original correctly returned `['Shah Rukh Khan', 'Dilwale Dulhania Le Jayenge', ...]`. The aligned crop is right for *our* embedder and wrong for *their* index | Slightly larger upload per search. Capped at 2048px / JPEG q90 |
| **D-35** | **Margin measured against the best NON-MATCH, not the runner-up** | The old rule rejected an Alia Bhatt run with four genuine matches (0.9225/0.9211/0.9164/0.9072 on linkedin/youtube/reddit) because they agreed within 0.0014. Abundant corroboration was being treated as ambiguity — the exact inverse of the rule's intent | Extra above-threshold matches are now `corroborating`, which needs its own non-rejection presentation |
| **D-36** | **CDN domains stay on the allowlist, and real post URLs are derived from CDN images where possible** | Measured: the highest-scoring *verifiable* hits were CDN-served (`preview.redd.it` 0.9786, `licdn.com` 0.9675, `pbs.twimg.com` 0.9594). Excluding CDNs discarded most usable evidence — one run threw away a 0.9771 `preview.redd.it` hit. Deriving `i.ytimg.com/vi/{id}/` → `youtube.com/watch?v={id}` means an ACCEPT cites an openable post, not a thumbnail | Reddit `preview.redd.it` and X `pbs.twimg.com` don't encode a parent post, so those correctly stay un-derivable and cite the CDN URL |
| **D-37** | **URL size-variant rewriting is additive-only (now R-23)** | A rewrite that replaces the provider's URL can destroy a working candidate. Proven live: `/profile_images/` rewritten with the `/media/` scheme produced five 404s where the untouched original returned HTTP 200 | The chain is up to 6 URLs per candidate on a cold run instead of 1. Absorbed by the R-04 HTTP cache on any repeat run |
| **D-38** | **Reject a manual "paste a content URL" escape hatch** | `ivocreates/facechain` offers one, and it would have rescued the failed `x.com` run. But the brief demands "a genuine search step, not a hardcoded/pre-picked result" — a human choosing the URL is precisely the failure mode that requirement exists to catch. Better to fix fetch coverage | No recovery path when every candidate fetch fails. Mitigated by R-23 making rewrites non-destructive, and by honest per-candidate reject reasons |

---

## 3i. Current state — what remains

**Done:** F1–F9 + G1. All three brief requirements met and observed live.
122 Python tests + 8 Foundry tests passing.

**In progress:** G1.1 (the R-23/R-24 regression fix).

**Remaining:** G1.2 (`content_kind`), G1.3 (inline tamper demo), **G2 (root
`README.md` — required by the brief, still absent)**, G3 (committed sample runs
incl. a genuine `NO_MATCH`), G4 (optional UI anchor/verify buttons), G5
(threshold calibration — recommended skip, see phases.md), G6 (recording).

**Known gaps carried forward:** no standalone `run-all` CLI command (the UI's
endpoints are the only driver); `MatchPolicy` is still `threshold=0.42,
is_placeholder=True`; no Bluesky index persistence across restarts; full S3
presigning for arbitrary local files remains future work (D-31).

---

## 3j. G1.1 fixed, then Tier 0 — a second, more serious defect found and closed

5 Sep 2026, same review thread that produced §3g/§3h. Two independent adversarial
reviews of the plan (not the code) both landed on real, previously-unknown defects.

**G1.1 — the twimg regression, closed.** `pipeline/search/media_urls.py` now
splits `pbs.twimg.com` by path (`/media/` = `?name=` query scheme, largest→
smallest `orig/large/medium/small/thumb`; `/profile_images/` = filename-suffix
scheme, largest→smallest bare-filename→`_400x400`→`_200x200`→`_bigger`→
`_normal`→`_mini` — measured live, bare filename 18136 bytes > `_400x400`'s
14046). New binding rule **R-23**: any URL rewrite must keep the provider's
original URL somewhere in the resulting fallback chain, appended last rather
than dropped, so the worst case of a wrong scheme guess is "no better than
before" rather than "worse". `tests/test_media_urls.py` asserts this as a
generic property over every known URL shape (`REAL_PROVIDER_URLS`), not a
per-platform test, so a newly added platform scheme inherits the guarantee.
New rule **R-24**: a reject reason must describe what actually happened —
closes the bug where a multi-variant fetch failure message named only the
smallest (last) variant tried, making a working fix look like it had never run.

**Tier 0 — the image-hash defect, the most important finding of the whole
thread.** An independent adversarial review (treating the plan itself, not
just the code, as the object of scrutiny) predicted three bugs correctly
before they were checked — the wrong-image-to-search issue (later D-34), the
margin-rule-rejects-famous-subjects issue (later D-35), and the thumbnail-
fails-the-face-gate issue (fixed in G1) — which is why its next claim was
worth verifying rather than dismissing: that our re-fetch-and-verify story
was incomplete. Verifying it surfaced something worse than incomplete.

`grep` for `image_sha256` across every real anchored run found the same
value in every single one: `""`. Root cause: `evidence/bundle.py` read
`post_meta.get("image_sha256", "")`, but **no provider — GCV or Bluesky —
ever wrote that key**. The winning candidate's actual image bytes were
fetched, scored, and then discarded inside `run_pipeline`; nothing threaded
them any further than that function. `evidence/bundle.py::image_sha256()`
was a correct, unit-tested, completely unused helper waiting for input it
was never given. Worse: `chain/evm.py`'s `anchor()` covered for the empty
value with `image_hash_hex = bundle.data["match"].get("image_sha256") or
"0" * 64` — so **every anchored on-chain record has an `imageHash` of 64
zeros**, silently, with no test catching it because no test exercised a
real `run_pipeline()` → `build_evidence()` → `anchor()` chain end to end
with real image bytes. A judge reading `evidence.json` next to the block
explorer would find this in under a minute, and it would retroactively
discredit the entire tamper-detection demo — the project's strongest claim.

Fixed by threading the bytes through properly rather than patching either
end in isolation: `PipelineResult.best_image_bytes`, `build_evidence(...,
image_bytes: bytes)` as a required argument with **no fallback** (raises on
empty/None), and `evm.anchor()` deleted the zero-fill line entirely and
raises instead — verified by both a behavioural test and a source-level
grep test that the fallback expression itself is gone, not merely
unreachable dead code.

Bundled into the same pass, since a second schema bump later is expensive
once sample runs are committed: `SCHEMA_VERSION` 1 → 2, adding
`post.content_kind` (post/profile/unknown, never null — G1.2, closes the
separate finding that a citable social media *post* and a bare profile page
are not the same thing under the brief's literal wording), `match.image_phash`
(same `imagehash`/Hamming convention as `verify/dedupe.py`, for a future
re-fetch check), `match.verified_against`. Also fixed in the same pass: a
half-written docstring in `bundle.py` had already claimed a "v2" that neither
`SCHEMA_VERSION` nor the code implemented — caught and closed as its own
doc-vs-code lie, with a test that asserts the docstring can never again
reference a version higher than `SCHEMA_VERSION` actually is. `get_http_cache()`'s
docstring claim that hit/miss stats "land in the audit log" was similarly
false until `build_audit()` gained a `cache_stats` parameter and
`webapp/server.py` started snapshotting `http.stats()` deltas per provider.

**`runs/` deleted, not migrated** — confirmed untracked in git first. Every
run in it carried a v1 bundle and a zero `imageHash`; regenerating later
under G3 costs one run each, and there is no version of "fix up the old
JSON files by hand" that is better than just not having them.

Six tests written first (confirmed failing against the pre-fix code), then
made to pass. Verified against the real running Anvil chain, not mocks — all
12 `test_chain_evm.py`/`test_reverify.py` tests pass, so the anchor→verify→
tamper round trip now genuinely anchors a non-zero `imageHash`. Full suite:
**145 tests passing**, pyflakes clean.

## 3k. Decision log continued (D-39, D-40)

| # | Decision | Rationale | Trade-off accepted |
|---|---|---|---|
| **D-39** | **`image_bytes` is a required build_evidence() argument with zero fallback, not an optional field with a documented absence** | An earlier draft of this fix considered "record the absence explicitly" as an alternative to hard failure. Rejected: an explicit "absent" still produces an anchored record whose headline verification field proves nothing — the whole point of the re-fetch check (Tier 1) is that `imageHash` means something. A silent gap and a documented gap are both gaps | A run that somehow reaches `build_evidence` with no image bytes now hard-fails instead of degrading gracefully. Judged correct: that state should be structurally unreachable given `run_pipeline`'s contract, so failing loudly surfaces a real upstream bug rather than masking one |
| **D-40** | **Bundle-only `image_phash`; no Solidity/contract change** | An earlier draft proposed a `bytes8 imagePhash` contract field alongside the fix. Rejected on the correct objection: `evidenceHash` already commits to the entire canonical bundle the moment it's anchored, so a separate on-chain phash field adds no integrity guarantee beyond what the bundle already provides — it would only help someone verifying with a bare tx hash and no bundle, which is not a use case this project has. Populating the *existing* `imageHash` field (which was silently zero) is the real fix; adding phash to the bundle only achieves the same property for strictly less work | None — this is a pure simplification versus the original proposal |

---

## 3l. Current state — what remains (supersedes §3i)

**Done:** F1–F9, G1, G1.1 (twimg regression + R-23/R-24), Tier 0 (image-hash
population + schema v2 + cache-stats). All three brief requirements met and
observed live, including against the real running Anvil chain. 145 tests
passing.

**Remaining, in order:** Tier 1 (re-fetch verifier check, `NO_CANDIDATES` /
`MATCH_NON_SOCIAL` verdicts, citability-aware headline selection, SerpApi
image-over-thumbnail, negative/false-negative harvest) — see `phases.md`'s
Tier 1 table for the full spec and the 2h budget cap on the re-fetch check's
origin-first behaviour. Then G2 (root `README.md`, still absent — required
by the brief), G3 (sample runs incl. a genuine `NO_MATCH`, generated only
after Tier 1 lands), G4 (tamper endpoint + UI anchor/verify buttons), G6
(recording, preceded by a three-subject `HTTP_CACHE=0` pre-verification).
G5 (threshold calibration) stays skipped — see D-29 and the live score-vs-
face-size table gathered during G1 as the stronger, more honest substitute.

---

## 3m. Two live test runs surface real defects faster than the plan reached them

5 Sep 2026, same session as Tier 0. Two genuine-search test runs — not
synthetic fixtures — produced findings ahead of schedule and one hard
constraint that changes testing methodology going forward.

### Run A: a private individual found by face alone, via GitHub

The strongest anti-hardcode evidence gathered so far: a non-celebrity probe,
GCV returning generic/no identity signal, was matched at **0.9363** to their
GitHub avatar (`avatars.githubusercontent.com/u/44129612?v=4`,
`github.com/<user>/isomorphic`), with the next-best impostor at only 0.1385
— a **0.80 gap**, on a real search, in the deployment domain. No name for
Google to have leaked; the match is face-only. This is a stronger "genuine
search, not hardcoded" argument than any public-figure run, because a public
figure's identity string could always be suspected of doing the work instead
of the face embedding.

It also exposed two real defects:

1. **A verdict/caption contradiction rendered on screen simultaneously.**
   The banner said "No match found ... this is a correct, honest outcome"
   while the caption underneath said "1 high-scoring non-social source also
   matched this face" — both sentences visible at once, directly
   contradicting each other. Root cause: the pipeline correctly rejected the
   GitHub hit as `reject-domain` (github.com was not yet allowlisted), but
   the UI's generic `isMatch ? X : Y` copy could not express "found, just
   not socially" as a distinct claim from "nothing found". Fixed by adding
   the `MATCH_NON_SOCIAL` verdict (matcher.py) and giving every verdict its
   own headline copy in `app.js` — never a shared template across verdicts.

2. **The allowlist was internally inconsistent.** `linkedin.com` was
   allowed; `github.com` was not, despite both being platforms where an
   individual maintains a public identity profile and publishes content
   under it — GitHub itself describes the platform as social. This was an
   oversight, not a considered exclusion, and adding GitHub is "fixing an
   inconsistency the result exposed," not "fitting the allowlist to one
   result." Written up as a binding principle in rules.md R-06, not just a
   one-line code change, specifically so it survives a judge's question
   about why THIS platform and not some other one.

**Consent handling.** The GitHub run identifies a real, non-consenting
private individual by face, GitHub handle, and (very likely, via their
profile) cross-platform social links. Per the project's own ethics position
(consented self-search, not surveillance) and R-01/R-05's spirit extended to
non-biometric PII, this run must never be committed, referenced in the
README, or used in the recording — doing so would be the exact contradiction
the privacy section argues against, one level up, and a judge who found it
would not believe the rest of the document. The run was moved to
`calibration/quarantine/` (new, gitignored directory), with `page_url` and
`image_url` replaced by truncated sha256 digests in the stored `audit.json`
and `identity_signals` stripped entirely. The redaction was verified
directly: the true-match row (score 0.9363) survives with its sha256 intact
for calibration purposes; every URL string is gone.

### Run B: a probe suspected to be a synthetic/AI-generated avatar

Studio backdrop, tuxedo, flawless symmetric skin — the house style of AI
headshot generators, which random X avatars disproportionately are. Every
scored candidate came back ≤ 0.135, and the correct verdict is `NO_MATCH`:
the pipeline did not hallucinate an identity for a face that likely has
none. This is a designed behaviour worth stating plainly in the eventual
README ("synthetic or stock avatars produce NO_MATCH; the pipeline never
manufactures a match"), not something to apologise for.

Two further findings from this run:

3. **`reject-platform-blocked` rows conflicted with the NO_MATCH banner
   in the same way as finding 1, from a different cause.** GCV returned a
   TikTok URL and a Facebook URL for the probe image — meaning the search
   engine asserts this image exists on those platforms — but both were
   correctly `reject-platform-blocked` since neither is fetchable. The
   banner's "not every face has a matching public post" reads as "nothing
   was found" when something more specific is true: found, but
   unverifiable by us. Fixed by adding `Candidate.match_kind` (`"full"` |
   `"partial"` | `"similar"` | `""`, sourced from GCV's own
   fullMatchingImages/partialMatchingImages/visuallySimilarImages
   classification — R-03 still applies, never used to accept/reject) and
   `MatchResult.unverifiable_platform_hits` (full/partial matches that are
   also platform-blocked), rendered as a mandatory clause on the NO_MATCH
   banner rather than a sixth verdict state.

4. **The TikTok block is on an endpoint, not the platform.** In this same
   run, `tiktok.com/api/img/?userId=...` was blocked while
   `tiktokcdn-us.com`'s signed CDN URL (a DIFFERENT TikTok candidate in the
   same run) fetched successfully at 200 and scored 0.0593 — a genuine
   negative, not a block. `is_media_blocked` previously treated all of
   TikTok as one blocked platform (inherited from the Instagram/Facebook
   pattern, where blocking really is domain-wide). Fixed: TikTok is now
   checked by path prefix (`/api/img/`, `/api/`) rather than by platform
   membership alone; Instagram/Facebook/Meta remain fully domain-blocked,
   since no fetchable variant has ever been found for them.

5. **The UI's stale `0.074` non-match ceiling claim.** Both live runs
   measured up to 0.1385 — the original n=9 pilot ceiling was always too
   small a sample to be an operating figure, and these two runs are exactly
   the evidence that should have replaced it sooner. Fixed the UI footer and
   `rules.md`'s R-21 rationale to state both numbers honestly (pilot 0.074,
   n=9, historical; live observed up to ~0.14, n=41) rather than repeating a
   number a judge could check against the visible table and find wrong.

### Testing methodology change (owner-approved)

**Stop sampling random X avatars for testing.** They are disproportionately
synthetic, stolen, or of people with zero footprint — every genuine positive
found this way is also one that can never be published (see the consent
constraint above). Replaced with a deliberate test set: 2–3 consenting
teammates (each with a GitHub + X/LinkedIn presence — this run proved that
path works for an ordinary developer, likely to work for a teammate too, and
turns the "cross-platform identity loop" beat into something legitimately
demonstrable on camera), 3 public figures (satisfies the brief's literal
"post" wording), 1 deliberately-chosen known-synthetic face (a designed,
publishable NO_MATCH). This also reorders the recording: lead with the
consenting-teammate face-only match as the strongest genuineness proof, then
a public figure for the literal-post case, then the tamper demo.

**Diagnostics table legibility.** The reason column previously showed the
full sentence inline, which wrapped `reject-platform-blocked` rows into
multi-line billboards on a recording. Fixed with `shortenReason()` in
`app.js`: a short, decision-specific label in the visible cell
(`platform-blocked (TikTok)`, `fetch-failed`, `no-face`, etc.), full sentence
preserved in the cell's `title` tooltip — R-24 still holds (nothing is
discarded), it is just not all inline at full length. Also closes the R-21
rule 4 gap where rows showing a bare score-less `—` (spotify, wiley, unir,
sbb in the harvest data) had a decision but no immediately visible reason.

## 3n. Decision log continued (D-41, D-42)

| # | Decision | Rationale | Trade-off accepted |
|---|---|---|---|
| **D-41** | **Allowlist membership is a principle (R-06), not a per-run adjustment** | Adding GitHub *because* a real run scored 0.9363 there would read as fitting the list to the result. Writing the test first ("a platform where an individual maintains a public identity profile and publishes content under it"), then showing GitHub already passes it and was simply missing, and keeping the pre-fix `NO_MATCH` audit log next to a post-fix re-run, makes the change defensible against "why THIS platform" rather than "why NOW" | The pre-fix quarantined run must be retained (redacted, not deleted) as the visible record of the honest old verdict |
| **D-42** | **A random-X-avatar test methodology is retired in favour of a deliberate, publishable test set** | Two consecutive genuine positives (a real private individual; a probable synthetic face) both turned out to be unpublishable — one for consent reasons, one because there's no clear "correct answer" to publish against. Continuing to sample randomly means every future positive has the same problem. A designed set (consenting teammates, public figures, one known-synthetic) front-loads the consent question instead of discovering it after the fact | Less "surprise" in testing; the deliberate set is smaller and requires coordinating with teammates before recording day |

---

## 3o. Current state — what remains (supersedes §3l)

**Done:** F1–F9, G1, G1.1 (twimg regression + R-23/R-24), Tier 0 (image-hash
population + schema v2 + cache-stats), and roughly two-thirds of Tier 1
(NO_CANDIDATES, MATCH_NON_SOCIAL, GitHub allowlist + R-06 principle,
match_kind, unverifiable_platform_hits, TikTok endpoint split, stale-ceiling
fix, negatives harvest, consent quarantine, diagnostics legibility). All
three brief requirements met and observed live, including against the real
running Anvil chain and against a genuine, non-celebrity, face-only match.
**167 tests passing.**

**Remaining in Tier 1:** the re-fetch verifier check (4 states, budget-capped
at 2h), citability-aware headline selection, SerpApi image-over-thumbnail.

**Remaining after Tier 1:** G2 (root `README.md`, still absent — required by
the brief; must now include the evidence-locker framing, the commitment-
scope wording, the GitHub/allowlist principle, the TikTok split, and the
synthetic-avatar NO_MATCH-is-correct framing), G3 (sample runs — from the NEW
deliberate test set, never from `calibration/quarantine/`), G4 (tamper
endpoint + UI anchor/verify buttons), G6 (recording, preceded by running the
deliberate test set under `HTTP_CACHE=0`). G5 stays skipped.
