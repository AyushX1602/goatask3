"""Regression tests for the margin-rule bug found in live testing
(5 Sep 2026, Alia Bhatt upload).

The rule measured margin against the RUNNER-UP. For any public figure with
many indexed photos, every genuine match scores similarly high, so the
runner-up sits within ~0.001 of the best — and the rule rejected all of
them. Four real matches across linkedin/youtube/reddit (0.9225, 0.9211,
0.9164, 0.9072) were all rejected against a threshold of 0.42.

Margin is now measured against the best candidate BELOW threshold (the
best non-match), which is the question the rule was always meant to ask.
Additional above-threshold candidates are reported as `corroborating`,
never as rejections.
"""

from __future__ import annotations

from pipeline.config import MatchPolicy
from pipeline.search.base import Candidate
from pipeline.verify.allowlist import SOCIAL_ALLOW
from pipeline.verify.matcher import score_candidates

POLICY = MatchPolicy(
    threshold=0.42, margin=0.08, model="w600k_r50", target_fmr=0.01, is_placeholder=True
)


def _c(url: str) -> Candidate:
    return Candidate(image_url=url + "#img", page_url=url, source="gcv_web_detection")


def test_the_exact_alia_bhatt_scores_now_produce_a_match():
    """Verbatim reproduction of the failing live run."""
    scored = [
        (_c("https://www.linkedin.com/posts/marketingmindin_x"), 0.9225, 1),
        (_c("https://www.youtube.com/watch?v=BBKhqoFD_AU"), 0.9211, 1),
        (_c("https://www.linkedin.com/posts/yourstory-com_y"), 0.9164, 1),
        (_c("https://www.reddit.com/r/BollywoodFashion/comments/z"), 0.9072, 1),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)

    assert result.verdict == "MATCH", "four genuine matches must not all be rejected"
    assert result.best is not None
    assert result.best.score == 0.9225
    assert result.best.candidate.page_url.startswith("https://www.linkedin.com")

    # The other three are real matches too — reported as corroboration,
    # never as rejections.
    decisions = [r.decision for r in result.all_scored]
    assert decisions.count("ACCEPT") == 1
    assert decisions.count("corroborating") == 3
    assert "reject-margin" not in decisions


def test_accept_reason_mentions_corroboration_count():
    scored = [
        (_c("https://www.linkedin.com/posts/a"), 0.92, 1),
        (_c("https://www.youtube.com/watch?v=b"), 0.91, 1),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)
    assert result.verdict == "MATCH"
    assert "corroborating" in result.best.reason


def test_margin_measured_against_best_sub_threshold_candidate():
    """A clear match plus a clear non-match: margin is the gap between
    them, and it should comfortably pass."""
    scored = [
        (_c("https://www.reddit.com/r/x/comments/match"), 0.90, 1),
        (_c("https://www.reddit.com/r/x/comments/noise"), 0.10, 1),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)
    assert result.verdict == "MATCH"
    # runner_up now means "best non-match"
    assert result.runner_up is not None
    assert result.runner_up.score == 0.10
    assert "0.1000" in result.best.reason


def test_no_sub_threshold_candidate_still_accepts():
    """If every candidate is above threshold there is no non-matching
    population to separate from, so the margin check passes trivially
    rather than blocking a valid match."""
    scored = [
        (_c("https://www.reddit.com/r/x/comments/a"), 0.95, 1),
        (_c("https://www.youtube.com/watch?v=b"), 0.94, 1),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)
    assert result.verdict == "MATCH"
    assert result.runner_up is None
    assert "no sub-threshold candidate" in result.best.reason


def test_genuine_ambiguity_still_rejected_by_margin():
    """The case the rule legitimately guards against: the top candidate is
    only barely above threshold and sits right on top of the non-matching
    population, so it is not distinguishable from noise."""
    tight_policy = MatchPolicy(
        threshold=0.42, margin=0.08, model="w600k_r50", target_fmr=0.01, is_placeholder=True
    )
    scored = [
        (_c("https://www.reddit.com/r/x/comments/a"), 0.44, 1),
        (_c("https://www.reddit.com/r/x/comments/b"), 0.41, 1),  # just below threshold
    ]
    result = score_candidates(scored, tight_policy, allowed_domains=SOCIAL_ALLOW)
    assert result.verdict == "NO_MATCH"
    best_row = max(result.all_scored, key=lambda r: r.score or -1)
    assert best_row.decision == "reject-margin"
    assert "not separated" in best_row.reason


