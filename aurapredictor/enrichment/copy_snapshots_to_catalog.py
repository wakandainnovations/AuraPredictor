"""Copies Wayback-derived point-in-time view counts from movie_video_snapshots
into india_movies_data_collection -- the table the manual curation UI
(AuraMath/scripts/india_movie_data_entry_ui.py, port 3033) actually reads and
edits. Without this step, values land in movie_video_snapshots and never reach
the catalog: that UI's own "Video snapshot matches" panel deliberately stops at
filling the *form* fields ("Apply" never writes to the DB on its own) because its
candidates are found by trigram name similarity, not an exact join, and a human
needs to eyeball a fuzzy match before it's saved.

This script only ever writes on an EXACT (movie_name, release_date, language)
match between the two tables -- no fuzzy matching, no human eyeball needed,
because there's no ambiguity to resolve there. A snapshot row whose identity
differs even slightly (diacritics, TMDB-vs-crawler release-date drift -- see the
UI's docstring for a real example: "Baahubali: The Epic" vs "Bahubali: The
Epic") simply won't match here and is left to that UI's fuzzy-match panel
instead, exactly as before this script existed.

Every write is COALESCE-only-if-missing, the same convention every other writer
of this table follows (enrich_movies.py, AuraDataFiller) -- never overwrites a
value a curator or another pipeline already filled in. In particular
{type}_views only gets the Wayback release-day count when it's still empty --
enrich_movies.py's live YouTube API cumulative count (today's view count, not
release day's) takes priority if it got there first, since that field is shared
between both writers with no way to tell which kind of number is already there.
"""

from __future__ import annotations

from typing import Any

from aurapredictor.constants import MOVIES_TABLE
from aurapredictor.dates import days_between
from aurapredictor.db import execute, fetch_all
from aurapredictor.enrichment import video_snapshots

VIDEO_TYPES = ("trailer", "teaser")


def fetch_exact_matches(video_type: str) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT m.movie_name, m.release_date, m.language,
               m.{video_type}_release_date AS catalog_release_date,
               s.published_at, s.views_on_release, s.views_7d_before_release
        FROM {video_snapshots.TABLE_NAME} s
        JOIN {MOVIES_TABLE} m
          ON m.movie_name = s.movie_name AND m.release_date = s.release_date AND m.language = s.language
        WHERE s.video_type = %s
          AND (s.views_on_release IS NOT NULL OR s.views_7d_before_release IS NOT NULL)
          AND (COALESCE(m.{video_type}_views, 0) = 0 OR m.{video_type}_views_7d_prior_to_release IS NULL)
        """,
        (video_type,),
    )


def build_update_params(row: dict[str, Any]) -> tuple[Any, ...]:
    """Pure (no network/DB) so it's directly unit-testable: the values to write,
    in the same order build_update_sql's placeholders expect. new_release_date
    falls back to the snapshot's published_at date only when the catalog has no
    release date of its own yet; new_days_to_release is then recomputed from
    whichever release date will actually end up on the row (recomputing from an
    already-present catalog value is harmless -- the UPDATE's own COALESCE keeps
    the original either way, so this never overwrites a value that was already
    there)."""
    new_release_date = row["catalog_release_date"] or (row["published_at"] or "")[:10] or None
    new_days_to_release = days_between(new_release_date, row["release_date"]) if new_release_date else None
    return (
        new_release_date,
        new_days_to_release,
        row["views_on_release"],
        row["views_7d_before_release"],
        row["movie_name"],
        row["release_date"],
        row["language"],
    )


def _apply_one(video_type: str, row: dict[str, Any]) -> None:
    execute(
        f"""
        UPDATE {MOVIES_TABLE} SET
            {video_type}_release_date = COALESCE({video_type}_release_date, %s),
            {video_type}_days_to_release = COALESCE({video_type}_days_to_release, %s),
            {video_type}_views = COALESCE(NULLIF({video_type}_views, 0), %s),
            {video_type}_views_7d_prior_to_release = COALESCE({video_type}_views_7d_prior_to_release, %s)
        WHERE movie_name = %s AND release_date = %s AND language = %s
        """,
        build_update_params(row),
    )


def run() -> dict[str, int]:
    counts = {}
    for video_type in VIDEO_TYPES:
        rows = fetch_exact_matches(video_type)
        for row in rows:
            _apply_one(video_type, row)
        counts[video_type] = len(rows)
    return counts
