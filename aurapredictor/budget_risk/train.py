"""Trains Budget_Opportunity_Risk: a CatBoostRegressor forecasting a movie's log
return (revenue_budget_ratio x budget) from pre-investment parameters + budget +
rating_10. predict.py then scans budget for where the forecast stops covering
HIT_RATIO x budget.

Why return-with-budget-as-input rather than the alternatives (5-fold CV, each
film's line drawn at its actual rating; "under" = its real budget was below it):

                                     hit/flop AUC  hit rate under / over  line at grid top
  log return, budget monotone up        0.781          80% / 37%               0%
  log ratio, budget monotone down       0.770          75% / 33%              19%
  return without budget, halved         0.749          74% / 37%               0%

The ratio target goes flat above ~5M budget (few films that big), i.e. it assumes
revenue keeps scaling with spend forever, so 19% of films never cross the line.
Forecasting return instead goes flat the *other* way -- past the films it has
seen, more spend buys no more audience -- the conservative assumption for a risk
line. Ignoring budget entirely loses how scale drives revenue.

5-fold out-of-fold forecasts give honest metrics and the residual distribution
that turns a forecast into a probability of clearing HIT_RATIO; the saved model
is then refit on every row.
"""

from __future__ import annotations

import json
import math
import pickle
from datetime import date

import numpy as np
from catboost import CatBoostRegressor
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import KFold

from aurapredictor.budget_risk import ARTIFACTS_DIR, ENCODERS_PATH, HIT_RATIO, LAYOUT_PATH, MODEL_NAME, MODEL_PATH
from aurapredictor.budget_risk.dataset import CATEGORICAL_FEATURES, TARGET, build_dataframe, load_training_rows
from aurapredictor.budget_risk.predict import budget_grid, budget_line, log_ratio_curves

CV_FOLDS = 5


def new_model(cat_features: list[str]) -> CatBoostRegressor:
    return CatBoostRegressor(
        # vs 600 @ 0.05: hit/flop AUC 0.782 -> 0.789, log-return MAE 0.660 -> 0.652.
        # Weighting big-budget films up (as model/train.py does for big earners) made
        # no difference here, and weighting by revenue would select on the outcome.
        iterations=1500,
        learning_rate=0.03,
        depth=6,
        loss_function="RMSE",
        cat_features=cat_features,
        # Spending more on the same movie, or a better audience rating, must never
        # forecast *less* revenue -- otherwise tree noise makes the budget line jump.
        monotone_constraints={"budget": 1, "rating_10": 1},
        verbose=False,
        random_seed=42,
    )


def train() -> None:
    rows = load_training_rows()
    X, y, encoders = build_dataframe(rows)
    cat_features = [c for c in CATEGORICAL_FEATURES if c in X.columns]
    y_np = y.to_numpy()
    budgets = X["budget"].to_numpy()
    # The scan stays inside what the model has seen: trees are flat beyond the
    # extremes, so a line found out there would say nothing.
    min_budget, max_budget = float(np.percentile(budgets, 1)), float(budgets.max())
    grid = budget_grid(min_budget, max_budget)
    log_hit = math.log(HIT_RATIO)

    oof = np.zeros(len(X))
    oof_line = np.zeros(len(X))
    for train_idx, test_idx in KFold(CV_FOLDS, shuffle=True, random_state=42).split(X):
        fold_model = new_model(cat_features)
        fold_model.fit(X.iloc[train_idx], y_np[train_idx])
        X_test = X.iloc[test_idx]
        oof[test_idx] = fold_model.predict(X_test)
        # Each held-out film's own line, at its actual rating: did its real budget sit under it?
        curves = log_ratio_curves(fold_model, X_test, grid)
        oof_line[test_idx] = [budget_line(grid, curve >= log_hit)[0] or 0.0 for curve in curves]

    residuals = y_np - oof
    actual_log_ratio = y_np - np.log(budgets)
    oof_log_ratio = oof - np.log(budgets)
    is_hit = actual_log_ratio >= log_hit
    under_line = budgets <= oof_line
    metrics = {
        "log_return_mae": float(np.mean(np.abs(residuals))),
        "median_return_ape_pct": float(np.median(np.abs(np.expm1(-residuals))) * 100),
        "hit_auc": float(roc_auc_score(is_hit, oof_log_ratio)),
        "hit_rate_under_line": float(np.mean(is_hit[under_line])),
        "hit_rate_over_line": float(np.mean(is_hit[~under_line])),
        "share_under_line": float(np.mean(under_line)),
        "base_hit_rate": float(np.mean(is_hit)),
        "n": len(X),
    }

    model = new_model(cat_features)
    model.fit(X, y_np)
    importances = sorted(zip(X.columns, model.get_feature_importance()), key=lambda pair: pair[1], reverse=True)

    print(f"=== {MODEL_NAME}: {CV_FOLDS}-fold out-of-fold evaluation (n={metrics['n']}) ===")
    print(f"  log-return MAE:            {metrics['log_return_mae']:.3f}")
    print(f"  median return error:       {metrics['median_return_ape_pct']:.1f}%")
    print(f"  hit (>= {HIT_RATIO:g}x) AUC:          {metrics['hit_auc']:.3f}")
    print(
        f"  hit rate, budget under line: {metrics['hit_rate_under_line']:.2f}  "
        f"over line: {metrics['hit_rate_over_line']:.2f}  "
        f"(base {metrics['base_hit_rate']:.2f}, {metrics['share_under_line']:.0%} of films under)"
    )
    print("\n=== Top 15 feature importances ===")
    for name, importance in importances[:15]:
        print(f"  {name:<40s} {importance:6.2f}")

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODEL_PATH))
    with open(ENCODERS_PATH, "wb") as f:
        pickle.dump(encoders, f)
    with open(LAYOUT_PATH, "w") as f:
        json.dump(
            {
                "model_name": MODEL_NAME,
                "model_version": date.today().isoformat(),
                "target": TARGET,
                "hit_ratio": HIT_RATIO,
                "columns": list(X.columns),
                "categorical_features": cat_features,
                "min_budget": min_budget,
                "max_budget": max_budget,
                # Percentiles 0..100 of out-of-fold (actual - forecast) log return, which
                # at a film's real budget is also its log-ratio error.
                "residual_quantiles": [float(q) for q in np.percentile(residuals, np.arange(101))],
                "metrics": metrics,
            },
            f,
            indent=2,
        )
    print(f"\nSaved model to {MODEL_PATH}")


if __name__ == "__main__":
    train()
