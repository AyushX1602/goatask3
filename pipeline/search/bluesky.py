"""Bluesky live search provider. See docs/design.md 2.2.

Zero API keys, zero signup — this is the provider that makes the
zero-key quickstart (docs/prd.md S10) possible.

Verified live against the real API on 5 Sep 2026 (docs/memory.md A-01/A-02):
  - public.api.bsky.app read endpoints work with no authentication.
  - app.bsky.feed.searchPosts returns 403 unauthenticated — NOT used here.
  - app.bsky.feed.getAuthorFeed / getFeed already return ready-made
    thumb/fullsize CDN URLs on image embeds, so no URL construction is
    needed (better than the A-02 assumption in docs/design.md).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import cv2
import numpy as np
import requests

from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.search.base import Candidate

APPVIEW = "https://public.api.bsky.app"
USER_AGENT = "face-chain-verify/0.1 (hackathon research tool; local demo only)"

# Popular, high-volume feed generators to seed the crawl from — no keyword
# search needed (and searchPosts needs auth anyway), we just want "posts
# with images", so we walk a handful of open feeds.
DEFAULT_SEED_FEEDS = [
    "at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot",
]


@dataclass
class PostRef:
    permalink: str
    author_handle: str
    author_did: str
    author_display: str
    text: str
    published_at: str
    image_url: str


class FaceIndex:
    """In-memory brute-force cosine index. See docs/design.md 2.2 — unnecessary
    to use FAISS below ~10^5 vectors (docs/architecture.md 5, D-05)."""

    def __init__(self) -> None:
        self._vecs: list[np.ndarray] = []
        self.meta: list[PostRef] = []

    def add(self, vec: np.ndarray, ref: PostRef) -> None:
        self._vecs.append(vec)
        self.meta.append(ref)

    def __len__(self) -> int:
        return len(self._vecs)

    def query(self, probe_vec: np.ndarray, k: int = 20) -> list[tuple[PostRef, float]]:
        if not self._vecs:
            return []
        mat = np.stack(self._vecs)  # (N, 512)
        scores = mat @ probe_vec  # (N,) — L2-normalised vectors, dot = cosine
        order = np.argsort(-scores)[:k]
        return [(self.meta[i], float(scores[i])) for i in order]


class BlueskyProvider:
    name = "bluesky"
    requires_credentials = False

    def __init__(
        self,
        crawl_limit: int = 2000,
        seed_feeds: list[str] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.crawl_limit = crawl_limit
        self.seed_feeds = seed_feeds or DEFAULT_SEED_FEEDS
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self.index = FaceIndex()
        self.crawled_at: float | None = None

    def available(self) -> bool:
        return True  # no credentials required, ever

    # --- crawl ---------------------------------------------------------

    def _fetch_feed_page(self, feed_uri: str, cursor: str | None) -> dict:
        params = {"feed": feed_uri, "limit": 50}
        if cursor:
            params["cursor"] = cursor
        resp = self._session.get(
            f"{APPVIEW}/xrpc/app.bsky.feed.getFeed", params=params, timeout=self.timeout_s
        )
        resp.raise_for_status()
        return resp.json()

    def _collect_image_refs(self) -> list[PostRef]:
        """Walks feed pages (sequential — cursor-chained, cheap: ~3s/50
        posts measured live) and returns PostRef metadata for every post
        with a viewable image, up to crawl_limit posts. Does NOT fetch
        image bytes — that happens concurrently in _fetch_images, since
        per-image fetches (~0.5-1s each measured live) are the actual
        bottleneck and are independent of each other."""
        refs: list[PostRef] = []
        seen = 0
        for feed_uri in self.seed_feeds:
            cursor = None
            while seen < self.crawl_limit:
                try:
                    page = self._fetch_feed_page(feed_uri, cursor)
                except requests.RequestException:
                    break

                items = page.get("feed", [])
                if not items:
                    break

                for item in items:
                    post = item.get("post", {})
                    embed = post.get("embed", {})
                    images = embed.get("images") if embed.get("$type") == "app.bsky.embed.images#view" else None
                    if not images:
                        continue

                    record = post.get("record", {})
                    author = post.get("author", {})
                    uri = post.get("uri", "")
                    rkey = uri.rsplit("/", 1)[-1] if uri else ""
                    handle = author.get("handle", "")
                    permalink = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else ""

                    for img in images:
                        thumb_url = img.get("thumb")
                        if not thumb_url:
                            continue
                        refs.append(
                            PostRef(
                                permalink=permalink,
                                author_handle=handle,
                                author_did=author.get("did", ""),
                                author_display=author.get("displayName", handle),
                                text=record.get("text", ""),
                                published_at=record.get("createdAt", ""),
                                image_url=thumb_url,
                            )
                        )

                    seen += 1
                    if seen >= self.crawl_limit:
                        return refs

                cursor = page.get("cursor")
                if not cursor:
                    break
        return refs

    def _fetch_one_image(self, ref: PostRef) -> tuple[PostRef, bytes] | None:
        try:
            resp = self._session.get(ref.image_url, timeout=self.timeout_s)
            resp.raise_for_status()
            return ref, resp.content
        except requests.RequestException:
            return None

    def _iter_image_posts(self, max_workers: int = 16):
        """Yields (PostRef, image_bytes) for posts with a viewable image.
        Image fetches run concurrently (R-12: capped worker pool, still a
        polite client — not one request per detected face, one per post)."""
        refs = self._collect_image_refs()
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(self._fetch_one_image, ref) for ref in refs]
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    yield result

    def crawl(self, detector: FaceDetector, embedder: FaceEmbedder) -> int:
        """Populates self.index. Returns the number of faces indexed.
        Always call this before search() the first time in a process."""
        count = 0
        for ref, img_bytes in self._iter_image_posts():
            arr = np.frombuffer(img_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            faces = detector.detect(img)
            for face in faces:
                try:
                    crop = align(img, face.kps5)
                    emb = embedder.embed(crop)
                except Exception:
                    continue
                self.index.add(emb.vec, ref)
                count += 1
        self.crawled_at = time.time()
        return count

    # --- SearchProvider interface ---------------------------------------

    def search(self, search_image_bytes: bytes, probe_vec: np.ndarray) -> list[Candidate]:
        hits = self.index.query(probe_vec, k=20)
        candidates = []
        for ref, _provider_similarity in hits:
            candidates.append(
                Candidate(
                    image_url=ref.image_url,
                    page_url=ref.permalink,
                    source=self.name,
                    provider_score=None,  # R-03: never trust this, even our own index's number
                    raw={
                        "author_handle": ref.author_handle,
                        "author_did": ref.author_did,
                        "published_at": ref.published_at,
                    },
                    post_meta={
                        "platform": "bluesky",
                        "author_handle": ref.author_handle,
                        "author_display": ref.author_display,
                        "author_did": ref.author_did,
                        "text": ref.text,
                        "published_at": ref.published_at,
                        "permalink": ref.permalink,
                    },
                )
            )
        return candidates
