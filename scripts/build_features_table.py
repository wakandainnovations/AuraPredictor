#!/usr/bin/env python3
"""Entry point for aurapredictor.features.build_features.

    python scripts/build_features_table.py            # create/upsert movie_prediction_features
    python scripts/build_features_table.py --dry-run  # read-only: print counts + samples, no writes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.features.build_features import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written without writing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
