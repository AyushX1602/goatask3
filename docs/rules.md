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

**Allowlist principle (added 5 Sep 2026, after a real inconsistency found via
a live run):** a platform belongs on `PLATFORM_DOMAINS`
(`verify/allowlist.py`) if and only if **it is a platform where an
individual maintains a public identity profile and publishes content under
it.** This is a test to apply to a new platform, not a list to match by
vibe. GitHub qualifies under this test — a public profile page, a follower
graph, and content (repos, commits, README authorship) published under that
identity, and GitHub itself describes the platform as social — and was
missing purely by oversight, not by considered exclusion. LinkedIn was
already on the list under the identical reasoning; the two platforms are not
meaningfully different along this axis. Applying the same test forward: a
CDN, a stock-photo host, a university faculty page, or a news outlet does
not qualify, because none of those is a platform where the pictured person
themselves maintains a profile and publishes under it — that is precisely
why `reject-domain` on those hosts is correct, not an overreach.

When this addition changes a real run's verdict, the pre-change audit log
must be kept (not deleted or overwritten) alongside a re-run under the new
list, so both are visible: what the honest old rule produced and what the
corrected rule produces. See `calibration/quarantine/` for the concrete
instance this principle was written from.

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

### R-21 — Never present a rejected candidate as a suggestion

When the verdict is `NO_MATCH`, the verdict is the headline. Rejected
candidates go behind a "show diagnostics" affordance, captioned as rejected.
Scores in the 0.0–0.2 band are **statistical noise** and must never be
rendered as a ranked list of possible people.

*Why:* the original n=9 pilot measured non-match scores topping out at 0.074
(mean −0.024, σ 0.054, mean+4σ = 0.193; `architecture.md` §2a). Since then,
two real live runs (`calibration/negatives_harvested.json`) pushed the
observed ceiling to 0.1385 — a genuine widening, not a contradiction: n=9 was
always too small to be the operating figure, which is exactly why it needed
replacing rather than repeating. Either way, a true match sits near 0.77. A
ranked table of 0.1-scoring faces reads as "the system thinks these might
be you" when the system in fact rejected all of them. This produced a real
false impression during testing that the engine was inaccurate, when the
engine was correct and only the presentation was wrong. Misrepresenting
confidence in a biometric tool is a correctness bug, not a cosmetic one.

### R-22 — Liveness must not claim what it cannot measure

`LIVE` may only be shown for a webcam capture that passed the anti-spoof
model. An uploaded file reports `not_applicable`, rendered neutrally, never
in the same style as a real pass. See D-20 and `design.md` §1.6.

*Why:* an anti-spoof model detects capture artifacts. Given a clean file it
will usually say "live" while proving nothing about physical presence.
Presenting that as a liveness pass is a false assurance.

### R-16 — `NO_MATCH` is a valid, first-class outcome

Never fabricate, relax a threshold, or widen a search to force a match. At least one committed run in `runs/` must legitimately be `NO_MATCH`.

*Why:* a system that always finds something is indistinguishable from a hardcoded one. The honest failure is evidence of authenticity.

### R-23 — URL rewriting is additive-only. Never drop a URL the provider gave us

Any code that rewrites a candidate's `image_url` — size-variant upgrades,
CDN normalisation, thumbnail derivation — must return the provider's
**original URL as a member of the resulting fallback chain**. A rewrite may
propose better variants and may order them first, but it may never *replace*
the one URL already known to have come from a real search result.

*Why:* this is not hypothetical. The first cut of the X/Twitter size-variant
upgrade (G1) replaced the original URL with five rewritten `?name=` variants.
That is correct for `pbs.twimg.com/media/...`, but `pbs.twimg.com/profile_images/...`
uses a completely different filename-suffix scheme where `?name=` returns
404. On a real non-celebrity probe, GCV returned a **working** profile-image
URL (HTTP 200, 14 KB), the rewrite turned it into five 404s, and the run
reported `NO_MATCH` where a `MATCH` should have been. A recall optimisation
made recall strictly worse, and it did so silently — the candidate simply
became `reject-fetch-failed`.

The invariant makes the worst case of any future rewrite "no better than
before" instead of "worse than before". Enforced in
`pipeline/search/media_urls.py::_with_original_first`, asserted generically
by a property test rather than one test per platform, so a newly added
platform scheme inherits the guarantee automatically.

### R-24 — A reject reason must describe what actually happened

