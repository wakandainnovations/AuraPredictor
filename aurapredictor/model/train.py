"""Trains a CatBoostRegressor on log(revenue) over the ~2,540 Indian movies that
currently have real budget & revenue data (movie_prediction_features joined back to
movies_data_collection). Saves the model + genre binarizer + feature-column layout
to aurapredictor/artifacts/ so the scenario engine (scenarios/engine.py) can rebuild
an identical feature vector for a hypothetical "what if" row and score it with the
same model.
"""

from __future__ import annotations

import json
import pickle
from datetime import date
from pathlib import Path

from catboost import CatBoostRegressor
from sklearn.model_selection import train_test_split

from aurapredictor.model.dataset import build_dataframe, categorical_feature_names, load_raw_rows, top_production_companies
from aurapredictor.model.evaluate import evaluate_predictions, print_report

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "model.cbm"
BINARIZER_PATH = ARTIFACTS_DIR / "genre_binarizer.pkl"
LANGUAGE_BINARIZER_PATH = ARTIFACTS_DIR / "language_binarizer.pkl"
FEATURE_LAYOUT_PATH = ARTIFACTS_DIR / "feature_layout.json"


def train() -> None:
    rows = load_raw_rows()
    X, y_log, binarizer, language_binarizer = build_dataframe(rows)
    cat_features = categorical_feature_names(X)
    keep_companies = sorted(top_production_companies(rows))

    X_train, X_test, y_train, y_test = train_test_split(X, y_log, test_size=0.2, random_state=42)

    model = CatBoostRegressor(
        iterations=500,
        learning_rate=0.05,
        depth=6,
        loss_function="RMSE",
        cat_features=cat_features,
        # A better audience rating must never predict *less* revenue. Without this
        # the trees learned a jagged curve (e.g. 9.0 below 8.0, where few movies
        # exist), which breaks the rating 4/6/8 what-if scenarios.
        monotone_constraints={"rating_10": 1},
        verbose=False,
        random_seed=42,
    )
    # No eval_set: CatBoost would pick the best iteration *on the test set* (leaking it
    # into model selection), and with the few near-zero-revenue rows in it that pick was
    # badly biased (median APE 75% vs 46% on the same split).
    model.fit(X_train, y_train)

    y_pred_log = model.predict(X_test)
    metrics = evaluate_predictions(y_test, y_pred_log)
    importances = sorted(
        zip(X.columns, model.get_feature_importance()), key=lambda pair: pair[1], reverse=True
    )
    print_report(metrics, importances)

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    model.save_model(str(MODEL_PATH))
    with open(BINARIZER_PATH, "wb") as f:
        pickle.dump(binarizer, f)
    with open(LANGUAGE_BINARIZER_PATH, "wb") as f:
        pickle.dump(language_binarizer, f)
    with open(FEATURE_LAYOUT_PATH, "w") as f:
        json.dump(
            {
                "columns": list(X.columns),
                "categorical_features": cat_features,
                "genre_columns": [c for c in X.columns if c.startswith("genre_")],
                "language_columns": [c for c in X.columns if c.startswith("language_")],
                "top_production_companies": keep_companies,
                "log_mae": metrics["log_mae"],
                "model_version": date.today().isoformat(),
            },
            f,
            indent=2,
        )

    print(f"\nSaved model to {MODEL_PATH}")


if __name__ == "__main__":
    train()
