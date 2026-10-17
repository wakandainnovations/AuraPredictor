#!/usr/bin/env python3
"""Sample of the long-format ("one row per movie x feature x as-of date") feature
store, populated from a handful of india_movies_data_collection movies chosen to
show different levels of data coverage: new movies with point-in-time trailer views,
movies with only current YouTube stats, and older movies with no YouTube data.

Two tables:
  feature_registry      -- one row per feature: what it means, its type, and when
                           it is knowable relative to release (leakage class).
  movie_feature_values  -- one row per (movie, feature, as_of_date). A missing
                           value has NO row, so a new feature is just new rows,
                           never a schema change. The same feature can hold
                           several values at different as-of dates (e.g.
                           trailer_views 7 days before release AND today).

    python scripts/build_sample_feature_store.py --per-bucket 4
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.constants import MOVIES_TABLE
from aurapredictor.dates import parse_release_date
from aurapredictor.db import execute, execute_many, fetch_all

CREATE_REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS feature_registry (
    feature_name text PRIMARY KEY,
    value_type text NOT NULL CHECK (value_type IN ('numeric', 'text')),
    role text NOT NULL CHECK (role IN ('feature', 'target')),
    timing text NOT NULL CHECK (timing IN ('static', 'time_varying', 'post_release', 'target')),
    source_column text NOT NULL,
    description text NOT NULL,
    added_on date NOT NULL DEFAULT CURRENT_DATE
)
"""

CREATE_VALUES_SQL = """
CREATE TABLE IF NOT EXISTS movie_feature_values (
    movie_name text NOT NULL,
    release_date date NOT NULL,
    language text NOT NULL,
    feature_name text NOT NULL REFERENCES feature_registry (feature_name),
    as_of_date date NOT NULL,
    value_num numeric,
    value_text text,
    source text NOT NULL,
    collected_at timestamp with time zone,
    PRIMARY KEY (movie_name, release_date, language, feature_name, as_of_date),
    CHECK ((value_num IS NULL) <> (value_text IS NULL))
)
"""

# (feature_name, value_type, role, timing, source_column, description)
# timing:
#   static        -- known before release and doesn't change; as_of_date = release_date
#   time_varying  -- a snapshot; as_of_date says when. Only rows with
#                    as_of_date < release_date are safe for a pre-release model.
#   post_release  -- only knowable after release (leaks the outcome); never a
#                    pre-release input, kept for analysis only
#   target        -- what the model predicts
REGISTRY: list[tuple[str, str, str, str, str, str]] = [
    ("language", "text", "feature", "static", "language", "Primary release language"),
    ("genre", "text", "feature", "static", "genre", "Comma-separated genre list"),
    ("budget", "numeric", "feature", "static", "budget", "Production budget (nominal)"),
    ("runtime", "numeric", "feature", "static", "runtime", "Runtime in minutes"),
    ("production_companies", "text", "feature", "static", "production_companies", "Production companies"),
    ("directors", "text", "feature", "static", "directors", "Director(s)"),
    ("cbfc_rating", "text", "feature", "static", "cbfc_rating", "CBFC certificate (U, UA, A)"),
    ("number_of_screens", "numeric", "feature", "static", "number_of_screens", "Screens at release"),
    ("release_event_type", "text", "feature", "static", "release_event_type", "Festival/Holiday/Normal/... release window"),
    ("remake_of", "text", "feature", "static", "remake_of", "REMAKE / NOT_REMAKE"),
    ("gdp_usd_billions", "numeric", "feature", "static", "gdp_usd_billions", "India GDP in release year"),
    ("inflation_rate_pct", "numeric", "feature", "static", "inflation_rate_pct", "India inflation in release year"),
    ("trailer_days_to_release", "numeric", "feature", "static", "trailer_days_to_release", "Days between trailer upload and release (negative = uploaded after release)"),
    ("teaser_days_to_release", "numeric", "feature", "static", "teaser_days_to_release", "Days between teaser upload and release"),
    ("lead_prior_films_count", "numeric", "feature", "static", "lead_prior_films_count", "Lead actor's prior films"),
    ("director_prior_films_count", "numeric", "feature", "static", "director_prior_films_count", "Director's prior films"),
    ("director_prior_hit_rate", "numeric", "feature", "static", "director_prior_hit_rate", "Director's prior hit rate"),
    ("ensemble_avg_prior_hit_rate", "numeric", "feature", "static", "ensemble_avg_prior_hit_rate", "Cast's average prior hit rate"),
    ("trailer_views", "numeric", "feature", "time_varying", "trailer_views / trailer_views_7d_prior_to_release", "Trailer YouTube views as of as_of_date"),
    ("teaser_views", "numeric", "feature", "time_varying", "teaser_views / teaser_views_7d_prior_to_release", "Teaser YouTube views as of as_of_date"),
    ("song_views", "numeric", "feature", "time_varying", "song_views", "First song YouTube views as of as_of_date"),
    ("trailer_comments", "numeric", "feature", "time_varying", "trailer_comments", "Trailer YouTube comments as of as_of_date"),
    ("teaser_comments", "numeric", "feature", "time_varying", "teaser_comments", "Teaser YouTube comments as of as_of_date"),
    ("rating_10", "numeric", "feature", "post_release", "rating_10", "Audience rating /10 (only exists after release)"),
    ("revenue", "numeric", "target", "target", "revenue", "Box-office revenue (nominal)"),
]

