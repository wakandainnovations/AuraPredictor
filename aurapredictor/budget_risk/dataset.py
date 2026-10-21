"""Training data for Budget_Opportunity_Risk, read straight from MOVIES_TABLE.

Features are what a production house knows (or decides) *before* investing:
language, genre, production company, cast/crew with their career film counts and
their previous films' budget/revenue/rating_range (*_prev3_* / *_prev_movie_*), the
planned release date and what it coincides with, GDP/inflation, CBFC certificate,
remake flag -- plus three "dials" set per prediction: budget (scanned), rating_10
(4 = bad, 6 = good, 8 = blockbuster) and rating_range (defaults to the typical band
for the language). Revenue is never a feature: the target is the return the film
actually made, revenue_budget_ratio x budget, in log space -- which is what the
model learns to forecast for a planned movie.

Deliberately left out:
- every *_diff_* / *_ratio_* person-history column: they embed this movie's own
  budget, revenue or rating_range (e.g. budget_diff_prev3_avg = budget - prev3 avg).
- number_of_screens (USE_NUMBER_OF_SCREENS): see the note there.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import pandas as pd
from sklearn.preprocessing import MultiLabelBinarizer

from aurapredictor.constants import MOVIES_TABLE
from aurapredictor.dates import release_day_of_week, release_month, release_week_of_year, release_year
from aurapredictor.budget_risk.prev_films import PREV_FILM_FEATURES
from aurapredictor.db import fetch_all
from aurapredictor.model.dataset import (
    PEOPLE_FEATURES,
    _bucket_production_company,
    fit_production_company_binarizer,
    has_plausible_revenue,
    multi_hot,
    normalize_release_event_name,
    split_genres,
    split_languages,
    split_production_companies,
    top_production_companies,
)

CATEGORICAL_FEATURES = [
    "primary_language",
    "production_company_bucketed",
    "cbfc_rating",
    "release_event_type",
    "release_event_name",
    "release_day",
    *PEOPLE_FEATURES,
]

MOVIE_COUNT_FEATURES = [f"{slot}_movie_count" for slot in PEOPLE_FEATURES]

# Off: screens are only settled close to release, so at investment time the input is
# almost always blank -- and a model trained with it scores a blank as "like the 38% of
# training films whose screens weren't captured" (43% hit rate vs 55%). 5-fold CV:
# log-return MAE 0.632 without it, 0.611 with real screens, 0.732 with it left blank.
# The revenue model (model/dataset.py) already takes screens for near-release questions.
USE_NUMBER_OF_SCREENS = False

NUMERIC_FEATURES = [
    "budget",
    "rating_10",
    "rating_range",
    *(["number_of_screens"] if USE_NUMBER_OF_SCREENS else []),
    "gdp_usd_billions",
    "inflation_rate_pct",
    "release_year",
    "release_month",
    "week_of_year",
    "num_languages",
    "is_remake",
    *MOVIE_COUNT_FEATURES,
    *PREV_FILM_FEATURES,
]

# Log space: errors are relative (a 2x miss is the same size on a 1M or a 50M film), and
# log(revenue) - log(budget) is directly the log ratio the risk line is drawn on.
TARGET = "log_return"

ROWS_SQL = f"""
    SELECT movie_name, release_date, language, genre, production_companies, budget, revenue,
           revenue_budget_ratio, rating_10, cbfc_rating, gdp_usd_billions, inflation_rate_pct,
           release_event_type, release_event_name, remake_of, rating_range, number_of_screens,
           {", ".join(PEOPLE_FEATURES + MOVIE_COUNT_FEATURES + PREV_FILM_FEATURES)}
    FROM {MOVIES_TABLE}
