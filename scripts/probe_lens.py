"""One-off research probe: does SerpApi Google Lens return anything usable
for a FACE query?

This exists to resolve memory.md A-07 empirically instead of guessing —
specifically:
  1. Does Lens identify a person from a face photo at all?
  2. Do any returned links land on SOCIAL MEDIA domains (what the brief
     requires), or only news/wiki/stock?
  3. Is there a usable identity signal (a name) in titles / related_content?

Every response is cached to .cache/lens_probe/ so we never spend the same
SerpApi credit twice (R-04). Free tier is ~100 searches/month.

Usage:
    python scripts/probe_lens.py --url <public image url> --label <name>
    python scripts/probe_lens.py --replay <label>      # read from cache only
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import get_config  # noqa: E402

CACHE_DIR = REPO_ROOT / ".cache" / "lens_probe"

SOCIAL_DOMAINS = {
    "bsky.app", "mastodon.social", "x.com", "twitter.com", "instagram.com",
    "facebook.com", "linkedin.com", "reddit.com", "youtube.com", "tiktok.com",
    "threads.net", "pinterest.com", "tumblr.com",
}


def registrable(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def fetch(label: str, image_url: str, api_key: str) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{label}.json"

    if cache_file.exists():
        print(f"[cache] replaying {cache_file.name} (0 credits spent)")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    print(f"[live]  querying SerpApi Google Lens for {label} (1 credit)")
    resp = requests.get(
        "https://serpapi.com/search",
        params={"engine": "google_lens", "url": image_url, "api_key": api_key},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[cache] saved -> {cache_file}")
    return data


def analyse(label: str, data: dict) -> None:
    print(f"\n{'=' * 66}\nANALYSIS: {label}\n{'=' * 66}")

    if "error" in data:
        print(f"  API ERROR: {data['error']}")
        return

    print(f"  top-level keys: {sorted(data.keys())}")

    vm = data.get("visual_matches", []) or []
    org = data.get("organic_results", []) or []
    rel = data.get("related_content", []) or []

    print(f"\n  visual_matches:   {len(vm)}")
    print(f"  organic_results:  {len(org)}")
    print(f"  related_content:  {len(rel)}")

    # Q3: identity signal
    if rel:
        print("\n  --- related_content queries (IDENTITY SIGNAL) ---")
        for r in rel[:10]:
            print(f"    * {r.get('query')}")

    if data.get("knowledge_graph"):
        print(f"\n  --- knowledge_graph present ---")
        print(f"    {json.dumps(data['knowledge_graph'])[:400]}")

    # Q2: social domains
    all_links = [(m.get("link", ""), m.get("title", ""), "visual") for m in vm]
    all_links += [(o.get("link", ""), o.get("title", ""), "organic") for o in org]

    domains = Counter(registrable(l) for l, _, _ in all_links if l)
    print(f"\n  --- top 15 domains returned ---")
    for dom, n in domains.most_common(15):
        flag = "  <-- SOCIAL" if dom in SOCIAL_DOMAINS else ""
        print(f"    {n:3d}  {dom}{flag}")

    social_hits = [(l, t, src) for l, t, src in all_links if registrable(l) in SOCIAL_DOMAINS]
    print(f"\n  *** SOCIAL MEDIA HITS: {len(social_hits)} of {len(all_links)} links ***")
    for link, title, src in social_hits[:12]:
        print(f"    [{src}] {registrable(link)}")
        print(f"           {title[:90]}")
        print(f"           {link[:110]}")

    # Q1: are titles carrying a name?
    print(f"\n  --- first 8 visual_match titles (name signal) ---")
    for m in vm[:8]:
        has_img = "img" if m.get("thumbnail") or m.get("image") else "NO-IMG"
        print(f"    [{has_img}] {(m.get('title') or '')[:88]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--label", required=True)
    ap.add_argument("--replay", action="store_true")
    args = ap.parse_args()

    cache_file = CACHE_DIR / f"{args.label}.json"

    if args.replay:
        if not cache_file.exists():
            print(f"no cached response for label '{args.label}'")
            return 1
        analyse(args.label, json.loads(cache_file.read_text(encoding="utf-8")))
        return 0

    key = get_config().serpapi_key
    if not key:
        print("SERPAPI_KEY not set in .env")
        return 1
    if not args.url:
        print("--url required unless --replay")
        return 1

    analyse(args.label, fetch(args.label, args.url, key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
