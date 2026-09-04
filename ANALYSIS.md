# Face Scan → Social Match → Blockchain Proof
## Deep analysis, architecture options, and final decision

**Document date:** 4 September 2026
**Status:** pre-build analysis. No code written yet. Workspace is greenfield.
**Purpose:** decide the architecture before burning hackathon hours, and record why every rejected option was rejected.

---

## 1. Deconstructing the problem statement

The brief asks for a three-stage pipeline:

```
face scan → web/social search → blockchain upload + verification
```

Three requirements, wildly unequal in difficulty. Recognising that imbalance is the single most important planning decision.

| Stage | Stated requirement | Real difficulty | Time budget |
|---|---|---|---|
| 1. Face identification | Detect + encode a face. "Any library or API is acceptable." | Low | ~2 h |
| 2. Social/web search | Find at least one **real** matching social post. **"This should be a genuine search step, not a hardcoded/pre-picked result."** | **High — this is the whole project** | ~10 h |
| 3. Blockchain verification | Upload post or its hash. "Any blockchain." Must **"demonstrate re-verifying the data against the on-chain record."** | Low to write, easy to do badly | ~5 h |

### Three things hidden in the wording

**(a) The anti-cheat clause is the grading rubric.** The organisers wrote *"not a hardcoded/pre-picked result"* because they know most submissions will fake step 2 — a dict of `{face_id: known_post_url}` behind a progress bar. Every design decision below is judged first on: *can a sceptical judge convince themselves this is real?*

**(b) "Re-verifying" is the graded part of stage 3, not "upload."** Writing bytes to a chain is trivial. The brief explicitly requires demonstrating verification against the on-chain record. Most teams will ship an `anchor` path and no `verify` path, or a `verify` that only ever returns true. A verifier that can also prove a **negative** is worth more than a fancier contract.

**(c) "No website required" is a scoring hint.** Time saved on UI must be visible somewhere else. Depth belongs in the pipeline, in the verification story, and in the recording.

### Deliverables, restated

- GitHub repo, full source
- README: what it does, how to run, which blockchain, known limitations
- Screen recording of the full pipeline working end to end
- One submission, no resubmissions — so build for reliability over ambition

---

## 2. Research findings

Everything in this section was checked against current sources on 4 September 2026, because several of the obvious dependencies have degraded during 2026. Links in §17.

### 2.1 Face layer — settled, low risk

| Option | Verdict |
|---|---|
| **InsightFace `buffalo_l` (ArcFace, 512-d) via onnxruntime** | **Chosen.** Current SOTA-class open model, CPU-viable, detection + 5-point landmarks + embedding in one pack. |
| `face_recognition` / dlib ResNet (128-d) | **Rejected.** 2017-era accuracy. Will produce a visible false match on video and discredit the demo. |
| DeepFace wrapper | Usable but adds a heavy abstraction over models you should control directly. |
| Cloud face APIs (AWS Rekognition, Azure Face) | Rejected. Azure Face is gated behind access review; adds keys and latency for no accuracy gain over ArcFace. |

Two implementation details that are commonly skipped and both hurt accuracy badly:

1. **Similarity transform alignment to the 5 landmarks before embedding.** Unaligned crops materially degrade ArcFace. This is the most frequently omitted step in hackathon face code.
2. **L2-normalise embeddings**, then cosine similarity reduces to a dot product. Keep it consistent everywhere or your thresholds mean nothing.

**Liveness / anti-spoof.** The brief says *face scan*, not *upload a JPEG*. A passive anti-spoof model upgrades the input stage at low cost — Silent-Face-Anti-Spoofing has ONNX ports that run fine on CPU. Also blocks the cheapest attack on your own demo: holding a printed photo to the webcam.

### 2.2 Search layer — every option, tested on paper

This is the decision that determines whether the project succeeds.

| Option | Genuine open-web? | Cost | Reliability | Verdict |
|---|---|---|---|---|
| **Google Lens via SerpApi** | Yes | Free tier ~100 searches/mo | Good | **Primary provider** |
| **Bing Visual Search** | Yes | Free tier | Good | **Secondary, redundancy** |
| **Bluesky (AT Protocol)** | Live social, closed corpus | Free, no auth for most reads | Excellent | **Fallback provider** |
| **Mastodon public timelines** | Live social, closed corpus | Free, no auth | Good | Optional third source |
| FaceCheck.ID REST API | Yes, true face index | ~3 credits/search at $0.10/credit, crypto-funded only | Good | Optional, off by default |
| Search4Faces JSON-RPC | Yes, social-weighted | Free tier | **Unverified** | Optional, off by default |
| PimEyes via Selenium/Playwright | Yes | Free | **Very poor** | **Rejected** |
| Yandex Images scraping | Yes | Free | **Very poor** | **Rejected** |
| TinEye API | Exact-copy only, not faces | Paid | Good | Rejected — wrong tool |

