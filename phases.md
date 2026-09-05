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

## Phase 2 — Provider interface + matcher  ·  ~2 h  ·  **DONE**

- [x] `search/base.py` — `Candidate`, `SearchProvider` protocol, `ProviderReport`
- [x] `search/orchestrator.py` — thread-pool fan-out, per-provider timeout, R-14 isolation
- [x] `verify/matcher.py` — cosine, threshold + margin rule, rejection reasons
- [x] `verify/dedupe.py` — URL normalisation then phash, Hamming ≤ 6
- [x] `verify/allowlist.py` — registrable-domain comparison, not substring
- [x] `audit/run_log.py` — the `audit.json` writer
- [ ] `cache/http_cache.py` — R-04 caching layer — **not built yet**, not exercised because Bluesky needed no paid quota to protect. Needed before Phase 5 (SerpApi).
- [x] Temporary `calibration/threshold.json` placeholder — actually implemented as `config.load_match_policy()` returning an in-code placeholder (`is_placeholder=True`) rather than a committed JSON file. Equivalent effect, different mechanism than originally planned.

**Exit criterion:** met via the demo UI's live run (see Phase 4a) rather than a standalone synthetic test — a real Bluesky search produced a complete `audit.json` with every candidate scored and a rejection reason. The synthetic fake-provider unit test described here was not separately written; consider adding it before Phase 5 if regression coverage on the matcher is wanted.

## Phase 3 — Bluesky live provider  ·  ~4 h  ·  **DONE (core), CLI wrapper not done**

- [x] **A-01 resolved:** Jetstream not used — `getFeed`/`getAuthorFeed` via public AppView confirmed working with zero auth, live. `searchPosts` confirmed returning 403 unauthenticated, live — this is why feed generators are used instead of keyword search.
- [x] **A-02 resolved, better than assumed:** image embeds return ready-made `thumb`/`fullsize` CDN URLs directly in the API response. No manual URL construction needed.
- [x] Ingest loop — `_collect_image_refs` (sequential feed pagination) + `_fetch_one_image` (concurrent, `ThreadPoolExecutor(max_workers=16)`). Respects `BLUESKY_CRAWL_LIMIT`.
- [x] Face detection + embedding over crawled images (`crawl()` method) — not batched (`embed_batch` exists but crawl() calls `embed()` per-face; fine at current scale, revisit if slow)
- [x] `FaceIndex` — numpy `(N,512)`, `query()` via matmul
- [ ] Persist to `.cache/bluesky_index.npz` — **not built**. Every server restart re-crawls. Fine for a demo session, a gap for a long-running deployment.
- [x] `PostRef` metadata: handle, DID, display name, text, `published_at`, permalink
- [ ] `python -m pipeline crawl --limit N` — **not built as a standalone CLI command.** The crawl is currently only reachable through `webapp/server.py`'s `/api/search` endpoint, which calls `BlueskyProvider.crawl()` directly. See memory.md "Not done" for the refactor note.

**Exit criterion — met, informally.** Measured live: 200 posts crawled, 86 faces indexed, 27.1s elapsed (first version, before the concurrency fix, timed out at 90s for 150 posts — see memory.md for the diagnosis). A probe face known to be absent from a 300-post crawl correctly returned `NO_MATCH` with all candidates rejected below threshold, proving the query path works and doesn't fabricate matches. A same-face-in-corpus rank-0 test (the originally planned exit criterion) was not separately run — worth doing before trusting this at scale.

## Phase 3b — CANCELLED (superseded by D-21)

Proposed a "scoped crawl" using hand-picked Bluesky seed handles so a MATCH could be demonstrated. **Cancelled.** Web detection reaches real social posts across many platforms without us choosing where to look, which removes both the need for scoped crawling and the honesty problem it carried. `config.bluesky_seed_handles` stays unused; remove it during Phase 5a cleanup.

---

# FINAL PLAN (5 Sep 2026, owner-approved) — scoped for a shortlisting task

