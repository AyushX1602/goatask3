"""Regression tests for pipeline/search/media_urls.py — the G1 recall
fixes (5 Sep 2026), grounded in scripts/probe_size_variants.py measurements:

  X/Twitter ?name=thumb (150x150, face 32px, below the 50px gate) versus
  ?name=orig (1080x1080, face 235px, passes) on the exact live URL that
  was rejected on a real SRK run.

  YouTube maxresdefault.jpg 404s for some videos but not others, and for
  at least one YouTube Short only maxresdefault clears the 50px gate
  while every smaller tier does not — so the fallback must try every
  tier, largest first, rather than assuming any fixed ordering rule.
"""

from __future__ import annotations

from pipeline.search.media_urls import (
    derive_image_url_and_fallbacks,
    derive_page_url,
    size_variants_for,
    x_size_variants,
    youtube_thumbnail_variants,
    youtube_video_id,
)

X_LIVE_URL = "https://pbs.twimg.com/media/HRSsRstbMAEzQxl.jpg?format=jpg&name=thumb"


def test_x_thumb_url_rewrites_to_orig_first():
    variants = x_size_variants(X_LIVE_URL)
    assert variants[0] == "https://pbs.twimg.com/media/HRSsRstbMAEzQxl.jpg?format=jpg&name=orig"


def test_x_variants_are_largest_first_and_include_every_tier():
    variants = x_size_variants(X_LIVE_URL)
    assert [v.split("name=")[1] for v in variants] == ["orig", "large", "medium", "small", "thumb"]


def test_x_url_without_name_param_gets_one_appended():
    variants = x_size_variants("https://pbs.twimg.com/media/abc.jpg")
    assert variants[0] == "https://pbs.twimg.com/media/abc.jpg?name=orig"


def test_non_x_url_has_no_size_variants():
    assert x_size_variants("https://i.ytimg.com/vi/abc/hqdefault.jpg") == ()
    assert size_variants_for("https://example.com/photo.jpg") == ()


def test_size_variants_for_dispatches_to_x():
    assert size_variants_for(X_LIVE_URL)[0].endswith("name=orig")


# ---------------- YouTube ----------------


def test_youtube_video_id_from_watch_url():
    assert youtube_video_id("https://www.youtube.com/watch?v=e2uRUMgozFQ") == "e2uRUMgozFQ"


def test_youtube_video_id_from_short_link():
    assert youtube_video_id("https://youtu.be/S35VETbs8ME") == "S35VETbs8ME"


def test_youtube_video_id_from_shorts_url():
    """The gap that produced live reject-no-image: /shorts/ URLs were not
    matched by the old regex at all."""
    assert youtube_video_id("https://www.youtube.com/shorts/V8UwSQAPDxs") == "V8UwSQAPDxs"


def test_youtube_thumbnail_variants_are_maxres_first():
    variants = youtube_thumbnail_variants("e2uRUMgozFQ")
    assert variants[0] == "https://i.ytimg.com/vi/e2uRUMgozFQ/maxresdefault.jpg"
    assert variants[-1] == "https://i.ytimg.com/vi/e2uRUMgozFQ/mqdefault.jpg"
    assert len(variants) == 4


def test_derive_image_url_and_fallbacks_for_shorts():
    primary, fallbacks = derive_image_url_and_fallbacks(
        "https://www.youtube.com/shorts/V8UwSQAPDxs"
    )
    assert primary == "https://i.ytimg.com/vi/V8UwSQAPDxs/maxresdefault.jpg"
    assert fallbacks == (
        "https://i.ytimg.com/vi/V8UwSQAPDxs/sddefault.jpg",
        "https://i.ytimg.com/vi/V8UwSQAPDxs/hqdefault.jpg",
        "https://i.ytimg.com/vi/V8UwSQAPDxs/mqdefault.jpg",
    )


def test_derive_image_url_and_fallbacks_unknown_platform_is_empty():
    primary, fallbacks = derive_image_url_and_fallbacks("https://open.spotify.com/track/abc")
    assert primary == ""
    assert fallbacks == ()


def test_derive_page_url_from_ytimg_thumbnail():
    assert derive_page_url("https://i.ytimg.com/vi/nDj8MIyitUs/maxresdefault.jpg") == (
        "https://www.youtube.com/watch?v=nDj8MIyitUs"
    )


def test_derive_page_url_unknown_cdn_is_none():
    assert derive_page_url("https://pbs.twimg.com/media/abc.jpg?name=orig") is None
    assert derive_page_url("https://preview.redd.it/abc.jpg") is None


# ---------------------------------------------------------------------------
# G1.1 — the twimg dual-scheme regression (R-23, R-24)
#
# pbs.twimg.com runs TWO mutually exclusive sizing schemes on one domain, and
# the first cut of the X fix applied the /media/ scheme to both. Measured live
# against the exact URL GCV returned on a real non-celebrity probe:
#
#   _400x400.jpg            -> HTTP 200, 14046 bytes   <- worked already
#   _400x400.jpg?name=orig  -> HTTP 404                <- what we produced
#
# Five 404s where a fetch would otherwise have succeeded, turning a MATCH into
# a NO_MATCH. These tests pin both schemes and the additive-only invariant.
# ---------------------------------------------------------------------------