# Picks a mix of movies with rich, partial, and sparse data, a few per bucket.
SELECT_SAMPLE_SQL = f"""
WITH buckets AS (
    SELECT *, CASE
        WHEN trailer_views_7d_prior_to_release IS NOT NULL THEN 1  -- point-in-time views
        WHEN trailer_views IS NOT NULL THEN 2                      -- current views only
        WHEN release_date < '2010-01-01' THEN 3                    -- old, pre-YouTube-era
        WHEN number_of_screens IS NOT NULL THEN 4                  -- screens, no YouTube
        ELSE 5                                                     -- sparse
    END AS bucket
    FROM {MOVIES_TABLE}
    WHERE revenue IS NOT NULL
), ranked AS (
    SELECT *, row_number() OVER (PARTITION BY bucket ORDER BY release_date DESC, movie_name) AS rn
    FROM buckets
)
SELECT * FROM ranked WHERE rn <= %s ORDER BY bucket, release_date DESC
"""


# A published trailer/teaser with exactly 0 views is a "not found" placeholder in
# the source data, not a real count -- storing it would teach the model that
# these movies had no marketing reach.
ZERO_MEANS_MISSING = frozenset({"trailer_views", "teaser_views"})


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def movie_to_feature_rows(movie: dict[str, Any]) -> list[tuple[Any, ...]]:
    release = parse_release_date(movie["release_date"])
    youtube_checked = parse_release_date(movie.get("youtube_last_checked"))
    key = (movie["movie_name"], release, movie["language"])
    rows: list[tuple[Any, ...]] = []

    def add(feature: str, value: Any, as_of: date | None, source: str, collected_at: str | None = None) -> None:
        if _is_blank(value) or as_of is None or (feature in ZERO_MEANS_MISSING and value == 0):
            return
        is_text = isinstance(value, str)
        rows.append(
            (*key, feature, as_of, None if is_text else value, value if is_text else None, source, collected_at)
        )

    for feature, _, _, timing, source_column, _ in REGISTRY:
        if timing in ("static", "post_release", "target"):
            add(feature, movie.get(source_column), release, f"{MOVIES_TABLE}.{source_column}")

    # The same feature at two points in time: 7 days before release (safe for a
    # pre-release model) and whenever YouTube was last checked (includes
    # post-release views for released movies, so it would leak).
    seven_days_before = release - timedelta(days=7)
    for feature in ("trailer_views", "teaser_views"):
        add(feature, movie.get(f"{feature}_7d_prior_to_release"), seven_days_before,
            f"{MOVIES_TABLE}.{feature}_7d_prior_to_release")
    for feature in ("trailer_views", "teaser_views", "song_views", "trailer_comments", "teaser_comments"):
        add(feature, movie.get(feature), youtube_checked, f"{MOVIES_TABLE}.{feature}",
            movie.get("youtube_last_checked"))

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--per-bucket", type=int, default=4, help="Movies per coverage bucket (5 buckets)")
    args = parser.parse_args()

    execute(CREATE_REGISTRY_SQL)
    execute(CREATE_VALUES_SQL)
    execute_many(
        """
        INSERT INTO feature_registry (feature_name, value_type, role, timing, source_column, description)
        VALUES %s
        ON CONFLICT (feature_name) DO UPDATE SET
            value_type = EXCLUDED.value_type, role = EXCLUDED.role, timing = EXCLUDED.timing,
            source_column = EXCLUDED.source_column, description = EXCLUDED.description
        """,
        REGISTRY,
    )

    # Upserts never remove rows, so clear values written before ZERO_MEANS_MISSING existed.
    execute(
        "DELETE FROM movie_feature_values WHERE feature_name = ANY(%s) AND value_num = 0",
        (sorted(ZERO_MEANS_MISSING),),
    )

    movies = fetch_all(SELECT_SAMPLE_SQL, (args.per_bucket,))
    value_rows = [row for movie in movies for row in movie_to_feature_rows(movie)]
    execute_many(
        """
        INSERT INTO movie_feature_values
            (movie_name, release_date, language, feature_name, as_of_date, value_num, value_text, source, collected_at)
        VALUES %s
        ON CONFLICT (movie_name, release_date, language, feature_name, as_of_date) DO UPDATE SET
            value_num = EXCLUDED.value_num, value_text = EXCLUDED.value_text,
            source = EXCLUDED.source, collected_at = EXCLUDED.collected_at
        """,
        value_rows,
    )
    print(f"Wrote {len(REGISTRY)} registry rows and {len(value_rows)} feature values for {len(movies)} movies.")


if __name__ == "__main__":
    main()
