"""Thin wrapper around the TMDB v3 API endpoints this project needs. Every method
returns the parsed JSON body (or a sub-list of it) -- callers in enrich_movies.py
decide what to do with it. Kept dependency-light (just `requests`) since this only
needs a handful of endpoints.
"""

from __future__ import annotations

from typing import Any

import requests

from aurapredictor.config import Config

_BASE_URL = "https://api.themoviedb.org/3"


class TMDBClient:
    def __init__(self, config: Config):
        token = config.tmdb_read_access_token
        self._api_key = config.tmdb_api_key
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        if self._api_key and "Authorization" not in self._session.headers:
            params["api_key"] = self._api_key
        response = self._session.get(f"{_BASE_URL}{path}", params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def find_by_imdb_id(self, imdb_id: str) -> dict[str, Any] | None:
        """/find/{imdb_id}?external_source=imdb_id -- for movies_data_collection
        rows whose `id` column already holds an IMDb tt-id rather than a TMDB id."""
        result = self._get(f"/find/{imdb_id}", {"external_source": "imdb_id"})
        movies = result.get("movie_results", [])
        return movies[0] if movies else None

    def search_movie(self, title: str, year: int | None = None) -> dict[str, Any] | None:
        params: dict[str, Any] = {"query": title}
        if year:
            params["year"] = year
        result = self._get("/search/movie", params)
        movies = result.get("results", [])
        return movies[0] if movies else None

    def movie_details(self, tmdb_id: int) -> dict[str, Any]:
        return self._get(f"/movie/{tmdb_id}")

    def movie_credits(self, tmdb_id: int) -> dict[str, Any]:
        """Returns {"cast": [...], "crew": [...]}. Directors are crew entries with
        job == "Director"."""
        return self._get(f"/movie/{tmdb_id}/credits")

    def movie_videos(self, tmdb_id: int) -> list[dict[str, Any]]:
        """List of {type, site, key, published_at, ...}; filter by type in
        ("Trailer", "Teaser") and site == "YouTube" to get trailer/teaser keys."""
        return self._get(f"/movie/{tmdb_id}/videos").get("results", [])

    def person_movie_credits(self, person_id: int) -> dict[str, Any]:
        return self._get(f"/person/{person_id}/movie_credits")


def directors_from_credits(credits: dict[str, Any]) -> list[str]:
    return [member["name"] for member in credits.get("crew", []) if member.get("job") == "Director"]


def earliest_video_date(videos: list[dict[str, Any]], video_type: str) -> tuple[str, str] | None:
    """Returns (published_at, youtube_key) for the earliest YouTube video of the
    given type ("Trailer" or "Teaser"), or None if there isn't one."""
    candidates = [
        v for v in videos if v.get("type") == video_type and v.get("site") == "YouTube" and v.get("key")
    ]
    if not candidates:
        return None
    earliest = min(candidates, key=lambda v: v.get("published_at") or "9999")
    return earliest.get("published_at"), earliest["key"]