Supersedes everything below. Rationale in `memory.md` §3b (D-25..D-33).
Remaining effort ≈ 8–10 focused hours. Face core is **done, do not touch it** (D-25).

| # | Step | Exit criterion (must be executed and observed) |
|---|---|---|
| **F1** | `cache/http_cache.py` — R-04 | ✅ DONE. Cache hit makes zero network calls, verified by test |
| **F2** | `face/quality.py` — `MIN_FACE_PX = 50` (D-23) | ✅ DONE. Small face rejected, large passes, verified by test |
| **F3** | `search/web_detect.py` — GCV primary, mocked fixtures (D-28, D-30) | ✅ DONE. 20 tests, including against a real captured SerpApi response |
| **F4** | Candidate verification loop, `verify/pipeline_run.py` | ✅ DONE. Live end-to-end MATCH: real Instagram post, score 0.7685, 47 candidates examined. Found+fixed a real YuNet large-image bug along the way (see memory.md 3c) |
| **F5** | Image upload input: `POST /api/upload` (D-19) | ✅ DONE. CLI `--image PATH` not yet added — UI path proven live instead |
| **F6** | UI correctness pass (R-21, D-24) | ✅ DONE. NO_MATCH headline + diagnostics toggle, 3-state liveness rendering |
| **F7** | `evidence/canonical.py` + `bundle.py` + salted commitment (D-26) | ✅ DONE. Live: real MATCH produced `0xef13f0ea...`, independently recomputed from the persisted file, byte-identical. No floats in hashed output (enforced, not just avoided) |
| **F8** | `contracts/EvidenceRegistry.sol` + Foundry + Anvil deploy (D-27) | ✅ DONE. Foundry v1.8.1 (precompiled win32 zip, not the bash installer). Deployed live to Anvil at `0x5FbDB2315678afecb367f032d93F642f64180aa3`. 8 Foundry tests green |
| **F9** | `chain/evm.py` + **the tamper demo** | ✅ DONE, observed live. SRK match → `anchor` → `tx=0xef23bf9f...` block 11 → `verify` → **PASS exit 0** → edited one character in `evidence.json` → **TAMPERED exit 1** → restored → PASS |
| **F10** | README front door, `docs/` move (D-33), 2–3 sample runs incl. a real `NO_MATCH` | ⬜ NOT DONE. See G2/G3 below |
| **F11** | Screen recording | ⬜ NOT DONE. See G6 below |

**Optional, only if F1–F11 are solid:** SerpApi secondary provider (needs S3 presigned URL, D-31) → Base Sepolia bonus anchor.

**Never cut:** candidate rejection table · tamper demo · committed `NO_MATCH` run · audit log · README limitations.

---

# G-SERIES — post-F9 recall & submission plan (5 Sep 2026, owner-approved)

All three brief requirements are met and verified live as of F9. The G-series
is (a) recovering measured recall we were silently discarding, and (b) the
remaining submission artifacts. Rationale in `memory.md` §3f–3g (D-34..D-38).

Ordering rationale: **G1 before G2/G3/G6.** G1 changes what the demo shows,
and the README, sample runs and recording all document post-G1 behaviour.
Writing the README first means writing it twice.