"""


def load_training_rows() -> list[dict[str, Any]]:
    return [
        row for row in fetch_all(ROWS_SQL)
        if has_plausible_revenue(row) and row.get("budget") and row.get("revenue_budget_ratio")
    ]


def _float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _prev_film_value(column: str, raw: Any) -> float | None:
    """0 budget/revenue is the fill's "no previous film in the tables" marker, not a
    real 0 -- NaN so the trees don't read it as the cheapest film ever made."""
    value = _float(raw)
    if value == 0 and not column.endswith("rating_range"):
        return None
    return value


def row_to_record(row: dict[str, Any], keep_companies: set[str]) -> dict[str, Any]:
    """One raw MOVIES_TABLE-shaped row -> flat feature record. Shared by training and
    budget_risk/predict.py, so a hypothetical movie goes through the same transform."""
    languages = split_languages(row.get("language"))
    release_date = row.get("release_date")
    return {
        "primary_language": languages[0] if languages else "unknown",
        "production_company_bucketed": _bucket_production_company(row.get("production_companies"), keep_companies),
        "cbfc_rating": row.get("cbfc_rating") or "Unknown",
        "release_event_type": row.get("release_event_type") or "Unknown",
        "release_event_name": normalize_release_event_name(row.get("release_event_name")),
        "release_day": release_day_of_week(release_date) or "Unknown",
        **{col: (row.get(col) or "").strip() or "Unknown" for col in PEOPLE_FEATURES},
        "budget": _float(row.get("budget")),
        "rating_10": _float(row.get("rating_10")),
        "rating_range": _float(row.get("rating_range")),
        **({"number_of_screens": _float(row.get("number_of_screens"))} if USE_NUMBER_OF_SCREENS else {}),
        "gdp_usd_billions": _float(row.get("gdp_usd_billions")),
        "inflation_rate_pct": _float(row.get("inflation_rate_pct")),
        "release_year": release_year(release_date),
        "release_month": release_month(release_date),
        "week_of_year": release_week_of_year(release_date),
        "num_languages": len(languages) or None,
        "is_remake": int((row.get("remake_of") or "").upper() == "REMAKE"),
        **{col: row.get(col) for col in MOVIE_COUNT_FEATURES},
        **{col: _prev_film_value(col, row.get(col)) for col in PREV_FILM_FEATURES},
    }


def feature_matrix(
    rows: list[dict[str, Any]],
    keep_companies: set[str],
    genre_binarizer: MultiLabelBinarizer,
    language_binarizer: MultiLabelBinarizer,
    company_binarizer: MultiLabelBinarizer,
) -> pd.DataFrame:
    # The company binarizer only keeps companies with MIN_PRODUCTION_COMPANY_FILMS
    # films and warns about every rarer one it drops -- intended, so silenced.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="unknown class", category=UserWarning)
        companies = multi_hot(
            company_binarizer, [split_production_companies(row.get("production_companies")) for row in rows], "company"
        )
    X = pd.concat(
        [
            pd.DataFrame([row_to_record(row, keep_companies) for row in rows]),
            multi_hot(genre_binarizer, [split_genres(row.get("genre")) for row in rows], "genre"),
            multi_hot(language_binarizer, [split_languages(row.get("language")) for row in rows], "language"),
            companies,
        ],
        axis=1,
    )
    for col in CATEGORICAL_FEATURES:
        X[col] = X[col].fillna("Unknown").astype(str)
    return X


def build_dataframe(rows: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.Series, dict[str, Any]]:
    """Returns (X, y_log_return, encoders), where encoders holds everything
    feature_matrix() needs to rebuild the same columns for a new movie."""
    encoders = {
        "keep_companies": top_production_companies(rows),
        "genre_binarizer": MultiLabelBinarizer().fit([split_genres(row.get("genre")) for row in rows]),
        "language_binarizer": MultiLabelBinarizer().fit([split_languages(row.get("language")) for row in rows]),
        "company_binarizer": fit_production_company_binarizer(rows),
    }
    X = feature_matrix(rows, **encoders)
    y = pd.Series(
        [math.log(float(row["revenue_budget_ratio"]) * float(row["budget"])) for row in rows], name=TARGET
    )
    return X, y, encoders
