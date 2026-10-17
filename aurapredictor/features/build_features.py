"""Builds `movie_prediction_features`: one row per Indian movie in MOVIES_TABLE
(constants.MOVIES_TABLE, currently `india_movies_data_collection`) with a real
revenue and budget, carrying the revenue:budget ratio, hit/flop/blockbuster tier,
and popularity-index + last-3-movie hit:flop history for the movie's lead actor,
primary director, and music director.

Join strategy: MOVIES_TABLE and actors_data_collection don't share a reliable
exact release_date (one table sometimes has a full date, the other just a year for
the same film). This project follows the same fuzzy key MOVIES_TABLE's own indexes
already use -- lower(trim(movie_name)) + left(release_date, 4) -- rather than
inventing a new one (see e.g. india_movies_data_collection_lower_left_idx in the
live schema).

Runs entirely in memory against the Indian-language subset of both tables (tens of
thousands of rows, not millions), rather than one DB round trip per person, since a
person's filmography needs looking up for every movie they appear in.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from aurapredictor.constants import MOVIES_TABLE, PRIOR_FILMS_WINDOW, is_indian_language, performance_tier
from aurapredictor.db import execute, execute_many, fetch_all
from aurapredictor.dates import release_year as parse_release_year
from aurapredictor.history import hit_flop_ratio_last_n, prior_films_count
from aurapredictor.people import (
    lead_actor_for_movie,
    music_director_for_movie,
    primary_director,
)

TABLE_NAME = "movie_prediction_features"

_WS_RE = re.compile(r"\s+")


def _norm_name(name: str | None) -> str:
    return _WS_RE.sub(" ", (name or "").strip().lower())


def _movie_key(movie_name: str | None, release_date: str | None) -> tuple[str, int | None]:
    return _norm_name(movie_name), parse_release_year(release_date)


def load_indian_movies() -> list[dict[str, Any]]:
    """All Indian-language rows from movies_data_collection, any revenue/budget --
    needed both as prediction targets (where revenue & budget > 0) and as the
    source of directors' full filmographies (prior movies don't need financials to
    count toward prior_films_count, only toward the hit/flop tiers)."""
    rows = fetch_all(
        f"""
        SELECT movie_name, release_date, language, revenue, budget, directors
        FROM {MOVIES_TABLE}
        WHERE language IS NOT NULL
        """
    )
    return [row for row in rows if is_indian_language(row["language"])]


def load_indian_actor_rows() -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT actor_name, movie_name, release_date, language, character_name, role_position
        FROM actors_data_collection
        WHERE language IS NOT NULL
        """
    )
    return [row for row in rows if is_indian_language(row["language"])]


def build_movie_index(movies: list[dict[str, Any]]) -> dict[tuple[str, int | None], list[dict[str, Any]]]:
    index: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for movie in movies:
        index[_movie_key(movie["movie_name"], movie["release_date"])].append(movie)
    return index


def build_actor_index(actor_rows: list[dict[str, Any]]) -> dict[tuple[str, int | None], list[dict[str, Any]]]:
    index: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for row in actor_rows:
        index[_movie_key(row["movie_name"], row["release_date"])].append(row)
    return index


