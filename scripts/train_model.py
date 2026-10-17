#!/usr/bin/env python3
"""Entry point: trains the CatBoost revenue model and saves it to
aurapredictor/artifacts/. Requires movie_prediction_features to already exist --
run scripts/build_features_table.py first."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.model.train import train

if __name__ == "__main__":
    train()
