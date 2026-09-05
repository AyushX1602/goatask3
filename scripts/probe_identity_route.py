"""Research probe: validate the identity-resolved search route end to end.

Tests the chain that the revised architecture depends on:
    face photo
      -> [already proven] Lens gives a NAME via related_content
      -> app.bsky.actor.searchActors(name)   : resolve real accounts
      -> app.bsky.feed.getAuthorFeed(handle) : fetch that account's real posts
      -> our ArcFace core                    : verify the face actually matches
      -> a real social post permalink

Nothing here is hardcoded to a result: the name comes from search output,
the account comes from the platform's own actor search, the post comes from
the platform's feed, and the accept/reject comes from our own embedding
comparison. This script only checks whether the plumbing yields a match.

Usage:
    python scripts/probe_identity_route.py --name "Barack Obama" \
        --probe tests/fixtures/obama1.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.face.align import align  # noqa: E402
from pipeline.face.detect import FaceDetector  # noqa: E402
from pipeline.face.embed import FaceEmbedder  # noqa: E402

APPVIEW = "https://public.api.bsky.app"
HEADERS = {"User-Agent": "face-chain-verify/0.1 (research probe)"}


def search_actors(name: str, limit: int = 5) -> list[dict]:
    r = requests.get(
        f"{APPVIEW}/xrpc/app.bsky.actor.searchActors",
        params={"q": name, "limit": limit},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("actors", [])


def author_images(handle: str, limit: int = 60) -> list[dict]:
    """Returns [{image_url, permalink, text, published_at}] for image posts."""
    out: list[dict] = []
    cursor = None
    fetched = 0
    while fetched < limit:
        params = {"actor": handle, "limit": min(50, limit - fetched)}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(
            f"{APPVIEW}/xrpc/app.bsky.feed.getAuthorFeed",
            params=params,
            headers=HEADERS,
            timeout=20,
        )
        if r.status_code != 200:
            break
        data = r.json()
        items = data.get("feed", [])
        if not items:
            break
        for it in items:
            fetched += 1
            post = it.get("post", {})
            embed = post.get("embed", {}) or {}
            if embed.get("$type") != "app.bsky.embed.images#view":
                continue
            uri = post.get("uri", "")
            rkey = uri.rsplit("/", 1)[-1] if uri else ""
            author = post.get("author", {})
            for img in embed.get("images", []):
                url = img.get("fullsize") or img.get("thumb")
                if not url:
                    continue
                out.append(
                    {
                        "image_url": url,
                        "permalink": f"https://bsky.app/profile/{author.get('handle','')}/post/{rkey}",
                        "text": (post.get("record", {}) or {}).get("text", ""),
                        "published_at": (post.get("record", {}) or {}).get("createdAt", ""),
                        "handle": author.get("handle", ""),
                    }
                )
        cursor = data.get("cursor")
        if not cursor:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="identity string, as Lens would give us")
    ap.add_argument("--probe", required=True, help="path to probe face image")
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    det = FaceDetector()
    emb = FaceEmbedder()

    probe_img = cv2.imread(args.probe)
    if probe_img is None:
        print(f"could not read probe {args.probe}")
        return 1
    pfaces = det.detect(probe_img)
    if not pfaces:
        print("no face in probe")
        return 1
    probe_vec = emb.embed(align(probe_img, pfaces[0].kps5)).vec
    print(f"probe: {args.probe}  (det {pfaces[0].det_score:.3f})\n")

    actors = search_actors(args.name)
    print(f"=== searchActors({args.name!r}) -> {len(actors)} accounts ===")
    for a in actors:
        print(f"  {a['handle']:38s} | {a.get('displayName','')}")
    print()

    results = []
    for a in actors:
        handle = a["handle"]
        imgs = author_images(handle, args.limit)
        print(f"--- {handle}: {len(imgs)} images in last ~{args.limit} posts ---")
        for rec in imgs:
            try:
                resp = requests.get(rec["image_url"], headers=HEADERS, timeout=15)
                resp.raise_for_status()
                arr = np.frombuffer(resp.content, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    continue
                faces = det.detect(img)
                if not faces:
                    continue
                best = max(
                    float(np.dot(emb.embed(align(img, f.kps5)).vec, probe_vec))
                    for f in faces
                )
                results.append((best, len(faces), rec))
            except requests.RequestException:
                continue

    results.sort(key=lambda t: -t[0])
    print(f"\n{'=' * 70}\nSCORED {len(results)} candidate images from real posts\n{'=' * 70}")
    for score, nfaces, rec in results[:15]:
        verdict = "MATCH-CANDIDATE" if score >= 0.42 else "below-threshold"
        print(f"  {score:.4f}  [{nfaces} face(s)]  {verdict}")
        print(f"          {rec['permalink']}")
        print(f"          {rec['text'][:80]!r}  @ {rec['published_at']}")

    above = [r for r in results if r[0] >= 0.42]
    print(f"\nRESULT: {len(above)} image(s) at/above the 0.42 placeholder threshold")
    if above:
        print(f"  best: {above[0][0]:.4f} -> {above[0][2]['permalink']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
