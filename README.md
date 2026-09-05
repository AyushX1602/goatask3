# face-chain-verify

HH Goa 2026 Shortlisting Task 3: a pipeline that takes a face scan, finds a
matching real social media post by genuinely searching the web, and anchors
the discovered evidence on a blockchain so it can be re-verified later —
including detecting tampering.

```
face scan / upload  ->  web/social search  ->  local re-verification (ArcFace)
                                                        |
                                          canonical evidence bundle
                                                        |
                                            blockchain anchor (Anvil)
                                                        |
                                    re-verify against the on-chain record
```

## What it does

1. **Face identification.** A photo (webcam capture or upload) is run through
   YuNet detection, 5-point similarity alignment, and ArcFace (`w600k_r50`,
   512-d, L2-normalised) to produce an embedding. Measured: same-person
   cosine similarity 0.7652, different-person ceiling 0.074 (pilot, n=9) to
   0.1385 (live-measured, n=41 — see `calibration/negatives_harvested.json`).

2. **Genuine web search.** The *original* photo (not the aligned crop —
   sending the crop measurably degrades or defeats identity recognition, see
   `pipeline/search/image_prep.py`) is sent to Google Cloud Vision's
   `WEB_DETECTION` feature (primary) or SerpApi Google Lens (secondary, needs
   a public image URL). This is a real API call against the live web on
   every run — nothing is hardcoded or pre-picked. A keyless Bluesky fallback
   exists so the repo is runnable with zero API keys, at the cost of
   searching a small self-built corpus instead of the open web (this is
   disclosed to the user as a "degraded closed-corpus run", never presented
   as equivalent to an open-web result).

3. **Local re-verification.** Every candidate the search engine returns is
   independently re-scored by our own ArcFace pipeline — the search
   provider's own similarity score is recorded for the audit log and **never**
   used to accept or reject a candidate (see `rules.md` R-03). This is the
   load-bearing property that separates this project from a search wrapper.

4. **Canonical evidence bundle.** The accepted match, the probe's salted face
   commitment (never the raw embedding), the exact image bytes' sha256 and
   perceptual hash, and post metadata are assembled into a schema-versioned,
   float-free, deterministically-ordered JSON structure and hashed with
   keccak256.

5. **Blockchain anchor.** The evidence hash, face commitment, image hash,
   post hash, and score are anchored on a local Anvil chain via a Foundry
   contract (`EvidenceRegistry.sol`). No embedding, crop, or biometric
   template ever reaches the chain.

6. **Re-verification, including tamper detection.** `verify` executes an ordered
   three-stage audit:
   - **Artifact Check:** Recomputes sha256 and perceptual hash directly from
     artifacts on disk (`match_image.jpg`), never trusting stored digests (`ARTIFACT_MISMATCH` / `ARTIFACT_MISSING`).
   - **Integrity Check:** Recomputes canonical keccak256 hash across the bundle (`BUNDLE_MODIFIED`).
   - **On-chain Record:** Verifies the recomputed hash exists on the EVM registry (`NOT_ANCHORED` or `PASS`).
   Demonstrates three distinct tamper modes (`swap-artifact`, `edit-bundle`, `forge-bundle`),
   each operating on an isolated scratch copy leaving original files untouched.
   The web UI provides an always-visible Evidence Explorer panel to inspect, verify,
   and tamper past runs without re-scanning.

## Why a local chain (Anvil) as default, with Base Sepolia as bonus

The brief explicitly permits "a local/simulated chain." Anvil provides a real
EVM with real transactions, blocks, and contract execution at zero faucet, RPC,
or network-deprecation risk (`memory.md` D-43). Base Sepolia is fully supported
as an opt-in bonus by setting `EVM_CHAIN=base-sepolia` (R-15: RPC switch only).

## How to run it

Prerequisites: Windows/PowerShell, Python 3.12.10, Foundry (`anvil`/`forge`).

