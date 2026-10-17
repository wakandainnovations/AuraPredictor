"""Resumable batch job: fills in the TMDB/YouTube-derived columns on MOVIES_TABLE
(constants.MOVIES_TABLE, currently `india_movies_data_collection`) for Indian
movies that still have them missing.

IMPORTANT -- this table is also actively maintained by a separate, pre-existing
pipeline (AuraDataFiller: moviedetails/runtimebudget/youtube packages) using
`movie_details_last_checked` as ITS OWN bookkeeping column (a revisit-after-N-days
cooldown, not a permanent "done" flag). This job does NOT read or gate on that
column -- it targets rows by directly checking which target fields (revenue,
budget, genre, production_companies, directors, runtime, trailer/teaser views) are
still actually missing, which is correct regardless of what AuraDataFiller has or
hasn't touched, and never collides with its cooldown semantics. Every write still
uses COALESCE/NULLIF-only-if-missing, matching AuraDataFiller's own convention, so
whichever pipeline gets to a row first never clobbers the other's value -- this is
intentionally kept as a second, independent source (TMDB is more accurate than
AuraDataFiller's other sources for some fields, and it won't cover every movie),
run after AuraDataFiller's own pass to mop up whatever it left missing.

Each movie is processed in its own try/except so one bad row (unresolvable title,
a deleted TMDB entry, ...) never aborts the batch.

Requires config/secrets.properties (or the AURA_DB_URL / TMDB_* / YOUTUBE_API_KEY
env vars) to be filled in with real credentials -- this only runs against the
placeholders checked into config/secrets.example.properties otherwise.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from aurapredictor.config import load_config
from aurapredictor.constants import MOVIES_TABLE, is_indian_language
from aurapredictor.dates import days_between, release_year
from aurapredictor.db import execute, fetch_all
from aurapredictor.enrichment import video_snapshots
from aurapredictor.enrichment.tmdb_client import TMDBClient, directors_from_credits, earliest_video_date
from aurapredictor.enrichment.youtube_client import YouTubeClient

_IMDB_ID_RE = re.compile(r"^tt\d+$")
_TMDB_ID_RE = re.compile(r"^\d+$")

# TMDB's real rate limit is 40 requests/second (confirmed by the user; consistent
# with AuraDataFiller's own code comment citing "~35 requests/second" measured in
# practice -- the earlier 5 req/s figure this was based on was wrong). 0.03s
# between every single TMDB call (including the id-resolution find/search call,
# not just the 3 detail calls after it) keeps us at ~33 req/s -- a safety margin,
# not just under the wire.
REQUEST_DELAY_SECONDS = 0.03


# Fields this pipeline can fill. A row qualifies for (re)processing whenever ANY of
# these still looks empty -- deliberately field-level rather than a single
# "done" checkpoint, since AuraDataFiller may have already filled some of these
# and not others for the same row.
_MISSING_FIELDS_SQL = """(
    COALESCE(revenue, 0) = 0
    OR COALESCE(budget, 0) = 0
    OR COALESCE(runtime, 0) = 0
    OR NULLIF(genre, '') IS NULL
    OR NULLIF(production_companies, '') IS NULL
    OR NULLIF(directors, '') IS NULL
    OR cbfc_rating IS NULL
    OR trailer_views IS NULL
    OR teaser_views IS NULL
)"""


def _fetch_pending_movies(limit: int, force: bool) -> list[dict[str, Any]]:
    rows = fetch_all(
        f"""
        SELECT movie_name, release_date, language, id
        FROM {MOVIES_TABLE}
        WHERE language IS NOT NULL
          AND release_date ~ '^[0-9]{{4}}'
        {"" if force else f"AND {_MISSING_FIELDS_SQL}"}
        ORDER BY release_date DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [row for row in rows if is_indian_language(row["language"])]


def _resolve_tmdb_id(tmdb: TMDBClient, movie: dict[str, Any]) -> int | None:
    raw_id = (movie.get("id") or "").strip()
    if _TMDB_ID_RE.match(raw_id):
        return int(raw_id)
    if _IMDB_ID_RE.match(raw_id):
        found = tmdb.find_by_imdb_id(raw_id)
        return found["id"] if found else None

    found = tmdb.search_movie(movie["movie_name"], release_year(movie["release_date"]))
    return found["id"] if found else None


def _collect_video_ids(
    trailer: tuple[str, str] | None, teaser: tuple[str, str] | None
) -> list[str]:
    """YouTube keys for whichever of trailer/teaser were actually found -- either
    can independently be None (no such video on TMDB), so this filters missing
    ones out *before* unpacking rather than after."""
    return [item[1] for item in (trailer, teaser) if item is not None]


