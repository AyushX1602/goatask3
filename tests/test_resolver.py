"""Tests for verify/resolver.py — the page-resolver cascade (6 Sep 2026,
owner instruction: "real crawling instead of --").

Pure-function tests for the three extraction routes (no network), plus
cascade-ordering tests against a fake HttpCache.
"""

from __future__ import annotations

from pipeline.verify.resolver import (
    CascadeResult,
    ResolveAttempt,
    extract_opengraph_image,
    extract_oembed_thumbnail,
    extract_reddit_image,
    oembed_endpoint_for,
    reddit_json_url,
    resolve_page_to_image_url,
)


# ---------------- OpenGraph extraction (pure) ----------------


def test_extract_opengraph_image_property_then_content():
    html = b'<html><head><meta property="og:image" content="https://example.com/pic.jpg"></head></html>'
    assert extract_opengraph_image(html) == "https://example.com/pic.jpg"


def test_extract_opengraph_image_content_then_property():
    html = b'<html><head><meta content="https://example.com/pic2.jpg" property="og:image"></head></html>'
    assert extract_opengraph_image(html) == "https://example.com/pic2.jpg"


def test_extract_opengraph_image_secure_url_variant():
    html = b'<meta property="og:image:secure_url" content="https://example.com/secure.jpg">'
    assert extract_opengraph_image(html) == "https://example.com/secure.jpg"


def test_extract_opengraph_falls_back_to_twitter_image():
    html = b'<meta name="twitter:image" content="https://example.com/twitter.jpg">'
    assert extract_opengraph_image(html) == "https://example.com/twitter.jpg"


def test_extract_opengraph_prefers_og_over_twitter_when_both_present():
    html = (
        b'<meta property="og:image" content="https://example.com/og.jpg">'
        b'<meta name="twitter:image" content="https://example.com/tw.jpg">'
    )
    assert extract_opengraph_image(html) == "https://example.com/og.jpg"


def test_extract_opengraph_returns_none_when_absent():
    html = b"<html><head><title>no meta tags here</title></head></html>"
    assert extract_opengraph_image(html) is None


def test_extract_opengraph_case_insensitive():
    html = b'<META PROPERTY="OG:IMAGE" CONTENT="https://example.com/upper.jpg">'
    assert extract_opengraph_image(html) == "https://example.com/upper.jpg"


# ---------------- oEmbed (pure) ----------------


def test_oembed_endpoint_for_youtube_watch_url():
    url = oembed_endpoint_for("https://www.youtube.com/watch?v=abc12345678")
    assert url == "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=abc12345678&format=json"


def test_oembed_endpoint_for_x_status_url():
    url = oembed_endpoint_for("https://x.com/someone/status/1234567890")
    assert url == "https://publish.twitter.com/oembed?url=https://x.com/someone/status/1234567890"


def test_oembed_endpoint_for_twitter_com_status_url():
    url = oembed_endpoint_for("https://twitter.com/someone/status/1234567890")
    assert url is not None


def test_oembed_endpoint_none_for_unsupported_platform():
    assert oembed_endpoint_for("https://www.instagram.com/p/abc123/") is None
    assert oembed_endpoint_for("https://example.com/random-page") is None


def test_extract_oembed_thumbnail():
    assert extract_oembed_thumbnail({"thumbnail_url": "https://img.test/t.jpg", "title": "x"}) == "https://img.test/t.jpg"


def test_extract_oembed_thumbnail_missing():
    assert extract_oembed_thumbnail({"title": "x"}) is None


# ---------------- Reddit .json (pure) ----------------


def test_reddit_json_url_for_a_post():
    url = reddit_json_url("https://www.reddit.com/r/pics/comments/abc123/some_title/")
    assert url == "https://www.reddit.com/r/pics/comments/abc123/some_title.json"


def test_reddit_json_url_none_for_a_bare_subreddit():
    assert reddit_json_url("https://www.reddit.com/r/pics/") is None


def test_reddit_json_url_none_for_a_profile():
    assert reddit_json_url("https://www.reddit.com/user/someone/") is None


def test_extract_reddit_image_from_url_overridden_by_dest():
    payload = [{"data": {"children": [{"data": {"url_overridden_by_dest": "https://i.redd.it/abc.jpg"}}]}}]
    assert extract_reddit_image(payload) == "https://i.redd.it/abc.jpg"


