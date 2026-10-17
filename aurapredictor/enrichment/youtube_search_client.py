"""Thin wrapper around YouTube Data API v3's search.list -- used only by
youtube_search_fallback.py to find a trailer video TMDB doesn't list (see that
module's docstring for why this exists as a separate, deliberately rarer call).

search.list costs 100 quota units per call (vs. 1 unit for videos.list, which is
all youtube_client.py needs). What the *daily* budget that buys is not assumed
here -- rather than cap call volume against a guessed number up front, this
detects the API's own real signal that the quota is exhausted and raises
YouTubeQuotaExceededError -- callers stop the whole batch on that specific
error rather than leaving real headroom unused (or, as happened once during
development: silently burning through hundreds of doomed calls after the quota
was already gone, because the detection only checked for HTTP 403).

Confirmed against a real exhausted-quota response from this project's own key:
Google returns HTTP 429 (not 403) for search.list specifically, with
error.errors[].reason == "rateLimitExceeded" and error.status ==
"RESOURCE_EXHAUSTED" ("Quota exceeded for quota metric 'Search Queries'...",
quota_limit_value 100/day for this project). Both 403 and 429 are checked --
other Google APIs are documented to use 403 with "quotaExceeded" /
"dailyLimitExceeded" for the same condition, which this still also recognizes
in case a different key/quota configuration ever hits that path instead.

Multi-key rotation (added 2026-09-18, once a second Cloud project's key became
available): each Google Cloud project's search.list quota is independent, so
this client holds config.youtube_api_keys (however many are configured) and
moves to the next one the moment the current one reports exhaustion -- the
*same* query is retried on the new key rather than being skipped, so a key
boundary never costs a movie. YouTubeQuotaExceededError only ever propagates
out of search_videos once every configured key has been tried and exhausted in
this process, at which point the caller's per-run 100/day/key logic no longer
applies and the whole batch really is out of options until quotas reset.
Rotation position is in-memory only (not persisted across process runs) --
re-probing an already-exhausted key at the start of a fresh run costs one
rejected call, not quota (a 429 isn't itself billed), so this stays simple
rather than tracking cross-run state for a one-call cost.
"""

from __future__ import annotations

from typing import Any

import requests

from aurapredictor.config import Config

_BASE_URL = "https://www.googleapis.com/youtube/v3/search"

_QUOTA_ERROR_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}


class YouTubeQuotaExceededError(RuntimeError):
    """Every configured key is exhausted -- distinct from every other error (a
    bad request, a transient 5xx, ...) so a caller can stop spending further
    calls immediately instead of retrying or skipping one row at a time
    against a quota that's already gone until it resets."""


def _quota_error_reason(response: requests.Response) -> str | None:
    """None if this 403/429 isn't a quota error at all (a plain bad-key/
    permission problem on a 403, say) -- should still surface as a normal
    HTTPError in that case, not get misread as a quota stop."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        return None
    for detail in error.get("errors", []):
        reason = detail.get("reason")
        if reason in _QUOTA_ERROR_REASONS:
            return reason
    if error.get("status") == "RESOURCE_EXHAUSTED":
        return "RESOURCE_EXHAUSTED"
    return None


class YouTubeSearchClient:
    def __init__(self, config: Config):
        self._api_keys = config.youtube_api_keys
        if not self._api_keys:
            raise RuntimeError(
                "No YouTube API key configured: set youtube.api.key in config/secrets.properties "
                "or the YOUTUBE_API_KEY environment variable."
            )
        self._key_index = 0
        self._session = requests.Session()

    def search_videos(self, query: str, *, relevance_language: str | None = None, max_results: int = 5) -> list[dict[str, Any]]:
        """Returns up to max_results {video_id, title, channel_title, published_at}
        dicts, ordered by YouTube's own relevance ranking (the default `order`).
        Transparently rotates to the next configured key and retries this same
        query if the current one turns out to be quota-exhausted -- see module
        docstring."""
        while True:
            params: dict[str, Any] = {
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": max_results,
                "key": self._api_keys[self._key_index],
            }
            if relevance_language:
                params["relevanceLanguage"] = relevance_language
            response = self._session.get(_BASE_URL, params=params, timeout=30)

            if response.status_code in (403, 429) and _quota_error_reason(response):
                if self._key_index + 1 < len(self._api_keys):
                    self._key_index += 1
                    continue  # retry the same query on the next key
                raise YouTubeQuotaExceededError(
                    f"All {len(self._api_keys)} configured YouTube API key(s) are quota-exhausted"
                )

            response.raise_for_status()
            results = []
            for item in response.json().get("items", []):
                video_id = item.get("id", {}).get("videoId")
                if not video_id:
                    continue
                snippet = item.get("snippet", {})
                results.append({
                    "video_id": video_id,
                    "title": snippet.get("title", ""),
                    "channel_title": snippet.get("channelTitle", ""),
                    "published_at": snippet.get("publishedAt"),
                })
            return results
