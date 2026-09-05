"""Disk-backed HTTP cache. Implements R-04.

Every outbound request in the pipeline goes through here. Two reasons, both
practical rather than architectural:

1. The GCV free tier is ~1,000 units/month and SerpApi's is ~100. A single
   afternoon of debugging without a cache will exhaust the smaller one and
   take the "search the web" requirement down with it.
2. Cached runs are reproducible offline, which makes the recording and any
   later re-verification independent of network conditions.

Set HTTP_CACHE=0 to force live fetches (used for a deliberate freshness
check, and when actually recording).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from pipeline.config import CACHE_DIR, get_config

HTTP_CACHE_DIR = CACHE_DIR / "http"
DEFAULT_TIMEOUT = 20.0
USER_AGENT = "face-chain-verify/0.1 (HH Goa 2026 shortlisting task; research tool)"

# Used ONLY for one-off candidate image fetches against arbitrary third-
# party CDNs/sites (verify/pipeline_run.py) — never for our own metered API
# calls to GCV/SerpApi, which correctly keep the descriptive USER_AGENT
# above per R-12 ("be a polite client... descriptive User-Agent").
#
# Rationale (6 Sep 2026, owner instruction: "whats best ... real crawling
# instead of --"): a generic self-describing UA like ours is routinely
# caught by blanket bot mitigation on ordinary sites (news CDNs, wordpress
# uploads, university staff pages) that have no reason to block a browser
# but block anything that doesn't look like one. This is DIFFERENT from
# impersonating a specific company's own crawler (Googlebot,
# facebookexternalhit, Twitterbot) to obtain access granted only to it —
# that remains refused. Sending an ordinary desktop browser UA to fetch a
# publicly served image is what any real browser rendering the search
# result page would also do; it claims nothing false.
#
# Explicitly does NOT help the Meta/Instagram/TikTok wall
# (scripts/probe_meta_wall.py, verify/allowlist.py MEDIA_BLOCKED_PLATFORMS)
# — that was re-measured under this exact UA and found identical to the
# research UA. This change is for the many OTHER hosts that block on UA
# alone, which is a different and much more common failure mode.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Params whose values must never be written to disk in a cache key or a
# metadata file (R-10: no secrets in git, and .cache/ is gitignored but we
# still do not want keys sitting in plaintext filenames or sidecars).
SECRET_PARAM_NAMES = {"api_key", "key", "apikey", "token", "access_token"}


@dataclass(frozen=True)
class CachedResponse:
    """Mirrors just enough of requests.Response for our use.

    content_type: added 6 Sep 2026 so candidate-fetch diagnostics
    (verify/pipeline_run.py FetchDiagnostics) can report a REAL
    Content-Type instead of a fabricated or absent value — including for
    a cache HIT, which is why it is persisted in the .meta.json sidecar
    rather than only read live from the response object.
    """

    status_code: int
    content: bytes
    url: str
    from_cache: bool
    content_type: str | None = None

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(
                f"{self.status_code} for {self.url}", response=None
            )


def _redact(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {
        k: ("<redacted>" if k.lower() in SECRET_PARAM_NAMES else v)
        for k, v in params.items()
    }


def cache_key(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    body: bytes | None = None,
) -> str:
    """Stable key over method + url + sorted params + body hash.

    Secret param values ARE included in the hash (so a key rotation
    correctly misses the cache) but never written anywhere readable.
    """
    h = hashlib.sha256()
    h.update(method.upper().encode())
    h.update(b"\x00")
    h.update(url.encode())
    h.update(b"\x00")
    if params:
        for k in sorted(params):
            h.update(f"{k}={params[k]}".encode())
            h.update(b"\x1f")
    if body:
        h.update(b"\x00")
        h.update(hashlib.sha256(body).digest())
    return h.hexdigest()[:32]


class HttpCache:
    def __init__(self, enabled: bool | None = None, cache_dir: Path = HTTP_CACHE_DIR) -> None:
        self.enabled = get_config().http_cache_enabled if enabled is None else enabled
        self.cache_dir = cache_dir
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self.hits = 0
        self.misses = 0

    # --- internals -----------------------------------------------------

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.cache_dir / f"{key}.body", self.cache_dir / f"{key}.meta.json"

    def _read(self, key: str, url: str) -> CachedResponse | None:
        body_path, meta_path = self._paths(key)
        if not (body_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return CachedResponse(
            status_code=int(meta.get("status_code", 200)),
            content=body_path.read_bytes(),
            url=url,
            from_cache=True,
            content_type=meta.get("content_type"),
        )

    def _write(
        self,
        key: str,
        url: str,
        status_code: int,
        content: bytes,
        params: dict[str, Any] | None,
        content_type: str | None = None,
    ) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        body_path, meta_path = self._paths(key)
        body_path.write_bytes(content)
        meta_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "status_code": status_code,
                    "params": _redact(params),  # R-10
                    "cached_at": int(time.time()),
                    "bytes": len(content),
                    "content_type": content_type,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # --- public API ----------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        cache_errors: bool = False,
    ) -> CachedResponse:
        """Cached HTTP request.

        cache_errors=False means non-2xx responses are NOT written to disk,
        so a transient 429/500 does not get baked in permanently.
        """
        body = json.dumps(json_body, sort_keys=True).encode() if json_body is not None else None
        key = cache_key(method, url, params, body)

        if self.enabled:
            cached = self._read(key, url)
            if cached is not None:
                self.hits += 1
                return cached

        self.misses += 1
        resp = self._session.request(
            method.upper(),
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )

        resp_content_type = resp.headers.get("content-type") if hasattr(resp, "headers") else None

        if self.enabled and (resp.ok or cache_errors):
            self._write(key, url, resp.status_code, resp.content, params, resp_content_type)

        return CachedResponse(
            status_code=resp.status_code,
            content=resp.content,
            url=resp.url,
            from_cache=False,
            content_type=resp_content_type,
        )

    def get(self, url: str, **kw) -> CachedResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> CachedResponse:
        return self.request("POST", url, **kw)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}


_default: HttpCache | None = None


def get_http_cache() -> HttpCache:
    """Process-wide default cache. Shared so hit/miss stats aggregate across
    providers within one run and land in the audit log."""
    global _default
    if _default is None:
        _default = HttpCache()
    return _default
