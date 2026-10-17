"""Scores every movie in movie_prediction_features and upserts the result into the
existing `movie_revenue_predictions` table -- found already shaped for exactly this
(predicted_revenue, confidence_band_low/high, actual_revenue, abs_pct_error,
model_name, model_version, generated_at) and matching the workspace's own "new
table, instantly readable by every other service" convention. This is a SHARED
table other AuraService code may read from -- worth confirming that's wanted
before relying on it in production, rather than assuming silently.

Confidence bands come from the trained model's own out-of-sample log-space MAE
(saved at training time), not a fabricated +/-X% guess: exp(log_pred +/- log_mae).

Scope note: only scores movies already in movie_prediction_features, i.e. already-
released Indian movies with known revenue & budget. True "upcoming movie" scoring
(is_upcoming=true, actual_revenue=NULL) needs person-history features computed for
a movie that was never added to movie_prediction_features in the first place --
not wired up yet, left as a follow-up once there's a concrete upcoming-movie use
case.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from aurapredictor.db import execute_many
from aurapredictor.model.dataset import load_raw_rows
from aurapredictor.scenarios.engine import load_context, score_row

MODEL_NAME = "aura_predictor_catboost"

_UPSERT_SQL = """
INSERT INTO movie_revenue_predictions
    (movie_name, release_date, language, predicted_revenue, confidence_band_low,
     confidence_band_high, actual_revenue, abs_pct_error, is_upcoming, model_name,
     model_version, generated_at)
VALUES %s
ON CONFLICT (movie_name, release_date, language) DO UPDATE SET
    predicted_revenue = EXCLUDED.predicted_revenue,
    confidence_band_low = EXCLUDED.confidence_band_low,
    confidence_band_high = EXCLUDED.confidence_band_high,
    actual_revenue = EXCLUDED.actual_revenue,
    abs_pct_error = EXCLUDED.abs_pct_error,
    is_upcoming = EXCLUDED.is_upcoming,
    model_name = EXCLUDED.model_name,
    model_version = EXCLUDED.model_version,
    generated_at = EXCLUDED.generated_at
"""


def run() -> int:
    context = load_context()
    log_mae = context.feature_layout.get("log_mae", 0.0)
    model_version = context.feature_layout.get("model_version", "unknown")

    rows = load_raw_rows()

    now = datetime.now(timezone.utc)
    records = []
    for row in rows:
        predicted = score_row(context, row)
        log_predicted = math.log1p(predicted)
        low = math.expm1(log_predicted - log_mae)
        high = math.expm1(log_predicted + log_mae)
        actual = float(row["revenue"]) if row.get("revenue") else None
        abs_pct_error = abs(predicted - actual) / actual * 100 if actual else None

        records.append(
            (
                row["movie_name"],
                row["release_date"],
                row["language"],
                predicted,
                low,
                high,
                actual,
                abs_pct_error,
                False,  # is_upcoming -- see module docstring
                MODEL_NAME,
                model_version,
                now,
            )
        )

    execute_many(_UPSERT_SQL, records)
    return len(records)