Each reject reason names a distinct, observable cause. Specifically:
`reject-no-face` means an image was decoded and contained no face;
`reject-not-an-image` means bytes arrived but did not decode;
`reject-platform-blocked` means the platform refuses programmatic media
access; `reject-fetch-failed` means the network fetch itself failed;
`reject-unsafe-url` means the URL failed SSRF validation (non-https scheme,
internal or private IP address, unresolvable host, response size cap exceeded,
or magic bytes mismatch). These
must not be collapsed into each other, and a message must not name only the
last of several URLs attempted.

*Why:* the diagnostics table is the anti-fabrication evidence. Two separate
live bugs came from violating this: Instagram/Facebook HTML stubs (HTTP 200,
not an image) were reported as `reject-no-face` — claiming we had examined a
photo we never received — and a multi-variant fetch failure reported only the
smallest variant tried, which made a working fix look like it had never run.
A diagnostics table that misdescribes our own behaviour is worse than no
table, because it invites exactly the wrong debugging conclusion.

### R-25 — Re-verification recomputes every digest from the source artifact. A stored digest is never reused as its own proof

`chain/reverify.py` must rebuild the hashable bundle by recomputing each
recorded digest (`match.image_sha256`, `match.image_phash`, and any future
artifact digest) from the file on disk, and only then canonicalise and hash.
No code path may carry a digest across from the stored bundle into the
structure it then verifies.

*Why:* found 7 Sep 2026 by reading a competitor's verifier and checking ours
against it. Ours recomputed keccak256 from `evidence.json` and compared it to
the anchored hash — which means **replacing `match_image.jpg` with a
completely different image still produced `PASS`**. The bundle JSON was
untouched, so its hash was untouched, and nothing ever compared the recorded
`image_sha256` against the bytes actually on disk. Tier 0 argued that
refusing to anchor an empty image hash mattered because "imageHash must mean
something"; leaving it unchecked at verify time made it decorative. A
verifier whose two sides both derive from the same stored field proves only
that hashing is deterministic.

### R-26 — Every externally-sourced string is escaped before it reaches the DOM

Any value originating outside this process — candidate `page_url`, `source`,
provider titles, reject-reason text built from provider data — must pass
through `escapeHtml()` before being interpolated into markup, including into
attribute positions. Prefer `textContent` and `createElement`/`setAttribute`
over `innerHTML` template interpolation for anything carrying external data.

*Why:* found 7 Sep 2026. `app.js` interpolated `c.page_url` raw into both an
`href` attribute and an element body, and `c.source`/`shortReason` raw into
table cells, while `escapeHtml()` already existed in the same file and was
applied to the adjacent `title` attribute. A provider-supplied URL containing
a double quote breaks out of the attribute. The practical blast radius is
small — a localhost demo with no credentials — but the fix is a few lines and
"an attacker would have to poison a search index first" is not a security
boundary we chose, it is one we got lucky with.

### R-27 — A new search representation ships only if it is measured to beat the current one on our own fixtures

Adding or changing what image is sent to a search provider requires a
before/after measurement on our own probe fixtures, recorded in `memory.md`
with candidate counts and outcome changes. A representation that a competitor
reports as better, or that is intuitively better, is a hypothesis until
measured here.

*Why:* D-34 established "send the original, not the aligned crop" from a real
measurement, and that measurement is why we trust it. A competitor measured a
different failure — a probe wearing distinctive clothing returned almost
entirely garment listings, because the engine locked onto the outfit — which
our two celebrity-portrait fixtures could never have surfaced. Both findings
are real, which is precisely why the next one has to be measured too rather
than argued.

### R-28 — An expanded candidate is a match only if it clears the threshold on its own face score. A published link is a claim, never a match

Any candidate discovered by expanding outward from an already-verified page
(handle propagation, outbound page links, link-in-bio hops, platform profile
guesses) re-enters the normal pipeline: fetch, detect, align, embed, score
against the probe, threshold, margin. Candidates that cannot be face-scored —
because the platform blocks media, or no image is resolvable — are classified
into one of two unscored tiers:

- `linked` — an explicit claim published on a verified page (outbound social
  link, link-in-bio reference, SERP-recovered profile). The page itself is
  already verified. Decision: `linked-claim`.
- `conjecture` — a same-handle structural guess from `derive_profile_urls()`
  (e.g. if the verified handle is `alice`, also try `x.com/alice`). This is a
  reasonable heuristic but NOT a published claim — the same handle may belong
  to a different person on another platform. Decision: `conjecture-claim`.

Both tiers are recorded, never silently dropped, never counted as a match, never
ranked among matches, and never shown with a score. They are displayed with
distinct visual styling in the UI (amber for `linked`, violet for `conjecture`).

