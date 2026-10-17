"""Metrics for the log-revenue regression, reported back in real rupee terms so
model quality is legible without mentally exponentiating log values."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def evaluate_predictions(y_true_log: pd.Series, y_pred_log: np.ndarray) -> dict[str, float]:
    y_true = np.expm1(y_true_log.to_numpy())
    y_pred = np.expm1(y_pred_log)

    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(math.sqrt(np.mean((y_true - y_pred) ** 2)))
    # Median, not mean, absolute percentage error: a handful of near-zero `revenue`
    # rows in the scraped data (data-quality noise, not real box-office outcomes)
    # blow the mean version up to meaningless six-figure percentages.
    mape = float(np.median(np.abs((y_true - y_pred) / np.maximum(y_true, 1))) * 100)

    log_mae = float(np.mean(np.abs(y_true_log.to_numpy() - y_pred_log)))

    return {
        "mae_revenue": mae,
        "rmse_revenue": rmse,
        "median_ape_pct": mape,
        "log_mae": log_mae,
        "n_test": len(y_true),
    }


def print_report(metrics: dict[str, float], feature_importances: list[tuple[str, float]]) -> None:
    print("=== Test set evaluation (revenue units, same currency as the `revenue` column) ===")
    print(f"  MAE:  {metrics['mae_revenue']:,.0f}")
    print(f"  RMSE: {metrics['rmse_revenue']:,.0f}")
    print(f"  Median APE: {metrics['median_ape_pct']:.1f}%")
    print(f"  (log-space MAE: {metrics['log_mae']:.3f}, n_test={metrics['n_test']})")
    print("\n=== Top 15 feature importances ===")
    for name, importance in feature_importances[:15]:
        print(f"  {name:<40s} {importance:6.2f}")
