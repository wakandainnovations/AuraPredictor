#!/usr/bin/env bash
# One command for a full day's increment of the trailer/teaser view-count
# backfill: TMDB/YouTube pass -> YouTube-search fallback (for movies TMDB has
# no video listing for) -> Wayback point-in-time view counts -> copy exact
# matches into the catalog the port-3033 curation UI reads.
#
# Safe to run daily (cron/launchd) or by hand -- every step is resumable and
# COALESCE-only-if-missing; a bad row in any step never aborts the batch.
# The search fallback runs until the API itself reports the quota is gone
# (exit code 2) rather than stopping at a guessed call count -- see
# aurapredictor/enrichment/youtube_search_client.py's docstring -- and that
# exit code is deliberately NOT fatal to this script: the Wayback and
# copy-back steps below are independent and should still run.
set -uo pipefail
cd "$(dirname "$0")/.."

echo "=== TMDB/YouTube pass ==="
./venv/bin/python scripts/run_enrichment.py --limit 300 || true

echo "=== YouTube search fallback ==="
./venv/bin/python scripts/run_youtube_search_fallback.py
fallback_status=$?
if [ "$fallback_status" -ne 0 ] && [ "$fallback_status" -ne 2 ]; then
    echo "YouTube search fallback failed unexpectedly (exit $fallback_status)"
fi

echo "=== Wayback view-count backfill ==="
./venv/bin/python scripts/run_wayback_enrichment.py --limit 300 || true

echo "=== Copy exact-identity matches into the catalog ==="
./venv/bin/python scripts/run_copy_snapshots_to_catalog.py