def _enrich_one(tmdb: TMDBClient, youtube: YouTubeClient, movie: dict[str, Any]) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    tmdb_id = _resolve_tmdb_id(tmdb, movie)
    time.sleep(REQUEST_DELAY_SECONDS)  # _resolve_tmdb_id may itself have called find/search

    if tmdb_id is None:
        execute(
            f"""UPDATE {MOVIES_TABLE} SET movie_details_last_checked = %s
               WHERE movie_name = %s AND release_date = %s AND language = %s""",
            (now_iso, movie["movie_name"], movie["release_date"], movie["language"]),
        )
        return

    details = tmdb.movie_details(tmdb_id)
    time.sleep(REQUEST_DELAY_SECONDS)
    credits_ = tmdb.movie_credits(tmdb_id)
    time.sleep(REQUEST_DELAY_SECONDS)
    videos = tmdb.movie_videos(tmdb_id)
    time.sleep(REQUEST_DELAY_SECONDS)

    directors = directors_from_credits(credits_)
    genres = [g["name"] for g in details.get("genres", [])]
    companies = [c["name"] for c in details.get("production_companies", [])]
    runtime_minutes = details.get("runtime")  # TMDB's runtime is already in minutes

    trailer = earliest_video_date(videos, "Trailer")
    teaser = earliest_video_date(videos, "Teaser")

    # Persist the resolved YouTube video ids (not stored anywhere else in the
    # schema) so scripts/run_wayback_enrichment.py can look up point-in-time view
    # counts for them later -- piggybacks on the TMDB call already made above, no
    # extra API cost.
    for video_type, found in (("trailer", trailer), ("teaser", teaser)):
        if found is not None:
            published_at, youtube_id = found
            video_snapshots.upsert_video_id(
                movie["movie_name"], movie["release_date"], movie["language"], video_type, youtube_id, published_at
            )

    trailer_views = trailer_comments = teaser_views = teaser_comments = None
    video_ids = _collect_video_ids(trailer, teaser)
    if video_ids:
        stats = youtube.video_statistics(video_ids)
        if trailer and trailer[1] in stats:
            trailer_views, trailer_comments = stats[trailer[1]]["views"], stats[trailer[1]]["comments"]
        if teaser and teaser[1] in stats:
            teaser_views, teaser_comments = stats[teaser[1]]["views"], stats[teaser[1]]["comments"]

    execute(
        f"""
        UPDATE {MOVIES_TABLE} SET
            id = %s,
            revenue = COALESCE(NULLIF(%s, 0), revenue),
            budget = COALESCE(NULLIF(%s, 0), budget),
            runtime = COALESCE(%s, runtime),
            runtime_mins = COALESCE(%s, runtime_mins),
            status = COALESCE(%s, status),
            genre = COALESCE(NULLIF(%s, ''), genre),
            genres = COALESCE(NULLIF(%s, ''), genres),
            production_companies = COALESCE(NULLIF(%s, ''), production_companies),
            directors = COALESCE(NULLIF(%s, ''), directors),
            trailer_release_date = %s,
            teaser_release_date = %s,
            trailer_days_to_release = %s,
            teaser_days_to_release = %s,
            trailer_views = %s,
            trailer_comments = %s,
            teaser_views = %s,
            teaser_comments = %s,
            youtube_last_checked = %s,
            movie_details_last_checked = %s
        WHERE movie_name = %s AND release_date = %s AND language = %s
        """,
        (
            str(tmdb_id),
            details.get("revenue"),
            details.get("budget"),
            runtime_minutes,
            runtime_minutes,
            details.get("status"),
            ", ".join(genres),
            ", ".join(genres),
            ", ".join(companies),
            ", ".join(directors),
            trailer[0] if trailer else None,
            teaser[0] if teaser else None,
            days_between(trailer[0], movie["release_date"]) if trailer else None,
            days_between(teaser[0], movie["release_date"]) if teaser else None,
            trailer_views,
            trailer_comments,
            teaser_views,
            teaser_comments,
            now_iso,
            now_iso,
            movie["movie_name"],
            movie["release_date"],
            movie["language"],
        ),
    )


def run(limit: int = 100, force: bool = False) -> tuple[int, int]:
    video_snapshots.ensure_table()
    config = load_config()
    tmdb = TMDBClient(config)
    youtube = YouTubeClient(config)

    movies = _fetch_pending_movies(limit, force)
    succeeded = failed = 0
    for movie in movies:
        try:
            _enrich_one(tmdb, youtube, movie)
            succeeded += 1
        except Exception as exc:  # one bad row must never abort the whole batch
            print(f"  [skip] {movie['movie_name']} ({movie['release_date']}): {exc}")
            failed += 1
    return succeeded, failed