**Google Lens is not a face search engine, and that is the opportunity.** Lens returns *visually similar* images and deliberately avoids face identification. Its raw output contains lookalikes, stock photos, and unrelated pages. That gap is exactly where your own contribution lives: you re-run your ArcFace pipeline on every candidate Lens returns and score it. You convert a fuzzy image search into a scored face match. That stage is yours, it is real engineering, and — critically — it is *visible on camera*.

**Bluesky is the reliability insurance.** Most AT Protocol read endpoints need no authentication, and the firehose gives a live event stream of real public posts. Crawl it at runtime, embed faces, index in FAISS, query. The corpus is yours, so a judge can fairly say "that isn't the web" — but as a *fallback provider inside* an open-web pipeline it contributes all its reliability without having to win that argument. Note for Mastodon: full-text status search needs authentication plus an ElasticSearch backend, so use public timelines rather than search.

#### Rejected search options, with reasons

These matter. Documenting them is what separates a considered design from a lucky one.

- **PimEyes scraping — reject.** The public GitHub tooling around this is a graveyard. One prominent repo is described by its own author as an exploit to bypass result obfuscation; another carries a header saying it no longer works after an API change; others advertise anti-bot evasion as a feature. Building a hackathon submission on a bypass of a paid gate is a legal and reputational liability, and it will break. The brief permits *"a scripted search approach"* — that means scripting permissive sources, not defeating paywalls.
- **Yandex Images scraping — reject.** Yandex runs its own captcha system and, per current scraping write-ups, can return a captcha page with **HTTP 200**. Your scraper fails *silently*, mid-demo, with no exception to catch. Unacceptable for a single-take recording.
- **Search4Faces — optional only.** It does publish a JSON-RPC 2.0 API and there is a Python client on PyPI, and it indexes social networks specifically, which fits the brief well. But the API doc page dates to 2022 and the client to early 2023, and the index is heavily weighted to VK and other Russian networks. Freshness unverified. Ship it as a disabled-by-default provider; do not build the demo on it.
- **FaceCheck.ID — optional only.** Genuinely good search over a real face index, but crypto-only prepayment means a judge cloning your repo cannot run it. That quietly undermines "full source code, how to run it."

### 2.3 Blockchain layer — the 2026 landscape shifted

Three findings that change the obvious answer:

**Avoid Polygon Amoy.** The official Polygon faucet has been retired, and Polygon's public RPC endpoints for both mainnet and Amoy were scheduled for deprecation. You would be assembling a third-party faucet plus a private RPC provider on hackathon time. No upside over the alternatives.

**Ethereum Sepolia is mid-transition.** Core devs targeted the Glamsterdam activation on Sepolia for around 6 October 2026, and community testnet trackers list Sepolia support ending 30 September 2026 with a successor running in parallel. Nothing breaks this week, but there is no reason to pick a network that is being wound down while you are being graded.

**Base Sepolia is the clean EVM pick.** Base's own docs list a faucet dispensing up to 0.1 test ETH per 24 hours, and Coinbase's developer platform faucet covers Base Sepolia, Ethereum Sepolia, and Solana Devnet with a programmatic endpoint. Roughly two-second blocks, negligible gas, and a public block explorer link a judge can open during the recording.

### 2.4 Trust and provenance layer — where I found a real upgrade

This is the part of the research that changed the recommendation. Three findings, all cheap to adopt:

**(a) OpenTimestamps gives you Bitcoin *mainnet* for free.** OTS public calendar servers aggregate timestamp requests from many users into a Merkle tree and pay the Bitcoin transaction fees themselves. No registration, no API key, no wallet, no gas. There is a maintained Python library and a CLI client. The brief says *"public testnet, mainnet, or a local/simulated chain."* Anchoring to Bitcoin mainnet at zero cost is the strongest available answer to that sentence, and almost nobody in the room will make that claim.

*Caveat, and it matters:* OTS is batched and then needs a Bitcoin block, so a fresh stamp is *pending* for a while — typically an hour or more. It therefore cannot be the live on-camera anchor. Handle it by stamping live to show the pending proof, and separately verifying a proof stamped the day before that is now fully confirmed.

**(b) EAS (Ethereum Attestation Service)** is deployed as a public good on Base and other chains, supports on-chain and zero-gas off-chain attestations, has a public explorer, and has SDKs including a Python one. Using it instead of a bespoke contract buys you a standard schema, composability, and no deployment step. The trade-off is you lose the "we wrote and tested a contract" credit, and the Python SDK is less mature than the TypeScript one. **Decision: write the small custom contract, mention EAS in the README as the production path.** A hackathon rewards showing you can do both and choosing deliberately.

**(c) C2PA Content Credentials** is the actual industry standard for content provenance, with an official Python binding from the Content Authenticity Initiative. Emitting the evidence bundle as a signed C2PA manifest and anchoring *that* makes the output portable and standards-based instead of a bespoke JSON blob. Prior art exists combining C2PA manifests with blockchain timestamping. Requires a signing certificate — self-sign for the demo and **disclose it**, since a self-signed cert is not on the C2PA trust list.

