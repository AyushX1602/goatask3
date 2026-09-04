# Phases — face-chain-verify

3-day build plan. Every phase has an **exit criterion that must be executed and observed**, not assumed (`rules.md` R-13).

Live status is tracked in [`memory.md`](./memory.md), not here. This file is the plan; that file is the state.

---

## Ordering rationale

The 24-hour plan in `ANALYSIS.md` §8 front-loaded the smart contract, because faucets and RPC keys are the things that fail at 3 a.m. **With 3 days and blockchain explicitly secondary, that order is wrong.** Revised priority:

1. **Face core first** — nothing else can be tested without it.
2. **Bluesky before Google Lens** — zero API keys, zero quota, zero signup. This yields a genuinely working `face → real social post` pipeline on Day 1 instead of blocking on a SerpApi account. Biggest single de-risking move available.
3. **Google Lens second** — satisfies "search the web" properly, with Bluesky already in place as a fallback.
4. **Chain last** — half a day is sufficient, and by then the evidence schema has stopped moving.

---

# DAY 1 — a working search pipeline with no API keys

Target by end of day: a live webcam capture finds a real Bluesky post and prints a scored candidate table.

## Phase 0 — Scaffold  ·  ~1 h

- [ ] `.venv` (done) + install pinned deps, confirm every wheel resolves on Windows / py312
- [ ] `requirements.txt` with `==` pins, `pyproject.toml`
- [ ] Package tree per `architecture.md` §6 with `__init__.py` files
- [ ] `pipeline/config.py` — `.env` loading, defaults that work with **no** `.env` present
- [ ] `.env.example`, `.gitignore` (`.venv/ models/ .cache/ .env runs/*/raw/`)
- [ ] `scripts/fetch_models.py` — YuNet + `w600k_r50` + anti-spoof, sha256-verified, idempotent
- [ ] `git init`, first commit

**Exit criterion:** `python -m pipeline --help` prints the command list. `python scripts/fetch_models.py` downloads all three models and passes checksum verification. Re-running it re-downloads nothing.

**Risk:** a wheel fails to build. Mitigation: `insightface` and `faiss` are already excluded for exactly this reason (`architecture.md` §5).

## Phase 1 — Face core  ·  ~3 h

- [ ] `face/types.py` — `DetectedFace`, `Embedding`, `LivenessResult`
- [ ] `face/detect.py` — YuNet wrapper, `setInputSize` on every frame-size change
- [ ] `face/align.py` — `canonical_kps` geometric reordering + similarity transform
- [ ] `face/embed.py` — ArcFace preprocessing exactly per `design.md` §1.5, L2-normalise
- [ ] `tests/test_face_core.py` — flip test, same-person test, different-person test, norm test

**Exit criterion — all four must be observed:**
1. Two different photos of the same person score **> 0.5**
2. Two different people score **< 0.3**
3. A horizontally flipped input scores **> 0.9** against its unflipped self (proves landmark ordering, `design.md` §1.3)
4. Every embedding satisfies `‖v‖ ≈ 1.0`

**Also record:** wall-clock ms per image on CPU. This decides Q5 (CUDA or not) and sets the Phase 3 crawl budget.

**Risk:** the flip test fails → landmark order is wrong. Fix in `canonical_kps` before proceeding. Do not continue with a broken face core; every later measurement would be meaningless.

## Phase 2 — Provider interface + matcher  ·  ~2 h

- [ ] `search/base.py` — `Candidate`, `SearchProvider` protocol, `ProviderReport`
- [ ] `search/orchestrator.py` — thread-pool fan-out, per-provider timeout, R-14 isolation
- [ ] `verify/matcher.py` — cosine, threshold + margin rule, rejection reasons
- [ ] `verify/dedupe.py` — URL normalisation then phash, Hamming ≤ 6
- [ ] `verify/allowlist.py` — registrable-domain comparison, not substring
- [ ] `audit/run_log.py` — the `audit.json` writer
- [ ] `cache/http_cache.py` — R-04 caching layer
- [ ] Temporary `calibration/threshold.json` with a placeholder, clearly marked provisional

**Exit criterion:** a synthetic test using a fake provider that returns three known image URLs produces a correct `MatchResult` and a complete `audit.json` containing every candidate with its rejection reason. A provider that raises is logged and the run still completes.

## Phase 3 — Bluesky live provider  ·  ~4 h

- [ ] **Verify A-01 first:** does Jetstream accept a connection and emit post JSON? If not, fall back to public AppView polling (`design.md` §2.2)
- [ ] **Verify A-02:** construct one `cdn.bsky.app` image URL from a real post and fetch it successfully
- [ ] Ingest loop — collect posts with image embeds, respect `BLUESKY_CRAWL_LIMIT`, R-12 politeness
- [ ] Face detection + batched embedding over crawled images
- [ ] `FaceIndex` — numpy `(N,512)`, `query()` via matmul
- [ ] Persist to `.cache/bluesky_index.npz`, always print corpus size and crawl timestamp
- [ ] `PostRef` metadata: handle, DID, display name, text, `published_at`, permalink
- [ ] `python -m pipeline crawl --limit N`

