"""pick_best_match's pure confidence-check logic -- no network. Each test isolates
one of the three checks (trailer/exclusion-marker, word-overlap, publish-date
window) so a future change to one can't silently break another unnoticed."""

from __future__ import annotations

from aurapredictor.enrichment.youtube_search_client import YouTubeQuotaExceededError
from aurapredictor.enrichment.youtube_search_fallback import (
    _conflicts_with_catalog_language,
    _language_query_term,
    pick_best_match,
    run,
)

_RELEASE_DATE = "2023-06-15"


def _candidate(title: str, published_at: str = "2023-05-01T00:00:00Z", video_id: str = "abc123") -> dict:
    return {"video_id": video_id, "title": title, "channel_title": "Some Channel", "published_at": published_at}


def test_matches_a_plausible_official_trailer():
    candidates = [_candidate("Kabali - Official Trailer")]
    result = pick_best_match(candidates, "Kabali", _RELEASE_DATE)
    assert result is not None
    assert result["video_id"] == "abc123"


def test_rejects_candidate_missing_trailer_keyword():
    candidates = [_candidate("Kabali - Full Movie Scene")]
    assert pick_best_match(candidates, "Kabali", _RELEASE_DATE) is None


def test_rejects_review_video_even_with_trailer_keyword():
    candidates = [_candidate("Kabali Trailer Review and Reaction")]
    assert pick_best_match(candidates, "Kabali", _RELEASE_DATE) is None


def test_rejects_real_false_positive_success_trailer_recut():
    """Caught in a manual spot-check before this ran at scale: a post-release
    "success trailer" recut passed every other check because it genuinely
    contains the movie name, the word "trailer", and (at only 25 days out) fit
    an earlier, looser publish window -- this is exactly why that window is
    tight and "success" is an excluded title marker."""
    candidates = [_candidate("Ayogya 2 - 25 Day's Success Trailer", published_at="2023-07-10T00:00:00Z")]
    assert pick_best_match(candidates, "Ayogya 2", "2023-06-15") is None
    # Isolate the title-marker check itself: still rejected even when published
    # within the (now tight) post-release window, so the marker -- not just the
    # window -- is doing real work.
    candidates = [_candidate("Ayogya 2 - Success Trailer", published_at="2023-06-18T00:00:00Z")]
    assert pick_best_match(candidates, "Ayogya 2", "2023-06-15") is None


def test_rejects_real_false_positive_fan_edit_channel():
    """Caught in the same spot-check: a fan "WhatsApp status" edit whose title
    contained the movie name and "trailer" with no other obvious tell besides
    the channel name itself."""
    candidates = [_candidate(
        "karavali kannada movie trailer whatsapp status",
        video_id="bad", published_at="2023-05-01T00:00:00Z",
    )]
    candidates[0]["channel_title"] = "SUSHANTH EDITZ"
    assert pick_best_match(candidates, "Karavali", _RELEASE_DATE) is None


def test_rejects_candidate_missing_movie_name_words():
    candidates = [_candidate("Some Other Movie - Official Trailer")]
    assert pick_best_match(candidates, "Kabali", _RELEASE_DATE) is None


def test_requires_all_significant_words_from_multiword_title():
    candidates = [_candidate("Baahubali - Official Trailer")]
    assert pick_best_match(candidates, "Baahubali: The Beginning", _RELEASE_DATE) is None
    candidates = [_candidate("Baahubali The Beginning - Official Trailer")]
    assert pick_best_match(candidates, "Baahubali: The Beginning", _RELEASE_DATE) is not None


def test_uses_only_primary_spelling_before_comma():
    candidates = [_candidate("Vilaayath Budha - Official Trailer")]
    result = pick_best_match(candidates, "Vilaayath Budha, Vilayath Budha", _RELEASE_DATE)
    assert result is not None


def test_rejects_candidate_published_too_long_before_release():
    candidates = [_candidate("Kabali - Official Trailer", published_at="2018-01-01T00:00:00Z")]
    assert pick_best_match(candidates, "Kabali", _RELEASE_DATE) is None


def test_rejects_candidate_published_after_release_window():
    candidates = [_candidate("Kabali - Official Trailer", published_at="2023-12-01T00:00:00Z")]
    assert pick_best_match(candidates, "Kabali", _RELEASE_DATE) is None


