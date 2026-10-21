#!/usr/bin/env python3
"""Entry point: serves the upcoming-movie revenue API (aurapredictor/api/app.py) that
AuraService calls. Requires a trained model in aurapredictor/artifacts/ -- run
scripts/train_model.py first. Port defaults to 8082 (AuraService is 8080, AuraMath
8081); override with --port or AURAPREDICTOR_PORT."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("AURAPREDICTOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AURAPREDICTOR_PORT", "8082")))
    args = parser.parse_args()
    # Single worker: each worker would load its own copy of the full DB-backed context.
    uvicorn.run("aurapredictor.api.app:app", host=args.host, port=args.port)
