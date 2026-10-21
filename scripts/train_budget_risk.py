#!/usr/bin/env python3
"""Entry point: trains the Budget_Opportunity_Risk model and saves it to
aurapredictor/artifacts/budget_opportunity_risk/. Reads india_movies_data_collection
directly -- no movie_prediction_features rebuild needed."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.budget_risk.train import train

if __name__ == "__main__":
    train()
