"""SSRF hardening and payload validation for outbound candidate fetches.

Implements R-29 (SSRF protection) and R-24 (reject-unsafe-url taxonomy).
Validates schemes, ensures hosts resolve only to public IP addresses,
verifies image magic bytes, bounds redirect hops, and streams responses
with a strict size cap.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from typing import Any

import requests

from pipeline.cache.http_cache import DEFAULT_TIMEOUT, HttpCache

# Maximum size for candidate images (10 MB)
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Maximum redirect hops allowed
DEFAULT_MAX_REDIRECTS = 3


class UnsafeUrlError(ValueError):
    """Raised when a candidate URL fails security checks."""

    def __init__(self, message: str, *, cause: str, url: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause  # "scheme" | "internal-address" | "not-an-image" | "size-cap"
        self.url = url


def is_prohibited_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if ip is private, loopback, link-local, multicast,
    reserved, unspecified, or non-global.

    Handles IPv4-mapped IPv6 (::ffff:0:0/96) and NAT64 translation
    prefixes (64:ff9b::/96 and 64:ff9b:1::/48) by checking the embedded
    IPv4 address against private/loopback/link-local ranges.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_prohibited_ip(ip.ipv4_mapped)
        nat64_wkp = ipaddress.IPv6Network("64:ff9b::/96")
        nat64_local = ipaddress.IPv6Network("64:ff9b:1::/48")
        if ip in nat64_wkp or ip in nat64_local:
            embedded_v4 = ipaddress.IPv4Address(ip.packed[12:])
            return is_prohibited_ip(embedded_v4)

    return bool(
        not ip.is_global
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> None:
    """Raise UnsafeUrlError unless url is https and resolves only to public IPs.

    Requirements:
    - Scheme must be https. Reject http, file, data, x-raw-image, and everything else.
    - Resolve the hostname via socket.getaddrinfo. Reject if ANY resolved address
      is private, loopback, link-local, reserved, or multicast.
    - An unresolvable host is treated as unsafe.
    """
    if not url:
        raise UnsafeUrlError("Empty URL", cause="scheme", url=url)

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise UnsafeUrlError(
            f"Prohibited scheme {parsed.scheme!r}; only https is permitted",
            cause="scheme",
            url=url,
        )

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL has no valid hostname", cause="internal-address", url=url)

    hostname = hostname.rstrip(".")

    # Direct IP literal check
    try:
        ip = ipaddress.ip_address(hostname)
        if is_prohibited_ip(ip):
            raise UnsafeUrlError(
                f"Prohibited internal IP literal: {ip}",
                cause="internal-address",
                url=url,
            )
        return
    except ValueError:
        # Not an IP literal, proceed to DNS resolution
        pass

    # Resolve hostname
    try:
        addrinfo = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, socket.herror, socket.timeout, OSError) as e:
        raise UnsafeUrlError(
            f"Could not resolve host {hostname!r}: {e}",
            cause="internal-address",
            url=url,
        )

    if not addrinfo:
        raise UnsafeUrlError(
            f"Host {hostname!r} did not resolve to any addresses",
            cause="internal-address",
            url=url,
        )

    for res in addrinfo:
        ip_str = res[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise UnsafeUrlError(
                f"Invalid address returned for host {hostname!r}: {ip_str}",
                cause="internal-address",
                url=url,
            )
        if is_prohibited_ip(ip):
            raise UnsafeUrlError(
                f"Host {hostname!r} resolves to prohibited internal address: {ip}",
                cause="internal-address",
                url=url,
            )


def looks_like_image(head: bytes) -> bool:
    """Validate against real magic bytes, never the declared content-type.

    Accepts:
    - JPEG: \\xff\\xd8\\xff
    - PNG: \\x89PNG\\r\\n\\x1a\\n
    - GIF: GIF8
    - WebP: RIFF....WEBP
    - BMP: BM
    - TIFF: II*\\x00 (little endian) and MM\\x00* (big endian)
    """
    if not head:
        return False
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head.startswith(b"GIF8"):
        return True
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP":
        return True
    if head.startswith(b"BM"):
        return True
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return True
    return False


def safe_fetch(
    http: Any,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    is_image: bool = True,
) -> tuple[bytes, int, str | None]:
    """Fetches url safely enforcing assert_safe_url, redirect hop verification,
    streaming size cap, and optional image magic byte validation.

    Returns (body, status_code, content_type).
    """
    assert_safe_url(url)

    # Check cache if available and enabled
    if hasattr(http, "enabled") and http.enabled and hasattr(http, "_read"):
        from pipeline.cache.http_cache import cache_key

        key = cache_key("GET", url, params=None, body=None)
        cached = http._read(key, url)
        if cached is not None:
            if is_image and not looks_like_image(cached.content):
                raise UnsafeUrlError(
                    f"Cached response from {url} is not an image",
                    cause="not-an-image",
                    url=url,
                )
            if len(cached.content) > max_bytes:
                raise UnsafeUrlError(
                    f"Cached response from {url} exceeded size cap of {max_bytes} bytes",
                    cause="size-cap",
                    url=url,
                )
            http.hits += 1
            return cached.content, cached.status_code, cached.content_type

    # If http is a duck-typed test fixture without requests session
    session = getattr(http, "_session", None)
    if session is None and hasattr(http, "get") and not isinstance(http, HttpCache):
        resp = http.get(url, headers=headers, timeout=timeout)
        content = getattr(resp, "content", b"")
        if not content and hasattr(resp, "json"):
            try:
                j = resp.json()
                if j is not None:
                    import json as _json

                    content = _json.dumps(j).encode("utf-8")
            except Exception:
                pass
        status = getattr(resp, "status_code", 200 if getattr(resp, "ok", True) else 404)
        ctype = getattr(resp, "content_type", None)
        if is_image and getattr(resp, "ok", True) and content:
            if not looks_like_image(content):
                raise UnsafeUrlError(
                    f"Response from {url} is not an image (magic bytes mismatch)",
                    cause="not-an-image",
                    url=url,
                )
        if len(content) > max_bytes:
            raise UnsafeUrlError(
                f"Response body exceeded size cap of {max_bytes} bytes",
                cause="size-cap",
                url=url,
            )
        return content, status, ctype

    if session is None:
        session = requests.Session()

    if hasattr(http, "misses"):
        http.misses += 1

    current_url = url
    redirects = 0

    while True:
        assert_safe_url(current_url)
        resp = session.get(
            current_url,
            headers=headers,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
        )

        # Handle redirects
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            redirects += 1
            if redirects > max_redirects:
                resp.close()
                raise UnsafeUrlError(
                    f"Too many redirects ({redirects} > {max_redirects})",
                    cause="internal-address",
                    url=current_url,
                )
            location = resp.headers.get("Location")
            if not location:
                resp.close()
                raise UnsafeUrlError(
                    "Redirect response missing Location header",
                    cause="internal-address",
                    url=current_url,
                )
            next_url = urllib.parse.urljoin(current_url, location)
            resp.close()
            assert_safe_url(next_url)
            current_url = next_url
            continue

        status_code = resp.status_code
        content_type = resp.headers.get("Content-Type")

        if not resp.ok:
            err_bytes = resp.raw.read(1024) if hasattr(resp, "raw") else b""
            resp.close()
            return err_bytes, status_code, content_type

        # Streaming download enforcing size cap
        chunks: list[bytes] = []
        total_bytes = 0
        head_checked = False

        for chunk in resp.iter_content(chunk_size=16384):
            if not chunk:
                continue
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                resp.close()
                raise UnsafeUrlError(
                    f"Response body exceeded size cap of {max_bytes} bytes",
                    cause="size-cap",
                    url=current_url,
                )
            chunks.append(chunk)

            if is_image and not head_checked and total_bytes >= 32:
                head = b"".join(chunks)[:32]
                if not looks_like_image(head):
                    resp.close()
                    raise UnsafeUrlError(
                        f"Response from {current_url} does not have image magic bytes",
                        cause="not-an-image",
                        url=current_url,
                    )
                head_checked = True

        resp.close()
        body = b"".join(chunks)

        if is_image and not head_checked:
            if not looks_like_image(body):
                raise UnsafeUrlError(
                    f"Response from {current_url} does not have image magic bytes",
                    cause="not-an-image",
                    url=current_url,
                )

        if hasattr(http, "enabled") and http.enabled and hasattr(http, "_write"):
            try:
                from pipeline.cache.http_cache import cache_key

                key = cache_key("GET", url, params=None, body=None)
                http._write(key, url, status_code, body, params=None, content_type=content_type)
            except Exception:
                pass

        return body, status_code, content_type