| # | Step | Status | Exit criterion (must be executed and observed) |
|---|---|---|---|
| **G1** | Recall & honesty fixes — X `?name=` variant, YouTube tier fallback, Shorts regex, truthful `reject-not-an-image`, diagnostics ordering | ✅ DONE, verified live | X went from discarded (32px face, below gate) to **the best match in the run at 0.9806**. YouTube 404→`sddefault` fallback and `/shorts/` both confirmed. All 11 Meta/TikTok rows now read `reject-platform-blocked`, not `reject-no-face`. 122 tests |
| **G1.1** | **Regression fix (R-23/R-24).** Split `pbs.twimg.com` by path: `/media/` = `?name=` scheme, `/profile_images/` = filename-suffix scheme. Additive-only chain. Fetch-failed message reports variants tried | 🔧 IN PROGRESS | A `/profile_images/` URL resolves and scores instead of producing five 404s. A property test asserts the provider's original URL survives in the chain **for every platform**, not just X. The `x.com/henry_kutrieb` run that produced a false `NO_MATCH` produces a scored candidate |
| **G1.2** | `content_kind` labelling — `post` vs `profile` vs `unknown`, carried into the evidence bundle | ⬜ TODO | An accepted profile match is cited as a profile, not passed off as a "post". Schema version bumped if the bundle shape changes (R-02) |
| **G1.3** | Inline tamper demo — run the tamper check automatically at the end of an anchored run | ⬜ TODO | A single command shows PASS then TAMPERED without a second manual invocation. Closes the self-demonstration gap vs SAJITH07N |
| **G2** | **Root `README.md`** — required by the brief | ✅ DONE | Covers what it does / how to run (incl. zero-key quickstart) / which chain and why local is allowed / the live anchor->PASS->TAMPERED->restore->PASS cycle with real tx/hash values / platform reach (X/GitHub dual CDN scheme, Meta/TikTok wall) / profiles-vs-posts / synthetic-avatar NO_MATCH / privacy & consent section documenting the quarantined runs / full limitations list |
| **G3** | Commit 2–3 curated sample runs incl. a genuine `NO_MATCH` (R-16) | ✅ DONE | `runs/2026-09-05T18-07-40Z` (MATCH, score 0.9806, anchored+verified+tampered+restored live, `anchor.json`+`match_image.jpg` included) and `runs/2026-09-05T18-19-36Z` (genuine non-degraded NO_MATCH, 21 real candidates, all <0.15). Both public-figure subjects; no private individual's data committed |
| **G4** | *(optional)* Wire `anchor`/`verify` buttons into the demo UI | ⬜ TODO | The recording flows without switching to a terminal. Likely absorbs G1.3 |
| **G5** | *(optional, weakest)* Threshold calibration to retire the `0.42` placeholder | ⬜ TODO | **Recommended SKIP.** Calibration without a proper benchmark set produces a number that *looks* more rigorous than it is. Current disclosure (0.42 default shown against the measured 0.765-vs-0.074 separation, explicitly labelled not benchmark-calibrated) is more honest than a hand-tuned replacement |
| **G6** | Screen recording (= F11) | ⬜ TODO | One take: face scan → search → real post → anchor → verify PASS → tamper FAIL → `NO_MATCH` run |

**Explicitly rejected for the G-series:** a manual "paste a content URL"
escape hatch (as `ivocreates/facechain` offers). It would have rescued the
failed `x.com` run, but it weakens the brief's "genuine search step, not a
hardcoded/pre-picked result" claim — the requirement most likely to be
scrutinised. Fix fetch coverage instead of adding a human override. See D-38.

---

## G1.1 — twimg dual-scheme regression fix — ✅ DONE

Fixed the false `NO_MATCH` on a real non-celebrity probe (`x.com/henry_kutrieb`).
`pbs.twimg.com` runs two mutually exclusive sizing schemes on one domain
(`/media/...?name=`, `/profile_images/..._suffix`); the first cut of the X fix
applied the `/media/` scheme to a `/profile_images/` URL and turned a working
HTTP 200 into five 404s. Fixed in `pipeline/search/media_urls.py` with a new
binding invariant (R-23): the provider's original URL must always survive in
any rewritten fallback chain. Verified live — the recovered candidate now
resolves the bare-filename variant (18136 bytes, 400x400, face 147px) on the
first attempt. `tests/test_media_urls.py` (20 tests) asserts R-23 as a generic
property over every known URL shape, not per-platform. `tests/test_pipeline_run.py`
gained the end-to-end regression case plus a fetch-failed-message test (R-24).

## Tier 0 — schema v2 + image-hash population — ✅ DONE (5 Sep 2026)

