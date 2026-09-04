# PRD — Face Scan → Social Match → Blockchain Proof

**Project codename:** `face-chain-verify`
**Owner:** Ayush
**Created:** 4 September 2026
**Window:** 3 days
**Decision record:** see [`ANALYSIS.md`](./ANALYSIS.md) — this PRD assumes Solution 4 was chosen there

---

## 1. Problem

A hackathon brief asks for a working pipeline that takes a face scan, finds a matching real social media post by searching the web, and anchors the discovered evidence on a blockchain so it can be re-verified later.

The brief adds one hard constraint that shapes everything: the search must be a **genuine search step, not a hardcoded or pre-picked result.**

## 2. What we are building

A command-line pipeline. Three stages, one command to run the whole thing, one command to verify a past result.

```
face scan  →  web/social search  →  evidence bundle  →  blockchain anchor
                                                             │
                                          verify (re-check against chain)
```

No web UI. No hosted service. No deployment. The pipeline **is** the product.

## 3. Who we are building for

The judge watching a screen recording. Every design decision is scored against one question:

> Can a sceptical viewer convince themselves this is real, and not a demo with a lookup table behind it?

This is the primary user. There is no second user in the 3-day window.

## 4. Goals

| # | Goal | Why it matters |
|---|---|---|
| G1 | Detect and encode a face from a live webcam capture | Brief requirement 1 |
| G2 | Find at least one real, live social media post matching that face | Brief requirement 2 |
| G3 | Make the search *visibly* genuine on camera | The brief's anti-cheat clause; the goal that decides the grade |
| G4 | Anchor a tamper-evident commitment of the evidence on a blockchain | Brief requirement 3 |
| G5 | Re-verify a past result against the on-chain record, and prove a negative | The graded part of requirement 3 |
| G6 | Runnable by a judge who clones the repo, with no paid account | Supports "how to run it" |
| G7 | Never publish or persist a recoverable biometric template | Ethics, legal, and a scoring differentiator |

## 5. Non-goals

Explicitly out of scope. Do not build these, even if there is time.

- Web UI, dashboard, or hosted API
- User accounts, auth, multi-tenancy
- Production deployment or CI/CD beyond a lint + test job
- Mobile app
- Real-time video stream processing (single frame capture is the input)
- Bulk or batch face search — deliberately excluded, see `rules.md` R-05
- Training or fine-tuning any model
- Mainnet EVM transactions with real value

## 6. Success criteria

The project ships when all of these are demonstrably true. "Demonstrably" means executed and recorded, not assumed.

| # | Criterion | How it is proven |
|---|---|---|
| S1 | A live webcam capture produces a 512-d face embedding | Console output, on camera |
| S2 | A printed photo held to the camera is rejected by the liveness gate | On camera |
| S3 | The pipeline returns a real social post URL that opens in a browser and shows a timestamp | On camera |
| S4 | Every candidate examined is printed with its similarity score and accept/reject reason | On camera |
| S5 | At least one run legitimately returns `NO_MATCH` | Committed in `runs/` |
| S6 | Raw third-party API responses are visible during the run | On camera |
| S7 | An evidence commitment is written to a blockchain and the tx is viewable on a public explorer | On camera |
| S8 | `verify` on an untouched bundle passes every check | On camera |
| S9 | `verify` on a bundle with one character changed reports `TAMPERED` | On camera |
| S10 | A judge can run the pipeline with zero API keys via the Bluesky provider | README quickstart |
| S11 | The accept threshold is derived from a committed ROC curve, not hardcoded | `calibration/` |
| S12 | No embedding, and no reversible derivative of one, appears in any committed artifact or on chain | Code review + `rules.md` R-01 |

## 7. Requirement traceability

Maps the brief's literal wording to where we satisfy it. Keep this current; it goes in the README.

| Brief requirement | Component | Status |
|---|---|---|
| Detect and encode a face from an input image | `pipeline/face/` | not started |
| Any face detection/recognition library acceptable | YuNet + ArcFace via onnxruntime | not started |
| Use the face to search the web | `pipeline/search/google_lens.py` | not started |
| Find at least one real matching social post | `pipeline/search/bluesky.py` + Lens | not started |
| Genuine search, not hardcoded | audit log, candidate table, `NO_MATCH` run | not started |
| Upload post or hash to a blockchain | `pipeline/chain/evm.py` | not started |
| Create a verifiable, tamper-evident record | `FaceEvidenceRegistry.sol` | not started |
| Demonstrate re-verifying against the on-chain record | `pipeline/chain/reverify.py` | not started |
| No website required | CLI only, by design | n/a |
| GitHub repo with full source | this repo | not started |
| README: what / how to run / which chain / limitations | `README.md` | not started |
| Screen recording of the pipeline end to end | `docs/recording-script.md` | not started |

## 8. Constraints

- **3 days.** Reliability beats ambition. One submission, no resubmissions.
- **Windows 11, Python 3.12.10.** Every dependency must have a working wheel. No source builds requiring a C compiler.
- **Judge must be able to run it.** At least one search path with zero API keys and zero paid accounts.
- **Single-take recording.** Any component that fails intermittently is a liability, not a feature.
- **Blockchain is secondary.** Confirmed by the owner. Budget half a day, not more.

## 9. Assumptions

| # | Assumption | Risk if wrong |
|---|---|---|
| A1 | The demo subject for the open-web path has a heavy indexed web presence | Lens returns nothing; fall back to Bluesky provider |
| A2 | We control a Bluesky account, or can find a suitable public post | Fallback demo path weakens |
| A3 | Bluesky public read endpoints remain unauthenticated | Would need an app password; free but adds a step |
| A4 | SerpApi free tier is sufficient for dev + demo | Mitigated by mandatory response caching, `rules.md` R-04 |
| A5 | CPU inference on 16 cores is fast enough for the crawl budget | Reduce corpus size, or enable CUDA on the RTX 4050 |

## 10. Known limitations to disclose

Written now, before a judge finds them. These go in the README verbatim.

1. Open-web coverage depends on the subject being indexed. A subject with no online presence correctly returns `NO_MATCH`.
2. The Bluesky provider searches a corpus we build at runtime, not the whole web. It is a fallback, not the primary open-web claim.
3. Google Lens returns visual similarity, not face identity. Accuracy comes from our local verification stage, which is why the ROC curve is published.
4. The similarity threshold is calibrated on a small labelled pair set, not a benchmark-scale evaluation.
5. Base Sepolia is a testnet. Records carry no economic finality.
6. Face recognition has documented demographic accuracy disparities. Our small calibration set cannot characterise them.

## 11. Open questions

Resolve before the corresponding phase starts.

| # | Question | Blocks | Owner |
|---|---|---|---|
| Q1 | Who is the demo subject for the open-web path? | Phase 5 | Ayush |
| Q2 | Do we control a Bluesky account for the fallback demo? | Phase 3 | Ayush |
| Q3 | Team size — can contract work run in parallel? | Phase 10 | Ayush |
| Q4 | Recording tool chosen and legibility tested at 1080p? | Phase 13 | Ayush |
| Q5 | Enable CUDA for onnxruntime, or stay CPU? | Phase 3 | decide after Phase 1 timing |