---

## 3. Candidate solutions

### Solution 1 — Face-search API wrapper

```
webcam → detect/crop → POST to commercial face-search API
       → filter to social domains → top hit → hash → anchor
```

**Build time:** ~4 hours.

**Strengths.** Genuinely searches the open web against a face index of billions of images. Will find an arbitrary judge if that person has any online footprint. Highest raw match quality of any option here.

**Weaknesses.**
- You are a thin client over someone else's product. The matching logic is a black box. "How does your matching work?" → "I don't know, the API does it."
- Crypto-only prepayment. A judge cloning the repo cannot run it.
- Single dependency, zero fallback. One rate-limit and the project does not exist.
- It makes requirement 1 vestigial — the API does its own detection, so your local encoder is decoration.

### Solution 2 — Reverse image search + local ArcFace re-verification

```
webcam + liveness → detect → align → ArcFace 512-d embed
   → fan out aligned crop to reverse-image engines
   → download EVERY candidate image
   → re-run own face pipeline on each candidate
   → cosine-score vs probe, reject below threshold
   → reject off-allowlist domains
   → scrape surviving post's author / text / timestamp / permalink
   → evidence bundle → hash → anchor
```

**Build time:** ~12 hours.

**Strengths.** Open-web search legitimacy, plus a verification stage that is yours and is demonstrable. You can print every candidate with its score and accept/reject reason — the rejections are the proof of authenticity. Requirement 1 becomes load-bearing rather than decorative.

**Weaknesses.** If the probe person has no indexed web presence, the honest output is "no match found." Correct behaviour, terrible demo.

### Solution 3 — Live social crawler + own vector index

```
Bluesky firehose / Mastodon timelines (live, at runtime)
   → detect faces in each post image → embed → FAISS index
   → query with probe embedding → matching post + real permalink
   → evidence bundle → hash → anchor
```

**Build time:** ~10 hours.

**Strengths.** Zero cost, zero API keys, no rate-limit cliff, fully reproducible by anyone who clones the repo. Real substance: streaming ingest, batched inference, ANN indexing, threshold calibration. Trivially provable as non-hardcoded — show that the matched post was published ninety seconds ago.

**Weaknesses.** Closed-world. You searched a corpus you built, not "the web." A sharp judge will say exactly that, and they will be right.

### Solution 4 — RECOMMENDED: dual-anchored provenance pipeline

Solution 2 as the core, Solution 3 as a fallback provider behind the same interface, and an upgraded trust layer.

```
┌─ STAGE 1 ─ face scan ───────────────────────────────────┐
│ webcam frame → anti-spoof gate → RetinaFace detect      │
│ → 5-point similarity-transform align → ArcFace 512-d    │
│ → L2 normalise                                          │
└─────────────────────────────────────────────────────────┘
                          │  probe embedding
                          ▼
┌─ STAGE 2 ─ search (parallel fan-out) ───────────────────┐
│  SearchProvider interface                               │
│   ├── GoogleLensProvider   (SerpApi)      open web      │
│   ├── BingVisualProvider   (Azure)        open web      │
│   ├── BlueskyLiveProvider  (AT Protocol)  live social   │
│   ├── MastodonLiveProvider (public API)   live social   │
│   ├── FaceCheckProvider    (paid, opt-in, default off)  │
│   └── Search4FacesProvider (opt-in, default off)        │
│                                                          │
│  → dedupe candidates by perceptual hash                 │
│  → SAME face pipeline on every candidate image          │
│  → cosine score, threshold + margin rule                │
│  → social-domain allowlist                              │
│  → scrape post metadata for survivors                   │
│  → ranked matches, full audit trail of rejects          │
└─────────────────────────────────────────────────────────┘
                          │  best match + provenance
                          ▼
┌─ STAGE 3 ─ evidence + dual anchor ──────────────────────┐
│ canonical evidence bundle (RFC 8785 JCS)                │
│   ├─ optional: sign as C2PA manifest                    │
│   ├─ full bundle → IPFS (CID)                           │
│   ├─ keccak256 commitment → Base Sepolia registry       │
│   │     instant, queryable, explorer link               │
│   └─ sha256 → OpenTimestamps → Bitcoin MAINNET          │
│         free, permanent, independent chain              │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─ VERIFY (separate command, 4 independent checks) ───────┐
│ 1. recompute bundle hash → matches commitment?          │
│ 2. C2PA signature valid, manifest intact?               │
│ 3. Base Sepolia record exists, fields match?            │
│ 4. Bitcoin OTS proof valid, block height + time?        │
│ → MATCH / TAMPERED, per check                           │
└─────────────────────────────────────────────────────────┘
```

**Build time:** ~16 hours, and it degrades gracefully — drop the C2PA and Bitcoin layers and you still have Solution 2.

---

## 4. Decision matrix