```powershell
# 1. Install
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\fetch_models.py     # downloads + sha256-verifies 3 ONNX models

# 2. Start Anvil (terminal 1)
$env:PATH += ";$env:USERPROFILE\.foundry\bin"
anvil

# 3. Deploy the contract (terminal 2, only needed once per Anvil process —
#    Anvil resets state on restart, so redeploy if you restart it)
cd contracts
$env:PATH += ";$env:USERPROFILE\.foundry\bin"
$env:PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # Anvil's public default key #0 — never use on a real chain
forge script script/Deploy.s.sol:Deploy --rpc-url http://127.0.0.1:8545 --broadcast

# 4. Start the demo UI (terminal 3)
cd ..
.\.venv\Scripts\python.exe -m pipeline serve
# open http://127.0.0.1:8000
```

### Zero-API-key quickstart

With no `.env` at all, steps 1–4 above still run end to end: the pipeline
falls back to the keyless Bluesky provider automatically. This searches a
small corpus we build at runtime, not the open web — the UI labels this
"CLOSED-CORPUS DEMO" so it is never mistaken for a real web search.

### Search API keys (optional, unlocks open-web search)

Copy `.env.example` to `.env` and set:

- `GCV_API_KEY` — Google Cloud Vision, feature `WEB_DETECTION`. **Primary.**
  ~1,000 free units/month. Accepts raw image bytes, so no public-URL problem.
  Needs a GCP project with billing attached (the free tier is not charged).
- `SERPAPI_KEY` — Google Lens via SerpApi. **Secondary.** ~100 free
  searches/month. Needs a publicly reachable image URL.

Neither is required for the pipeline to run; both are required to search the
real open web instead of the Bluesky fallback.

### CLI

```powershell
python -m pipeline version                     # config + version
python -m pipeline serve                        # local demo UI (used above)
python -m pipeline anchor <run_id>              # anchor runs/<run_id>/evidence.json on chain
python -m pipeline verify <run_id>              # re-verify runs/<run_id> against the chain
```

`scan`/`search`/`run-all` as standalone CLI subcommands do not exist yet —
those code paths are currently reachable only through the demo UI's
`/api/scan`, `/api/upload`, and `/api/search/{run_id}` endpoints, which call
the exact same `pipeline.*` functions the CLI would (`webapp/server.py`
contains no duplicate scoring logic — see `architecture.md` §5a).

### CLI Subcommands and Structured Exit Codes (T2.7)

The full verification and blockchain lifecycle is available via CLI:

```powershell
# 1. Scan a probe photograph
python -m pipeline.cli scan path/to/photo.jpg --run-id my-run

# 2. Search web/social providers for the scanned probe
python -m pipeline.cli search my-run

# 3. Or execute scan -> search -> anchor in a single command
python -m pipeline.cli run-all path/to/photo.jpg --anchor

# 4. Anchor an existing match's evidence bundle
python -m pipeline.cli anchor my-run

# 5. Re-verify against the on-chain registry
python -m pipeline.cli verify my-run

# 6. Demonstrate tamper detection (scratch copy, zero risk to real run)
python -m pipeline.cli verify my-run --tamper swap-artifact
python -m pipeline.cli verify my-run --tamper edit-bundle
python -m pipeline.cli verify my-run --tamper forge-bundle
```

**Deterministic exit codes:**
| Code | Meaning |
|---|---|
| `0` | OK / PASS |
| `1` | Verification mismatch (`ARTIFACT_MISMATCH`, `BUNDLE_MODIFIED`, `NOT_ANCHORED`) |
| `2` | No face detected or quality gate failure in probe |
| `3` | Provider error / network failure across all search backends |
| `4` | Search completed honestly with `NO_MATCH` (no candidate above threshold) |
| `5` | EVM RPC or contract execution error |

### Evidence Explorer & Three Tamper Modes (T2.1, T2.2, T2.4)

The demo UI features an always-visible **Evidence Explorer** panel allowing judges
to browse historical runs under `runs/`, inspect their schema version and anchored
receipts, and execute three distinct tamper demonstration modes with zero setup:

