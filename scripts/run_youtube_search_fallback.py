#!/usr/bin/env python3
"""Entry point for the YouTube-search trailer fallback -- for movies TMDB has no
video listing for at all (see aurapredictor/enrichment/youtube_search_fallback.py
docstring). Requires scripts/run_enrichment.py to have already run at least once
per movie (it's what sets youtube_last_checked, this script's own "already tried
TMDB" gate).

    python scripts/run_youtube_search_fallback.py

Runs until the movie list is exhausted or the API reports the quota is actually
gone (exit code 2) -- doesn't guess a per-day call ceiling; see
aurapredictor/enrichment/youtube_search_client.py's docstring for why. Safe to
just re-run whenever (a cron, or by hand) -- it always resumes from wherever the
last run left off, and a quota-exhausted exit means "try again once it resets",
not "something is broken".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.enrichment.youtube_search_fallback import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=2000, help="Max movies to search this run (default: 2000)")
    args = parser.parse_args()

    matched, unmatched, quota_exceeded = run(limit=args.limit)
    print(f"YouTube search fallback run complete: {matched} matched, {unmatched} unmatched/skipped.")
    if quota_exceeded:
        print("Stopped early: YouTube API quota exhausted for today. Re-run after it resets.")
        sys.exit(2)


if __name__ == "__main__":
    main()