def test_below_threshold_candidates_still_rejected():
    scored = [
        (_c("https://www.reddit.com/r/x/comments/a"), 0.15, 1),
        (_c("https://www.youtube.com/watch?v=b"), 0.08, 1),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)
    assert result.verdict == "NO_MATCH"
    assert all(r.decision == "reject-below-threshold" for r in result.all_scored)


def test_off_allowlist_high_scorer_still_never_reported():
    """The Alia run also had 0.9771 on preview.redd.it and 0.9678 on
    variety.com — correctly domain-rejected. That behaviour must survive
    this change."""
    scored = [
        (_c("https://variety.com/2025/film/x"), 0.9678, 1),
        (_c("https://www.reddit.com/r/x/comments/a"), 0.90, 1),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)
    assert result.verdict == "MATCH"
    assert result.best.candidate.page_url.startswith("https://www.reddit.com")
    variety = next(r for r in result.all_scored if "variety.com" in r.candidate.page_url)
    assert variety.decision == "reject-domain"


# ---------------- G-series: NO_CANDIDATES, MATCH_NON_SOCIAL, unverifiable hits ----------------


def test_no_scored_and_no_prerejected_is_no_candidates():
    """Distinct from NO_MATCH: nothing was ever examined."""
    result = score_candidates([], POLICY, allowed_domains=SOCIAL_ALLOW, prerejected=[])
    assert result.verdict == "NO_CANDIDATES"
    assert result.all_scored == []


def test_high_scoring_domain_reject_produces_match_non_social():
    """A candidate that would have passed threshold+margin but sits off the
    allowlist must not collapse into a plain NO_MATCH — that would
    contradict a caption reporting the same high score (found live 5 Sep
    2026: both sentences rendered on screen at once)."""
    scored = [
        (_c("https://www.etnownews.com/article"), 0.97, 4),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)
    assert result.verdict == "MATCH_NON_SOCIAL"
    assert result.best is None
    assert result.all_scored[0].decision == "reject-domain"


def test_low_scoring_domain_reject_stays_plain_no_match():
    scored = [
        (_c("https://www.etnownews.com/article"), 0.05, 1),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)
    assert result.verdict == "NO_MATCH"


def test_unverifiable_platform_hits_populated_for_full_match_kind():
    from pipeline.search.base import Candidate as _Cand
    from pipeline.verify.matcher import score_candidates as _score

    blocked_cand = _Cand(
        image_url="https://lookaside.fbsbx.com/x", page_url="https://www.instagram.com/p/abc/",
        source="gcv_web_detection", match_kind="full",
    )
    prerejected = [(blocked_cand, "reject-platform-blocked", "Meta serves media only to its own crawler")]
    result = _score([], POLICY, allowed_domains=SOCIAL_ALLOW, prerejected=prerejected)
    assert len(result.unverifiable_platform_hits) == 1
    assert result.unverifiable_platform_hits[0].candidate is blocked_cand


def test_unverifiable_platform_hits_excludes_similar_match_kind():
    """Only full/partial matches are surfaced as "the search engine
    asserts this is the same image" — a mere visual lookalike on a
    platform-blocked domain is not a meaningful signal."""
    from pipeline.search.base import Candidate as _Cand
    from pipeline.verify.matcher import score_candidates as _score

    lookalike_cand = _Cand(
        image_url="https://lookaside.fbsbx.com/x", page_url="https://www.instagram.com/p/abc/",
        source="gcv_web_detection", match_kind="similar",
    )
    prerejected = [(lookalike_cand, "reject-platform-blocked", "blocked")]
    result = _score([], POLICY, allowed_domains=SOCIAL_ALLOW, prerejected=prerejected)
    assert result.unverifiable_platform_hits == ()