A second, independent defect found in the same review pass, more serious
than G1.1: **every previously anchored run had `match.image_sha256 == ""`**
because no provider (GCV or Bluesky) ever populated it, and `chain/evm.py`
silently zero-filled the on-chain `imageHash` with `"0" * 64` rather than
failing. A judge reading `evidence.json` next to the block explorer would
have found this in seconds and it would have discredited the tamper-demo
verifier entirely.

Fix, six tests written first (all initially failing, then made to pass):

- `PipelineResult.best_image_bytes` — the actual bytes fetched and scored
  for the winning candidate, threaded out of `run_pipeline()`'s existing
  `images` dict rather than discarded once scoring finishes. One path, both
  providers — no Bluesky-specific exemption (confirmed Bluesky had the
  identical gap before assuming otherwise).
- `build_evidence(..., image_bytes: bytes)` is now a required, explicit
  argument with **no fallback**. Raises `ValueError` on empty/None. Computes
  both `image_sha256` (already existed, was simply never fed real input) and
  a new `image_phash` — same `imagehash` library and Hamming convention as
  `verify/dedupe.py`, so a future re-fetch check's "perceptually identical"
  verdict means the same thing in both places.
- `chain/evm.py`'s `anchor()` deleted the `or "0" * 64` fallback outright and
  raises instead. Guarded by both a behavioural test and a source-level grep
  test (the expression must not exist, not just be unreachable).
- **Schema bumped to v2** in one combined pass, done deliberately before any
  sample run is committed: `post.content_kind` (post/profile/unknown, never
  null — see verify/allowlist.content_kind), `match.image_phash`,
  `match.verified_against` (search_engine_cache/platform_origin). A stale
  half-written docstring had claimed a "v2" that `SCHEMA_VERSION` and the
  code did not actually implement — the exact doc-vs-code lie R-24 exists to
  prevent. Fixed alongside the real bump, and a test now asserts the
  docstring never references a version number higher than `SCHEMA_VERSION`.
- `get_http_cache()`'s docstring claimed hit/miss stats "land in the audit
  log"; they never did. `build_audit()` now takes optional per-provider
  `cache_stats` and computes an aggregate; `webapp/server.py` snapshots
  `http.stats()` before/after each provider call.
- `runs/` deleted (not migrated) — confirmed untracked in git first. Every
  run in it had a v1 bundle and a zero `imageHash`.

**Verified against the real running Anvil chain, not mocks:** all 12
`test_chain_evm.py`/`test_reverify.py` tests pass, meaning the anchor→verify→
tamper round trip now anchors a genuinely non-zero `imageHash`. Full suite:
145 passed. pyflakes clean (same handful of pre-existing unrelated warnings).

## Tier 1 — re-fetch check, verdicts, headline selection — PARTIALLY DONE (5 Sep 2026)

Triggered by two real, genuine-search test runs (a private individual found
via face alone through GitHub, and a probe suspected to be a synthetic/AI
avatar) that surfaced defects live, on screen, faster than the plan had
gotten to them. See `memory.md` §3m for the full narrative.