Weighted against what the brief actually grades.

| Criterion | Weight | S1 API wrapper | S2 RIS + verify | S3 live crawler | **S4 recommended** |
|---|---|---|---|---|---|
| Satisfies "genuine search" | 25% | 3/5 | 5/5 | 3/5 | **5/5** |
| Provable as non-hardcoded on camera | 20% | 1/5 | 5/5 | 5/5 | **5/5** |
| Re-verification depth | 20% | 2/5 | 3/5 | 3/5 | **5/5** |
| Technical depth on display | 15% | 1/5 | 4/5 | 4/5 | **5/5** |
| Demo reliability, single take | 10% | 2/5 | 3/5 | 5/5 | **5/5** |
| Reproducible from repo by a judge | 5% | 1/5 | 4/5 | 5/5 | **5/5** |
| Buildable in the window | 5% | 5/5 | 4/5 | 4/5 | **3/5** |
| **Weighted total** | | **2.0** | **4.2** | **3.8** | **4.9** |

**Verdict: Solution 4.**

---

## 5. Why Solution 4 wins

**vs Solution 1.** S1 has better raw search but you own none of it. The brief awards a genuine *search step*; S4 owns search *and* verification and can narrate every decision on camera. S1 also cannot be run by a judge without funding a crypto account, which quietly breaks the "how to run it" requirement.

**vs Solution 2.** S4 *is* S2 at the core. The delta is two things. First, S3 slots in as another `SearchProvider`, which removes S2's only real failure mode — a probe face with no indexed presence — for maybe 20% more code, because the verification core is identical whether a candidate came from Google Lens or from a Bluesky post. Second, the trust layer goes from one check to four, which targets the requirement the brief actually grades.

**vs Solution 3.** Same depth, but S3 loses on the plain reading of "search the web." Demoted to a provider inside S4, it contributes its reliability without having to carry that argument.

**The compounding argument.** One face pipeline serves detection, probe encoding, candidate verification, and index building. One canonicalisation function serves IPFS, both chains, and the verifier. Marginal cost per added capability is low because the primitives are shared. That is why a strictly larger design is still buildable in the window.

**The claim nobody else will make.** *"Every record is anchored on two independent blockchains — Base Sepolia for instant queryable lookup, and Bitcoin mainnet via OpenTimestamps for permanence. Zero cost, no wallet needed for the Bitcoin side."* That is true, cheap, and memorable.

---

## 6. Reference architecture

### 6.1 Repo layout

```
face-chain-verify/
├── pipeline/
│   ├── face/
│   │   ├── detect.py          RetinaFace via insightface
│   │   ├── align.py           5-point similarity transform
│   │   ├── embed.py           ArcFace buffalo_l, L2-normalised
│   │   └── liveness.py        Silent-Face anti-spoof, ONNX
│   ├── search/
│   │   ├── base.py            SearchProvider ABC + Candidate dataclass
│   │   ├── google_lens.py     SerpApi
│   │   ├── bing_visual.py     Azure Visual Search
│   │   ├── bluesky.py         AT Protocol firehose + FAISS
│   │   ├── mastodon.py        public timelines
│   │   ├── facecheck.py       opt-in, default off
│   │   └── search4faces.py    opt-in, default off
│   ├── verify/
│   │   ├── matcher.py         cosine + threshold + margin rule
│   │   ├── calibrate.py       ROC over labelled pairs, pick operating point
│   │   ├── dedupe.py          perceptual hash
│   │   └── allowlist.py       social domain rules
│   ├── evidence/
│   │   ├── canonical.py       RFC 8785 JCS, frozen schema
│   │   ├── bundle.py          assemble evidence
│   │   ├── c2pa.py            sign manifest (optional layer)
│   │   └── ipfs.py            Pinata upload
│   ├── chain/
│   │   ├── evm.py             anchor + read, Base Sepolia / Anvil
│   │   ├── ots.py             OpenTimestamps stamp + verify
│   │   ├── reverify.py        the 4-check verifier
│   │   └── abi/
│   ├── audit/
│   │   └── run_log.py         every request, timing, candidate, decision
│   └── cli.py                 scan | search | anchor | verify | run-all
├── contracts/
│   ├── src/FaceEvidenceRegistry.sol
│   ├── test/FaceEvidenceRegistry.t.sol
│   └── script/Deploy.s.sol                     Foundry
├── runs/                      committed sample runs, incl. a no-match run
├── calibration/               pair set + ROC plot
├── .env.example
└── README.md
```

### 6.2 Provider interface

Everything hangs off this. Keep it boring.

```python
from dataclasses import dataclass
from typing import Protocol
import numpy as np

@dataclass(frozen=True)
class Candidate:
    image_url: str          # the candidate image to download and verify
    page_url: str           # the post / page it appeared on
    source: str             # provider name, for the audit log
    provider_score: float | None   # provider's own score, NOT trusted
    raw: dict               # untouched provider response, for the audit log

class SearchProvider(Protocol):
    name: str
    def search(self, aligned_face_png: bytes,
               probe_embedding: np.ndarray) -> list[Candidate]: ...
```

