# face-chain-verify — deep architecture reference

Written 7 Sep 2026. This document is the long-form companion to `architecture.md`
(the compact version kept at repo root for quick orientation). It exists because
the project has grown past what a single-page architecture doc can hold without
losing precision, and because two competitor repos are now analyzed here at
source-code depth rather than README depth — that standard needs a home that
isn't `memory.md`'s chronological log.

Everything in this file was verified against actual code at the time of
writing (7 Sep 2026, commit `0a62f17`). Where a claim could not be verified
(e.g. a competitor's live behavior we did not run ourselves), that is stated
explicitly rather than inferred.

---

## 1. What this system is, in one paragraph

A face scan produces a 512-dimensional ArcFace embedding, locally, once, and
that embedding never leaves the process in reversible form. That embedding
drives two independent things: a live reverse-image search across the open
web (currently GCV `WEB_DETECTION` primary, SerpApi Google Lens secondary,
escalating between them on result quality), and — for every candidate that
search returns — a second, independent face comparison against the original
probe. The search provider's own notion of similarity is recorded for
transparency and never used to decide anything (R-03). Only a MATCH that
clears a calibration-derived threshold and margin produces an evidence
bundle, which is canonicalized to float-free JSON, hashed, and anchored on a
real local EVM chain (Anvil). A separate re-verification path recomputes
every digest from the artifacts actually on disk and checks them against
both the anchored hash and the chain itself — never trusting a digest that
was merely copied out of the stored bundle.

That last sentence is the difference between this system and every one of
its three publicly available competitors' default configurations, and it
took a real defect (found by reading a competitor's code, not by writing
tests speculatively) to get there. §4 covers it in detail.

---

## 2. System shape

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐     ┌─────────────┐
│   INPUT      │───▶│  FACE CORE    │───▶│  SEARCH LAYER  │───▶│  MATCHING    │
│ webcam/upload│     │ detect→align  │     │ GCV/Lens/      │     │ ArcFace      │
│              │     │ →embed→live   │     │ Bluesky        │     │ cosine, R-03 │
└─────────────┘     └──────────────┘     │ +expansion     │     └──────┬──────┘
                                          └───────────────┘             │
                                                                        ▼
┌─────────────┐     ┌──────────────┐     ┌───────────────┐     ┌─────────────┐
│  RE-VERIFY   │◀───│  ANCHOR       │◀───│   EVIDENCE     │◀───│   VERDICT    │
│ artifact +    │     │ Anvil EVM    │     │  canonical     │     │ MATCH /      │
│ bundle + chain│     │ real eth_call│     │  JSON + hash   │     │ NO_MATCH/... │
└─────────────┘     └──────────────┘     └───────────────┘     └─────────────┘
```

Every arrow is a real, separately testable module boundary (`architecture.md`
§3's boundary table). No stage may reach backward into a stage that has
already run — search cannot re-decide a face was detected, matching cannot
re-run search, anchoring cannot alter the evidence it was handed.

---

## 3. Face core

### 3.1 Detection — YuNet

`cv2.FaceDetectorYN` on `face_detection_yunet_2023mar.onnx`. Chosen over
SCRFD (used by two of the three competitors) specifically because OpenCV
performs anchor decoding and NMS internally — one less hand-written failure
mode. Largest face wins for the probe; every face is scored independently
for a candidate image (a group photo candidate is not silently reduced to
one face).

### 3.2 Alignment — the standard ArcFace 5-point template

112×112 output, similarity transform to a fixed destination template (left
eye, right eye, nose, left mouth corner, right mouth corner — ordered by
**image x-position**, not by "subject's left/right" naming, which is the
single most common silent bug in face recognition code: a landmark-order
mixup produces a horizontally mirrored alignment that degrades accuracy with
no error raised). Enforced by R-08: no code path may pass an unaligned crop
to `embed()`.

### 3.3 Embedding — ArcFace `w600k_r50`, 512-d

From the `buffalo_l` pack, via onnxruntime, CPU. L2-normalized once, inside
`embed()` — every downstream consumer assumes `‖v‖ ≈ 1` (R-07). This is the
single largest architectural gap between us and all three competitors, who
each use a 128-dimensional embedding (`face_recognition`/dlib, or OpenCV
SFace). Higher-dimensional embeddings generally separate identities better;
we have not run a head-to-head accuracy benchmark against any of them, so
this is stated as an architectural fact, not a measured win.

### 3.4 Liveness — MiniFASNetV2 anti-spoof

Three-state, never two: `LIVE`, `SPOOF`, `not_applicable`. The third state
exists because an uploaded image cannot honestly claim liveness at all — R-22
forbids presenting an upload's liveness as `LIVE` regardless of what a model
score says, since there was no live capture to assess. None of the three
competitor repos implement a liveness gate at all.

### 3.5 Quality gate — measured, not guessed

`MIN_FACE_PX = 50`, derived from `scripts/probe_accuracy.py`: cosine
self-similarity holds ≥0.95 down to 43px then collapses (0.868 @ 31px, 0.767
@ 20px). Blur was tested and rejected as a gate — self-similarity held at
0.904 under a 31px gaussian kernel, so a blur gate would add cost for
nothing measured to justify it.

---

## 4. Search layer

### 4.1 Two backends, one provider contract

`pipeline/search/web_detect.py` implements one `SearchProvider` with two
interchangeable backends:

- **GCV `WEB_DETECTION`** — accepts raw base64, no public URL needed.
  ~1,000 free units/month. Historically primary (D-28) for quota headroom.
- **SerpApi Google Lens** — needs a publicly reachable image URL.
  ~100 free searches/month.

**This asymmetry is the root cause of a real recall gap found this session.**
GCV answers "what looks similar to this," which degrades to
`visuallySimilarImages` lookalikes when it has no indexed match — and two
real runs (`runs/2026-09-05T22-52-19Z`, `runs/2026-09-05T22-53-02Z`) showed
GCV returning 20/20 candidates of `match_kind='similar'` on university
faculty pages, `identity_signals` of `[]` and `['Human']`, and max cosine
0.21/0.14 — while Lens, which answers "where does this exact image appear,"
never ran at all, because `_resolve_backend()` picked one backend at
construction and the two were mutually exclusive. See §8 for the full
diagnosis and the fix (F1/F1b, quality-triggered escalation).

### 4.2 The public-URL problem, and why it's now solved narrowly

SerpApi's `google_lens` engine is documented around an image URL, but it
also exposes a direct upload endpoint: `POST https://serpapi.com/image`
returns an `image_id` that `engine=google_lens` accepts as `image_id=`
(verified live — the endpoint answers 401 without a key, and its limits are
documented at `serpapi.com/image-api`: JPG/PNG/WebP, 500 KB max). Found in
the hhgoa-provenance review; adopted 7 Sep 2026. This removes the public-
hosting hop entirely: the crop is held by SerpApi for ~10 minutes, never
placed on a public image host, and needs no extra API key beyond
`SERPAPI_KEY`.

Our answer, `pipeline/search/uploader.py` (F1a): upload **only the
background-removed head crop**, never the original photograph, directly to
SerpApi. This is structurally enforced, not left to caller
discipline — `upload_crop_to_serpapi()` takes a keyword-only `is_head_crop: bool`
argument and **raises `ValueError` if it is `False`**, so passing the
original photo through this function is a hard error, not a documentation
promise. `SEARCH_LENS_UPLOAD` defaults to `0`; a fresh clone never uploads
anything until the owner opts in. (The earlier variant uploaded the crop to
imgbb with a 5-minute expiry; it is retired — the public-host disclosure it
required is gone, not softened.)

### 4.3 Head crop as a search query — measured, not borrowed on faith

`pipeline/face/headcrop.py` builds a 512px square, background masked to
neutral grey (deliberately not white — white biases engines toward
catalogue/stock imagery), bbox grown asymmetrically (55% up / 30% side / 18%
down, since downward growth adds collar and shoulders, exactly what's being
excluded). This exists because a competitor measured a probe in distinctive
clothing returning ~60 Lens results that were almost entirely garment
listings — Lens locked onto the outfit, not the face.

Per R-27, this ships only if measured better than original-only on our own
fixtures, using social-domain candidate count as the decision metric — not
raw candidate count, which is exactly the metric that already caused one
real bug (§8.2). As of this writing `use_head_crop` defaults to `False`
pending that measurement (`scripts/probe_query_representation.py`, not yet
run against the new pipeline; tracked in `phases.md` T2.5-M).

### 4.4 Deduplication and allowlist

The same image is served from many CDNs at many sizes; `verify/dedupe.py`
collapses these before spending inference on them, recording collapsed
duplicates in the audit log rather than discarding the information.
`verify/allowlist.py` maps registrable domains (not substrings — a
`evil-x.com.attacker.net` cannot pass as `x.com`) to platform names, and
deliberately includes CDN hosts (`pbs.twimg.com`, `licdn.com`,
`preview.redd.it`) alongside the platform's www domain, because excluding
CDNs was measured to discard most usable evidence (D-36: `preview.redd.it`
0.9786, `licdn.com` 0.9675, `pbs.twimg.com` 0.9594 — all previously rejected
as "not on allowlist" before this was fixed).

### 4.5 Profile expansion — four honest evidence tiers

`pipeline/search/expand.py`, gated strictly on an already-verified candidate
(`match.best is not None`) and strictly face-scored where a face-scoreable
image exists (R-28). This is where we improve on prior reference
benchmark designs, not just replicate them — see §9.3 for
the direct comparison. Four tiers, not two:

| tier | `origin` | scored? | basis |
|---|---|---|---|
| verified | `face` | yes | cleared threshold + margin independently |
| corroborating | `face` | yes | above threshold, not top-ranked |
| linked | `linked` | no | a claim **published on the verified page itself** |
| conjecture | `conjecture` | no | a same-handle **guess** on another platform, unproven |

The `linked`/`conjecture` split matters because the two claims carry
different evidential weight and conflating them (as both competitors do —
each has exactly two origins) overstates the weaker one. A same-handle guess
that a person also has an account named `janedoe` on some other platform is
a hypothesis; a link that platform-verified page **actually published** is a
real claim by the verified party. Neither is a face match, and neither is
ever counted as one, but they are not equally strong evidence either.

### 4.6 LinkedIn specifically — the ceiling is HTTP 999, and it does not move

`linkedin.com/in/...` returns HTTP 999 to programmatic clients — verified
independently by us and reported identically by both competitor repos, who
each built a workaround around never requesting the page:

- `pipeline/search/serp_resolve.py` runs a real Google search via
  `SerpApi engine=google` (not `google_lens`), query
  `site:linkedin.com/in "{handle}"`, and takes the profile photo straight
  from Google's own SERP `thumbnail` field when one is present.
- LinkedIn is **never requested directly**. Google already crawled the
  public profile; we read its public search result instead of fighting a
  platform's access control.
- A profile hit with **no** thumbnail is emitted as `origin="linked"`,
  `image_url=""`, no score — a real claim, honestly labeled as unscored,
  never silently dropped (this was a real earlier defect: `derive_profile_urls()`
  used to guess `linkedin.com/in/{handle}` from a same-handle template, which
  is structurally wrong because LinkedIn vanity URLs are not the handle
  string the way a GitHub username is — that guess has been removed).

**Important correction, recorded here because it drove real rework this
session:** neither competitor repo actually *fetches a face-scoreable
LinkedIn image* in the general case either. The reference baseline's own `_linkedin_claim()`
docstring: "Keep a LinkedIn profile URL even when LinkedIn refuses to serve
the page (HTTP 999)... The link is still a handle-based claim, not a face
match." Their same-handle LinkedIn guess has an empty avatar slot by
construction and falls through to an unreachable page fetch. Their SERP
route only scores an image when SerpApi's organic result happens to carry a
thumbnail — the same opportunistic condition ours now has. LinkedIn showing
up unscored in a competitor's demo run is not evidence they solved something
we haven't; it is the same ceiling, reached the same way.

---

## 5. Matching — the one place a decision is made

`pipeline/verify/matcher.py` is the **only** module permitted to decide
accept/reject (R-03). Every provider's own score, match_kind, or ranking is
recorded for the audit trail and never touched again.

### 5.1 Threshold and margin

`MATCH_THRESHOLD` and `MATCH_MARGIN` are read from
`calibration/threshold.json` — never a numeric literal in pipeline code
(R-09). Measured separation: same-person cosine 0.7652, non-match ceiling
0.1385 (n=41, live-harvested negatives — `calibration/negatives_harvested.json`),
threshold 0.42. That is a ~0.28 margin above the worst measured non-match and
~0.35 below true-match — wide, but explicitly not a benchmark-scale
calibration (n=41 is a pilot, not a statistically powered evaluation, and
the README says so).

### 5.2 Margin against the best non-match, not the runner-up

D-35: an earlier version of the rule rejected a real Alia Bhatt run with
**four genuine matches** (0.9225/0.9211/0.9164/0.9072 across linkedin,
youtube, reddit) because they agreed with each other to within 0.0014 — the
rule was treating abundant corroboration as ambiguity, exactly backwards.
Fixed by measuring margin against the best-scoring *non-match* candidate
instead of the second-best overall. Extra above-threshold matches are now
`corroborating`, never treated as doubt.

### 5.3 Citability-aware headline selection

Within an identity-agreeing cluster (everyone who already passed
threshold+margin), the *headline* — which one is shown as the citable
"post" — prefers an openable platform page over a bare CDN image URL, then
a `post` content-kind over `profile` over `unknown`. This is presentation
sorting, not scoring: score never re-enters the accept decision. Verified on
a live case: an X media hit at 0.9806 (not citable — a bare image URL) was
correctly relegated to `corroborating` while a YouTube hit at 0.9618 (a real
citable page) became the `ACCEPT` headline.

---

## 6. Evidence and re-verification — where a real defect was found and fixed

This section exists because it is the single most important correctness
property in the project, and because it was **wrong** for a real stretch of
this project's history in a way that no test caught until a competitor's
code was read closely enough to notice the shape of the bug.

### 6.1 The defect (R-25)

`chain/reverify.py` originally read `evidence.json`, recomputed keccak256
over the JSON, and compared that hash against `anchor.json`. **Nothing ever
recomputed `sha256(match_image.jpg)` and compared it to the recorded
`match.image_sha256`.** Consequence: replacing `match_image.jpg` on disk with
an entirely different image left the bundle JSON — and therefore its hash —
untouched, and `verify` reported `PASS`.

This was found during threat-modeling reverification routines, whose core principle
opens by naming the exact failure mode it was written to avoid ("a verifier
that loads the stored evidence hash for BOTH sides of the comparison").
Checking our own code against that description surfaced the gap. It is
recorded here, not softened, because Tier 0 (an earlier phase of this
project) argued specifically that refusing to anchor an empty image hash
mattered because "the hash must mean something" — and it did not, at verify
time, until this was fixed.

### 6.2 The fix — `pipeline/evidence/artifacts.py`

`rebuild_from_artifacts(run_dir)` is now the **only** function permitted to
construct the structure that gets verified. It:

1. Loads the stored bundle's artifact manifest (`artifacts[]` in schema v3,
   or a fallback to `match_image.jpg` for v1/v2 bundles, so the two
   pre-existing committed sample runs still verify without being
   regenerated or hand-edited)
2. For every referenced file, reads the **actual bytes on disk**, recomputes
   sha256 and perceptual hash, and **overwrites** the recorded digest —
   never carries a stored digest through unexamined
3. Returns per-artifact `ArtifactCheck` records plus a `failure_state`:
   `None`, `"ARTIFACT_MISSING"`, or `"ARTIFACT_MISMATCH"`

`chain/reverify.py` consumes this. Verdicts are now specific rather than one
vague `TAMPERED` string: `PASS`, `ARTIFACT_MISMATCH`, `ARTIFACT_MISSING`,
`BUNDLE_MODIFIED`, `NOT_ANCHORED`, `ERROR`. Each names a distinct, real
failure mode — the same discipline R-24 requires of reject reasons, now
applied to verifier output.

### 6.3 Verification never re-fetches a hosted URL

Deliberately. A candidate's platform image may 403, expire, or be deleted;
a search-engine cache URL expires by design. Re-verification reads **local
disk plus exactly one `eth_call`** — nothing else. What was fetched and
scored at anchor time (`match.verified_against`) and what the verifier
checks afterward are different questions, answered separately, on purpose.

### 6.4 Three tamper modes, not one

`pipeline/chain/tamper.py`. Each proves something the others don't, and each
operates only on a **temp-directory copy** — the real `runs/<id>/` is
verified byte-identical before and after by a dedicated test:

| mode | mutates | proves | verdict |
|---|---|---|---|
| `swap-artifact` | one byte in `match_image.jpg` | the bundle commits to actual image bytes | `ARTIFACT_MISMATCH` |
| `edit-bundle` | `match.score_bps` | the anchored hash pins the bundle's contents | `BUNDLE_MODIFIED` |
| `forge-bundle` | a fresh, internally consistent, unanchored bundle | consistency ≠ provenance | `NOT_ANCHORED` |

One button proving "hash changed when I edited a field" only demonstrates
that keccak256 is deterministic. Three modes each targeting a different
trust boundary is a materially stronger demonstration, and it is the direct
answer to a real design flaw found by reading a competitor's code (§9.1).

---

## 7. Chain layer

Anvil (local Foundry chain), real deployed `FaceEvidenceRegistry.sol`, real
`eth_call`. Chosen and reaffirmed (D-27, D-43) specifically because:

- the brief explicitly permits a local/simulated chain
- a judge cloning the repo runs the full anchor→verify→tamper cycle with
  zero account, zero faucet, zero funds
- it is a **real EVM contract**, not a mock — see §9.1 for exactly how far
  short of "real" one competitor's default chain actually is

Base Sepolia remains a documented opt-in bonus, never on the recording's
critical path, so a dry faucet on demo day cannot break anything.

`EVM_CHAIN` switches the RPC endpoint and nothing else (R-15) — if Anvil and
a public chain ever needed different logic, that would mean the abstraction
was wrong.

---

## 8. The LinkedIn investigation — a full case study in wrong diagnosis, corrected

Kept here in detail because it is the clearest example in this project of
"the bug was upstream of where I was looking," and because the corrected
diagnosis is the reason F1–F5 exist at all.

### 8.1 What was observed

The owner reported LinkedIn never appearing in results despite being
allowlisted since the beginning. Two real runs were used as evidence:

```
run 2026-09-05T22-52-19Z   identity_signals=[]          20/20 match_kind='similar'   max cosine 0.2144
run 2026-09-05T22-53-02Z   identity_signals=['Human']   20/20 match_kind='similar'   max cosine 0.1379
                           source: gcv_web_detection = 20/20   (SerpApi Lens: 0)
```

Rank-0 candidate in the second run: `chevening.org/.../Anjal-square-427x427.jpg`
— a university/fellowship headshot, wrong person, `reject-fetch-failed`.

### 8.2 First (wrong) hypothesis

Initial suspicion was that `pipeline/search/expand.py`'s LinkedIn handling
was broken — specifically, that `derive_profile_urls()`'s hardcoded
`https://www.linkedin.com/in/{handle}` guess combined with
`_add_expanded_candidate()` forcing LinkedIn to `origin="linked"`,
`image_url=""` meant LinkedIn could never be scored even in principle. This
diagnosis was **half right and half a distraction**: the guessed URL genuinely
was wrong (LinkedIn vanity slugs are not the account handle), but chasing it
implied expansion was the bottleneck, when expansion was never running at
all — see 8.3.

### 8.3 The actual root cause

`expand_verified_candidates()` is called from `pipeline_run.py` behind:

```python
if expand_profiles and match.best is not None:
```

Both real runs produced **zero survivors** — max cosine 0.21 and 0.14
against a 0.42 threshold. `match.best` was `None` in both. Expansion never
executed, not once. No amount of fixing `expand.py` could matter, because
the code path was never reached. The bottleneck was retrieval, not
expansion — GCV `WEB_DETECTION` had not found a single matching image for
this subject, only 20 unrelated lookalikes, and SerpApi Lens — which
answers a categorically different and more useful question — never ran
because `_resolve_backend()` picked one backend at construction time and
the two were mutually exclusive.

### 8.4 Second correction, mid-investigation

A follow-up hypothesis proposed that reference implementations *do* fetch and
face-score LinkedIn avatars, and that we should replicate that mechanism.
Reading reference pipeline implementations and `search/page_links.py` in
full (not just the function names) showed this was also wrong: their
`_linkedin_claim()` exists precisely because their same-handle LinkedIn
guess also dead-ends at HTTP 999, and their own docstring says the kept URL
is "a handle-based claim, not a face match." Nobody in this comparison set
face-verifies a LinkedIn avatar in the general case. See §9.2 for the full,
corrected comparison.

### 8.5 What actually fixes it — F1 through F5, `phases.md`

1. **F4** SSRF hardening (independent prerequisite, found while reading the
   fetch path — see §9.1)
2. **F1** Lens as a real, reachable second backend: head-crop-only upload
   (§4.2), dual-representation search (original + head crop, matching what
   competitors' bundles record as `queries: ["face_crop", "full_photo"]`)
3. **F5** the head-crop measurement, gated by R-27, decided on social-domain
   candidate count
4. **F2** escalation triggered on *result quality* (`match_kind` and
   allowlist membership), explicitly never on candidate count — because
   "20 candidates" already looked like success once before and wasn't
   (§4.1)
5. **F3** the four-tier taxonomy (§4.5) so that whatever LinkedIn evidence
   *is* recoverable is shown honestly, scored when possible and clearly
   labeled as a claim when not

The honest acceptance criterion for F1, recorded so it cannot be quietly
lowered later: if Lens *also* returns nothing usable for a given subject,
that is a legitimate finding — the subject is not indexed by either engine —
and must be recorded as a measured negative, not worked around.

---

## 9. Competitor analysis — source-level, not README-level

Three repos were reviewed for the same brief (HH Goa 2026 Task 3). The rule
followed throughout: **read the source before drawing a conclusion.**
Two earlier passes in this project's history drew conclusions from README
text alone and were subsequently corrected once the actual code was read —
both corrections are recorded in `memory.md` §3w rather than erased, because
a review process that only remembers being right is not trustworthy.

### 9.1 `shaikmohammedyasin-create/face-evidence-blockchain`

Stack: OpenCV YuNet + SFace (128-d), SerpApi Google Lens + Yandex Images +
Bing Visual Search (dead since 11 Aug 2025 — Microsoft retired the Bing
Search APIs), web3.py, Solidity `^0.8.20`.

**Where it is genuinely stronger:**
- Multi-face candidate scanning with explicit `matched_face_index` /
  `candidate_faces_count` reporting — we take the largest face in a
  candidate image and do not currently report which index matched or how
  many faces were present.
- A per-query statistical Z-score margin check (`Z ≥ 2.0`) as a third tier
  above absolute threshold + margin — we have not independently verified
  its live behavior against a stress case.
- A generated single-file HTML certificate with an embedded Etherscan QR
  code — a nicer take-home artifact for a judge than raw JSON.

**Where source-reading found real, verified problems:**
- **The default "Local Simulated EVM" is not an EVM.** Read in full,
  `app/blockchain/client.py`'s `SimulatedClient` is a Python dict:
  `_SHARED_STORE[fingerprint] = {...}`; `verify_fingerprint` is
  `fingerprint in store`; `tx_hash` is
  `sha256(f"{fingerprint}:{ts}:{project_id}")`; `block_number` is
  `6_428_190 + len(store) + 1`; `contract_address` is a hardcoded
  `# sample mock address` (`0x71C7656EC7ab88b098defB751B7401B5f6d8976F`,
  a well-known web3-tutorial address). It is labeled `[SIMULATION]`
  honestly in logs — that much is to their credit — but the quickstart asks
  only for `SERPAPI_KEY`, so this dict **is** the judge's default path, and
  the README calls it an EVM executing a full verification lifecycle. It
  also does not persist across process restarts (`_SHARED_STORE` is a class
  attribute), so anchor-today-verify-tomorrow is impossible on this path.
- **The RFC 8785 claim does not hold.** `app/evidence/canonical.py` is
  `json.dumps(sort_keys=True, ensure_ascii=True, separators=(",",":"))`.
  That diverges from RFC 8785 (JSON Canonicalization Scheme) in three
  specific, checkable ways: JCS mandates literal UTF-8 with only mandatory
  escaping (`ensure_ascii=True` escapes every non-ASCII character instead);
  JCS specifies ECMAScript `Number::toString` serialization (`1.0` renders
  as `1` under JCS, not as `1.0`); and JCS orders keys by UTF-16 code unit,
  not Python's code-point ordering. Deterministic Python-to-Python, so
  their own anchor/verify cycle still works — this is a documentation
  overclaim, not a broken hash.
- **The probe face is uploaded to an anonymous public host** (`tmpfiles.org`,
  1-hour TTL) to satisfy SerpApi Lens's URL requirement. No account, no
  scoping, no expiry control beyond the host's default.

### 9.2 Reference Benchmark Architecture (ArcFace + EVM)

Stack: SCRFD (InsightFace) + ArcFace `w600k_r50` 512-d (**same embedder as
us** — no architectural gap here, unlike the 128-d repos), SerpApi Google
Lens + FaceCheck.ID (paid, demo mode by default) + Yandex (best-effort
markup scraping), Solidity `^0.8.24` on real Ethereum Sepolia or in-process
`eth-tester`.

**Where it is genuinely stronger:**
- **Profile expansion is a real, working second search topology.**
  `_linked_from_verified_page()` and `_expand_same_handle()` propagate a
  handle *from an already face-verified page* outward to other platforms,
  and every expansion result is re-scored against the probe before it can
  count as a match. This directly informed our own F3 four-tier design
  (§4.5) — credited here as the origin of the idea, though our
  implementation splits their single `linked` origin into two (`linked`
  vs `conjecture`) because their code conflates a page's *published claim*
  with an unproven *same-handle guess*, which are not equally strong.
- **SSRF hardening on every candidate fetch**, in `search/fetch.py`:
  https-only, `socket.getaddrinfo` resolution with rejection of
  private/loopback/link-local/reserved addresses, size cap enforced
  *during* streaming (not after buffering), bounded redirects (max 3), and
  image-magic-byte validation instead of trusting the declared
  content-type. **We had none of this before F4 in this project's own
  Tier 2 work** — a real, previously-unaddressed gap, closed directly in
  response to reading this file.
- **A provider failure raises, and never silently becomes `[]`.**
  `search/lens.py`'s docstring states this as the single most important
  behavior in the module, with the reasoning that a broken API key looking
  like a legitimate negative result is the most damaging bug available on
  a project whose entire claim rests on genuine search. Directly informed
  our own F2 fix (escalation and failure semantics must never let a
  transport failure masquerade as `NO_MATCH`).
- Structured CLI exit codes (0–5) and full `scan`/`search`/`run`/`verify`/
  `serve` subcommands.

**Corrections recorded from reading this repo closely, in order found:**
- Initially believed (from function signatures alone) that they search only
  the aligned 112×112 crop. **Wrong** — `create_image_representations()`
  builds `original`, `face_crop`, and a third `expanded_crop` (40% margin
  padding), and their bundle records `queries: ["face_crop", "full_photo"]`.
  They search multiple representations; at the time this was written, we
  search one (see §4.3 for our own head-crop work, which independently
  converges on the same idea).
- Initially conflated their handle-propagation expansion with a
  previously-rejected "search a name the provider leaked" pivot. **Wrong,
  and it cost real time.** The two are not the same move: propagating a
  handle from a page our own embedder already verified keeps the face
  load-bearing at both ends of the search; searching a name a provider
  volunteered does not use the face at all after the first lookup. This
  distinction is now written into `rules.md` as R-28.
- Initially believed their code *fetches and face-scores* a LinkedIn avatar.
  **Wrong** — see §8.4. Their `_linkedin_claim()` exists specifically
  because that path also dead-ends at HTTP 999; the kept URL is explicitly
  documented as "a handle-based claim, not a face match."
- Their `--network local` mode is in-process `eth-tester`, which does not
  persist between CLI invocations — so by their own README, `run` then
  `verify` as two separate commands requires Sepolia. Our Anvil path
  persists across invocations without needing a public chain at all.

### 9.3 `rudrapatel1908/hhgoa-provenance` ("TRACE")

Stack: `face_recognition`/dlib (128-d), SerpApi Google Lens (real image
upload endpoint for `discover`, URL-based for `register`), Polygon Amoy
(80002), FastAPI adapter, Solidity `^0.8.24` compiled via real `solc`
(`scripts/compile_contract.py` — not hand-written ABI).

Read in source (`src/pipeline.py`, `src/manifest.py`) rather than from the
README summary, in keeping with this document's standing rule.

**Where it is genuinely stronger, verified in code:**

- **Two distinct entry points with different epistemic honesty, not one
  blurred flow.** `discover(image_path)` takes an image only and never
  touches the blockchain — it is explicitly "the primary Task 3 flow" per
  their own comment, and its result ceiling is `DISCOVERED` or `NO_MATCH`.
  `register(image_path, claimed_url)` takes a claimed source and requires
  it to independently survive a **second, separate live search of the
  original input image** before advancing past `VERIFIED` to
  `CORROBORATED`. The ordering is intentionally reversed between the two
  flows (`discover` searches first, then validates; `register` fetches and
  compares the claimed source *first*, then searches — specifically so the
  independent search cannot be influenced by anything already known about
  the claimed URL). This is a genuinely careful piece of design: it treats
  "a URL was supplied" and "a URL was independently rediscovered starting
  from nothing but the face" as different strengths of evidence and refuses
  to conflate them. We do not have an equivalent two-tier discovery/
  verification distinction — our pipeline always searches from the probe
  and never accepts an externally claimed URL as a privileged starting
  point, which sidesteps the problem their two-flow design solves, but at
  the cost of not offering the "verify a specific claimed post" mode at all.
- **`register()` searches the ORIGINAL input image, not the claimed
  source's own image, for corroboration** — and their own code comment
  states this was a real design change, made *after* an audit found the
  earlier version searched the source's image instead. Their reasoning,
  read directly from the comment: searching the source's own image only
  proves the source is independently indexed, whereas searching the
  original probe proves something stronger — that starting from nothing
  but the input photo, live search independently arrives back at this
  exact claimed URL. That is a materially different and stronger claim,
  and the fact that they found and fixed this themselves (rather than us
  finding it for them) is worth recording plainly.
- **The same fail-open tamper-detection bug we found in our own code, found
  and fixed independently in theirs, with an unusually clear write-up.**
  Their `audit()` docstring: re-hashing a *tampered* manifest and looking
  up *that* hash on-chain returns "not found," not "found but mismatched,"
  because a tampered manifest hashes to a value that was never registered
  under that key — so a naive audit would have reported
  `NOT_FOUND_ON_CHAIN` instead of `TAMPER_DETECTED`. Their fix (read
  `verification.json`'s **originally registered** hash, query the chain
  with *that* hash to confirm it's genuinely registered, then compare it
  against the hash of the manifest as it exists on disk right now) is the
  same category of fix as our own R-25/`rebuild_from_artifacts()` — an
  independent discovery of the same class of bug, arrived at differently.
  This is worth taking seriously: it is not one team's oversight, it is a
  failure mode general enough that two independently-built systems for the
  same brief both had it and both had to find and fix it explicitly.
- **A confirmation gate in front of every real transaction, at both the
  CLI and API layer**, and — notably — `/api/register` is a *separate
  endpoint* from `/api/verify` rather than one endpoint with a dry-run
  boolean flag, specifically so a frontend bug that flips a boolean cannot
  accidentally trigger a real transaction; it would have to call the wrong
  route entirely, which is a harder mistake to make by accident. `discover`
  and `register --dry-run` never touch the chain at all. This is a real,
  well-reasoned safety property in front of an irreversible action
  (spending testnet funds and writing an immutable record) that we should
  consider for our own anchor/tamper UI endpoints, where a single boolean
  or query parameter currently gates a real chain write.
- **The project's stated status is unusually honest about what has and has
  not actually been executed.** Their README's "Current real project state"
  section states plainly that the real on-chain registration has
  *deliberately not been run yet*, that the deployed contract and wiring
  have been confirmed via a genuine read-only call, and that the remaining
  testnet funds are being reserved for one final deliberate registration
  once everything upstream is proven. This is the same standard R-13 in our
  own `rules.md` holds us to (a phase is done only when its exit criterion
  has been executed and observed, not merely coded) — applied by another
  team, to themselves, in public. Worth naming as a good practice
  regardless of whose repo it appears in.

**Where our system is stronger, and where the comparison is close or
unverifiable:**

- **Embedding dimensionality**: 512-d ArcFace vs their 128-d
  `face_recognition`/dlib encoding. Same architectural gap noted against
  the other two 128-d repos (§9.1) — stated as fact, not as a measured
  accuracy win, since no head-to-head benchmark has been run against any
  competitor.
- **Local chain choice**: Polygon Amoy is a real public testnet requiring a
  faucet, an RPC endpoint, and network liveness on the actual demo take —
  exactly the risk profile D-27/D-43 chose to avoid by defaulting to Anvil.
  Their README's own stated plan (reserve funds for one single, deliberate,
  final registration) is a sound mitigation of that risk, but it is a
  mitigation of a risk that only exists because a public testnet was chosen
  as the default path in the first place. We consider this a legitimate
  values difference rather than a defect on either side: their design
  produces a genuinely public, permanent, third-party-verifiable
  transaction if and when it lands; ours produces a real EVM transaction
  that any judge can reproduce with zero setup and zero risk of a dry
  faucet or RPC outage during a recording.
- **Search recall mechanism is architecturally similar to ours before F1**:
  they rely on SerpApi Google Lens exclusively (via the real 500KB image
  upload endpoint for `discover`, capped and fails closed rather than
  silently re-encoding oversized files), with **no cross-platform profile
  expansion, no LinkedIn-specific resolution route, and no head-crop query
  representation**. Their `discover` mode validates social candidates
  strictly in rank order and never assumes `results[0]` is correct — a
  discipline we share (`architecture.md`'s explicit rejection of a
  count-based or rank-0 shortcut) — but it does not attempt to recover a
  match that Lens's ranking alone does not surface, the way our F3
  expansion tiers (§4.5) or F1's dual-backend escalation (§4.1) do. This is
  the clearest area where our post-F1 pipeline is more capable, once F1–F5
  land.
- **Verification is a single linear flow through `register()`/`discover()`
  with no separate re-fetch-and-recompute utility analogous to our
  `rebuild_from_artifacts()`** as an independently callable, independently
  tested module — their tamper-detection fix (above) is real and correctly
  reasoned, but it lives inline in `audit()` rather than as a reusable
  artifact-verification primitive with its own per-artifact check records
  the way `pipeline/evidence/artifacts.py` provides. This is a structural
  preference, not a correctness gap in their code — noted for completeness
  rather than as a criticism.
- **Unverified claims** (not run by us, stated here rather than assumed):
  their "78 tests, all external services mocked" claim was not independently
  executed against their repository as part of this analysis — recorded
  from the README, consistent with the standing rule that a claim not
  personally verified must be labeled as such rather than presented as
  confirmed fact.

### 9.4 What we take from all three, concretely

| idea | source | our status |
|---|---|---|
| Provider failure must raise, never silently become `[]` | Reference Baseline `lens.py` | F2, in progress |
| SSRF hardening on every candidate fetch | Reference Baseline `fetch.py` | F4, implemented (`pipeline/cache/urlguard.py`, R-29) |
| Handle-propagation expansion, face-gated | Reference Baseline `pipeline.py` | F3, implemented — improved with the linked/conjecture split |
| SERP-based resolution for platforms that block direct fetch | Reference Baseline `websearch.py` | implemented (`pipeline/search/serp_resolve.py`) |
| Search the original photo, not only a face crop | independently measured by us (D-34) and reference benchmarks | both directions now covered — original + head crop |
| Search the *original probe image* for corroboration, not the claimed source's own image | TRACE `pipeline.py` `register()` | not directly applicable (we have no claimed-URL verification mode), but the underlying principle — a search's independence is only as strong as what it's searching *from* — is worth re-checking against our own expansion searches |
| A confirmation gate as a separate endpoint, not a boolean flag, in front of irreversible actions | TRACE `api.py` | worth adopting for our own anchor/tamper endpoints — not yet done |
| Explicit "what has and hasn't actually been executed" status reporting | TRACE README | consistent with our own R-13; worth an explicit README section modeled on theirs |
| Real EVM (not a dict) as the default local path | our own design, reaffirmed by contrast with face-evidence-blockchain | already true (D-27, D-43) |
| Four evidence tiers instead of two | our own synthesis of reference benchmark's `linked`/`face` split | implemented (F3) |

---

## 10. Test posture

290 tests passing as of commit `0a62f17` (up from 252 at the start of this
session's hardening pass). Categories added this session: SSRF/URL guard,
XSS hardening, backend escalation, LinkedIn/Instagram SERP resolution, four-
tier origin classification, tamper mode differentiation, CLI exit codes.
pyflakes clean apart from pre-existing unused-import warnings in
`pipeline/chain/reverify.py:27`, `tests/test_evidence.py:16,27`,
`tests/test_pipeline_run.py:17,20`, `tests/test_reverify.py:13`.

---

## 11. Open work, honestly stated

- **F1/F5 not yet measured live.** The uploader and escalation logic exist
  and are unit-tested with mocks, but the acceptance criterion — a live
  `HTTP_CACHE=0` run showing SerpApi Lens returning a social-domain
  candidate that GCV alone did not — has not yet been executed and recorded
  in `memory.md`. This document does not claim it has.
- **Head-crop measurement not yet run.** `use_head_crop` remains `False` by
  default. R-27 forbids flipping it without the measurement.
- **No benchmark-scale accuracy comparison against any competitor.**
  Every dimensionality/architecture claim in §9 is stated as fact about the
  code, not as a measured accuracy delta, because no such benchmark exists
  on either side.
- **TRACE's real on-chain registration was, by their own account, not yet
  executed as of the version reviewed** — so any claim about their live
  transaction behavior is necessarily about their *code path*, not an
  observed transaction.