| # | Item | Status | Notes |
|---|---|---|---|
| `NO_CANDIDATES` verdict | ✅ DONE | Distinct from `NO_MATCH` — every provider failed/returned nothing vs candidates were examined and none matched. `score_candidates([], ..., prerejected=[])` returns it directly |
| `MATCH_NON_SOCIAL` verdict | ✅ DONE | Fires when the best domain-rejected candidate would have passed threshold+margin. Closes a live UI contradiction: the banner said "No match found ... honest outcome" while the caption said "1 high-scoring non-social source also matched" — both on screen at once |
| Allowlist principle + GitHub added | ✅ DONE | R-06 in `rules.md`: a platform qualifies iff an individual maintains a public identity profile and publishes content under it. `github.com`/`githubusercontent.com` added under this test — was missing by oversight, not by considered exclusion, once compared against `linkedin.com` already being allowed |
| GitHub repo→profile page_url derivation | ✅ DONE | `media_urls.derive_github_profile_url`: `github.com/<user>/<repo>` → `page_url=github.com/<user>`, original repo URL kept as `raw["found_on"]`. `content_kind` classifies a bare GitHub profile as `profile`, never `post` |
| `Candidate.match_kind` | ✅ DONE | `"full"` \| `"partial"` \| `"similar"` \| `""`, from GCV's own `fullMatchingImages`/`partialMatchingImages`/`visuallySimilarImages` classification. R-03: never used to accept/reject, reporting only |
| `MatchResult.unverifiable_platform_hits` | ✅ DONE | Candidates with `match_kind` full/partial that are also `reject-platform-blocked` — surfaced as a caveat on top of whatever verdict applies, not a sixth verdict state. UI renders it as a mandatory clause on the `NO_MATCH` banner |
| TikTok endpoint-vs-domain block fix | ✅ DONE | `tiktok.com/api/img/...` is blocked; `tiktokcdn-us.com` signed CDN URLs are fetchable (verified live in the same run, score 0.0593). `is_media_blocked` now checks path prefix for TikTok specifically rather than blocking the whole platform |
| Stale `0.074` ceiling copy | ✅ DONE | Two live runs measured up to 0.1385 (n=9 pilot was always too small). UI footer, `rules.md` R-21 rationale updated to cite both figures honestly rather than repeating the stale single number next to a table that visibly contradicted it |
| Negatives harvest | ✅ DONE | `calibration/negatives_harvested.json` — 41 genuine negatives across two runs + the 68px YouTube Short false negative, each `label_source: "manual_inspection"` |
| Consent quarantine | ✅ DONE | The two runs (private individual; suspected-synthetic avatar) moved to `calibration/quarantine/` (gitignored), page/image URLs replaced with sha256, never committed, never used in the recording |
| Diagnostics reason-column fix | ✅ DONE | Full reason moved to a `title` tooltip; visible cell text is short and non-wrapping (`shortenReason()` in `app.js`). No row can show a bare `—` with no visible reason (R-21 rule 4) |
| Re-fetch verifier check | ⬜ NOT STARTED (deprioritised) | Superseded in practice by the resolver cascade below, which solves the more common problem (missing image, not stale image). Still open if time allows |
| Headline selection (citability sort) | ✅ DONE (6 Sep 2026) | `matcher.py`: within the D-35 identity cluster, `min(above, key=(not-citable, content_kind_rank, -score))`. Verified live: X media hit (0.9806, not citable — bare JPEG) now `corroborating`; YouTube hit (0.9618, a real openable page) is `ACCEPT`. Score never re-enters the accept decision. `media_urls.is_citable_page()` new. 4 new tests |
| SerpApi `image` over `thumbnail` | ✅ DONE (6 Sep 2026) | `parse_serpapi_lens` now prefers full-size `image`/`images[0]`; `raw["image_field_used"]` records which field won. 3 new tests |

### Additional G1-adjacent work landed 6 Sep 2026 (owner instruction: "use whats best ... real crawling instead of --")