`provider_score` is recorded but never used for the accept decision. Only your own ArcFace score decides. State that explicitly in the README — it is the crux of the "genuine search" argument.

### 6.3 Contract — anchor commitments, never biometrics

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title FaceEvidenceRegistry
/// @notice Anchors tamper-evident commitments to face->social-post match evidence.
/// @dev Stores NO biometric data. faceCommitment is a salted hash; the salt
///      never leaves the operator's machine. An immutable public ledger plus a
///      recoverable biometric template would be an un-deletable biometric
///      record, which is incompatible with GDPR Art. 9 and India's DPDP Act.
contract FaceEvidenceRegistry {
    struct Record {
        bytes32 faceCommitment; // keccak256(salt || quantize(embedding))
        bytes32 imageHash;      // sha256 of matched image bytes
        bytes32 postHash;       // sha256 of canonical post metadata
        string  cid;            // IPFS CID of full evidence bundle
        uint32  scoreBps;       // cosine similarity in basis points
        uint64  anchoredAt;     // block.timestamp
        address submitter;
    }

    mapping(bytes32 => Record) private _records; // key = evidenceHash

    event EvidenceAnchored(
        bytes32 indexed evidenceHash,
        bytes32 indexed faceCommitment,
        string  cid,
        uint32  scoreBps
    );

    error AlreadyAnchored(bytes32 evidenceHash);
    error ZeroHash();

    function anchor(
        bytes32 evidenceHash,
        bytes32 faceCommitment,
        bytes32 imageHash,
        bytes32 postHash,
        string calldata cid,
        uint32  scoreBps
    ) external {
        if (evidenceHash == bytes32(0)) revert ZeroHash();
        if (_records[evidenceHash].anchoredAt != 0) revert AlreadyAnchored(evidenceHash);

        _records[evidenceHash] = Record({
            faceCommitment: faceCommitment,
            imageHash:      imageHash,
            postHash:       postHash,
            cid:            cid,
            scoreBps:       scoreBps,
            anchoredAt:     uint64(block.timestamp),
            submitter:      msg.sender
        });

        emit EvidenceAnchored(evidenceHash, faceCommitment, cid, scoreBps);
    }

    function verify(bytes32 evidenceHash)
        external view returns (bool exists, Record memory rec)
    {
        rec = _records[evidenceHash];
        exists = rec.anchoredAt != 0;
    }
}
```

**Why the salted commitment is the most important line in the project.** Most teams will serialise the float array and write it on chain. That creates a permanent, public, un-deletable biometric template — a direct collision with GDPR Art. 9 and the DPDP Act. A salted commitment still lets you prove later that a given face produced this record, without ever publishing the biometric. One paragraph in the README about this is a genuine differentiator, and it is the kind of thing an experienced judge asks about.

### 6.4 Canonicalisation — where live demos die

If the evidence JSON reorders keys or reformats a float between anchor and verify, the hash changes and re-verification fails **on stage**. Non-negotiable rules, frozen on day one:

- RFC 8785 JSON Canonicalization Scheme, or a fixed schema with lexicographically sorted keys
- **No floats, ever.** Similarity → integer basis points. Timestamps → Unix integer seconds.
- Explicit `schema_version` field
- Byte-level round-trip test in CI: `canonical(parse(canonical(x))) == canonical(x)`

```python
EVIDENCE_SCHEMA_V1 = {
    "schema_version": 1,
    "probe": {
        "face_commitment": "0x…",   # salted, NOT the embedding
        "liveness_passed": True,
        "captured_at": 1757000000,
    },
    "match": {
        "page_url": "https://…",
        "image_url": "https://…",
        "image_sha256": "0x…",
        "provider": "google_lens",
        "score_bps": 6842,           # 0.6842 cosine
        "margin_bps": 3910,          # gap to runner-up
    },
    "post": {
        "author_handle": "…",
        "author_display": "…",
        "text": "…",
        "published_at": 1756900000,
        "platform": "…",
    },
    "run": {
        "candidates_examined": 12,
        "candidates_rejected": 11,
        "providers_queried": ["google_lens", "bing_visual", "bluesky"],
        "pipeline_version": "…",
    },
}
```

### 6.5 The verifier — four independent checks

```
$ python -m pipeline verify --bundle runs/2026-09-04T12-30-11Z/evidence.json

[1/4] bundle integrity
      recomputed keccak256 : 0x9f3c…a12b
      expected             : 0x9f3c…a12b                     PASS
[2/4] C2PA manifest
      signature valid, 1 assertion, self-signed cert          PASS (untrusted CA)
[3/4] Base Sepolia  chainId 84532
      record found, block 21,443,901
      cid / imageHash / postHash / scoreBps all match         PASS