**Exit criterion:** `crawl --limit 2000` ingests ≥ 2000 real posts, finds ≥ 200 faces, and prints a crawl timestamp within the last few minutes. Querying with a face known to be in the corpus returns its post at rank 0 with a real permalink that opens in a browser.

**Risk:** face yield per post is low. Mitigation: raise the crawl limit; log the yield ratio so the budget is data-driven rather than guessed.

## Phase 4 — CLI end to end  ·  ~2 h

- [ ] `cli.py` — `scan`, `crawl`, `search`, `run-all` (`anchor` and `verify` stubbed)
- [ ] `scan --webcam` with a live preview window and keypress capture
- [ ] `rich` candidate table: score, source, domain, decision, reason
- [ ] `runs/<id>/` layout per `architecture.md` §7, score-and-decision encoded in candidate filenames
- [ ] `--dry-run` flag (R-06)

**Exit criterion — the Day 1 milestone.** `python -m pipeline run-all --webcam` captures from the webcam, embeds, searches the live Bluesky corpus, prints the full scored candidate table, and writes a complete `runs/<id>/`. **Zero API keys configured.**

Commit and stop. Day 1 is a demoable pipeline.

---

# DAY 2 — open-web search, calibrated

Target by end of day: Google Lens finds a real post for a public-figure probe, with a threshold derived from a committed ROC curve.

## Phase 5 — Google Lens provider  ·  ~3 h

- [ ] SerpApi account, key into `.env`
- [ ] **Resolve A-03:** can SerpApi take uploaded bytes, or is a public image URL required? (`design.md` §2.3). This is the main unknown in the open-web path
- [ ] `search/google_lens.py` — parse `visual_matches[]` → `Candidate`
- [ ] R-04 caching **wired before the first live call**, not after
- [ ] Write raw responses to `runs/<id>/raw/google_lens.json`
- [ ] **Resolve Q1:** confirm Lens actually finds the chosen demo subject *before* building the demo around them

**Exit criterion:** a probe of the demo subject returns ≥ 5 candidates from Lens, each with a real `page_url`. A second identical run is served entirely from cache and consumes zero quota (verify the SerpApi dashboard counter does not move).

**Risk:** Lens returns only lookalikes for the chosen subject. Mitigation: pick a different subject with heavier indexed presence. Resolve this early in the phase, not at the end.

## Phase 6 — Candidate pipeline hardening  ·  ~3 h

