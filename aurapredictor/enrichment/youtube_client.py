"""Thin wrapper around YouTube Data API v3's videos.list (statistics part). Batches
up to 50 video IDs per call, per the API's own limit -- 1 quota unit per call
regardless of how many IDs/parts are requested, so batching is essentially free.
"""

from __future__ import annotations

from typing import Any

import requests

from aurapredictor.config import Config

_BASE_URL = "https://www.googleapis.com/youtube/v3/videos"
_BATCH_SIZE = 50


class YouTubeClient:
    def __init__(self, config: Config):
        self._api_key = config.youtube_api_key
        self._session = requests.Session()

    def video_statistics(self, video_ids: list[str]) -> dict[str, dict[str, int]]:
        """video_id -> {"views": int, "comments": int}. Missing/deleted videos are
        simply absent from the result rather than raising."""
        stats: dict[str, dict[str, int]] = {}
        for i in range(0, len(video_ids), _BATCH_SIZE):
            batch = video_ids[i : i + _BATCH_SIZE]
            response = self._session.get(
                _BASE_URL,
                params={"part": "statistics", "id": ",".join(batch), "key": self._api_key},
                timeout=30,
            )
            response.raise_for_status()
            for item in response.json().get("items", []):
                statistics = item.get("statistics", {})
                stats[item["id"]] = {
                    "views": int(statistics.get("viewCount", 0)),
                    "comments": int(statistics.get("commentCount", 0)),
                }
        return stats
