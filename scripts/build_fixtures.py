"""Builds search-provider test fixtures.

The SerpApi fixture is derived from a REAL cached response captured during
research (.cache/lens_probe/obama_portrait.json), trimmed to keep the repo
small while preserving the exact field shapes and a representative mix of
social / non-social domains. Using real captured data rather than a
hand-written mock means the parser is tested against what the API actually
returns, including its quirks.

The GCV fixture is constructed from the documented WEB_DETECTION schema,
because no GCV key exists yet (D-30: build against fixtures, wire the key
later). It deliberately includes the known edge case where an entry has
webEntities but no image arrays (A-08).

Usage:  python scripts/build_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHED_LENS = REPO_ROOT / ".cache" / "lens_probe" / "obama_portrait.json"
OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "search"

SOCIAL_HINTS = ("instagram.com", "facebook.com", "youtube.com", "x.com", "reddit.com")


def build_serpapi_fixture() -> dict:
    if not CACHED_LENS.exists():
        print(f"[skip] no cached Lens response at {CACHED_LENS}")
        return {}

    data = json.loads(CACHED_LENS.read_text(encoding="utf-8"))

    def keep_vm(m: dict) -> dict:
        return {
            k: m.get(k)
            for k in ("position", "title", "link", "source", "thumbnail", "image")
            if m.get(k) is not None
        }

    vms = data.get("visual_matches", [])
    # Keep every social-domain hit (the interesting ones) plus a few others
    # so the allowlist filter has both cases to exercise.
    social = [m for m in vms if any(h in (m.get("link") or "") for h in SOCIAL_HINTS)]
    other = [m for m in vms if m not in social][:6]
    kept_vms = [keep_vm(m) for m in (social + other)]

    def keep_org(o: dict) -> dict:
        return {
            k: o.get(k)
            for k in ("position", "title", "link", "source", "thumbnail")
            if o.get(k) is not None
        }

    orgs = data.get("organic_results", [])
    social_orgs = [o for o in orgs if any(h in (o.get("link") or "") for h in SOCIAL_HINTS)]
    kept_orgs = [keep_org(o) for o in (social_orgs + orgs[:3])]

    fixture = {
        "_fixture_note": (
            "Trimmed from a real SerpApi google_lens response captured "
            "2026-09-05. Field shapes unmodified."
        ),
        "related_content": [
            {"query": r.get("query")} for r in data.get("related_content", []) if r.get("query")
        ],
        "visual_matches": kept_vms,
        "organic_results": kept_orgs,
    }
    return fixture


def build_gcv_fixture() -> dict:
    """Schema per Google Cloud Vision WEB_DETECTION docs."""
    return {
        "_fixture_note": (
            "Constructed from the documented WEB_DETECTION schema. No GCV key "
            "available at authoring time (D-30). Shapes match the reference docs."
        ),
        "responses": [
            {
                "webDetection": {
                    "webEntities": [
                        {"entityId": "/m/02mjmr", "score": 0.921, "description": "Barack Obama"},
                        {"entityId": "/m/07t65", "score": 0.402, "description": "President"},
                        {"entityId": "/m/0d075m", "score": 0.311},  # no description on purpose
                    ],
                    "bestGuessLabels": [{"label": "barack obama", "languageCode": "en"}],
                    "pagesWithMatchingImages": [
                        {
                            "url": "https://www.instagram.com/p/Dc1RWQ9DhXe/",
                            "pageTitle": "A post shared on Instagram",
                            "fullMatchingImages": [
                                {"url": "https://scontent.cdninstagram.com/v/example_full.jpg"}
                            ],
                        },
                        {
                            "url": "https://www.reddit.com/r/Presidents/comments/1vewo5f/example/",
                            "pageTitle": "Discussion thread",
                            "partialMatchingImages": [
                                {"url": "https://preview.redd.it/example_partial.jpg"}
                            ],
                        },
                        {
                            # Known edge case: a page with NO image arrays at all.
                            "url": "https://en.wikipedia.org/wiki/Barack_Obama",
                            "pageTitle": "Barack Obama - Wikipedia",
                        },
                        {
                            # Non-social domain: must be scored but never reported.
                            "url": "https://www.britannica.com/biography/Barack-Obama",
                            "pageTitle": "Barack Obama | Britannica",
                            "fullMatchingImages": [
                                {"url": "https://cdn.britannica.com/example.jpg"}
                            ],
                        },
                    ],
                    "fullMatchingImages": [
                        {
                            "url": "https://upload.wikimedia.org/wikipedia/commons/8/8d/President_Barack_Obama.jpg"
                        }
                    ],
                    "visuallySimilarImages": [
                        {"url": "https://example.test/similar_but_different_person.jpg"}
                    ],
                }
            }
        ],
    }


def build_gcv_entities_only_fixture() -> dict:
    """A-08: GCV sometimes returns ONLY webEntities/bestGuessLabels with every
    image array absent. Must be handled as a valid zero-candidate result."""
    return {
        "_fixture_note": "A-08 edge case: labels present, all image arrays absent.",
        "responses": [
            {
                "webDetection": {
                    "webEntities": [
                        {"entityId": "/m/02mjmr", "score": 0.88, "description": "Barack Obama"}
                    ],
                    "bestGuessLabels": [{"label": "barack obama", "languageCode": "en"}],
                }
            }
        ],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    written = []
    serp = build_serpapi_fixture()
    if serp:
        p = OUT_DIR / "serpapi_lens_obama.json"
        p.write_text(json.dumps(serp, indent=2), encoding="utf-8")
        written.append((p, len(serp.get("visual_matches", [])), len(serp.get("organic_results", []))))

    for name, builder in [
        ("gcv_web_detection_obama.json", build_gcv_fixture),
        ("gcv_web_detection_entities_only.json", build_gcv_entities_only_fixture),
    ]:
        p = OUT_DIR / name
        p.write_text(json.dumps(builder(), indent=2), encoding="utf-8")
        written.append((p, None, None))

    for p, nvm, norg in written:
        extra = f"  visual_matches={nvm} organic_results={norg}" if nvm is not None else ""
        print(f"wrote {p.relative_to(REPO_ROOT)}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
