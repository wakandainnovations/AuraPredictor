"""Pure-logic tests for the enrichment pipeline's deterministic pieces -- TMDB id
resolution strategy and trailer/teaser date selection -- with a fake TMDBClient so
no real network call or API key is needed. The DB-writing half of enrich_movies.py
(_enrich_one) is exercised manually against real TMDB/YouTube keys by the user, not
here -- see run_enrichment.py's docstring.
"""

from __future__ import annotations

from aurapredictor.enrichment.enrich_movies import _collect_video_ids, _resolve_tmdb_id
from aurapredictor.enrichment.tmdb_client import directors_from_credits, earliest_video_date


class FakeTMDBClient:
    def __init__(self, find_result=None, search_result=None):
        self._find_result = find_result
        self._search_result = search_result
        self.find_calls = []
        self.search_calls = []

    def find_by_imdb_id(self, imdb_id):
        self.find_calls.append(imdb_id)
        return self._find_result

    def search_movie(self, title, year=None):
        self.search_calls.append((title, year))
        return self._search_result


def test_resolve_uses_existing_numeric_tmdb_id_without_any_api_call():
    client = FakeTMDBClient()
    result = _resolve_tmdb_id(client, {"id": "1492516", "movie_name": "X", "release_date": "2020"})
    assert result == 1492516
    assert client.find_calls == [] and client.search_calls == []


def test_resolve_uses_find_endpoint_for_imdb_id():
    client = FakeTMDBClient(find_result={"id": 42})
    result = _resolve_tmdb_id(client, {"id": "tt2140465", "movie_name": "X", "release_date": "2020"})
    assert result == 42
    assert client.find_calls == ["tt2140465"]


def test_resolve_falls_back_to_search_when_id_missing():
    client = FakeTMDBClient(search_result={"id": 99})
    result = _resolve_tmdb_id(client, {"id": "", "movie_name": "Some Movie", "release_date": "2019-05-01"})
    assert result == 99
    assert client.search_calls == [("Some Movie", 2019)]


def test_resolve_returns_none_when_nothing_matches():
    client = FakeTMDBClient(search_result=None)
    result = _resolve_tmdb_id(client, {"id": None, "movie_name": "Unknown", "release_date": None})
    assert result is None


def test_directors_from_credits_filters_to_director_job():
    credits_ = {
        "crew": [
            {"name": "A", "job": "Director"},
            {"name": "B", "job": "Producer"},
            {"name": "C", "job": "Director"},
        ]
    }
    assert directors_from_credits(credits_) == ["A", "C"]


def test_earliest_video_date_picks_earliest_youtube_trailer():
    videos = [
        {"type": "Trailer", "site": "YouTube", "key": "late", "published_at": "2022-02-01"},
        {"type": "Trailer", "site": "YouTube", "key": "early", "published_at": "2022-01-01"},
        {"type": "Trailer", "site": "Vimeo", "key": "ignored", "published_at": "2021-01-01"},
        {"type": "Teaser", "site": "YouTube", "key": "not-a-trailer", "published_at": "2020-01-01"},
    ]
    assert earliest_video_date(videos, "Trailer") == ("2022-01-01", "early")


def test_earliest_video_date_returns_none_when_no_match():
    assert earliest_video_date([], "Trailer") is None


def test_collect_video_ids_handles_either_side_missing():
    # Regression test: a movie with a trailer but no teaser (or vice versa) must
    # not crash trying to unpack the missing side as a tuple.
    assert _collect_video_ids(("2022-01-01", "trailer-key"), None) == ["trailer-key"]
    assert _collect_video_ids(None, ("2022-01-01", "teaser-key")) == ["teaser-key"]
    assert _collect_video_ids(None, None) == []
    assert _collect_video_ids(("2022-01-01", "t1"), ("2022-01-02", "t2")) == ["t1", "t2"]