| # | Item | Status | Notes |
|---|---|---|---|
| T1.1 | Meta/TikTok wall re-measured with a browser UA | ✅ DONE | `scripts/probe_meta_wall.py`. Result: **no change** — Meta/Instagram return byte-identical stubs under research-UA vs browser-UA vs browser-UA+referer; TikTok's `/api/img/` endpoint timed out under all three. The wall is real server-side access control, not naive UA sniffing. Documented in `allowlist.py` and `rules.md` |
| — | Browser UA for candidate image fetches (generic sites) | ✅ DONE | `http_cache.BROWSER_USER_AGENT`, used only for one-off candidate fetches (never our own GCV/SerpApi API calls, which keep the descriptive R-12 UA). Distinct from T1.1: this targets ordinary blanket bot-mitigation on non-Meta/TikTok hosts, and DOES help there |
| — | Page-resolver cascade | ✅ DONE | New `pipeline/verify/resolver.py`: OpenGraph/Twitter Card meta-tag extraction, keyless oEmbed (YouTube, X), Reddit `.json`. Tried as a last resort when the normal image-URL walk is exhausted. All keyless, no login, no scraper package, no crawler-UA impersonation (I-05). 25 new tests |
| — | Real diagnostics instead of a bare `—` | ✅ DONE | `FetchDiagnostics` (HTTP status, content-type, byte count, image dimensions, faces found, largest face px, routes tried) threaded through `pipeline_run.py` -> `build_audit()` -> UI. A row with no cosine score now shows real measured observations instead of a dash or a fabricated number |
| — | `POST /api/anchor/{run_id}`, `/api/verify/{run_id}`, `/api/tamper/{run_id}` | ✅ DONE (G4) | Same `pipeline.chain.*` functions the CLI uses. Tamper endpoint mutates a scratch copy only — verified live and by test that the real `evidence.json` is never touched. UI gained a "3. Blockchain" panel with anchor/verify/tamper buttons |

All items verified live against the real running Anvil chain and demo server, not just unit tests. 224 tests passing, pyflakes clean.

### G6 prep — everything buildable without a camera, done 6 Sep 2026

| # | Item | Status | Notes |
|---|---|---|---|
| — | `scripts/warmup.py` | ✅ DONE | Pre-loads all 3 ONNX sessions, runs one real inference to absorb JIT warmup, checks: models present, search key set, `HTTP_CACHE` state, Anvil reachable, contract deployed, deployer funded, exactly the intended sample runs present with no stray scratch runs. Exit 0 only if every check passes. Run live: caught real stray-run clutter and correctly flagged `HTTP_CACHE=1` |
| — | `docs/recording-beat-sheet.md` | ✅ DONE | Beat-by-beat script: consenting teammate -> public figure -> synthetic face, in that order, per the methodology decided in §3o/§3p. Explicit "what NOT to do" section (no `HTTP_CACHE=1`, no non-consenting subjects, no misrepresenting `corroborating`/`match_kind`) |
| — | README updated | ✅ DONE | Test count corrected (224), new sections for the UI anchor/verify/tamper buttons and the resolver cascade/diagnostics, "Before recording" section pointing at `warmup.py` |
| G6 | Actual recording | ⬜ NOT STARTED — requires a camera and a consenting human, neither of which this session can supply |

**Test methodology change (owner-approved):** stop sampling random X avatars
for testing — they are disproportionately synthetic, stolen, or of people
with zero footprint, and a genuine positive found this way can never be
published. Build a deliberate test set instead: 2–3 consenting teammates
(closes the loop legitimately — GitHub profile links to the account the
photo came from), 3 public figures (the `post`-case, brief-literal
satisfaction), 1 known-synthetic face (a designed, publishable `NO_MATCH`).

**Recording order, revised:** lead with a consenting teammate found by face
alone (the strongest genuineness proof — no name Google could leak), then a
public figure (satisfies the brief's literal "post" wording + shows
consensus across platforms), then the tamper demo.

**Then, unchanged from before:** G2 (README, required), G3 (sample runs incl.
`NO_MATCH`, generated only after Tier 1 fully lands so nothing bakes in stale
shape), G4 (tamper endpoint + UI anchor/verify buttons), ~~G5~~ (skip —
replaced by the live score-vs-face-size table + measured false negative),
G6 (recording, preceded by the deliberate test set above under
`HTTP_CACHE=0`).

---

# Superseded plan (kept for traceability)

## Earlier revision — web detection as the primary path

Supersedes the original Phase 5/6 ordering. Rationale in `memory.md` D-21..D-24 and §3a. Everything below is free of blockchain work; the pre-existing stop instruction still applies at the end of Phase 5d.

## Phase 5a — Web detection provider  ·  ~3 h  ·  **THE CRITICAL PHASE**

Nothing else in the project satisfies "search the web". If this does not work, the submission does not meet the brief.