[4/4] Bitcoin mainnet  via OpenTimestamps
      attested by block 921,077, 2026-09-03 18:22:04 UTC      PASS

RESULT: VERIFIED — evidence is unmodified since anchoring.
```

Then the money shot. Change one character in the bundle and re-run:

```
[1/4] bundle integrity
      recomputed keccak256 : 0x4d81…7c09
      expected             : 0x9f3c…a12b                      FAIL

RESULT: TAMPERED — bundle does not match the on-chain commitment.
```

A verifier that only ever prints PASS proves nothing. Fifteen seconds of the recording should be this.

### 6.6 Threshold calibration

Do not hardcode a number from a blog post. Build `calibrate.py`:

1. Assemble a small labelled pair set — positives and negatives, ~200 pairs is enough to be meaningful
2. Sweep the cosine threshold, plot ROC, pick the operating point at a **stated** false-match rate
3. Commit the plot and the chosen number, and cite the FMR in the README

Expect to land somewhere in the 0.35–0.45 band for `buffalo_l` normalised embeddings, but **derive it**.

**Add a margin rule.** Accept only if `best_score >= threshold AND (best_score - second_best_score) >= margin`. This single rule kills most lookalike false positives, and it is easy to explain on camera.

---

## 7. Proving "not hardcoded"

Engineer for this, don't assert it.

- **Let the judge pick the input** at demo time — live webcam, or a photo they hand over
- **Stream raw provider JSON to the console** as it arrives. Unedited third-party responses are hard to fake
- **Print the full candidate table** — every URL fetched, its score, accept/reject and why. The rejections carry the argument
- **Commit `runs/`** with 4–5 different subjects producing different outcomes, including one legitimate `NO_MATCH`
- **On the Bluesky path, print the matched post's publish timestamp** so its recency is visible
- **Commit the audit log** for each run: providers queried, HTTP status codes, latencies, candidate counts

The `NO_MATCH` sample run is counter-intuitively one of the strongest artifacts in the repo. A hardcoded system never fails to find a match.

---

## 8. Build plan

Front-load anything with an external dependency. Faucets, RPC keys, and cert generation are what go wrong at 3 a.m.

| Hours | Work | Exit criterion |
|---|---|---|
| 0–2 | Face core: detect, align, embed, cosine matcher | Two photos of the same person score high; two different people score low |
| 2–4 | `calibrate.py`, pair set, ROC, pick threshold + margin | Committed plot and a justified number |
| 4–7 | Contract, Foundry tests, deploy to Anvil then Base Sepolia | `anchor` → `verify` round trip green, **including the tamper case** |
| 7–8 | Canonicalisation, frozen schema, round-trip test | Byte-identical re-serialisation in CI |
| 8–12 | `SearchProvider` ABC, Google Lens provider, candidate download + verify loop, allowlist, metadata scrape | End-to-end on a public figure |
| 12–15 | Bluesky provider + FAISS index | End-to-end on a face Lens cannot find |
| 15–16 | OpenTimestamps stamp + verify | Bitcoin proof verifies against a real block |
| 16–17 | Liveness gate, IPFS upload, CLI polish, console output styled for video | Output is legible at 1080p |
| 17–18 | C2PA manifest — **cut this first if behind** | Manifest signs and verifies |
| 18–21 | Record. Three takes minimum | Clean single take, under 5 minutes |
| 21–24 | README, sample runs, limitations, traceability matrix | Every brief requirement has a named section |

**Cut order if time runs short:** C2PA → Bing provider → Mastodon provider → liveness → IPFS. Never cut: the tamper demo, the candidate rejection table, the `NO_MATCH` sample run.

---

## 9. Recording script

Roughly four minutes. Rehearse it; do not improvise.

| Time | Beat | Purpose |
|---|---|---|
| 0:00–0:20 | One sentence on what the pipeline does. Show the repo tree | Orient the viewer |
| 0:20–0:50 | Live webcam capture. Anti-spoof gate passes. Hold a printed photo to camera — gate **rejects** it | Proves "face scan," not "file upload" |
| 0:50–1:10 | Detection box, 512-d embedding printed, truncated | Requirement 1, visibly satisfied |
| 1:10–2:00 | Search runs. **Raw provider JSON streams past.** Candidate table fills in with scores | Requirement 2, and the anti-hardcode proof |
| 2:00–2:20 | Zoom the table: 11 rejected at 0.09–0.31, one accepted at 0.68, margin 0.37. Open the matched post in a browser — real, live, with a timestamp | The match is real |
| 2:20–2:50 | Bundle → IPFS → CID. Anchor to Base Sepolia. **Open the explorer link, show the tx** | Requirement 3, independently checkable |
| 2:50–3:05 | OTS stamp to Bitcoin mainnet. Note it is pending, explain batching | The differentiator |
| 3:05–3:35 | `verify` on the fresh bundle: 4/4 PASS, including a Bitcoin proof from yesterday's confirmed stamp | Re-verification, the graded part |
| 3:35–3:55 | **Edit one character.** Re-run `verify`. TAMPERED | Proves the verifier actually checks |
| 3:55–4:10 | Second run on a different face, plus the `NO_MATCH` run | Kills any remaining hardcode suspicion |

Two production notes: increase terminal font size before recording, and pre-warm every model so there is no 40-second ONNX download on camera.

---

## 10. Failure modes and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Probe face not indexed on the open web | High | Pipeline returns NO_MATCH | Bluesky fallback provider; choose a public figure for the Lens demo |
| SerpApi free quota exhausted while debugging | **High** | Dead search path | **Cache every response to disk from commit #1**, replay from cache while iterating |
| Base Sepolia RPC or faucet flakes mid-demo | Medium | Anchor fails | Anvil mirror behind an env var; OTS path is independent |
| Canonicalisation drift | Medium | **Verify fails on stage** | Freeze schema day one, CI round-trip test, no floats |
| OTS still pending during recording | **Certain** | Looks incomplete | Stamp a bundle the day before, verify that one live; explain batching |
| Model download on camera | Medium | Dead air | Pre-warm all models, commit a warm-up script |
| Lens returns lookalikes only | Medium | False match | Margin rule + calibrated threshold; show rejections |
| C2PA cert hassle eats hours | Medium | Scope creep | It is the first thing cut |
| Judge asks "is this legal?" | High | Credibility | Consent gate, `--dry-run`, ethics section in README |

---

## 11. Ethics, legal, and why it affects your score

A face-search tool shipped in 2026 with no privacy story reads as careless, and India's DPDP Act makes this concrete rather than theoretical. Cheap mitigations, high perceived value:

- **No raw biometrics on chain.** Salted commitment only. Already designed in (§6.3)
- **`--consent-record` flag** writing a signed consent statement into the evidence bundle
- **`--dry-run`** that runs the full pipeline and anchors nothing
- **Domain allowlist** limited to public social platforms; no dating sites, no mugshot aggregators, no adult platforms
- **Rate limiting and no bulk mode.** The tool searches one face at a time by design
- **README ethics section**: intended use is consented self-search and provenance research; explicitly not surveillance
- **No ToS-violating providers**, which is a second, independent reason to reject the PimEyes and Yandex scraping routes

State the biometric-privacy reasoning out loud in the recording. It takes eight seconds and it signals seniority.

---

## 12. Requirement traceability

Fill this in as you build; put it in the README.

| Brief requirement | Where satisfied | Demo timestamp |
|---|---|---|
| Detect and encode a face | `pipeline/face/` — RetinaFace + ArcFace 512-d | 0:20–1:10 |
| Any detection/recognition library acceptable | InsightFace `buffalo_l`, documented in README | — |
| Search web using the face | `pipeline/search/google_lens.py`, `bing_visual.py` | 1:10–2:00 |
| At least one real matching social post | Live permalink opened in browser | 2:00–2:20 |
| Genuine search, not hardcoded | Raw provider JSON + candidate rejection table + `NO_MATCH` sample run + audit logs | 1:10–2:20, 3:55–4:10 |
| Upload post or hash to a blockchain | Base Sepolia registry + OTS Bitcoin mainnet | 2:20–3:05 |
| Tamper-evident record | keccak256 commitment, immutable mapping | 2:20–2:50 |
| **Re-verify against on-chain record** | `pipeline/chain/reverify.py`, 4 checks | 3:05–3:35 |
| Demonstrate tamper detection | Single-character edit → TAMPERED | 3:35–3:55 |
| No website | CLI only, deliberate | — |
| GitHub repo + README | This repo | — |
| README: what / how to run / which chain / limitations | README §§1–4 | — |
| Screen recording | 4-minute single take | — |

---

## 13. README outline

Judges read the README before the code. Structure it to answer their questions in order.

1. **What it does** — the three-stage pipeline in four sentences, plus one architecture diagram
2. **How to run** — prerequisites, `.env` setup, faucet links, `pip install`, then the exact commands for `scan`, `run-all`, `verify`. Include a keyless quickstart using only the Bluesky provider so a judge can run *something* in two minutes
3. **Which blockchain and why** — Base Sepolia (chainId 84532) with contract address and explorer link; Bitcoin mainnet via OpenTimestamps; why not Polygon Amoy; why not Ethereum Sepolia right now; EAS noted as the production path
4. **How verification works** — the four checks, and how to reproduce the tamper test yourself
5. **Accuracy and calibration** — pair set, ROC plot, chosen threshold, stated FMR, the margin rule
6. **Privacy and ethics** — no biometrics on chain, salted commitments, consent flag, allowlist, intended use
7. **Known limitations** — write these before a judge finds them:
   - Open-web coverage depends on the subject being indexed; a subject with no online presence returns `NO_MATCH`
   - The Bluesky provider searches a locally built corpus, not the whole web. It is a fallback, not the primary claim
   - Google Lens returns visual similarity, not face identity; accuracy depends on the local verification stage, which is why the ROC is published
   - OTS Bitcoin confirmation is batched and takes time; a fresh stamp is pending
   - The C2PA certificate is self-signed and not on the C2PA trust list
   - Base Sepolia is a testnet; records carry no economic finality. The Bitcoin anchor is the durable one
   - Threshold is calibrated on a small pair set, not a benchmark-scale evaluation
8. **Architecture notes** — the provider interface, why provider scores are recorded but never trusted
9. **Sample runs** — pointers into `runs/`, including the `NO_MATCH` case

---

## 14. Final answer, in one paragraph

Build **Solution 4**. The face layer is InsightFace `buffalo_l` with alignment and an anti-spoof gate. The search layer is a parallel fan-out across Google Lens and Bing for open-web reach, with a live Bluesky index as a fallback, all behind one `SearchProvider` interface, and every candidate re-verified locally with ArcFace so the accept decision is yours and never the provider's. The trust layer canonicalises the evidence, anchors a keccak256 commitment to a small custom contract on Base Sepolia for instant queryable proof, and independently stamps it to Bitcoin mainnet through OpenTimestamps for free permanence. The verifier performs four independent checks and can prove a negative. No raw biometric ever touches a chain. It beats the API-wrapper approach because you own and can explain the matching; it beats the pure crawler because it genuinely searches the open web; and it beats the plain reverse-image approach because it has no single point of failure and it maximises the requirement the brief actually grades — re-verification.

---

## 15. Open questions to resolve before hour zero

1. Who is the demo subject for the open-web path? Pick someone with heavy indexed presence and confirm Lens finds them **before** building around it.
2. Do you control a Bluesky account for the fallback demo? A real post from a real account is still a genuine search result — it just must not be hardcoded.
3. GPU available, or CPU only? Changes the crawl budget for the Bluesky index.
4. Team size? Contract work and search work are cleanly separable and can run in parallel.
5. Recording tool chosen and tested? Do a 30-second test capture early and check terminal legibility at 1080p.

---

## 16. Compliance note

Findings in this document were gathered from public web sources and **rephrased for compliance with licensing restrictions**. No source is quoted at length. All factual claims about pricing, API availability, faucet limits, and network deprecation reflect sources as of 4 September 2026 and should be re-checked before relying on them, since several of these changed during 2026.

---

## 17. Sources

**Face recognition and liveness**
- InsightFace — https://github.com/deepinsight/insightface
- Silent-Face-Anti-Spoofing, ONNX port — https://github.com/QingHeYang/Silent-Face-Anti-Spoofing-onnx

**Search providers**
- SerpApi Google Lens API — https://serpapi.com/google-lens-api
- SerpApi Lens upload walkthrough, free tier figure — https://serpapi.com/blog/uploading-images-and-searching-with-google-lens-via-serpapi/
- Bluesky API hosts and auth — https://docs.bsky.app/docs/advanced-guides/api-directory
- Bluesky firehose — https://docs.bsky.app/docs/advanced-guides/firehose
- Mastodon search API, auth requirement — https://docs.joinmastodon.org/methods/search/
- Mastodon public timelines — https://docs.joinmastodon.org/methods/timelines/
- FaceCheck.ID API — https://facecheck.id/en/topics/API
- Search4Faces API — https://search4faces.com/en/api.html
- Yandex scraping and captcha-on-200 behaviour — https://scrape.do/blog/yandex-scraping/

**Blockchain and testnets**
- Base network faucets — https://docs.base.org/docs/tools/network-faucets/
- Coinbase CDP onchain tools and faucets — https://docs.cdp.coinbase.com/onchain-tools/overview
- Polygon faucet retirement — https://docs.polygon.technology/tools/gas/matic-faucet/
- Polygon public RPC deprecation — https://forum.polygon.technology/t/deprecation-of-polygons-public-rpc-endpoints-mainnet-amoy/22014
- Sepolia / Glamsterdam timeline, ACDC #186 — https://etherworld.co/ethereum-protocol-update-acdc-186/
- Ethereum testnet status tracker — https://chaindrop.org/

**Provenance and attestation**
- OpenTimestamps — https://opentimestamps.org/
- python-opentimestamps — https://github.com/opentimestamps/python-opentimestamps
- OpenTimestamps client — https://github.com/opentimestamps/opentimestamps-client
- Ethereum Attestation Service — https://attest.org/
- EAS Python SDK — https://pypi.org/project/eas-sdk/
- C2PA Python binding — https://github.com/contentauth/c2pa-python
- C2PA open-source docs — https://opensource.contentauthenticity.org/docs/c2pa-python/
- Pinata free tier — https://pinata.cloud/blog/how-to-use-pinata-a-step-by-step-guide-for-beginners/

**Standards**
- RFC 8785, JSON Canonicalization Scheme — https://www.rfc-editor.org/rfc/rfc8785