def test_falls_through_to_second_candidate_when_first_fails():
    candidates = [
        _candidate("Kabali Trailer Reaction"),
        _candidate("Kabali - Official Trailer", video_id="good_one"),
    ]
    result = pick_best_match(candidates, "Kabali", _RELEASE_DATE)
    assert result is not None
    assert result["video_id"] == "good_one"


def test_returns_none_for_unparseable_release_date():
    candidates = [_candidate("Kabali - Official Trailer")]
    assert pick_best_match(candidates, "Kabali", "unknown") is None


def test_returns_none_for_empty_candidate_list():
    assert pick_best_match([], "Kabali", _RELEASE_DATE) is None


class _FakeClientQuotaAfterTwo:
    """Raises YouTubeQuotaExceededError on the 3rd call -- simulates the quota
    running out mid-batch."""

    def __init__(self):
        self.calls = 0

    def search_videos(self, query, **kwargs):
        self.calls += 1
        if self.calls > 2:
            raise YouTubeQuotaExceededError("quotaExceeded")
        return [_candidate(f"{query} - Official Trailer")]


def test_run_stops_immediately_on_quota_exceeded(monkeypatch):
    import aurapredictor.enrichment.youtube_search_fallback as mod

    fake_client = _FakeClientQuotaAfterTwo()
    monkeypatch.setattr(mod, "ensure_table", lambda: None)
    monkeypatch.setattr(mod.video_snapshots, "ensure_table", lambda: None)
    monkeypatch.setattr(mod.video_snapshots, "upsert_video_id", lambda *a, **k: None)
    monkeypatch.setattr(mod, "load_config", lambda: None)
    monkeypatch.setattr(mod, "YouTubeSearchClient", lambda config: fake_client)
    monkeypatch.setattr(mod, "_record_status", lambda *a, **k: None)
    monkeypatch.setattr(
        mod,
        "fetch_pending_movies",
        lambda limit: [
            {"movie_name": f"Movie{i}", "release_date": _RELEASE_DATE, "language": "hindi"} for i in range(5)
        ],
    )

    matched, unmatched, quota_exceeded = run(limit=5)

    assert quota_exceeded is True
    assert matched == 2  # first two calls succeeded before the quota hit
    assert unmatched == 0
    assert fake_client.calls == 3  # stopped right after the failing 3rd call, not all 5


# --- Language-conflict checks, added after a 2026-09-18 audit found 9/95 ---
# --- confirmed matches were a pan-India film's trailer in the WRONG language ---

def test_conflicts_with_catalog_language_when_title_names_a_different_language():
    assert _conflicts_with_catalog_language("mark (hindi) - official trailer", "tamil") is True


def test_no_conflict_when_title_names_the_same_language():
    assert _conflicts_with_catalog_language("mark (tamil) - official trailer", "tamil") is False


def test_no_conflict_when_title_names_no_language_at_all():
    assert _conflicts_with_catalog_language("mark - official trailer", "tamil") is False


def test_odia_and_oriya_treated_as_the_same_language():
    assert _conflicts_with_catalog_language("some movie (odia) - trailer", "oriya") is False
    assert _conflicts_with_catalog_language("some movie (oriya) - trailer", "odia") is False


def test_pick_best_match_rejects_real_wrong_language_case():
    """The exact real case caught in the audit: catalog says Tamil, the top
    result is explicitly the Hindi version."""
    candidates = [
        _candidate("Mark (Hindi) - Official Trailer"),
        _candidate("Mark (Tamil) - Official Trailer", video_id="correct_one"),
    ]
    result = pick_best_match(candidates, "Mark", _RELEASE_DATE, "tamil")
    assert result is not None
    assert result["video_id"] == "correct_one"


def test_pick_best_match_rejects_video_game_trailer():
    """The other real case caught in the audit: a generic single-word catalog
    title ("Screen Bound") collided with an unrelated video game's trailer."""
    candidates = [_candidate("Screenbound - Official Gameplay Trailer")]
    assert pick_best_match(candidates, "Screen Bound", _RELEASE_DATE, "hindi") is None


def test_language_query_term_returns_recognized_language_name():
    assert _language_query_term("Tamil") == "tamil"
    assert _language_query_term("hindi, english") == "hindi"


def test_language_query_term_none_for_unrecognized_or_missing():
    assert _language_query_term(None) is None
    assert _language_query_term("xx") is None
