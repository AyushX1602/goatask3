"""Tests for verify/allowlist.py: the allowlist itself, the R-06 platform
principle, is_media_blocked's TikTok endpoint-vs-domain split, and
content_kind classification.

Grounded in two real, live, genuine-search runs on 5 Sep 2026 (see
calibration/quarantine/ for the redacted audit logs and
calibration/negatives_harvested.json for the harvested scores):

  Run A: a private individual's GitHub avatar scored 0.9363 and was
  rejected as "github.com not on social allowlist" — an inconsistency,
  since linkedin.com was already allowed under the identical R-06 test.

  Run B: a tiktok.com/api/img/... URL was correctly reject-platform-blocked,
  while in the SAME run a tiktokcdn-us.com signed CDN URL fetched
  successfully (score 0.0593) — proving the block is on the endpoint, not
  the platform domain.
"""

from __future__ import annotations

from pipeline.verify.allowlist import (
    content_kind,
    is_allowed,
    is_media_blocked,
    platform_name,
)


# ---------------- R-06 allowlist principle: GitHub ----------------


def test_github_is_on_the_allowlist():
    assert is_allowed("https://github.com/SilenNaihin/isomorphic")
    assert platform_name("https://github.com/SilenNaihin/isomorphic") == "GitHub"


def test_github_avatar_cdn_is_on_the_allowlist():
    assert is_allowed("https://avatars.githubusercontent.com/u/44129612?v=4")


def test_github_and_linkedin_are_allowed_under_the_same_principle():
    """Both are platforms where an individual maintains a public identity
    profile and publishes content under it — the R-06 test. Neither
    should be treated as a special case of the other."""
    assert is_allowed("https://github.com/someone")
    assert is_allowed("https://www.linkedin.com/in/someone")


def test_a_cdn_or_news_domain_is_still_not_allowed():
    """The principle cuts both ways — it is not "add more domains", it is
    a test. A CDN/news host fails the test because the pictured person
    does not themselves maintain a profile there."""
    assert not is_allowed("https://doximity-res.cloudinary.com/images/foo.jpg")
    assert not is_allowed("https://media.cnn.com/api/v1/images/foo.jpg")


# ---------------- TikTok: endpoint-blocked, not domain-blocked ----------------


def test_tiktok_api_img_endpoint_is_blocked():
    url = "https://www.tiktok.com/api/img/?userId=699826076&location=2&aid=1988"
    assert is_media_blocked(url) is True


def test_tiktok_signed_cdn_url_is_not_blocked():
    """Verified live 5 Sep 2026: this exact URL shape fetched successfully
    (200, scored 0.0593 as a genuine negative) in the same run where the
    api/img endpoint above was blocked."""
    url = (
        "https://p16-common-sign.tiktokcdn-us.com/tos-alisg-avt-0068/"
        "966be298ac012c8837d7631859ceaa02~tplv-tiktokx-cropcenter:720:720.jpeg"
        "?dr=9640&x-expires=1788699600&x-signature=abc%3D"
    )
    assert is_media_blocked(url) is False


def test_instagram_facebook_meta_remain_fully_blocked():
    """Unlike TikTok, these have no known fetchable variant — the whole
    platform is blocked, not just one endpoint."""
    assert is_media_blocked("https://lookaside.fbsbx.com/lookaside/crawler/media/?media_id=1") is True
    assert is_media_blocked("https://lookaside.instagram.com/seo/google_widget/crawler/?media_id=1") is True


# ---------------- content_kind: GitHub ----------------


def test_github_profile_url_is_a_profile_not_a_post():
    assert content_kind("https://github.com/SilenNaihin") == "profile"


def test_github_repo_url_alone_is_unknown_not_profile():
    """content_kind() itself does not rewrite repo URLs to profile URLs —
    that rewrite happens upstream in search/media_urls.derive_github_
    profile_url before the candidate's page_url is ever set. This test
    documents that content_kind is deliberately dumb about that distinction
    so there is exactly one place (media_urls.py) that owns the rewrite."""
    assert content_kind("https://github.com/SilenNaihin/isomorphic") == "unknown"
