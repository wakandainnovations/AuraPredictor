"""movie_video_snapshots: tracks which specific YouTube video (trailer/teaser) TMDB
resolved for a movie, plus point-in-time view counts reconstructed from the Wayback
Machine for two dates -- 7 days before release and on release day.

Why not just use the current trailer_views/teaser_views columns on
movies_data_collection? Those come straight from the YouTube API, which only ever
returns the video's *current* cumulative view count -- for a movie that released
months or years ago, that number includes every post-release rewatch, not just
pre-release marketing reach. It's a real, unavoidable leakage risk for a model
whose premise is "predict revenue from pre-release signals" (flagged up front when
this project started). The two Wayback-derived numbers here are the actual
leakage-free fix: what the view count genuinely was at each of those two moments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aurapredictor.db import execute, fetch_all

TABLE_NAME = "movie_video_snapshots"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    movie_name text NOT NULL,
    release_date text NOT NULL,
    language text NOT NULL,
    video_type text NOT NULL,
    youtube_video_id text NOT NULL,
    published_at text,
    views_7d_before_release bigint,
    views_7d_before_snapshot_date text,
    views_on_release bigint,
    views_on_release_snapshot_date text,
    wayback_last_checked timestamp with time zone,
    PRIMARY KEY (movie_name, release_date, language, video_type)
)
"""


def ensure_table() -> None:
    execute(CREATE_TABLE_SQL)


def upsert_video_id(
    movie_name: str,
    release_date: str,
    language: str,
    video_type: str,
    youtube_video_id: str,
    published_at: str | None,
) -> None:
    execute(
        f"""
        INSERT INTO {TABLE_NAME}
            (movie_name, release_date, language, video_type, youtube_video_id, published_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (movie_name, release_date, language, video_type) DO UPDATE SET
            youtube_video_id = EXCLUDED.youtube_video_id,
            published_at = COALESCE(EXCLUDED.published_at, {TABLE_NAME}.published_at)
        """,
        (movie_name, release_date, language, video_type, youtube_video_id, published_at),
    )


def fetch_pending_wayback_targets(limit: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT movie_name, release_date, language, video_type, youtube_video_id, published_at
        FROM {TABLE_NAME}
        WHERE views_7d_before_release IS NULL OR views_on_release IS NULL
        ORDER BY release_date DESC
        LIMIT %s
        """,
        (limit,),
    )


def save_wayback_result(
    movie_name: str,
    release_date: str,
    language: str,
    video_type: str,
    views_7d_before_release: int | None,
    views_7d_before_snapshot_date: str | None,
    views_on_release: int | None,
    views_on_release_snapshot_date: str | None,
) -> None:
    execute(
        f"""
        UPDATE {TABLE_NAME} SET
            views_7d_before_release = COALESCE(%s, views_7d_before_release),
            views_7d_before_snapshot_date = COALESCE(%s, views_7d_before_snapshot_date),
            views_on_release = COALESCE(%s, views_on_release),
            views_on_release_snapshot_date = COALESCE(%s, views_on_release_snapshot_date),
            wayback_last_checked = %s
        WHERE movie_name = %s AND release_date = %s AND language = %s AND video_type = %s
        """,
        (
            views_7d_before_release,
            views_7d_before_snapshot_date,
            views_on_release,
            views_on_release_snapshot_date,
            datetime.now(timezone.utc),
            movie_name,
            release_date,
            language,
            video_type,
        ),
    )