def test_extract_reddit_image_falls_back_to_preview():
    payload = [
        {
            "data": {
                "children": [
                    {
                        "data": {
                            "url_overridden_by_dest": "https://www.reddit.com/gallery/abc",  # not an image ext
                            "preview": {"images": [{"source": {"url": "https://preview.redd.it/x.jpg?a=1&amp;b=2"}}]},
                        }
                    }
                ]
            }
        }
    ]
    assert extract_reddit_image(payload) == "https://preview.redd.it/x.jpg?a=1&b=2"


def test_extract_reddit_image_returns_none_on_malformed_payload():
    assert extract_reddit_image([]) is None
    assert extract_reddit_image([{"data": {"children": []}}]) is None
    assert extract_reddit_image("not even a list") is None


# ---------------- Cascade ordering (fake HTTP) ----------------


class _FakeResp:
    def __init__(self, ok: bool, status_code: int = 200, json_data=None, content: bytes = b""):
        self.ok = ok
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def json(self):
        return self._json


class _FakeHttp:
    """Maps exact URLs to responses, records every URL requested."""

    def __init__(self, responses: dict[str, _FakeResp]) -> None:
        self._responses = responses
        self.requested: list[str] = []

    def get(self, url: str, **kw):
        self.requested.append(url)
        return self._responses.get(url, _FakeResp(False, 404))


def test_cascade_prefers_oembed_over_opengraph_for_youtube():
    page = "https://www.youtube.com/watch?v=abc12345678"
    oembed_url = oembed_endpoint_for(page)
    http = _FakeHttp({oembed_url: _FakeResp(True, json_data={"thumbnail_url": "https://i.ytimg.com/vi/abc12345678/hq.jpg"})})

    result = resolve_page_to_image_url(http, page)

    assert result.image_url == "https://i.ytimg.com/vi/abc12345678/hq.jpg"
    assert result.routes_tried == ("oembed",)
    # opengraph must NOT have been requested since oembed already won
    assert page not in http.requested


def test_cascade_falls_through_to_opengraph_when_oembed_has_no_thumbnail():
    page = "https://www.youtube.com/watch?v=abc12345678"
    oembed_url = oembed_endpoint_for(page)
    http = _FakeHttp({
        oembed_url: _FakeResp(True, json_data={"title": "no thumbnail field"}),
        page: _FakeResp(True, content=b'<meta property="og:image" content="https://example.com/fallback.jpg">'),
    })

    result = resolve_page_to_image_url(http, page)

    assert result.image_url == "https://example.com/fallback.jpg"
    assert result.routes_tried == ("oembed", "opengraph")


def test_cascade_prefers_reddit_json_over_opengraph():
    page = "https://www.reddit.com/r/pics/comments/abc123/title/"
    json_url = reddit_json_url(page)
    payload = [{"data": {"children": [{"data": {"url_overridden_by_dest": "https://i.redd.it/real.jpg"}}]}}]
    http = _FakeHttp({json_url: _FakeResp(True, json_data=payload)})

    result = resolve_page_to_image_url(http, page)

    assert result.image_url == "https://i.redd.it/real.jpg"
    assert result.routes_tried == ("reddit_json",)


def test_cascade_returns_none_when_every_route_fails():
    page = "https://example.com/some-random-page"
    http = _FakeHttp({})  # every request 404s

    result = resolve_page_to_image_url(http, page)

    assert result.image_url is None
    assert result.routes_tried == ("opengraph",)  # only route applicable to a generic page


def test_cascade_records_a_failed_attempt_even_when_no_image_found():
    page = "https://example.com/some-random-page"
    http = _FakeHttp({})

    result = resolve_page_to_image_url(http, page)

    assert len(result.attempts) == 1
    assert isinstance(result.attempts[0], ResolveAttempt)
    assert result.attempts[0].succeeded is False


def test_cascade_result_is_a_frozen_dataclass_with_routes_tried_property():
    r = CascadeResult(image_url="https://x.test/1.jpg", attempts=(ResolveAttempt("opengraph", True, "https://x.test/1.jpg"),))
    assert r.routes_tried == ("opengraph",)
