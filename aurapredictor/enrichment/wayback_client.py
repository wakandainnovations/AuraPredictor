"""Reconstructs a YouTube video's view count as it stood on a specific historical
date, via the Wayback Machine -- used to get "views 7 days before release" and
"views on release day" for a movie's trailer/teaser, which the live YouTube API
cannot give us (it only ever returns the *current* cumulative count; see
video_snapshots.py's docstring for why that's a leakage problem).

Compliance: checks web.archive.org's robots.txt at runtime (via urllib.robotparser,
not a hardcoded assumption) before every request, both for the CDX search API and
for fetching an actual snapshot -- if robots.txt can't be read at all, this fails
closed (treats everything as disallowed) rather than assuming permission. As of
this writing web.archive.org serves no robots.txt (404), which robotparser
correctly treats as "nothing disallowed"; this stays live-checked rather than
baked in as a comment so behavior tracks reality if that ever changes.

Snapshot selection ("wisely choose the snapshot"): archive.org has captured any
given YouTube watch page many times, rarely on the exact date wanted. For each
target date this searches a window centered on it and picks the ONE snapshot whose
capture date has the smallest absolute distance to the target -- not the first or
most recent result the CDX API happens to return -- widening the window once
before giving up if nothing nearby exists.
"""

from __future__ import annotations

import re
import time
import urllib.robotparser
from datetime import date, timedelta
from typing import Any

import requests

_ROBOTS_URL = "https://web.archive.org/robots.txt"
_CDX_URL = "http://web.archive.org/cdx/search/cdx"
_SNAPSHOT_URL_TEMPLATE = "http://web.archive.org/web/{timestamp}/{original_url}"
_USER_AGENT = "AuraPredictorBot/1.0 (movie revenue research; contact: local project owner)"

# archive.org publishes no documented hard rate limit for CDX/playback (unlike
# TMDB's explicit 5 req/s), but a low, polite request rate avoids soft throttling
# on a shared public resource -- 1 request/second, never more.
REQUEST_DELAY_SECONDS = 1.0

_VIEWCOUNT_MODERN_RE = re.compile(r'"viewCount"\s*:\s*"(\d+)"')
_VIEWCOUNT_LEGACY_RE = re.compile(r'watch-view-count"[^>]*>([\d,]+)<')


class RobotsDisallowed(RuntimeError):
    pass


def extract_view_count(html: str) -> int | None:
    """Modern (~2017+) archived YouTube pages embed the exact view count as
    "viewCount":"<digits>" in inlined JSON (videoDetails) -- tried first since
    it's an exact integer, and (verified against real archived pages) appears
    exactly once per page for the main video, unlike the several "N views" text
    strings that also appear for sidebar/related videos. Legacy (pre-2017) pages
    instead render a comma-formatted number in a watch-view-count element."""
    match = _VIEWCOUNT_MODERN_RE.search(html)
    if match:
        return int(match.group(1))

    match = _VIEWCOUNT_LEGACY_RE.search(html)
    if match:
        return int(match.group(1).replace(",", ""))

    return None


def _parse_snapshot_date(timestamp: str) -> date:
    return date(int(timestamp[0:4]), int(timestamp[4:6]), int(timestamp[6:8]))


def choose_closest_snapshot(rows: list[list[str]], header: list[str], target_date: date) -> dict[str, Any] | None:
    """Pure selection logic (no network) so it's directly unit-testable: given CDX
    API rows and their header, returns the one row whose capture date is closest
    to target_date, or None if there are no data rows."""
    if not rows:
        return None
    idx = {name: i for i, name in enumerate(header)}
    best = min(rows, key=lambda row: abs((_parse_snapshot_date(row[idx["timestamp"]]) - target_date).days))
    return {
        "timestamp": best[idx["timestamp"]],
        "original_url": best[idx["original"]],
        "snapshot_date": _parse_snapshot_date(best[idx["timestamp"]]),
    }


