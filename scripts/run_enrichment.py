#!/usr/bin/env python3
"""Entry point for the TMDB/YouTube enrichment pipeline. Requires real API
credentials in config/secrets.properties (see config/secrets.example.properties)
or the TMDB_API_READ_ACCESS_TOKEN / TMDB_API_KEY / YOUTUBE_API_KEY env vars.

    python scripts/run_enrichment.py --limit 200        # process up to 200 pending movies
    python scripts/run_enrichment.py --limit 50 --force # re-process even already-checked rows
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.enrichment.enrich_movies import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=100, help="Max movies to process this run (default: 100)")
    parser.add_argument(
        "--force", action="store_true", help="Re-process movies even if every target field already looks filled"
    )
    args = parser.parse_args()

    succeeded, failed = run(limit=args.limit, force=args.force)
    print(f"Enrichment run complete: {succeeded} succeeded, {failed} failed/skipped.")


if __name__ == "__main__":
    main()