def build_person_filmography(
    actor_rows: list[dict[str, Any]],
    movie_index: dict[tuple[str, int | None], list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """actor_name -> that person's other movie appearances (release_date, revenue,
    budget), deduped by (normalized movie name, year) so a person credited on
    multiple scraped rows for what is really the same release (a common duplicate
    in this dataset -- e.g. alternate/regional-title rows) isn't double-counted."""
    filmography: dict[str, dict[tuple[str, int | None], dict[str, Any]]] = defaultdict(dict)

    for row in actor_rows:
        key = _movie_key(row["movie_name"], row["release_date"])
        movies_for_key = movie_index.get(key)
        if not movies_for_key:
            continue
        movie = movies_for_key[0]
        filmography[row["actor_name"]][key] = {
            "release_date": movie["release_date"],
            "revenue": movie["revenue"],
            "budget": movie["budget"],
        }

    return {person: list(movies.values()) for person, movies in filmography.items()}


def build_director_filmography(movies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    filmography: dict[str, dict[tuple[str, int | None], dict[str, Any]]] = defaultdict(dict)

    for movie in movies:
        director = primary_director(movie["directors"])
        if not director:
            continue
        key = _movie_key(movie["movie_name"], movie["release_date"])
        filmography[director][key] = {
            "release_date": movie["release_date"],
            "revenue": movie["revenue"],
            "budget": movie["budget"],
        }

    return {person: list(movies.values()) for person, movies in filmography.items()}


def _person_feature_block(
    prefix: str,
    person: str | None,
    filmography: dict[str, list[dict[str, Any]]],
    as_of_date: str,
) -> dict[str, Any]:
    if not person:
        return {
            f"{prefix}_name": None,
            f"{prefix}_prior_films_count": None,
            f"{prefix}_hits_last3": None,
            f"{prefix}_flops_last3": None,
            f"{prefix}_hit_flop_ratio_last3": None,
        }

    person_history = filmography.get(person, [])
    hit_flop = hit_flop_ratio_last_n(person_history, as_of_date, n=PRIOR_FILMS_WINDOW)
    return {
        f"{prefix}_name": person,
        f"{prefix}_prior_films_count": prior_films_count(person_history, as_of_date),
        f"{prefix}_hits_last3": hit_flop["hits"],
        f"{prefix}_flops_last3": hit_flop["flops"],
        f"{prefix}_hit_flop_ratio_last3": hit_flop["hit_flop_ratio"],
    }


def compute_feature_row(
    movie: dict[str, Any],
    actor_index: dict[tuple[str, int | None], list[dict[str, Any]]],
    actor_filmography: dict[str, list[dict[str, Any]]],
    director_filmography: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    as_of_date = movie["release_date"]
    ratio = float(movie["revenue"]) / float(movie["budget"])

    cast_and_crew = actor_index.get(_movie_key(movie["movie_name"], movie["release_date"]), [])
    lead_actor = lead_actor_for_movie(cast_and_crew)
    music_director = music_director_for_movie(cast_and_crew)
    director = primary_director(movie["directors"])

    row = {
        "movie_name": movie["movie_name"],
        "release_date": movie["release_date"],
        "language": movie["language"],
        "revenue_budget_ratio": ratio,
        "performance_tier": performance_tier(ratio),
    }
    row.update(_person_feature_block("lead_actor", lead_actor, actor_filmography, as_of_date))
    row.update(_person_feature_block("director", director, director_filmography, as_of_date))
    row.update(_person_feature_block("music_director", music_director, actor_filmography, as_of_date))
    return row


CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    movie_name text NOT NULL,
    release_date text NOT NULL,
    language text NOT NULL,
    revenue_budget_ratio numeric,
    performance_tier text,
    lead_actor_name text,
    lead_actor_prior_films_count integer,
    lead_actor_hits_last3 integer,
    lead_actor_flops_last3 integer,
    lead_actor_hit_flop_ratio_last3 numeric,
    director_name text,
    director_prior_films_count integer,
    director_hits_last3 integer,
    director_flops_last3 integer,
    director_hit_flop_ratio_last3 numeric,
    music_director_name text,
    music_director_prior_films_count integer,
    music_director_hits_last3 integer,
    music_director_flops_last3 integer,
    music_director_hit_flop_ratio_last3 numeric,
    computed_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (movie_name, release_date, language)
)
"""

_COLUMNS = [
    "movie_name",
    "release_date",
    "language",
    "revenue_budget_ratio",
    "performance_tier",
    "lead_actor_name",
    "lead_actor_prior_films_count",
    "lead_actor_hits_last3",
    "lead_actor_flops_last3",
    "lead_actor_hit_flop_ratio_last3",
    "director_name",
    "director_prior_films_count",
    "director_hits_last3",
    "director_flops_last3",
    "director_hit_flop_ratio_last3",
    "music_director_name",
    "music_director_prior_films_count",
    "music_director_hits_last3",
    "music_director_flops_last3",
    "music_director_hit_flop_ratio_last3",
]

_UPSERT_SQL = f"""
INSERT INTO {TABLE_NAME} ({", ".join(_COLUMNS)})
VALUES %s
ON CONFLICT (movie_name, release_date, language) DO UPDATE SET
    {", ".join(f"{col} = EXCLUDED.{col}" for col in _COLUMNS if col not in ("movie_name", "release_date", "language"))},
    computed_at = now()
"""


def build_all_feature_rows() -> list[dict[str, Any]]:
    movies = load_indian_movies()
    actor_rows = load_indian_actor_rows()

    movie_index = build_movie_index(movies)
    actor_index = build_actor_index(actor_rows)
    actor_filmography = build_person_filmography(actor_rows, movie_index)
    director_filmography = build_director_filmography(movies)

    target_movies = [m for m in movies if m["revenue"] and m["budget"]]

    return [
        compute_feature_row(movie, actor_index, actor_filmography, director_filmography)
        for movie in target_movies
    ]


def run(dry_run: bool = False) -> list[dict[str, Any]]:
    rows = build_all_feature_rows()

    if dry_run:
        print(f"[dry-run] would upsert {len(rows)} rows into {TABLE_NAME}")
        for sample in rows[:5]:
            print(sample)
        return rows

    execute(CREATE_TABLE_SQL)
    execute_many(_UPSERT_SQL, [tuple(row[col] for col in _COLUMNS) for row in rows])
    print(f"Upserted {len(rows)} rows into {TABLE_NAME}")
    return rows
