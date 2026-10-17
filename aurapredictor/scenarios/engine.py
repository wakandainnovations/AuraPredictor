"""Core scoring engine shared by every what-if handler in handlers.py: load the
trained model once, fetch a movie's real feature row from the DB, score it, and
score a modified copy of that row. Every scenario question in this project reduces
to "take a real row, change a few fields the same way training-time feature
building would compute them, re-run predict()".
"""

from __future__ import annotations

import json
import math
import pickle
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from catboost import CatBoostRegressor

from aurapredictor.constants import MOVIES_TABLE
from aurapredictor.db import fetch_all
from aurapredictor.features.build_features import (
    build_actor_index,
    build_director_filmography,
    build_movie_index,
    build_person_filmography,
    load_indian_actor_rows,
    load_indian_movies,
)
from aurapredictor.model.dataset import (
    RAW_ROWS_SQL,
    multi_hot,
    row_to_record,
    split_genres,
    split_languages,
    log_budget,
    split_production_companies,
)
from aurapredictor.track_record import load_people_films

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"


class ArtifactsNotFoundError(RuntimeError):
    pass


@dataclass
class ScenarioContext:
    """Everything the handlers need, built once per process: the trained model,
    the person-filmography indexes (actor/director/music-director history), and
    the year -> (gdp, inflation) lookup used by the release-date scenario."""

    model: CatBoostRegressor
    binarizer: Any
    feature_layout: dict[str, Any]
    keep_companies: set[str]
    actor_index: dict
    actor_filmography: dict
    director_filmography: dict
    year_macro: dict[int, dict[str, float]] = field(default_factory=dict)
    language_binarizer: Any = None
    company_binarizer: Any = None
    # release_date -> (release_event_type, release_event_name) as recorded on other
    # movies released that exact day, so the release-date what-if can move the event too.
    date_events: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    # role ("lead_actor" / "director" / "music_director") -> person -> [(release_date,
    # that film's *_movie_count)], so a what-if that swaps someone in can look up the
    # new person's career count as of the scenario's release date.
    people_movie_counts: dict[str, dict[str, list[tuple[str, int]]]] = field(default_factory=dict)
    # role -> person -> their films in MOVIES_TABLE, for the track-record features.
    people_films: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)


@lru_cache(maxsize=1)
def load_context() -> ScenarioContext:
    model_path = ARTIFACTS_DIR / "model.cbm"
    binarizer_path = ARTIFACTS_DIR / "genre_binarizer.pkl"
    language_binarizer_path = ARTIFACTS_DIR / "language_binarizer.pkl"
    company_binarizer_path = ARTIFACTS_DIR / "production_company_binarizer.pkl"
    layout_path = ARTIFACTS_DIR / "feature_layout.json"
    required = (model_path, binarizer_path, language_binarizer_path, company_binarizer_path, layout_path)
    if not all(path.exists() for path in required):
        raise ArtifactsNotFoundError(
            "No trained model found in aurapredictor/artifacts/. Run "
            "scripts/build_features_table.py then scripts/train_model.py first."
        )

    model = CatBoostRegressor()
    model.load_model(str(model_path))
    with open(binarizer_path, "rb") as f:
        binarizer = pickle.load(f)
    with open(language_binarizer_path, "rb") as f:
        language_binarizer = pickle.load(f)
    with open(company_binarizer_path, "rb") as f:
        company_binarizer = pickle.load(f)
    with open(layout_path) as f:
        layout = json.load(f)

    movies = load_indian_movies()
    actor_rows = load_indian_actor_rows()
    movie_index = build_movie_index(movies)
    actor_index = build_actor_index(actor_rows)
    actor_filmography = build_person_filmography(actor_rows, movie_index)
    director_filmography = build_director_filmography(movies)

    from aurapredictor.dates import release_year as parse_release_year

    # load_indian_movies() doesn't carry the macro columns, so read them directly.
    macro_rows = fetch_all(
        f"""
        SELECT release_date, gdp_usd_billions, inflation_rate_pct
        FROM {MOVIES_TABLE}
        WHERE gdp_usd_billions IS NOT NULL OR inflation_rate_pct IS NOT NULL
        """
    )
    year_macro: dict[int, dict[str, float]] = {}
    for movie in macro_rows:
        year = parse_release_year(movie["release_date"])
        if year is None or year in year_macro:
            continue
        if movie.get("gdp_usd_billions") or movie.get("inflation_rate_pct"):
            year_macro[year] = {
                "gdp_usd_billions": movie.get("gdp_usd_billions"),
                "inflation_rate_pct": movie.get("inflation_rate_pct"),
            }

    return ScenarioContext(
        people_movie_counts=load_people_movie_counts(),
        people_films=load_people_films(),
        model=model,
        binarizer=binarizer,
        feature_layout=layout,
        keep_companies=set(layout.get("top_production_companies", [])),
        actor_index=actor_index,
        actor_filmography=actor_filmography,
        director_filmography=director_filmography,
        year_macro=year_macro,
        language_binarizer=language_binarizer,
        company_binarizer=company_binarizer,
        date_events=load_date_events(),
    )


