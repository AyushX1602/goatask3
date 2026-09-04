# Rules — face-chain-verify

Binding constraints for anyone (human or agent) writing code in this repo.

`prd.md` says what to build. `design.md` says how. **This file says what must never break.**

A rule is cited by ID in code comments and PR descriptions. If a rule blocks progress, change the rule deliberately in this file with a reason — do not work around it silently.

---

## Hard invariants

Violating one of these is a defect, not a style preference.

### R-01 — No biometric data leaves the machine, ever

No face embedding, and no reversible derivative of one, may be written to a blockchain, uploaded to IPFS, committed to git, or included in any published artifact.

Only a **salted commitment** may be published: `keccak256(salt ‖ quantize(embedding))`. The salt lives in `.env` and is never committed.

*Why:* an immutable public ledger plus a recoverable biometric template creates a permanent, un-deletable biometric record. That collides directly with GDPR Art. 9 and India's DPDP Act. The commitment still proves "this same face produced this record" without publishing anything biometric.

Structurally enforced: `chain/` and `evidence/` accept hashes, never `Embedding` objects (`architecture.md` §3).

### R-02 — Canonical JSON is frozen and float-free

Any structure that gets hashed must go through `evidence/canonical.py`. No exceptions, no ad-hoc `json.dumps`.

- Lexicographically sorted keys, UTF-8, no insignificant whitespace
- **No floats.** Similarity → integer basis points. Timestamps → integer Unix seconds.
- `schema_version` is mandatory
- Frozen at the end of Phase 9. Later changes require a version bump, not an edit

*Why:* if serialisation drifts between `anchor` and `verify`, the hash changes and verification fails during the recording. This is the single most common way hash-anchoring demos die.

### R-03 — Provider scores are recorded but never trusted

`Candidate.provider_score` goes into the audit log and nowhere else. The accept/reject decision comes only from our own ArcFace cosine similarity in `verify/matcher.py`.

*Why:* it is the core of the "genuine search" claim. Google Lens returns visual similarity, not face identity. If we deferred to a provider's ranking we would be a search wrapper. Because we re-verify locally, we own the decision and can defend it on camera.

### R-04 — Every outbound HTTP response is cached on first fetch

All external calls go through `cache/http_cache.py`. Development and testing replay from `.cache/`. `HTTP_CACHE=0` forces live fetches, and is used only for the recording and for deliberate freshness checks.

*Why:* the SerpApi free tier is roughly 100 searches per month. Debugging without a cache will burn it in an afternoon and kill the open-web path.

### R-05 — One face per invocation. No bulk mode

No batch face-search API, no CSV input of many faces, no loop over a directory of probes. Crawling a corpus of *posts* is fine; searching many *people* is not.

*Why:* the difference between a provenance tool and a surveillance tool is throughput. This constraint is a design position, and it gets stated in the README.

### R-06 — Consent and dry-run are first-class

`--dry-run` must execute the full pipeline and anchor nothing. `--consent-record` writes a consent statement into the evidence bundle. The domain allowlist is an allowlist, not a denylist: dating platforms, mugshot aggregators, and adult sites are never surfaced regardless of match score.

### R-07 — Embeddings are L2-normalised at creation

Normalisation happens once, inside `embed()`. Every downstream consumer may assume `‖v‖ ≈ 1` and use a dot product for cosine. Never construct an `Embedding` by hand.

*Why:* mixing normalised and un-normalised vectors makes every threshold meaningless, and fails silently.

### R-08 — Always align before embedding

No code path may pass an unaligned crop to `embed()`. Alignment is a similarity transform to the ArcFace 5-point template (`design.md` §1.4).

*Why:* ArcFace was trained on that exact geometry. Skipping alignment degrades accuracy badly with no error raised. It is the most commonly omitted step in face recognition code.

### R-09 — Thresholds come from calibration, never from a literal

`MATCH_THRESHOLD` and `MATCH_MARGIN` are read from `calibration/threshold.json`. No numeric literal for either may appear in pipeline code.

*Why:* a hardcoded magic number cannot be defended when a judge asks where it came from. A committed ROC curve can.

### R-10 — No secrets in git

`.env` is gitignored; `.env.example` is committed with empty values. Never log a key, a private key, or the face-commitment salt. Scan the diff before every commit.

### R-11 — No ToS-violating or captcha-bypassing providers

Forbidden: PimEyes scraping, Yandex scraping, captcha solving or evasion, headless-browser fingerprint spoofing, any tool describing itself as bypassing a paywall or obfuscation.

