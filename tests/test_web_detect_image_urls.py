"""Regression tests for the image-URL bug found on a live Hrithik Roshan
probe (5 Sep 2026).

`parse_gcv` fell back to using the PAGE url as the image url whenever GCV
returned a page with no matching-image url of its own. The pipeline then
downloaded HTML, cv2.imdecode returned None, and it reported
`reject-no-face` -- "no face detected in candidate image" -- when in truth
no image had ever been retrieved.

Verified live: fetching https://www.youtube.com/watch?v=gcJgOxNrno4
returned content-type text/html and imdecode returned None. Four youtube
results were discarded this way on that run, of which at least two DO have
a detectable face once the real thumbnail is fetched.
"""

from __future__ import annotations

from pipeline.search.web_detect import _derive_image_url, parse_gcv


def test_youtube_thumbnail_is_derived_from_watch_url():
    got = _derive_image_url("https://www.youtube.com/watch?v=gcJgOxNrno4")
    assert got == "https://i.ytimg.com/vi/gcJgOxNrno4/maxresdefault.jpg"


def test_youtube_short_link_is_derived():
    got = _derive_image_url("https://youtu.be/S35VETbs8ME")
    assert got == "https://i.ytimg.com/vi/S35VETbs8ME/maxresdefault.jpg"


def test_youtube_url_with_extra_params_is_derived():
    got = _derive_image_url("https://www.youtube.com/watch?v=kEVtpdTdgUQ&t=42s")
    assert got == "https://i.ytimg.com/vi/kEVtpdTdgUQ/maxresdefault.jpg"


def test_unknown_platform_derives_nothing():
    assert _derive_image_url("https://open.spotify.com/track/4qfBqWCUYLHXUhd9B5xFUX") is None
    assert _derive_image_url("https://www.imdb.com/video/embed/vi4148937497/") is None


def test_page_without_image_never_reuses_the_page_url_as_an_image_url():
    """The core of the bug: a page URL must never be used as an image URL."""
    payload = {
        "webDetection": {
            "pagesWithMatchingImages": [
                {"url": "https://open.spotify.com/track/abc", "pageTitle": "a track"},
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    assert len(cands) == 1
    assert cands[0].page_url == "https://open.spotify.com/track/abc"
    assert cands[0].image_url == "", "must be empty, not the page URL"


def test_youtube_page_without_image_gets_a_derived_thumbnail():
    payload = {
        "webDetection": {
            "pagesWithMatchingImages": [
                {"url": "https://www.youtube.com/watch?v=gcJgOxNrno4", "pageTitle": "vid"},
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    assert cands[0].image_url == "https://i.ytimg.com/vi/gcJgOxNrno4/maxresdefault.jpg"
    assert cands[0].page_url == "https://www.youtube.com/watch?v=gcJgOxNrno4"


def test_unfetchable_x_raw_image_scheme_is_skipped_in_page_images():
    """GCV returns x-raw-image:///<hash> for images it holds internally but
    will not serve. Seen live as candidate #4 on the Hrithik run."""
    payload = {
        "webDetection": {
            "pagesWithMatchingImages": [
                {
                    "url": "https://www.youtube.com/watch?v=gcJgOxNrno4",
                    "fullMatchingImages": [{"url": "x-raw-image:///deadbeef"}],
                }
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    # must ignore the unfetchable scheme and fall through to derivation
    assert cands[0].image_url == "https://i.ytimg.com/vi/gcJgOxNrno4/maxresdefault.jpg"


def test_unfetchable_scheme_dropped_from_standalone_images():
    payload = {
        "webDetection": {
            "fullMatchingImages": [
                {"url": "x-raw-image:///deadbeef"},
                {"url": "https://real.test/photo.jpg"},
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    urls = [c.image_url for c in cands]
    assert "https://real.test/photo.jpg" in urls
    assert not any(u.startswith("x-raw-image:") for u in urls)


def test_real_image_url_still_preferred_over_derivation():
    payload = {
        "webDetection": {
            "pagesWithMatchingImages": [
                {
                    "url": "https://www.youtube.com/watch?v=gcJgOxNrno4",
                    "fullMatchingImages": [{"url": "https://i.ytimg.com/vi/real/hq.jpg"}],
                }
            ]
        }
    }
    cands, _ = parse_gcv(payload)
    assert cands[0].image_url == "https://i.ytimg.com/vi/real/hq.jpg"
