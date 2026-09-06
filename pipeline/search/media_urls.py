"""Platform CDN URL knowledge. See docs/rules.md R-21 discussion of lost recall.

Moved out of web_detect.py deliberately: that module's job is parsing
provider API responses, not knowing that X serves several pixel sizes of
the same image. This module owns exactly one concern — given a candidate
URL from any provider, what is the best (and what are the fallback)
fetchable image URL(s) for it.

Everything here is derived from live measurement
(scripts/probe_size_variants.py, 5 Sep 2026), not guessed:

    X/Twitter pbs.twimg.com/media/...  ?name= query-param variant
        thumb    150x150   face 32px   below 50px gate  <- what we fetched
        small    680x680   face 149px  PASSES
        medium  1080x1080  face 235px  PASSES
        large   1080x1080  face 235px  PASSES
        orig    1080x1080  face 235px  PASSES

    X/Twitter pbs.twimg.com/profile_images/...  filename-SUFFIX variant
    (a DIFFERENT, mutually exclusive scheme on the same domain — the
    ?name= param 404s on this path entirely; verified live, 5 Sep 2026,
    against a real profile picture URL returned by GCV):
        bare filename (no suffix)  18136 bytes  <- largest
        _400x400                   14046 bytes
        _200x200                    6000 bytes
        _bigger                     2439 bytes
        _normal                     1807 bytes
        _mini                       1451 bytes  <- smallest

    YouTube  i.ytimg.com thumbnail tiers (varies per video)
        video e2uRUMgozFQ: maxresdefault 404s; sddefault 640x480 face 75px
              PASSES; hqdefault 480x360 face 54px PASSES; mqdefault below gate
        video V8UwSQAPDxs (a Short): sddefault/hqdefault/mqdefault all
              below gate; ONLY maxresdefault (1280x720, face 68px) passes

    That second video is why the fallback cannot simply be "largest that
    fetches" or "smallest that's reliable" — it has to be a real
    largest-first walk where each candidate is independently checked
    against the face-size gate, which requires the detector
    (verify/pipeline_run.py._resolve_candidate_image), not just a URL
    rewrite. This module supplies the ordered candidate list; the fetch
    loop is what decides which one wins.

INVARIANT (added after a live regression, 5 Sep 2026): every function
below that returns a fallback chain MUST include the provider's original
URL as one of the entries. A first cut of the X rewrite instead REPLACED
the original with rewritten variants; on a real non-celebrity subject the
original ?name=thumb URL was in fact a WORKING url (a bare-filename
profile-image URL, unrelated to the ?name= scheme), and appending
?name=orig/large/... to it produced five 404s where a fetch would
otherwise have succeeded. A NO_MATCH was produced where a MATCH should
have been. Any rewrite scheme is therefore additive-only: it may propose
better variants, but it may never remove the one URL already known to
resolve. size_variants_for()/x_size_variants() enforce this directly;
verify/pipeline_run.py trusts it and does not re-check.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# X / Twitter
# --------------------------------------------------------------------------

_X_NAME_PARAM = re.compile(r"([?&])name=[a-z]+")

# /media/... URLs use the ?name= query-param scheme.
_X_MEDIA_PATH = re.compile(r"pbs\.twimg\.com/media/")

# /profile_images/... URLs use a filename-SUFFIX scheme instead — ?name=
# is not recognised on this path and 404s. Matches the suffix (if any) so
# it can be stripped and replaced; ".jpg"/".png"/etc is captured too since
# the suffix sits before the extension.
_X_PROFILE_IMAGE = re.compile(
    r"^(https://pbs\.twimg\.com/profile_images/[^?]+?)"
    r"(?:_(?:mini|normal|bigger|\d+x\d+))?"
    r"(\.[a-zA-Z0-9]+)(\?.*)?$"
)

# Largest-first. The bare filename (no suffix) is the largest, then the
# suffixes shrink from there — verified live against a real profile
# picture URL, see the module docstring.
_X_PROFILE_SUFFIXES: tuple[str | None, ...] = (None, "400x400", "200x200", "bigger", "normal", "mini")


def _x_media_variants(image_url: str) -> tuple[str, ...]:
    sizes = ("orig", "large", "medium", "small", "thumb")
    if _X_NAME_PARAM.search(image_url):
        variants = [_X_NAME_PARAM.sub(rf"\1name={size}", image_url) for size in sizes]
    else:
        sep = "&" if "?" in image_url else "?"
        variants = [f"{image_url}{sep}name={size}" for size in sizes]
    return _with_original_first(image_url, variants)


def _x_profile_image_variants(image_url: str) -> tuple[str, ...]:
    m = _X_PROFILE_IMAGE.match(image_url)
    if not m:
        return ()
    base, ext, query = m.group(1), m.group(2), m.group(3) or ""
    variants = [
        f"{base}{f'_{suffix}' if suffix else ''}{ext}{query}"
        for suffix in _X_PROFILE_SUFFIXES
    ]
    return _with_original_first(image_url, variants)


def _with_original_first(original: str, variants: list[str]) -> tuple[str, ...]:
    """Enforces the invariant documented at module level: `original` must
    survive in the returned chain even if the rewrite logic above would
    otherwise have dropped it.

    The computed (largest-guessed-first) variants still go FIRST, so a
    correct rewrite still gets the size upgrade before anything else is
    tried. `original` is appended at the end rather than prepended — it is
    already known to be fetchable (the provider gave it to us), so it is
    the correct last resort, not the first choice, when every rewritten
    guess turns out to be wrong for this particular URL (as happened live:
    a /profile_images/ URL rewritten with the /media/ ?name= scheme 404'd
    on every guess, and only the untouched original actually worked).
    """
    ordered = variants + ([original] if original not in variants else [])
    return tuple(ordered)


def x_size_variants(image_url: str) -> tuple[str, ...]:
    """Given any pbs.twimg.com URL, returns known size variants, with the
    ORIGINAL url always first (see module-level INVARIANT), followed by
    other same-scheme variants largest-first. Empty tuple if this isn't a
    recognised pbs.twimg.com path — callers should treat that as "no
    variants known" and use the URL as-is.
    """
    if _X_MEDIA_PATH.search(image_url):
        return _x_media_variants(image_url)
    if "pbs.twimg.com/profile_images/" in image_url:
        return _x_profile_image_variants(image_url)
    return ()


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------

# Matches /watch?v=<id>, youtu.be/<id>, and /shorts/<id> — i.e. PAGE urls.
_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)

# Matches i.ytimg.com/vi/<id>/ — i.e. CDN thumbnail urls, the reverse
# direction of _YT_ID_RE above.
_YTIMG_ID_RE = re.compile(r"i\.ytimg\.com/vi/([A-Za-z0-9_-]{11})/")

# Largest first. maxresdefault is the highest-resolution tier and is what a
# YouTube Short's single detectable frame typically needs (measured: only
# maxres cleared the 50px gate for V8UwSQAPDxs), but it 404s for many
# ordinary videos, so callers must fall through the whole list.
_YT_THUMB_TIERS = ("maxresdefault", "sddefault", "hqdefault", "mqdefault")


def youtube_video_id(url: str) -> str | None:
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else None


def youtube_thumbnail_variants(video_id: str) -> tuple[str, ...]:
    """Ordered, largest-first thumbnail URLs for a YouTube video id."""
    return tuple(f"https://i.ytimg.com/vi/{video_id}/{tier}.jpg" for tier in _YT_THUMB_TIERS)


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# --------------------------------------------------------------------------
# Generic entry points used by web_detect.py
# --------------------------------------------------------------------------


def derive_page_url(image_url: str) -> str | None:
    """Derives the real POST url from a platform CDN image url, so an
    accepted match cites a page a human can open rather than a bare
    thumbnail.

    Only derivable where the CDN path embeds a stable content id. YouTube
    does; Reddit's preview.redd.it and X's pbs.twimg.com do not encode the
    parent post, so those correctly stay un-derivable.
    """
    m = _YTIMG_ID_RE.search(image_url)
    if m:
        return youtube_watch_url(m.group(1))
    return None


# GitHub — a repo/blob/gist page carries the OWNER's avatar, and the
# owner's profile (github.com/<user>) is the citable identity page, not
# the repo README. Found live 5 Sep 2026: a genuine-search candidate
# scored 0.9363 with page_url = github.com/<user>/<repo> and image_url =
# avatars.githubusercontent.com/u/<id>?v=4 — the repo is where the search
# engine happened to find the avatar rendered, not the profile itself.
_GITHUB_REPO_PAGE = re.compile(r"^https://github\.com/([A-Za-z0-9_-]+)/[^/]+/?")


def derive_github_profile_url(page_url: str) -> str | None:
    """Given a github.com/<user>/<repo>[/...] URL, returns the profile URL
    github.com/<user>. Returns None if page_url is already a bare profile
    URL or isn't a GitHub URL at all — callers should treat None as
    "nothing to derive, use page_url as-is"."""
    m = _GITHUB_REPO_PAGE.match(page_url)
    if m:
        return f"https://github.com/{m.group(1)}"
    return None


def derive_image_url_and_fallbacks(page_url: str) -> tuple[str, tuple[str, ...]]:
    """Derives a fetchable thumbnail URL (plus ordered fallbacks) from a
    known platform's PAGE url, for pages where the provider returned no
    matching-image URL of its own.

    Returns ("", ()) when the platform is unknown — callers must not fall
    back to using the page URL itself as an image URL, since that
    downloads HTML and gets misreported as "no face detected" (see
    tests/test_web_detect_image_urls.py).
    """
    vid = youtube_video_id(page_url)
    if vid:
        variants = youtube_thumbnail_variants(vid)
        return variants[0], variants[1:]
    return "", ()


_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def is_citable_page(page_url: str, image_url: str) -> bool:
    """True if page_url is an openable PAGE a human can visit and see the
    post/profile on, rather than a bare image file. Used by
    verify/matcher.py's citability-aware headline selection (6 Sep 2026):
    within a cluster of candidates that all agree on the same identity
    (D-35), prefer citing a real page over a bare CDN media URL, even when
    the bare CDN hit scored higher — a judge clicking the citation should
    land on a post, not a raw JPEG.

    Heuristic, not a full URL classifier: a page_url that is identical to
    the image_url, or that itself looks like a direct image file (ends in
    a known image extension), is NOT citable. This catches the concrete
    case that motivated it: X media URLs
    (pbs.twimg.com/media/....jpg?name=thumb) where GCV sets page_url to
    the same bare-image URL because no parent post is derivable
    (derive_page_url returns None for exactly this reason, see above).
    """
    if not page_url:
        return False
    if page_url == image_url:
        return False
    path = page_url.split("?", 1)[0].lower()
    return not path.endswith(_IMAGE_EXTENSIONS)


def size_variants_for(image_url: str) -> tuple[str, ...]:
    """Ordered, largest-first alternates for an already-known image URL,
    for platforms where the SAME candidate is available at several
    resolutions under the same path (currently: X/Twitter).

    Returns () when this URL has no known size-variant scheme.
    """
    return x_size_variants(image_url)