*Why:* two independent reasons. It is a legal and reputational liability on a submitted project, and these paths break unpredictably — Yandex returns captcha pages with HTTP 200, so the scraper fails *silently* mid-recording. See `ANALYSIS.md` §2.2.

### R-12 — Be a polite client

Descriptive User-Agent with a contact URL. Capped concurrency. `tenacity` exponential backoff on 429 and 5xx. Honour a hard cap on crawl volume. Never hammer a free public API.

### R-13 — Verify before claiming done

A phase is complete only when its exit criterion in `phases.md` has been **executed** and its output observed. A command exiting zero is not evidence a feature works. Never report a task complete based on code having been written.

### R-14 — No single provider can abort a run

A provider that raises, times out, or returns nothing is logged and contributes zero candidates. The orchestrator continues. A run with every provider failing produces a valid `NO_MATCH`, not a crash.

*Why:* single-take recording. Any component that can abort the pipeline is a liability.

### R-15 — Local and public chains share one code path

`EVM_CHAIN` switches the RPC endpoint and nothing else. If Anvil and Base Sepolia need different logic, the abstraction is wrong.

*Why:* the offline fallback is only useful if it is provably the same pipeline.

### R-16 — `NO_MATCH` is a valid, first-class outcome

Never fabricate, relax a threshold, or widen a search to force a match. At least one committed run in `runs/` must legitimately be `NO_MATCH`.

*Why:* a system that always finds something is indistinguishable from a hardcoded one. The honest failure is evidence of authenticity.

---

## Engineering conventions

### Code

- Python 3.12. Type hints on every public function. `from __future__ import annotations` not needed on 3.12.
- `@dataclass(frozen=True)` for data carried across module boundaries. No dicts as informal structs.
- `pathlib.Path` everywhere. No string path concatenation, no `os.path`.
- No bare `except:`. Catch specific exceptions. `except Exception` is acceptable only at the orchestrator boundary implementing R-14, and must log the traceback.
- No `os.system`, no `shell=True`. Use `subprocess.run` with a list.
- Module-level constants in `SCREAMING_SNAKE`. No magic numbers inline — name them.
- Cite rule IDs in comments where non-obvious code exists because of a rule: `# R-01: commitment only, never the vector`.

### Dependencies

- Pinned in `requirements.txt` with `==`. No open ranges.
- Every dependency must have a prebuilt wheel for Windows / CPython 3.12. **No source builds requiring a C compiler.** This is why `insightface` is not used (`architecture.md` §5).
- Adding a dependency requires a one-line justification in `memory.md`.

### Windows-first

Development and the recording both happen on Windows 11, PowerShell. Chain commands with `;`, not `&&`. Environment variables are `$env:NAME`. Never assume a POSIX shell.

### Models

- Downloaded by `scripts/fetch_models.py`, verified by sha256, written to `models/`.
- `models/` is gitignored. Never commit a model binary.
- The script is idempotent and safe to re-run. Run it before recording so nothing downloads on camera.

### Console output

`rich` for everything user-facing. This is a recording deliverable: tables must be legible at 1080p, scores right-aligned and fixed-width, decisions colour-coded (accept green, reject dim). Print the candidate table even when the verdict is `NO_MATCH` — the rejections are the proof.

### Git

- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`.
- Stage named files. Never `git add -A` or `git add .`.
- Commit at every phase boundary in `phases.md`.
- Never commit `.env`, `models/`, `.cache/`, `.venv/`.
- `runs/` is **selectively** committed: the demo runs and the `NO_MATCH` run are deliverables. Strip any raw probe photo of a person who has not consented.

### Determinism

Fixed seeds where randomness exists. Quantise before hashing (`design.md` §4.3). Same input plus same cache must produce the same evidence hash.

---

## Scope discipline

The 3-day window is the binding constraint. When time is short, cut in this order:

```
1. C2PA manifest
2. Bing Visual provider
3. Mastodon provider
4. IPFS upload
5. OpenTimestamps
6. Liveness gate
```

**Never cut, under any circumstance:**

- The candidate table with rejection reasons
- The tamper demo (`verify` reporting `TAMPERED`)
- The committed `NO_MATCH` run
- The audit log
- README known-limitations section

*Why this asymmetry:* the never-cut list is what the brief actually grades. Everything on the cut list is polish.

Adding anything not in `prd.md` §4 requires updating `prd.md` first. Non-goals in `prd.md` §5 are closed — no UI, no deployment, no accounts, however tempting.
