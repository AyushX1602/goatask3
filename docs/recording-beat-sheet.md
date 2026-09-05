# Recording beat sheet (G6)

What I can prepare: this script, the warmup check, and confirmation that
every step below has already been executed live at least once this
session. What I cannot do: hold the camera, choose the subjects, or press
record. This file is the handoff.

Run `python scripts/warmup.py` immediately before starting. Fix anything
it flags, then set `HTTP_CACHE=0` for the take itself (it will tell you to).

## Subjects (per the methodology decided this session — see memory.md §3o/§3p)

Never sample random strangers. Use, in order:

1. **A consenting teammate** with a real GitHub/X/LinkedIn presence — the
   strongest genuineness proof, because there is no public name for GCV to
   leak into the result. This is the beat that answers "how do we know this
   isn't hardcoded" better than any public-figure run can.
2. **A public figure** (e.g. `srk.jpg`, already in the repo, or any similar
   subject with a heavy indexed presence) — satisfies the brief's literal
   "social media post" wording and shows multi-platform corroboration.
3. **One deliberately synthetic/AI-generated face** — a designed,
   publishable `NO_MATCH`. Confirms the pipeline never manufactures an
   identity for a face that has none, rather than apologising for a
   negative result.

Do not substitute a real private individual found by accident for any of
these three. If a stranger's face turns up as a genuine positive during
rehearsal, do not use it, do not screenshot it, do not save the photo —
see the consent incidents in memory.md §3m/§3p for exactly why this
matters and how easily it slips in.

## Beat by beat

**1. Face scan (teammate, subject 1).** Upload the photo. Show the aligned
crop, detector confidence, and the liveness badge rendering
`N/A — provenance unverified` for an upload — say out loud that this is
correct and deliberate (an anti-spoof model proves nothing about an
uploaded file's provenance; claiming `LIVE` here would be a false
assurance, R-22).

**2. Search.** Click "Search for matches". While it runs, note on camera
that this is a live call to Google Cloud Vision's `WEB_DETECTION`, not a
lookup table. Once results land, open the diagnostics table:
- Point at a row with `HTTP 403` or `HTTP 200 · text/html` in place of a
  score, and say why — that platform (Instagram/Facebook/TikTok) refused
  or served a stub instead of a photo, verified independently this
  session with three different client profiles including a browser UA.
- Point at the `match_kind` column and explain it's the provider's own
  claim, never used to decide anything (R-03) — our own ArcFace score is
  what decides.
- If the headline result differs from the highest raw score in the table,
  point that out and explain the citability rule: an openable post is
  preferred over a bare CDN image URL, so the citation opens something a
  viewer can actually visit.

**3. Open the cited post in a browser.** Click through from the table.
Show it's a real, live page.

**4. Anchor.** Click "Anchor on chain" in the new "3. Blockchain" panel.
Show the returned tx hash, block number, and contract address.

**5. Verify — PASS.** Click "Verify". Show the `PASS` badge and the
recomputed hash matching the anchored one.

**6. Tamper — TAMPERED.** Click "Tamper & re-verify". Show the `TAMPERED`
badge and say explicitly that this button only mutates a throwaway copy —
the real evidence file on disk never changes (this is tested and was
verified live this session).

**7. Second subject (public figure).** Repeat scan -> search quickly.
Highlight multiple platforms in the candidate table (X, YouTube, etc.) and
any `corroborating` rows — several independent sources agreeing is
stronger evidence than one hit.

**8. Third subject (synthetic face).** Repeat scan -> search. Let it land
on `NO_MATCH`. Say the line: *"A hardcoded system never fails to find a
match — this one just did, correctly."*

## What NOT to do on camera

- Do not use `HTTP_CACHE=1` for the actual take — a replayed run is
  checkable in `audit.json` and reads as canned.
- Do not use any photo of a private individual who has not explicitly
  consented, even if it produces the most dramatic result.
- Do not claim `LIVE` liveness for an upload.
- Do not present a `corroborating` row as a rejection, or a `match_kind`
  value as part of the accept decision — both are R-03 violations if
  mis-narrated, even if the code itself is correct.

## Already verified live this session (nothing here is untested)

- Full anchor -> verify(PASS) -> tamper(TAMPERED) -> confirm-file-untouched
  cycle, both via the CLI and via the UI's HTTP API.
- Citability headline selection on a real multi-platform result.
- Resolver cascade recovering a candidate via OpenGraph.
- Real diagnostics rendering for platform-blocked and fetch-failed rows.
- The Meta/Instagram/TikTok wall confirmed identical under three UA
  profiles, including a browser UA (`scripts/probe_meta_wall.py`).
