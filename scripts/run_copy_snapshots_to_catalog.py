#!/usr/bin/env python3
"""Entry point for copying Wayback-derived trailer/teaser view counts from
movie_video_snapshots into india_movies_data_collection on an exact identity
match (see aurapredictor/enrichment/copy_snapshots_to_catalog.py docstring).
Safe to re-run any time -- every write is COALESCE-only-if-missing.

    python scripts/run_copy_snapshots_to_catalog.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurapredictor.enrichment.copy_snapshots_to_catalog import run


def main() -> None:
    counts = run()
    for video_type, count in counts.items():
        print(f"{video_type}: {count} exact-identity matches copied into the catalog.")


if __name__ == "__main__":
    main()