- [ ] `cache/http_cache.py` **first** — R-04. Must exist before any live SerpApi call, or debugging will burn the ~100/mo quota. Key on (url, sorted params), store response bodies under `.cache/`, honour `HTTP_CACHE=0`.
- [ ] **Resolve the last open unknown (A-03):** how to submit a *local* probe image to SerpApi. It needs a publicly reachable URL. Options: temporary upload host, or a SerpApi file-upload path if one exists. **Settle this before writing the provider** — it gates the whole phase.
- [ ] `search/web_detect.py` with a `serpapi` backend per `design.md` §2.1a. Parse `visual_matches[]` + `organic_results[]` into `Candidate`. Record `related_content[].query` as the identity signal, context only (R-03).
- [ ] Persist every raw response to `runs/<id>/raw/` — unedited third-party JSON is the anti-fabrication evidence
- [ ] Remove dead config: `bluesky_seed_handles`

**Exit criterion:** a probe of a public figure returns ≥5 candidates including ≥1 on a social domain, with a real `page_url` that opens in a browser. A second identical run is served entirely from cache and the SerpApi dashboard counter **does not move**.

## Phase 5b — Verification loop + quality gate  ·  ~3 h

- [ ] `face/quality.py` — `MIN_FACE_PX = 50` gate per `design.md` §1.7, applied to both probe and candidates
- [ ] Concurrent candidate image download, cached, per-image timeout
- [ ] Score **every** face in each candidate image, keep the max
- [ ] Wire `dedupe.py` (phash) and `allowlist.py` into the real path
- [ ] Runner-up must come from a **different** `page_url` for the margin rule
- [ ] New reject reason `reject-face-too-small`, logged with pixel size
- [ ] Off-allowlist candidates scored and logged, never reported as the match

**Exit criterion:** an end-to-end run on a public figure yields `verdict: MATCH` with a real social post URL, a score above threshold, and an `audit.json` listing every candidate with its score and reject reason. Satisfies prd.md S14 and S15.

## Phase 5c — Image upload input  ·  ~1.5 h

- [ ] `POST /api/upload` and a CLI `--image PATH` path, sharing one code path with webcam capture
- [ ] Liveness returns `not_applicable` for uploads; `LivenessResult` gains that state (D-20, R-22)
- [ ] UI: two input tabs, Webcam and Upload

**Exit criterion:** an uploaded public-figure photo drives a full run, and its liveness renders `N/A — provenance unverified`, never `LIVE`. Satisfies S16.

## Phase 5d — UI correctness pass  ·  ~1.5 h

Fixes the real defect behind "it showed me random female faces" — a presentation fault, not a model fault (D-24).

- [ ] On `NO_MATCH`, verdict is the headline; candidates collapse behind "show diagnostics", captioned as rejected
- [ ] Never render 0.0–0.2 scores as ranked suggestions (R-21)
- [ ] Show the active threshold and margin alongside every score
- [ ] Show which providers ran, and label a Bluesky-only run as a degraded closed-corpus run
- [ ] Show probe face pixel size and the gate outcome

**Exit criterion:** a `NO_MATCH` run cannot be misread as "these people might be you" (S18), and a too-small face reports a clear reason (S17).

**STOP HERE for owner verification, per the standing instruction. No blockchain work.**

## Phase 5e — Calibration  ·  ~2 h  *(after the stop, before the recording)*

- [ ] `calibration/pairs/` — ~100 positive, ~100 negative pairs
- [ ] `verify/calibrate.py` — sweep, ROC, `roc.png`
- [ ] Pick the operating point at a declared FMR ≤ 1%; write real `calibration/threshold.json`
- [ ] Confirm no threshold literal remains in pipeline code (R-09)

**Exit criterion:** `roc.png` and `threshold.json` committed with `measured_fmr`, and the Phase 5b run re-checked against the derived numbers. Until this lands, every score shown is against a provisional 0.42 and the UI should say so.