def load_date_events() -> dict[str, tuple[str | None, str | None]]:
    """Most common (event type, event name) per exact release date in MOVIES_TABLE."""
    rows = fetch_all(
        f"""
        SELECT release_date, release_event_type, release_event_name, count(*) AS n
        FROM {MOVIES_TABLE}
        WHERE release_event_type IS NOT NULL
        GROUP BY 1, 2, 3
        ORDER BY n DESC
        """
    )
    events: dict[str, tuple[str | None, str | None]] = {}
    for row in rows:
        events.setdefault(row["release_date"], (row["release_event_type"], row["release_event_name"]))
    return events


_PEOPLE_COUNT_COLUMNS = {
    "lead_actor": [(f"lead_actor_{i}", f"lead_actor_{i}_movie_count") for i in range(1, 6)],
    "director": [("director", "director_movie_count")],
    "music_director": [("music_director", "music_director_movie_count")],
}


def load_people_movie_counts() -> dict[str, dict[str, list[tuple[str, int]]]]:
    rows = fetch_all(
        f"""
        SELECT release_date, {", ".join(col for pairs in _PEOPLE_COUNT_COLUMNS.values() for pair in pairs for col in pair)}
        FROM {MOVIES_TABLE}
        """
    )
    counts: dict[str, dict[str, list[tuple[str, int]]]] = {role: {} for role in _PEOPLE_COUNT_COLUMNS}
    for row in rows:
        for role, pairs in _PEOPLE_COUNT_COLUMNS.items():
            for name_col, count_col in pairs:
                name, count = (row[name_col] or "").strip(), row[count_col]
                if name and count is not None and row["release_date"]:
                    counts[role].setdefault(name, []).append((row["release_date"], count))
    return counts


def get_movie_row(movie_name: str, release_date: str, language: str) -> dict[str, Any] | None:
    """Fetches one movie's raw feature-source row -- same shape as
    dataset.load_raw_rows(), for exactly one movie -- to use as a scenario baseline."""
    rows = fetch_all(
        RAW_ROWS_SQL
        + """
        WHERE f.movie_name = %s AND f.release_date = %s AND f.language = %s
        """,
        (movie_name, release_date, language),
    )
    return rows[0] if rows else None


def find_movies(name_query: str, limit: int = 10) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT movie_name, release_date, language
        FROM movie_prediction_features
        WHERE lower(movie_name) LIKE lower(%s)
        ORDER BY release_date DESC
        LIMIT %s
        """,
        (f"%{name_query}%", limit),
    )


def score_row(context: ScenarioContext, raw_row: dict[str, Any]) -> float:
    """Returns predicted revenue (real units, not log-space) for one raw feature-
    source row (same shape as dataset.load_raw_rows())."""
    record = row_to_record(raw_row, context.keep_companies, context.people_films)
    record_df = pd.DataFrame([record])
    genre_df = multi_hot(context.binarizer, [split_genres(raw_row.get("genre"))], "genre")
    language_df = multi_hot(
        context.language_binarizer, [split_languages(raw_row.get("language"))], "language"
    )

    company_df = multi_hot(
        context.company_binarizer, [split_production_companies(raw_row.get("production_companies"))], "company"
    )

    X = pd.concat([record_df, genre_df, language_df, company_df], axis=1)
    # Line up with the exact training-time column order/set; any genre/language the
    # model never saw at training time was already dropped by the binarizer's transform().
    X = X.reindex(columns=context.feature_layout["columns"], fill_value=0)
    for col in context.feature_layout["categorical_features"]:
        X[col] = X[col].fillna("Unknown").astype(str)

    log_prediction = context.model.predict(X)[0]
    if context.feature_layout.get("target") == "log_revenue_budget_ratio":
        log_prediction += log_budget(X)[0]
    return math.expm1(log_prediction)