- [ ] Concurrent candidate image download with caching and per-image timeout
- [ ] Score **all** faces in each candidate image, keep the max (`design.md` §3.1)
- [ ] Post metadata scraping per platform — OpenGraph tags first, platform-specific fallbacks second
- [ ] Runner-up must come from a **different** `page_url`
- [ ] Off-allowlist candidates scored and logged but never reported as the match
- [ ] Bing Visual provider *(cut candidate #2)*
- [ ] Mastodon provider *(cut candidate #3)*

**Exit criterion:** a single `run-all` queries Bluesky and Lens in parallel, deduplicates overlapping candidates, and produces one ranked table. Killing network access to one provider mid-run still yields a valid result from the other (R-14).

## Phase 7 — Calibration  ·  ~2 h

- [ ] `calibration/pairs/` — ~100 positive, ~100 negative pairs
- [ ] `verify/calibrate.py` — threshold sweep, TPR/FPR, `roc.png`
- [ ] Pick the operating point at a declared FMR ≤ 1%
- [ ] Write real `calibration/threshold.json`, replacing the Phase 2 placeholder
- [ ] Confirm no numeric threshold literal remains anywhere in pipeline code (R-09)

**Exit criterion:** `roc.png` and `threshold.json` committed, `measured_fmr` recorded, and a grep for hardcoded thresholds returns nothing. Re-run Phase 5's probe with the calibrated numbers and confirm the verdict is unchanged.

## Phase 8 — Liveness gate  ·  ~1.5 h  *(cut candidate #6)*

- [ ] `face/liveness.py` — Silent-Face anti-spoof, 2.7× loose crop
- [ ] Wire into `scan --webcam` only; skipped for downloaded candidates
- [ ] Graceful degradation when the model file is absent

**Exit criterion:** a live face passes. **A printed or on-screen photo held to the webcam is rejected**, on camera. This is the S2 success criterion and a strong 10 seconds of the recording.

---

# DAY 3 — evidence, chain, recording

Target: submission-ready.

## Phase 9 — Evidence bundle  ·  ~2 h

- [ ] `evidence/canonical.py` — JCS, sorted keys, **no floats** (R-02)
- [ ] `evidence/bundle.py` — schema v1 per `design.md` §4.2
- [ ] `face_commitment()` — salted, quantised (R-01)
- [ ] `evidence/ipfs.py` — Pinata upload *(cut candidate #4)*
- [ ] Canonical round-trip test in CI

**Exit criterion:** two runs on identical input with a warm cache produce **byte-identical** `evidence.json` and the same keccak256. A grep for `.` in numeric positions of the canonical output finds no floats. **Freeze the schema here.**

## Phase 10 — Chain anchoring  ·  ~3 h

- [ ] Foundry init, `FaceEvidenceRegistry.sol` (`ANALYSIS.md` §6.3)
- [ ] Foundry tests: round trip, double-anchor reverts, unknown hash, event fields
- [ ] Deploy to Anvil, then Base Sepolia (84532) via faucet
- [ ] `chain/evm.py` — one code path, `EVM_CHAIN` switch only (R-15)
- [ ] `runs/<id>/anchor.json`
- [ ] `chain/ots.py` *(cut candidate #5)*
- [ ] **Stamp one bundle via OTS today** so a `CONFIRMED` Bitcoin proof exists tomorrow

**Exit criterion:** `anchor --chain anvil` and `anchor --chain base-sepolia` both succeed with no code change beyond the flag. The Base Sepolia tx opens on the public explorer. All Foundry tests green.

**Risk:** faucet dry or RPC flake. Mitigation: Anvil path is identical and already proven; the demo cannot be blocked.

## Phase 11 — Verifier + tamper demo  ·  ~2 h

- [ ] `chain/reverify.py` — four independent checks (`design.md` §5.4)
- [ ] `SKIPPED` reported distinctly from `PASS`, never silently
- [ ] Exit code 0 only when every attempted check passes
- [ ] `rich` output styled for the recording

**Exit criterion — the highest-value criterion in the project.** `verify` on an untouched bundle reports every attempted check as PASS. Changing **one character** in `evidence.json` makes check 1 report FAIL with a non-zero exit code. Both observed and rehearsed.

## Phase 12 — README, sample runs, docs  ·  ~2 h

- [ ] README per `ANALYSIS.md` §13, including the zero-key quickstart
- [ ] Contract address + explorer link
- [ ] `roc.png` and stated FMR
- [ ] Known limitations copied verbatim from `prd.md` §10
- [ ] Privacy section: R-01 reasoning, R-05 no-bulk position
- [ ] **Produce and commit the `NO_MATCH` run** (R-16)
- [ ] Commit 3–4 sample runs on different subjects
- [ ] Fill the `prd.md` §7 traceability table with demo timestamps

**Exit criterion:** a clean clone plus `pip install -r requirements.txt` plus `fetch_models.py` plus `run-all` works with **no** `.env`, using only Bluesky. Verified by actually doing it in a fresh directory.

## Phase 13 — Recording  ·  ~2 h

- [ ] `scripts/warmup.py` — pre-load every ONNX session, warm the cache
- [ ] Terminal font size up; verify legibility at 1080p
- [ ] Rehearse the beat sheet in `ANALYSIS.md` §9
- [ ] Three takes minimum
- [ ] Confirm on playback: raw provider JSON visible, rejection table legible, explorer page opened, tamper failure visible

**Exit criterion:** one clean take under five minutes covering face scan → liveness rejection → search → candidate table → real post opened in a browser → anchor → explorer → verify PASS → tamper FAIL → second subject → `NO_MATCH` run.

---

## Cut order

Under time pressure, drop in this order (mirrors `rules.md`):

```
1. C2PA manifest
2. Bing Visual provider
3. Mastodon provider
4. IPFS upload
5. OpenTimestamps
6. Liveness gate
```

**Never cut:**

- Candidate table with rejection reasons
- Tamper demo
- Committed `NO_MATCH` run
- Audit log
- README known-limitations section

The never-cut list is what the brief grades. The cut list is polish.

---

## Dependencies

```
Phase 0 ─► Phase 1 ─► Phase 2 ─┬─► Phase 3 ─► Phase 4  (Day 1 milestone)
                               └─► Phase 5 ─► Phase 6 ─► Phase 7
                                                  Phase 8  (independent)
Phase 6 ─► Phase 9 ─► Phase 10 ─► Phase 11 ─► Phase 12 ─► Phase 13
```

Phase 8 depends only on Phase 1 and can be done any time. Phase 10's contract work is fully independent of the Python pipeline and is the natural parallel track if a second person is available (Q3).

## Slack

Roughly 4 hours of unallocated time across 3 days. Reserve it for Phase 5 (A-03 is the largest unknown) and Phase 13 (retakes always take longer than planned). Do not spend it on cut-list items.