X_LIVE_PROFILE_URL = (
    "https://pbs.twimg.com/profile_images/2095112698706984962/NuBx6HAF_400x400.jpg"
)

# Every URL shape we have actually seen come back from a provider. Used by the
# invariant test below so a newly added scheme inherits the guarantee.
REAL_PROVIDER_URLS = [
    X_LIVE_PROFILE_URL,
    "https://pbs.twimg.com/profile_images/2095112698706984962/NuBx6HAF.jpg",
    "https://pbs.twimg.com/profile_images/123/abc_normal.jpg",
    "https://pbs.twimg.com/profile_images/123/abc_bigger.png",
    "https://pbs.twimg.com/profile_images/123/abc_200x200.jpg",
    X_LIVE_URL,
    "https://pbs.twimg.com/media/abc.jpg",
    "https://pbs.twimg.com/media/abc.jpg?format=jpg&name=small",
]


def test_profile_image_never_gets_the_name_query_param():
    """The actual regression: ?name= 404s on /profile_images/ entirely."""
    variants = size_variants_for(X_LIVE_PROFILE_URL)
    assert variants, "profile images must still produce a variant chain"
    assert all("name=" not in v for v in variants), (
        "?name= is a /media/-only scheme and 404s on /profile_images/"
    )


def test_profile_image_variants_are_largest_first_by_measured_bytes():
    """Order verified live: bare 18136 B > _400x400 14046 > _200x200 6000 >
    _bigger 2439 > _normal 1807 > _mini 1451."""
    variants = size_variants_for(X_LIVE_PROFILE_URL)
    base = "https://pbs.twimg.com/profile_images/2095112698706984962/NuBx6HAF"
    assert variants[0] == f"{base}.jpg", "bare filename is the largest variant"
    assert variants[1] == f"{base}_400x400.jpg"
    assert variants[2] == f"{base}_200x200.jpg"
    assert variants[3] == f"{base}_bigger.jpg"
    assert variants[4] == f"{base}_normal.jpg"
    assert variants[5] == f"{base}_mini.jpg"


def test_profile_image_preserves_the_file_extension():
    variants = size_variants_for("https://pbs.twimg.com/profile_images/123/abc_normal.png")
    assert all(v.endswith(".png") for v in variants)


def test_profile_image_already_bare_still_yields_a_chain():
    url = "https://pbs.twimg.com/profile_images/2095112698706984962/NuBx6HAF.jpg"
    variants = size_variants_for(url)
    assert variants[0] == url
    assert url in variants


def test_media_and_profile_schemes_do_not_cross_contaminate():
    media = size_variants_for(X_LIVE_URL)
    profile = size_variants_for(X_LIVE_PROFILE_URL)
    assert all("name=" in v for v in media), "/media/ uses the query-param scheme"
    assert all("name=" not in v for v in profile), "/profile_images/ does not"


# --- R-23, as a property over every known URL shape rather than per-platform --


def test_original_url_always_survives_in_the_variant_chain():
    """R-23. This is the generic guard: a rewrite may reorder or add, but it
    may NEVER drop the URL the provider actually gave us. Asserted over every
    real URL shape we've observed, so adding a new platform scheme inherits
    the guarantee instead of needing its own bespoke test.

    Had this existed before G1, the /profile_images/ regression could not have
    shipped: the untouched original was the only URL that resolved.
    """
    for url in REAL_PROVIDER_URLS:
        variants = size_variants_for(url)
        if not variants:
            continue  # no known scheme for this URL; caller uses it as-is
        assert url in variants, (
            f"R-23 violated: rewriting dropped the provider's original URL {url}"
        )


def test_variant_chains_have_no_duplicates():
    """A duplicate costs a wasted HTTP request on the cold path."""
    for url in REAL_PROVIDER_URLS:
        variants = size_variants_for(url)
        assert len(variants) == len(set(variants)), f"duplicate variant for {url}"


# ---------------- GitHub profile derivation ----------------


def test_derive_github_profile_url_from_repo_page():
    from pipeline.search.media_urls import derive_github_profile_url

    assert derive_github_profile_url("https://github.com/SilenNaihin/isomorphic") == (
        "https://github.com/SilenNaihin"
    )


def test_derive_github_profile_url_from_repo_subpath():
    from pipeline.search.media_urls import derive_github_profile_url

    assert derive_github_profile_url(
        "https://github.com/SilenNaihin/isomorphic/blob/main/README.md"
    ) == "https://github.com/SilenNaihin"


def test_derive_github_profile_url_returns_none_for_bare_profile():
    from pipeline.search.media_urls import derive_github_profile_url

    assert derive_github_profile_url("https://github.com/SilenNaihin") is None


def test_derive_github_profile_url_returns_none_for_non_github():
    from pipeline.search.media_urls import derive_github_profile_url

    assert derive_github_profile_url("https://gitlab.com/someone/repo") is None
