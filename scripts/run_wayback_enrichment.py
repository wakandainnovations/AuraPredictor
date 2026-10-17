#!/usr/bin/env python3
"""Entry point for the Wayback Machine point-in-time view-count backfill. Requires
scripts/run_enrichment.py to have already run at least once (it's what populates
the trailer/teaser YouTube video ids this script looks up in the Wayback Machine).

    python scripts/run_wayback_enrichment.py --limit 100
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.enrichment.wayback_enrichment import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=100, help="Max (movie, video) pairs to process this run")
    args = parser.parse_args()

    succeeded, failed = run(limit=args.limit)
    print(f"Wayback enrichment run complete: {succeeded} succeeded, {failed} failed/skipped.")


if __name__ == "__main__":
    main()
