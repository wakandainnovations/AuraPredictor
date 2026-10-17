"""Assembles the modeling dataset by joining movie_prediction_features (the
derived popularity-index / hit-flop-history columns) back to the raw MOVIES_TABLE
columns (budget, genre, production_companies, rating, ...).

IMPORTANT: revenue_budget_ratio and performance_tier in movie_prediction_features
are computed FROM this movie's own revenue -- they are correct to use as *history*
for a different movie (e.g. was an actor's past film a hit), but must never be used
as an input feature for predicting a movie's own revenue, since that would leak the
target. This module only pulls them for a movie's *people's history*, never for the
movie's own row.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd
from sklearn.preprocessing import MultiLabelBinarizer

from aurapredictor.constants import MOVIES_TABLE
from aurapredictor.db import fetch_all
from aurapredictor.track_record import TRACK_RECORD_FEATURES, PeopleFilms, load_people_films, track_record_fields

# Filled in on MOVIES_TABLE itself, billing order (lead_actor_1 = top-billed). Each
# slot is its own column, so the model learns per slot how much a name matters --
# e.g. A.R. Rahman as music_director, or a star as lead_actor_1 vs lead_actor_5.
PEOPLE_FEATURES = [
    "lead_actor_1",
    "lead_actor_2",
    "lead_actor_3",
    "lead_actor_4",
    "lead_actor_5",
    "director",
    "music_director",
]

# CatBoost is given these directly as categorical columns (native handling, no
# manual one-hot/frequency-encoding needed). For the PEOPLE_FEATURES that means
# ordered target statistics: a person's value is learned from the revenue of their
# *other* films only, so a movie's own revenue never leaks into its own row.
CATEGORICAL_FEATURES = [
    "primary_language",
    "production_company_bucketed",
    "cbfc_rating",
    "release_event_type",
    "release_event_name",
    *PEOPLE_FEATURES,
]

# Filled in on MOVIES_TABLE itself: each person's career film count as of this
# movie's release (not today), so safe as a pre-release signal. Unlike the
# *_prior_films_count columns below, these aren't limited to films in MOVIES_TABLE.
MOVIE_COUNT_FEATURES = [
    "lead_actor_1_movie_count",
    "lead_actor_2_movie_count",
    "lead_actor_3_movie_count",
    "lead_actor_4_movie_count",
    "lead_actor_5_movie_count",
    "director_movie_count",
    "music_director_movie_count",
]

# Off until MOVIES_TABLE covers people's filmographies: today ~97% of movies have at
# least one cast/crew member whose previous films are partly missing from it, so these
# "last 5 films" are really "last 5 films that happen to be in the table" -- and adding
# them made 5-fold CV worse (log-MAE 0.814 -> 0.827, worse in 13/15 folds).
USE_TRACK_RECORD_FEATURES = False

NUMERIC_FEATURES = [
    "budget",
    "runtime",
    "rating_10",
    "gdp_usd_billions",
    "inflation_rate_pct",
    "release_month",
    "day_of_year",
    "week_of_year",
    "num_languages",
    "rating_range",
    "number_of_screens",
    "is_remake",
    *MOVIE_COUNT_FEATURES,
    *(TRACK_RECORD_FEATURES if USE_TRACK_RECORD_FEATURES else []),
    "lead_actor_prior_films_count",
    "lead_actor_hit_flop_ratio_last3",
    "director_prior_films_count",
    "director_hit_flop_ratio_last3",
    "music_director_prior_films_count",
    "music_director_hit_flop_ratio_last3",
]

TOP_PRODUCTION_COMPANIES = 40

# A company gets its own company_* 0/1 column only if it's credited (anywhere in the
# comma-separated list, not just first) on at least this many training movies --
# rarer ones would be near-empty columns the model can't learn anything from.
MIN_PRODUCTION_COMPANY_FILMS = 5

# Training labels below either bar are scrape errors, not real box office (e.g.
# Theri at revenue 69, a 100M-budget film at revenue 1) -- they're dropped from
# training only; every movie is still scored by write_predictions.py.
MIN_PLAUSIBLE_REVENUE = 1000
MIN_PLAUSIBLE_REVENUE_BUDGET_RATIO = 0.01
# Above Dangal's ~$311M, the all-time Indian record -- rows past it (e.g. The Kerala
# Story at 1,640M) are mis-scraped, and only teach the model wrong big-film values.
MAX_PLAUSIBLE_REVENUE = 320_000_000


def has_plausible_revenue(row: dict[str, Any]) -> bool:
    revenue, budget = float(row["revenue"] or 0), float(row["budget"] or 0)
    return MIN_PLAUSIBLE_REVENUE <= revenue <= MAX_PLAUSIBLE_REVENUE and (
        not budget or revenue / budget >= MIN_PLAUSIBLE_REVENUE_BUDGET_RATIO
    )


def _split_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def split_genres(raw: str | None) -> list[str]:
    """movies_data_collection.genre is inconsistently formatted in the source data:
    sometimes a clean "Action, Adventure" string, sometimes a literal Python-list
    repr "['Action', 'Adventure']". Strips brackets/quotes per token before
    lowercasing + deduping, so both formats end up as the same clean genre labels
    instead of colliding into stray genre_'action' columns."""
    tokens = (raw or "").strip("[]").split(",")
    cleaned = (token.strip().strip("'\"").lower() for token in tokens)
    return sorted({token for token in cleaned if token})


# Stray codes/spellings seen in the language column, mapped to the canonical name.
_LANGUAGE_ALIASES = {"as": "assamese", "oriya": "odia"}


def split_languages(raw: str | None) -> list[str]:
    """language is a single language or a comma-separated list for a multi-language
    release (e.g. "kannada, hindi, telugu"), original language first. Returns the
    cleaned, de-duplicated list in that same order (index 0 = primary language)."""
    languages: list[str] = []
    for token in _split_list(raw):
        name = _LANGUAGE_ALIASES.get(token.lower(), token.lower())
        if name not in languages:
            languages.append(name)
    return languages


def split_production_companies(raw: str | None) -> list[str]:
    return sorted(set(_split_list(raw)))


def fit_production_company_binarizer(rows: list[dict[str, Any]]) -> MultiLabelBinarizer:
    counts = Counter(company for row in rows for company in split_production_companies(row.get("production_companies")))
    keep = sorted(name for name, n in counts.items() if n >= MIN_PRODUCTION_COMPANY_FILMS)
    return MultiLabelBinarizer(classes=keep).fit([])


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_WS_RE = re.compile(r"\s+")


def normalize_release_event_name(raw: str | None) -> str:
    """release_event_name carries the year for recurring events ("2011 ICC Cricket
    World Cup"); stripped so every edition of the same event shares one category."""
    return _WS_RE.sub(" ", _YEAR_RE.sub("", raw or "")).strip(" -—") or "Unknown"


def multi_hot(binarizer: MultiLabelBinarizer, label_lists: list[list[str]], prefix: str) -> pd.DataFrame:
    """0/1 column per label the binarizer was fitted on; labels it never saw are dropped."""
    return pd.DataFrame(
        binarizer.transform(label_lists),
        columns=[f"{prefix}_{label.replace(' ', '_')}" for label in binarizer.classes_],
    )


def _bucket_production_company(raw: str | None, keep: set[str]) -> str:
    companies = _split_list(raw)
    if not companies:
        return "Unknown"
    primary = companies[0]
    return primary if primary in keep else "Other"


def top_production_companies(rows: list[dict[str, Any]]) -> set[str]:
    company_counts = Counter()
    for row in rows:
        companies = _split_list(row.get("production_companies"))
        if companies:
            company_counts[companies[0]] += 1
    return {name for name, _ in company_counts.most_common(TOP_PRODUCTION_COMPANIES)}


def row_to_record(
    row: dict[str, Any], keep_companies: set[str], people_films: PeopleFilms | None = None
) -> dict[str, Any]:
    """Turns one raw DB-shaped row (movies_data_collection columns + the
    movie_prediction_features person-history columns) into the flat feature record
    used both to build the training matrix and, in scenarios/engine.py, to score a
    hypothetical "what if" row with the exact same transform used at training time."""
    from aurapredictor.dates import release_day_of_year, release_week_of_year
    from aurapredictor.dates import release_month as parse_release_month

    languages = split_languages(row.get("language"))
    return {
        "primary_language": languages[0] if languages else "unknown",
        "production_company_bucketed": _bucket_production_company(
            row.get("production_companies"), keep_companies
        ),
        "cbfc_rating": row.get("cbfc_rating") or "Unknown",
        "release_event_type": row.get("release_event_type") or "Unknown",
        "release_event_name": normalize_release_event_name(row.get("release_event_name")),
        **{col: (row.get(col) or "").strip() or "Unknown" for col in PEOPLE_FEATURES},
        "budget": float(row["budget"]) if row.get("budget") else None,
        "runtime": float(row["runtime"]) if row.get("runtime") else None,
        "rating_10": float(row["rating_10"]) if row.get("rating_10") else None,
        "gdp_usd_billions": float(row["gdp_usd_billions"]) if row.get("gdp_usd_billions") else None,
        "inflation_rate_pct": float(row["inflation_rate_pct"]) if row.get("inflation_rate_pct") else None,
        "release_month": parse_release_month(row.get("release_date")),
        # Computed from release_date here rather than read from the DB's generated
        # day_of_year/week_of_year columns, so the change-release-date what-if scenario
        # (which only swaps release_date) gets them re-derived for the new date.
        "day_of_year": release_day_of_year(row.get("release_date")),
        "week_of_year": release_week_of_year(row.get("release_date")),
        "num_languages": len(languages) or None,
        # Being filled in manually (still NULL for most rows) -- NaN is fine for CatBoost.
        "rating_range": float(row["rating_range"]) if row.get("rating_range") is not None else None,
        # Only known for ~1/3 of movies -- NaN for the rest. Still lowered 5-fold CV
        # log-MAE 0.852 -> 0.821 (better in 15/15 folds).
        "number_of_screens": float(row["number_of_screens"]) if row.get("number_of_screens") else None,
        "is_remake": int((row.get("remake_of") or "").upper() == "REMAKE"),
        **{col: row.get(col) for col in MOVIE_COUNT_FEATURES},
        **(track_record_fields(row, people_films) if USE_TRACK_RECORD_FEATURES else {}),
        "lead_actor_prior_films_count": row.get("lead_actor_prior_films_count"),
        "lead_actor_hit_flop_ratio_last3": row.get("lead_actor_hit_flop_ratio_last3"),
        "director_prior_films_count": row.get("director_prior_films_count"),
        "director_hit_flop_ratio_last3": row.get("director_hit_flop_ratio_last3"),
        "music_director_prior_films_count": row.get("music_director_prior_films_count"),
        "music_director_hit_flop_ratio_last3": row.get("music_director_hit_flop_ratio_last3"),
    }


# Shared by training, scenarios/engine.py and model/write_predictions.py, so every
# path that scores a movie reads exactly the columns row_to_record() expects.
RAW_ROWS_SQL = f"""
    SELECT
        f.movie_name, f.release_date, f.language,
        f.lead_actor_prior_films_count, f.lead_actor_hit_flop_ratio_last3,
        f.director_prior_films_count, f.director_hit_flop_ratio_last3,
        f.music_director_prior_films_count, f.music_director_hit_flop_ratio_last3,
        m.revenue, m.budget, m.genre, m.production_companies, m.rating_10, m.runtime,
        m.cbfc_rating, m.gdp_usd_billions, m.inflation_rate_pct,
        m.rating_range, m.number_of_screens,
        m.release_event_type, m.release_event_name, m.remake_of, {", ".join(f"m.{col}" for col in MOVIE_COUNT_FEATURES + PEOPLE_FEATURES)}
    FROM movie_prediction_features f
    JOIN {MOVIES_TABLE} m
      ON m.movie_name = f.movie_name AND m.release_date = f.release_date AND m.language = f.language
"""


def load_raw_rows() -> list[dict[str, Any]]:
    return fetch_all(RAW_ROWS_SQL)


def load_training_rows() -> list[dict[str, Any]]:
    return [row for row in load_raw_rows() if has_plausible_revenue(row)]


def build_dataframe(
    rows: list[dict[str, Any]] | None = None,
    people_films: PeopleFilms | None = None,
) -> tuple[pd.DataFrame, pd.Series, MultiLabelBinarizer, MultiLabelBinarizer, MultiLabelBinarizer]:
    """Returns (X, y_log_revenue, genre_binarizer, language_binarizer,
    production_company_binarizer). X's categorical columns are left as plain
    strings/NaN for CatBoost's native handling; genre_*, language_* and company_*
    columns are already multi-hot (0/1) by the time this returns -- a multi-language
    release gets a 1 in every language it was released in, a co-production a 1 for
    every credited company."""
    rows = rows if rows is not None else load_raw_rows()
    if people_films is None and USE_TRACK_RECORD_FEATURES:
        people_films = load_people_films()

    keep_companies = top_production_companies(rows)

    genre_lists = [split_genres(row.get("genre")) for row in rows]
    genre_binarizer = MultiLabelBinarizer().fit(genre_lists)
    language_lists = [split_languages(row.get("language")) for row in rows]
    language_binarizer = MultiLabelBinarizer().fit(language_lists)
    company_lists = [split_production_companies(row.get("production_companies")) for row in rows]
    company_binarizer = fit_production_company_binarizer(rows)

    records = [row_to_record(row, keep_companies, people_films) for row in rows]

    X = pd.concat(
        [
            pd.DataFrame(records),
            multi_hot(genre_binarizer, genre_lists, "genre"),
            multi_hot(language_binarizer, language_lists, "language"),
            multi_hot(company_binarizer, company_lists, "company"),
        ],
        axis=1,
    )
    for col in CATEGORICAL_FEATURES:
        if col in X.columns:
            X[col] = X[col].fillna("Unknown").astype(str)

    y_revenue = pd.Series([float(row["revenue"]) for row in rows], name="revenue")
    y_log = pd.Series(y_revenue.apply(math.log1p), name="log_revenue")

    return X, y_log, genre_binarizer, language_binarizer, company_binarizer


def log_budget(X: pd.DataFrame) -> np.ndarray:
    """The model predicts log(revenue) - log(budget), i.e. log revenue:budget ratio;
    adding this back gives log revenue. Trees can't extrapolate, so predicting revenue
    directly capped big-budget films at what the few largest training films earned."""
    return np.log1p(X["budget"].fillna(0).to_numpy(dtype=float))


def categorical_feature_names(X: pd.DataFrame) -> list[str]:
    return [c for c in CATEGORICAL_FEATURES if c in X.columns]
