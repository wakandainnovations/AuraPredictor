#!/usr/bin/env python3
"""Entry point: scores every movie in movie_prediction_features and upserts into
the existing shared movie_revenue_predictions table. Requires a trained model in
aurapredictor/artifacts/ -- run scripts/train_model.py first.

NOTE: movie_revenue_predictions is a pre-existing table in the shared `aura` DB
that other AuraService code may read. Confirm that's the intended destination
before relying on this in a pipeline that other services consume."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.model.write_predictions import run

if __name__ == "__main__":
    n = run()
    print(f"Upserted {n} rows into movie_revenue_predictions")