- `swap-artifact`: flips 1 byte in `match_image.jpg` on disk -> detected as `ARTIFACT_MISMATCH`
  (proves the bundle cryptographically pins real image bytes on disk, not just metadata; R-25).
- `edit-bundle`: modifies `match.score_bps` in `evidence.json` -> detected as `BUNDLE_MODIFIED`
  (proves the on-chain keccak256 hash pins bundle contents).
- `forge-bundle`: crafts a fresh, unanchored bundle -> detected as `NOT_ANCHORED`
  (proves internal consistency is not on-chain provenance).

Every tamper mode operates on an isolated scratch copy in a temporary directory;
the real run directory is guaranteed byte-identical before and after.

### Profile Expansion with Strict Face Gating (T2.6 / R-28)

To close the social recall gap without compromising verification integrity:
- When a candidate clears threshold, `pipeline/search/expand.py` extracts verified handles
  strictly from URL structures, filtering reserved segments (`/p/`, `/reel/`, `/pub/`, `/shorts/`).
- Inspects outbound social links on the verified page and traverses 1-hop link-in-bio hosts.
- **R-28 strict face gating:** Candidates with resolvable avatars (e.g. GitHub) re-enter
  the ArcFace verification pipeline (`origin="face"`), requiring threshold and margin.
- Media-blocked platforms (e.g. LinkedIn, Instagram) are preserved as `origin="linked"`,
  rendering as unscored profile claims (`decision="linked-claim"`) without falsely inflating
  biometric face match counts.

### Recall recovery: resolver cascade and real diagnostics

When a candidate has no usable image URL (or every known size variant
fails), `pipeline/verify/resolver.py` tries three keyless, credential-free
routes before giving up: OpenGraph/Twitter Card meta tags on the candidate's
page, YouTube/X's public oEmbed endpoints, and Reddit's `.json` suffix. No
login, no scraper package, no crawler-UA impersonation. Every attempt is
recorded, so a rejection can honestly say which routes were tried.

Rows with no cosine score to show (no face was ever embedded) display real
measured observations instead of a bare dash — HTTP status, content-type,
byte count, image dimensions, faces detected, largest face size in pixels —
never a fabricated confidence value.

## Verified live, end to end (this exact repo, this exact commit)

A real search against the live web, using GCV, on an unmodified probe photo
of a public figure:

```powershell
python -m pipeline.cli anchor 2026-09-05T18-07-40Z
  anchored tx=0x0cf1cc99973cc51d80a2aaed865b353a648d5fe478c562b506e8812b71de82f2
  chain_id: 31337   block: 30
  contract: 0x5FbDB2315678afecb367f032d93F642f64180aa3
  evidence: 0xa4a6bd6319cac0c1f098570bea5cfbb6dcdb51f94a310658a64e038430f85fd6

python -m pipeline.cli verify 2026-09-05T18-07-40Z
  RESULT: PASS — recomputed hash matches the on-chain record exactly
  exit code: 0

python -m pipeline.cli verify 2026-09-05T18-07-40Z --tamper swap-artifact
  Result:  ARTIFACT_MISMATCH
  Target:  match_image.jpg
  Details: artifact sha256 mismatch
  exit code: 0 (demonstration succeeded)
```

Both sample runs below are committed under `runs/` exactly as produced —
nothing hand-edited.

### Sample run 1 — `runs/2026-09-05T18-07-40Z` — MATCH

44 candidates examined via GCV web detection on a public figure's photo. The
accepted match:

- Score **0.9806**, margin 0.7010 above the best non-match
- `page_url`: `pbs.twimg.com/media/HRSsRstbMAEzQxl.jpg` (an X/Twitter media
  URL — `content_kind: "post"`, `match_kind: "partial"`, `verified_against:
  "platform_origin"`)
- 43 rejected candidates in the same run, spanning `reject-platform-blocked`
  (Instagram/Facebook — the search engine reported the image there, but
  those platforms serve media only to their own crawler), `reject-domain`
  (news/CDN hosts, correctly excluded because they are not social platforms),
  `reject-below-threshold`, `reject-no-face`, and `reject-fetch-failed` — the
  full honest taxonomy, every row with a real reason (`rules.md` R-24).