*Why:* this is the boundary that separates legitimate cross-platform
expansion from the name-search pivot rejected earlier (see `memory.md` D-45).
Searching a *name* that a provider leaked replaces face-driven discovery with
string-driven discovery, which the brief's "use the face to search the web"
forbids. Propagating a *handle* off a page our own embedder already verified,
and then face-gating every result, keeps the embedding load-bearing at both
ends. The distinction is real and it is entirely carried by this rule — drop
the face gate and the feature becomes the thing we refused to build.

### R-29 — All external candidate fetches must be hardened against SSRF

Candidate URLs and page URLs originate from untrusted search engine results.
Every outbound fetch of a candidate URL or page must pass through
`pipeline/cache/urlguard.py:assert_safe_url` and `safe_fetch`:
- Scheme must be `https`. `http`, `file`, `data`, and loopback are unconditionally rejected.
- Host must resolve via DNS to a public internet IP. Loopback (`127.0.0.1`, `::1`), private RFC1918 (`10.*`, `172.16-31.*`, `192.168.*`), link-local (`169.254.*`), multicast, and reserved addresses are strictly rejected.
- Redirect hops are capped at 3, and each intermediate hop is re-validated before being followed.
- Response payloads must be streamed and size-capped, terminating the connection as soon as the threshold is exceeded.
- Image fetches must verify real magic bytes (`looks_like_image`) rather than trusting the declared `Content-Type`.

*Why:* search results contain arbitrary strings. Without strict egress filtering, an attacker or poisoned search index could induce the local verification pipeline to hit local services (e.g. `127.0.0.1:8545` Anvil RPC, `localhost:8000` demo UI) or cloud metadata endpoints (`169.254.169.254`).

---

## Integrity principles (I-series)

Narrower than the hard invariants above and more situational, but cited
directly in code. Added to `rules.md` 7 Sep 2026: `pipeline/verify/resolver.py`
and `scripts/warmup.py` already cited `I-05` and `I-10` before this section
existed, which was itself a doc-vs-code inconsistency of exactly the kind
Tier 0 was about. The principles were real and implemented; only the write-up
was missing.

### I-05 — Fetch honestly. Never impersonate a privileged crawler

An ordinary browser User-Agent is acceptable for one-off candidate image
fetches: it claims nothing false, only that we are a normal HTTP client.
Sending `Googlebot`, `facebookexternalhit`, `Twitterbot`, or any UA that
claims to *be* a specific company's own crawler is forbidden, because it
seeks access that platform granted to that company and not to us. Our own
API calls to GCV and SerpApi keep the descriptive contact UA (R-12).

*Why:* Instagram, Facebook and TikTok were re-measured under three client
profiles (our UA, a browser UA, browser UA + Referer) and are byte-identically
blocked under all three — so crawler impersonation was never a capability
question, only an honesty one. We report `reject-platform-blocked` instead.

### I-10 — A recorded run must be live, not replayed

`HTTP_CACHE=1` is correct during development (it protects a ~100/month free
tier, R-04) and wrong for the recording take. `scripts/warmup.py` fails the
readiness check while `HTTP_CACHE=1` is set, deliberately.

*Why:* `audit.json` records per-provider cache hit/miss counts. A run
recorded off a warm cache is visibly a replay to anyone who opens that file,
and reads as canned even though the original fetch was genuine.

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
2. Google Cloud Vision backend  (SerpApi backend alone is sufficient)
3. Bluesky keyless fallback     (nice-to-have; primary path does not need it)
4. IPFS upload
5. OpenTimestamps
6. Liveness gate
```

**Never cut, under any circumstance:**

- Image upload as an input mode — the web-detection path is untestable without it (D-19)
- The web-detection provider — it is the only thing that satisfies "search the web"
- Local ArcFace re-verification of every candidate — without it we are a search wrapper
- The candidate table with rejection reasons
- The quality gate (`MIN_FACE_PX`)
- Correct `NO_MATCH` presentation (R-21)
- The tamper demo (`verify` reporting `ARTIFACT_MISMATCH`/`BUNDLE_MODIFIED`/`NOT_ANCHORED` — `TAMPERED` retired as a verdict string, R-25/design.md §5.4)
- The committed `NO_MATCH` run
- The audit log
- README known-limitations section

*Why this asymmetry:* the never-cut list is what the brief actually grades. Everything on the cut list is polish.

Adding anything not in `prd.md` §4 requires updating `prd.md` first. Non-goals in `prd.md` §5 are closed — no UI, no deployment, no accounts, however tempting.