## Phase 5f — Google Cloud Vision backend  ·  ~1.5 h  ·  *optional, cut candidate #2*

- [ ] Second backend in `web_detect.py`, `WEB_DETECTION` feature, mapped to the same `Candidate`
- [ ] `WEB_DETECT_BACKEND` switch, `auto` prefers gcv for its 10× quota
- [ ] Handle the known case where only `webEntities` come back with image arrays absent

**Exit criterion:** same probe, same `Candidate` shape, from either backend selected by env var alone.

---

## Original Phase 5 — superseded

Kept for traceability. The old plan treated Google Lens as one provider among several including Bing Visual Search (retired, D-18) and paid face APIs (D-17). Replaced by 5a–5f above.

## Phase 4 — CLI end to end  ·  NOT DONE, superseded by Phase 4a for the demo path

`scan`, `crawl`, `search`, `run-all` as standalone CLI commands do not exist yet. `version` and `serve` are the only commands in `cli.py`. The functionality exists but is currently only reachable via the web UI's endpoints. **This is a gap, not a design decision** — revisit before relying on the CLI for anything beyond `serve`.

## Phase 4a — Local demo UI  ·  ~2.5 h  ·  **DONE**  *(amended 5 Sep 2026, prd.md G8/S13)*

Owner's explicit instruction: build the scanning interface and its logic, then **stop before any blockchain work**. This phase is that scanning interface.

- [x] `webapp/server.py` — FastAPI, `/api/scan`, `/api/search/{run_id}`, calling `pipeline.face` and `pipeline.search.bluesky`/`verify.matcher` directly, no duplicate logic. (`GET /api/run/{id}` from the original design was not built — audit files are written to `runs/` but there's no endpoint to re-fetch one; low priority.)
- [x] `webapp/static/index.html` + `app.js` — plain HTML/JS, no framework. Webcam capture via `getUserMedia`, capture button, aligned-crop preview, liveness verdict badge
- [x] Candidate table rendered in the page: rank, score (with a visual bar), source, post link, decision, reason — sorted, accept row highlighted
- [x] Bound to `127.0.0.1` only, no auth. Documented in server.py's module docstring and architecture.md 5a.
- [x] `python -m pipeline serve` launches it

**Exit criterion — met and verified live, not just started.** Ran the full loop via direct HTTP calls: `/api/scan` on a real photo returned a found face, liveness 0.9999 (live), det_score 0.945, and a base64 aligned crop. `/api/search/{run_id}` triggered a real 300-post crawl (107 faces indexed), re-verified all 20 candidates through our own face core, and returned a correct `NO_MATCH` (probe wasn't in the small random sample). `runs/<id>/audit.json` confirmed written with real scores. Browser-based manual click-through (as opposed to direct HTTP calls) and a screenshot for `runs/` were not captured this session — worth doing before the actual recording.

**STOPPED HERE per owner instruction.** Do not proceed to Phase 9 (evidence bundle) or Phase 10 (chain anchoring) without explicit go-ahead.

---

# DAY 2 — open-web search, calibrated

Target by end of day: Google Lens finds a real post for a public-figure probe, with a threshold derived from a committed ROC curve.

## Phase 5 — Google Lens provider  ·  ~3 h  ·  **PRIORITY RAISED**

Now our **only** open-web provider after D-17 removed the commercial face-search APIs. If this phase fails, the "search the web" requirement rests entirely on a corpus we built ourselves, which is a materially weaker claim.

- [x] SerpApi account, key into `.env` — **done by owner 5 Sep 2026**, key present. Not yet used by any code.
- [ ] **Resolve A-07 FIRST, before writing the provider:** do Lens results for a public figure actually include social-media pages that survive the domain allowlist? Spend one manual query on this. If Lens returns only news and stock photography, reconsider the approach before spending 3 hours on it.
- [ ] **Resolve A-03:** can SerpApi take uploaded bytes, or is a public image URL required? (`design.md` §2.3)
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