- `runs/2026-09-05T18-07-40Z/match_image.jpg` — the exact image bytes that
  were fetched and scored, saved locally so the run is self-contained.
- Anchored and re-verified live (see above). `anchor.json` records the real
  tx hash, block number, and evidence hash.

### Sample run 2 — `runs/2026-09-05T18-19-36Z` — NO_MATCH

A genuine, non-degraded, non-fabricated `NO_MATCH`: 21 real candidates
examined via GCV, every score below 0.15, verdict correctly `NO_MATCH`
(`rules.md` R-16 — a system that always finds something is indistinguishable
from a hardcoded one). No evidence bundle is built for a `NO_MATCH` — there
is nothing to anchor.

## Which platforms this reaches, and which it doesn't

One `WEB_DETECTION`/Lens call reaches any platform Google has indexed —
measured live: YouTube, X/Twitter, Reddit, LinkedIn, GitHub, Instagram, and
Facebook links have all been returned in real runs against real probe
photos.

**Structurally unfetchable, verified live under three client profiles (our
UA, a browser UA, browser UA + Referer):** Instagram and Facebook serve a
tiny `text/html` stub (HTTP 200) to every profile tested, not a login wall
that a different header could defeat. This is deliberate access control on
their side. When GCV reports the image is on one of these platforms anyway
(`match_kind: "full"` or `"partial"`), the pipeline records it as
`reject-platform-blocked` — "post found but unverifiable" — rather than
silently discarding it or misreporting it as `reject-no-face`.

**TikTok is a split case**, found live: `tiktok.com/api/img/...` is blocked,
but `tiktokcdn-us.com` signed CDN URLs fetch successfully. Blocking is
keyed on the specific endpoint, not the whole platform (`pipeline/verify/allowlist.py`).

**X/Twitter and GitHub have two independent CDN sizing schemes that must
not be confused.** `pbs.twimg.com/media/...` uses a `?name=` query parameter
(largest: `orig`); `pbs.twimg.com/profile_images/...` uses a filename suffix
instead, and `?name=` 404s on that path entirely. A URL rewrite that
assumed one scheme for both caused a real, measured regression — a working
profile-image URL was rewritten into five 404s, turning a true `MATCH` into
a false `NO_MATCH`. Fixed with a hard invariant (`rules.md` R-23): any URL
rewrite must keep the provider's original URL in the resulting fallback
chain, so the worst case of a wrong scheme guess is "no better than before,"
never worse.

## Profiles vs. posts

The brief asks for "a real, matching social media post." A profile page is
not strictly a post. For a private individual, their profile picture is
often the *only* image any search engine has indexed of them — refusing
profiles entirely would mean the pipeline only works on public figures. Both
are accepted and labelled honestly via `post.content_kind` (`"post"` |
`"profile"` | `"unknown"`, never null) rather than either dropped or
silently overclaimed. This never affects the accept/reject decision
(`rules.md` R-03) — it is reporting only.

## Synthetic and stock avatars

A probe that is a stock photo or an AI-generated face will correctly produce
`NO_MATCH`: the pipeline never manufactures an identity for a face that has
none. Observed live on a probe judged very likely synthetic (studio
backdrop, flawless symmetric skin — the house style of AI headshot
generators): every scored candidate came back below 0.135. This is a
designed behaviour, not a failure — see the previous section's `NO_MATCH`
sample.

## Privacy and consent

- **No biometric data ever reaches the chain or any committed artifact.**
  Only a salted commitment (`keccak256(salt ‖ quantised_embedding)`) is
  published — never the raw embedding, never an image. The salt lives
  locally and is never committed. This commits to *the probe used in this
  run*, not to a person's identity in general — a second photo of the same
  person produces a different commitment.
- **No raw face crop, no probe photo, and no private individual's identity
  is committed to this repository.** Every committed sample run is either a
  public figure or a genuine, honest `NO_MATCH`.
