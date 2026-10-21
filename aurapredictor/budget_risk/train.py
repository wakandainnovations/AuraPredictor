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

5-fold out-of-fold forecasts give the metrics. Probabilities instead come from
forward-in-time forecasts (each year from FORWARD_START_YEAR predicted by a model
trained on earlier years only): random folds always have same-era films to lean on,
so their residuals understate the error on a film that hasn't been made yet. On the
2025-26 holdout, OOF-based 90%+ calls at a 1x bar were right only 75% of the time;
forward residuals plus a per-bar Platt correction (calibrate()) brought that to 81%,
with Brier score equal or better at every bar from 1x to 2.5x. The saved model is
then refit on every row.
"""

from __future__ import annotations

import json
import math
import pickle
import warnings
from datetime import date

import numpy as np
from catboost import CatBoostRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import KFold

from aurapredictor.budget_risk import ARTIFACTS_DIR, ENCODERS_PATH, HIT_RATIO, LAYOUT_PATH, MODEL_NAME, MODEL_PATH
from aurapredictor.budget_risk.dataset import (
    CATEGORICAL_FEATURES,
    TARGET,
    build_dataframe,
    feature_matrix,
    load_training_rows,
)
from aurapredictor.budget_risk.predict import (
    MAX_HIT_RATIO,
    MIN_HIT_RATIO,
    budget_grid,
    budget_line,
    log_ratio_curves,
    logit,
    raw_hit_probability,
)

CV_FOLDS = 5

# First year forecast forward; earlier years only ever train. ~1,000 films before it.
FORWARD_START_YEAR = 2016

# Bars with their own Platt correction; predict.py interpolates between them.
CALIBRATION_HIT_RATIOS = [round(r, 2) for r in np.arange(MIN_HIT_RATIO, MAX_HIT_RATIO + 0.01, 0.1)]


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


def forward_log_ratios(rows: list[dict], cat_features: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """(forecast, actual) log ratios for every film from FORWARD_START_YEAR on, each
    year scored by a model trained only on the years before it."""
    forecasts, actuals = [], []
    last_year = max(int(r["release_date"][:4]) for r in rows)
    for year in range(FORWARD_START_YEAR, last_year + 1):
        cutoff = f"{year}-01-01"
        earlier = [r for r in rows if r["release_date"] < cutoff]
        this_year = [r for r in rows if r["release_date"][:4] == str(year)]
        if not this_year:
            continue
        X, y, encoders = build_dataframe(earlier)
        year_cats = [c for c in cat_features if c in X.columns]
        model = new_model(year_cats)
        model.fit(X, y.to_numpy())
        # A year can bring a genre/language no earlier film had (the first Gujarati
        # film): dropped, as for any new movie, so not worth a warning each time.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="unknown class", category=UserWarning)
            X_year = feature_matrix(this_year, **encoders).reindex(columns=X.columns, fill_value=0)
        forecasts.append(model.predict(X_year) - np.log(X_year["budget"].to_numpy()))
        actuals.append(np.log([float(r["revenue_budget_ratio"]) for r in this_year]))
    return np.concatenate(forecasts), np.concatenate(actuals)


def calibrate(forecast: np.ndarray, actual: np.ndarray, layout: dict) -> dict[str, list[float]]:
    """Per-bar Platt correction: logit(p) = intercept + slope * logit(raw p), fitted
    on forward forecasts so it learns how over- or under-confident raw probabilities
    are on films the model hasn't seen the era of."""
    intercepts, slopes = [], []
    for hit_ratio in CALIBRATION_HIT_RATIOS:
        raw = raw_hit_probability(forecast, layout, hit_ratio)
        platt = LogisticRegression(C=1e6).fit(logit(raw)[:, None], actual >= math.log(hit_ratio))
        intercepts.append(float(platt.intercept_[0]))
        slopes.append(float(platt.coef_[0, 0]))
    return {"hit_ratios": CALIBRATION_HIT_RATIOS, "intercepts": intercepts, "slopes": slopes}


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

    print(f"Forward-in-time forecasts for calibration ({FORWARD_START_YEAR} on)...")
    forward_forecast, forward_actual = forward_log_ratios(rows, cat_features)
    forward_residuals = forward_actual - forward_forecast
    residual_quantiles = [float(q) for q in np.percentile(forward_residuals, np.arange(101))]
    calibration = calibrate(forward_forecast, forward_actual, {"residual_quantiles": residual_quantiles})
    metrics["forward_log_ratio_mae"] = float(np.mean(np.abs(forward_residuals)))
    metrics["forward_n"] = len(forward_residuals)

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
    print(f"  forward-in-time MAE:       {metrics['forward_log_ratio_mae']:.3f} (n={metrics['forward_n']})")
    print("  Platt correction (intercept, slope) at " + ", ".join(
        f"{r:g}x: ({a:.2f}, {b:.2f})"
        for r, a, b in zip(calibration["hit_ratios"], calibration["intercepts"], calibration["slopes"])
        if r in (1.0, 1.5, 2.0, 2.5)
    ))
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
                # Percentiles 0..100 of forward-in-time (actual - forecast) log ratio.
                "residual_quantiles": residual_quantiles,
                "calibration": calibration,
                "metrics": metrics,
            },
            f,
            indent=2,
        )
    print(f"\nSaved model to {MODEL_PATH}")


if __name__ == "__main__":
    train()