# A shared public resource occasionally answers slowly, drops a connection, or
# (observed in practice during a real overnight run: ~150 consecutive CDX calls
# all failing with 503) throttles a sustained burst of traffic -- a transient
# timeout/connection error or 5xx response gets a few retries with growing
# backoff before being treated as a real failure for that row. 5xx specifically
# gets a longer backoff than a plain timeout/connection hiccup, since a real
# soft-throttle needs more than a couple seconds to clear.
#
# _MAX_SECONDS_PER_REQUEST is a HARD wall-clock cap on top of the attempt count:
# observed in practice that attempt-count-only retries can silently chain into a
# multi-minute stall per URL when a resource is persistently uncooperative (a
# 60-item batch made 3 successful checks in the first 33 minutes, then produced
# zero further progress for the next 34+ minutes while burning ~1s of CPU time --
# it wasn't hung, it was legitimately waiting out retries with no overall time
# budget). One bad URL must never be able to block an entire unattended run.
_MAX_ATTEMPTS = 3
_REQUEST_TIMEOUT_SECONDS = 15
_RETRY_BACKOFF_SECONDS = 3.0
_SERVER_ERROR_BACKOFF_SECONDS = 5.0
_MAX_SECONDS_PER_REQUEST = 45.0


def _sleep_within_deadline(seconds: float, deadline: float) -> None:
    """Never sleeps past the per-request deadline, so a backoff wait can't itself
    be the thing that blows the time budget."""
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(seconds, remaining))


class WaybackClient:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers["User-Agent"] = _USER_AGENT
        self._robots = self._load_robots()

    def _load_robots(self) -> urllib.robotparser.RobotFileParser:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(_ROBOTS_URL)
        try:
            parser.read()
        except Exception:
            # Unreachable/unparsable robots.txt -- fail closed, never assume permission.
            parser.disallow_all = True
        return parser

    def _check_allowed(self, url: str) -> None:
        if not self._robots.can_fetch(_USER_AGENT, url):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")

    def _get_with_retry(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        deadline = time.monotonic() + _MAX_SECONDS_PER_REQUEST
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            is_last = attempt == _MAX_ATTEMPTS - 1 or time.monotonic() >= deadline
            try:
                response = self._session.get(url, params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
                response.raise_for_status()
                return response
            except requests.exceptions.HTTPError as exc:
                last_error = exc
                # 4xx (e.g. a genuinely missing page) won't fix itself on retry;
                # only 5xx (server overload/throttling -- what actually happened
                # in practice) is worth waiting out, and only within the deadline.
                if exc.response is None or exc.response.status_code < 500 or is_last:
                    raise
                _sleep_within_deadline(_SERVER_ERROR_BACKOFF_SECONDS * (attempt + 1), deadline)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = exc
                if is_last:
                    raise
                _sleep_within_deadline(_RETRY_BACKOFF_SECONDS * (attempt + 1), deadline)
        raise last_error

    def find_snapshot_near(self, target_url: str, target_date: date, window_days: int = 14) -> dict[str, Any] | None:
        for days in (window_days, window_days * 3):
            snapshot = self._search_cdx(target_url, target_date, days)
            if snapshot:
                return snapshot
        return None

    def _search_cdx(self, target_url: str, target_date: date, window_days: int) -> dict[str, Any] | None:
        self._check_allowed(_CDX_URL)
        response = self._get_with_retry(
            _CDX_URL,
            params={
                "url": target_url,
                "output": "json",
                "from": (target_date - timedelta(days=window_days)).strftime("%Y%m%d"),
                "to": (target_date + timedelta(days=window_days)).strftime("%Y%m%d"),
                "filter": "statuscode:200",
                "collapse": "timestamp:8",  # at most one snapshot per calendar day
            },
        )
        time.sleep(REQUEST_DELAY_SECONDS)
        rows = response.json()
        if len(rows) <= 1:  # header only, or genuinely empty
            return None
        header, *data_rows = rows
        return choose_closest_snapshot(data_rows, header, target_date)

    def fetch_view_count_near(self, target_url: str, target_date: date, window_days: int = 14) -> dict[str, Any] | None:
        """Full pipeline for one (video, target_date) pair: find the closest
        snapshot, fetch it, extract the view count. Returns None (never a guess)
        if no snapshot exists nearby or the page's view count can't be parsed."""
        snapshot = self.find_snapshot_near(target_url, target_date, window_days)
        if snapshot is None:
            return None

        snapshot_url = _SNAPSHOT_URL_TEMPLATE.format(
            timestamp=snapshot["timestamp"], original_url=snapshot["original_url"]
        )
        self._check_allowed(snapshot_url)
        response = self._get_with_retry(snapshot_url)
        time.sleep(REQUEST_DELAY_SECONDS)

        views = extract_view_count(response.text)
        if views is None:
            return None

        return {
            "views": views,
            "snapshot_date": snapshot["snapshot_date"].isoformat(),
        }