- **Real, non-consenting private individuals were found during testing** (the
  pipeline works — that is the point), and were deliberately excluded from
  every artifact in this repo. Their runs were redacted (every URL replaced
  with a truncated sha256, all identity signals stripped) and kept only
  locally under `calibration/quarantine/` (gitignored) as sha256-only
  calibration data — never as a demo, a README example, or a recording
  subject. See `rules.md`'s consent principle and `memory.md` §3m for the
  full account of why this matters and how it was handled.
- **One face per invocation. No bulk mode.** (`rules.md` R-05.) The
  difference between a provenance tool and a surveillance tool is
  throughput; this is a design position, not an incidental limitation.

## Known limitations

1. Open-web coverage depends on the subject being indexed by Google. A
   subject with little or no online presence correctly returns `NO_MATCH` —
   a property of the underlying search index, not of the local verifier.
2. Verification happens against the exact bytes the search engine (or, when
   derivable, the platform origin) served at fetch time, recorded in
   `match.verified_against`. The evidence bundle records the exact URL and
   its sha256/phash; it does not assert the platform's current live image is
   byte-identical to what was scored.
3. The Bluesky provider searches a small corpus built at runtime, not the
   whole web. A Bluesky-only run is a demonstration of the verifier, not a
   genuine web search, and the UI labels it "CLOSED-CORPUS DEMO" — never
   presented as equivalent to a real search result.
4. Instagram and Facebook are structurally unfetchable (see above) —
   reported honestly as `reject-platform-blocked`, not silently discarded.
   TikTok is fetchable via its signed CDN but not via `tiktok.com/api/img`.
5. The similarity threshold (0.42) is a documented default, not a
   benchmark-scale calibration. Measured separation: same-person 0.7652 vs a
   *pilot* non-match ceiling of 0.074 (n=9, too small to be an operating
   statistic) — since widened by live measurement to 0.1385 (n=41, see
   `calibration/negatives_harvested.json`). Still 0.28 below threshold, but
   the pilot number alone understated the real noise band. A measured false
   negative also exists at small face sizes: a genuine match (a YouTube
   Short, face ~68px) scored 0.2795, below threshold — disclosed as real
   cost, not hidden.
6. Faces below ~50px produce unreliable embeddings and are rejected rather
   than scored (measured: cosine self-similarity falls to 0.868 at 31px and
   0.767 at 20px).
7. Liveness applies to webcam capture only. An uploaded file's liveness
   renders `N/A — provenance unverified`, never `LIVE` — an anti-spoof model
   detects capture artifacts, and a clean uploaded photo would otherwise
   score "live" while proving nothing about physical presence.
8. Face recognition has documented demographic accuracy disparities. The
   small calibration set used here cannot characterise them, and no claim of
   uniform accuracy across groups is made.
9. Anvil provides no economic finality (by design — see "why a local chain"
   above). A public-testnet anchor remains optional future work.

## Architecture, decisions, and full history

- `architecture.md` — module boundaries and the request/response flow
- `design.md` — the evidence bundle schema, quality gates, and score bands
- `rules.md` — binding invariants (R-01 through R-24) and the reasoning
  behind each one
- `memory.md` — the full decision log and session-by-session history,
  including every regression found and how it was fixed
- `phases.md` — the build plan and current completion status

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q      # 224 tests
cd contracts; forge test -vv                         # 8 tests, incl. a 256-run fuzz test
```

## Before recording

```powershell
.\.venv\Scripts\python.exe scripts\warmup.py
```

Pre-loads every ONNX session (avoids a "loading model..." stall mid-take),
and checks every precondition a judge would notice missing: models present,
a search API key set, Anvil reachable, the contract deployed, the deployer
funded, exactly the intended sample runs present under `runs/` with nothing
stray left over from testing. Exits non-zero with the specific failing
check printed if anything needs fixing. Deliberately flags `HTTP_CACHE=1`
as a failure — that variable should stay `1` during development (so
iterating doesn't burn API quota, R-04) and only be set to `0` right before
the actual take, so the recorded run is provably live rather than replayed
from cache (a judge can check this in `audit.json`'s `cache` block).