# ---------------- Citability-aware headline selection (6 Sep 2026) ----------------
#
# Found live: an X media hit scored 0.9806 (bare pbs.twimg.com/....jpg,
# page_url == image_url, no derivable parent post) while a YouTube hit in
# the SAME identity cluster scored 0.9618 (a real, openable watch?v= page).
# Citing the X URL as the headline ACCEPT would open a raw JPEG in a
# browser, not a post. Both are equally accepted matches (both pass
# threshold+margin); only WHICH one is displayed as the headline changes.


def _x_media_candidate(score: float) -> tuple[Candidate, float, int]:
    """A bare X media hit: page_url == image_url, no derivable post —
    is_citable_page() returns False for exactly this shape."""
    url = "https://pbs.twimg.com/media/HRSsRstbMAEzQxl.jpg?name=orig"
    return (Candidate(image_url=url, page_url=url, source="gcv_web_detection"), score, 1)


def _youtube_post_candidate(score: float) -> tuple[Candidate, float, int]:
    return (
        Candidate(
            image_url="https://i.ytimg.com/vi/nDj8MIyitUs/maxresdefault.jpg",
            page_url="https://www.youtube.com/watch?v=nDj8MIyitUs",
            source="gcv_web_detection",
        ),
        score,
        1,
    )


def test_citable_youtube_page_wins_headline_over_higher_scoring_x_media_url():
    """The exact live case: X scored HIGHER but is not citable."""
    scored = [
        _x_media_candidate(0.9806),
        _youtube_post_candidate(0.9618),
    ]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)

    assert result.verdict == "MATCH"
    assert result.best.candidate.page_url == "https://www.youtube.com/watch?v=nDj8MIyitUs"
    assert result.best.decision == "ACCEPT"

    # The X hit is still accepted evidence — just not the headline.
    x_row = next(r for r in result.all_scored if "twimg" in r.candidate.page_url)
    assert x_row.decision == "corroborating"


def test_headline_selection_never_affects_the_accept_decision_itself():
    """R-03/D-35: every candidate in `above` already independently passed
    threshold+margin BEFORE headline selection runs. Citability only
    changes WHICH one is displayed as ACCEPT, never whether the run is a
    MATCH at all, and never a candidate's own score."""
    scored = [_x_media_candidate(0.9806), _youtube_post_candidate(0.9618)]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)

    scores = {r.candidate.page_url: r.score for r in result.all_scored}
    assert scores["https://pbs.twimg.com/media/HRSsRstbMAEzQxl.jpg?name=orig"] == 0.9806
    assert scores["https://www.youtube.com/watch?v=nDj8MIyitUs"] == 0.9618


def test_single_candidate_still_becomes_headline_even_if_not_citable():
    """No cluster to prefer within — a lone accepted candidate is still
    the headline regardless of citability. Citability only matters when
    there is a CHOICE between multiple agreeing candidates."""
    scored = [_x_media_candidate(0.9806)]
    result = score_candidates(scored, POLICY, allowed_domains=SOCIAL_ALLOW)

    assert result.verdict == "MATCH"
    assert result.best.candidate.page_url == "https://pbs.twimg.com/media/HRSsRstbMAEzQxl.jpg?name=orig"


def test_content_kind_post_preferred_over_profile_when_both_citable():
    """Two citable pages, same platform family: a real post beats a
    profile page even if the profile scored slightly higher."""
    post = (
        Candidate(
            image_url="https://avatars.githubusercontent.com/u/1?v=4",
            page_url="https://github.com/someone",  # profile (bare github.com/<user>)
            source="gcv_web_detection",
        ),
        0.95,
        1,
    )
    yt_post = _youtube_post_candidate(0.94)
    result = score_candidates([post, yt_post], POLICY, allowed_domains=SOCIAL_ALLOW)

    assert result.verdict == "MATCH"
    assert result.best.candidate.page_url == "https://www.youtube.com/watch?v=nDj8MIyitUs"
