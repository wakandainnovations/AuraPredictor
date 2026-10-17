"""Batch job: for every trailer/teaser YouTube video id tracked in
movie_video_snapshots (populated by enrich_movies.py's TMDB pass, which is why
that must run first) that's still missing one or both point-in-time view counts,
looks them up via the Wayback Machine and saves the result.

Resumable by construction: fetch_pending_wayback_targets only returns rows still
missing a value, and save_wayback_result's own COALESCE means a partial success
(only the "7 days before" side resolved, say) is never lost on a later retry that
fills in the other side.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from aurapredictor.dates import parse_release_date
from aurapredictor.enrichment import video_snapshots
from aurapredictor.enrichment.wayback_client import WaybackClient


def _published_date(published_at: str | None) -> date | None:
    if not published_at:
        return None
    try:
        return date.fromisoformat(published_at[:10])
    except ValueError:
        return None


def _process_one(client: WaybackClient, target: dict[str, Any]) -> None:
    release_date = parse_release_date(target["release_date"])
    if release_date is None:
        return  # can't compute "7 days before"/"on release" without a real date

    url = f"https://www.youtube.com/watch?v={target['youtube_video_id']}"
    before_date = release_date - timedelta(days=7)
    published = _published_date(target.get("published_at"))

    # No point querying a date before the video even existed -- Wayback will
    # correctly find nothing nearby anyway, this just saves the round trip.
    before_result = None if published and before_date < published else client.fetch_view_count_near(url, before_date)
    release_result = client.fetch_view_count_near(url, release_date)

    video_snapshots.save_wayback_result(
        target["movie_name"],
        target["release_date"],
        target["language"],
        target["video_type"],
        before_result["views"] if before_result else None,
        before_result["snapshot_date"] if before_result else None,
        release_result["views"] if release_result else None,
        release_result["snapshot_date"] if release_result else None,
    )


def run(limit: int = 100) -> tuple[int, int]:
    video_snapshots.ensure_table()
    client = WaybackClient()

    targets = video_snapshots.fetch_pending_wayback_targets(limit)
    succeeded = failed = 0
    for target in targets:
        try:
            _process_one(client, target)
            succeeded += 1
        except Exception as exc:  # one bad row must never abort the whole batch
            print(f"  [skip] {target['movie_name']} ({target['video_type']}): {exc}")
            failed += 1
    return succeeded, failed
