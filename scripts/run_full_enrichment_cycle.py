#!/usr/bin/env python3
"""Runs the full post-AuraDataFiller enrichment cycle in sequence:

  1. run_enrichment       -- TMDB/YouTube current-stat backfill, targeting whatever
                              fields (revenue, budget, genre, production_companies,
                              directors, runtime, trailer/teaser views) are still
                              missing after AuraDataFiller's own pass
  2. run_wayback_enrichment -- point-in-time trailer/teaser view counts (7 days
                              before release, on release day) via the Wayback
                              Machine, for whatever video ids step 1 resolved
  3. build_features_table  -- rebuilds movie_prediction_features against the
                              now-more-complete data
  4. train_model           -- retrains on the refreshed feature table

Meant to be triggered once, well after AuraDataFiller has had time to run its own
pass (it targets the same underlying gaps, so running this too early just means
step 1 finds fewer rows still missing anything -- harmless, just less to do).

    python scripts/run_full_enrichment_cycle.py --tmdb-limit 1000 --wayback-limit 300
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.enrichment.enrich_movies import run as run_tmdb_enrichment
from aurapredictor.enrichment.wayback_enrichment import run as run_wayback
from aurapredictor.features.build_features import run as build_features
from aurapredictor.model.train import train


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tmdb-limit", type=int, default=1000)
    parser.add_argument("--wayback-limit", type=int, default=300)
    args = parser.parse_args()

    print("=== Step 1/4: TMDB/YouTube enrichment ===")
    succeeded, failed = run_tmdb_enrichment(limit=args.tmdb_limit)
    print(f"  {succeeded} succeeded, {failed} failed/skipped")

    print("\n=== Step 2/4: Wayback Machine point-in-time view counts ===")
    succeeded, failed = run_wayback(limit=args.wayback_limit)
    print(f"  {succeeded} succeeded, {failed} failed/skipped")

    print("\n=== Step 3/4: Rebuild movie_prediction_features ===")
    build_features()

    print("\n=== Step 4/4: Retrain model ===")
    train()

    print("\nFull enrichment cycle complete.")


if __name__ == "__main__":
    main()
