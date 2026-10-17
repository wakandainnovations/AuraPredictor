"""_quota_error_reason's pure response-parsing logic, plus YouTubeSearchClient's
multi-key rotation -- both exercised without real network calls."""

from __future__ import annotations

from unittest.mock import MagicMock

from aurapredictor.enrichment.youtube_search_client import (
    YouTubeQuotaExceededError,
    YouTubeSearchClient,
    _quota_error_reason,
)


def _response(status_code: int, body: dict | None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if body is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = body
    return resp


def test_detects_legacy_quota_exceeded_reason():
    body = {"error": {"errors": [{"reason": "quotaExceeded", "message": "..."}]}}
    assert _quota_error_reason(_response(403, body)) == "quotaExceeded"


def test_detects_real_429_rate_limit_exceeded_response():
    """The exact shape a real exhausted search.list quota returned in practice --
    HTTP 429, not 403, with reason "rateLimitExceeded" and status
    "RESOURCE_EXHAUSTED". Caught after an earlier version only checked for 403
    and silently burned through the rest of a batch making doomed calls."""
    body = {
        "error": {
            "code": 429,
            "errors": [{"reason": "rateLimitExceeded", "domain": "global", "message": "Quota exceeded..."}],
            "status": "RESOURCE_EXHAUSTED",
        }
    }
    assert _quota_error_reason(_response(429, body)) == "rateLimitExceeded"


def test_detects_daily_limit_exceeded_reason():
    body = {"error": {"errors": [{"reason": "dailyLimitExceeded"}]}}
    assert _quota_error_reason(_response(403, body)) == "dailyLimitExceeded"


def test_detects_resource_exhausted_status():
    body = {"error": {"status": "RESOURCE_EXHAUSTED", "errors": []}}
    assert _quota_error_reason(_response(403, body)) == "RESOURCE_EXHAUSTED"


def test_returns_none_for_unrelated_403():
    body = {"error": {"errors": [{"reason": "keyInvalid"}]}}
    assert _quota_error_reason(_response(403, body)) is None


def test_returns_none_for_unparseable_body():
    assert _quota_error_reason(_response(403, None)) is None


def test_returns_none_for_missing_error_key():
    assert _quota_error_reason(_response(403, {})) is None


class _FakeConfig:
    def __init__(self, keys: list[str]):
        self.youtube_api_keys = keys


_QUOTA_BODY = {"error": {"errors": [{"reason": "rateLimitExceeded"}], "status": "RESOURCE_EXHAUSTED"}}


def _quota_response() -> MagicMock:
    return _response(429, _QUOTA_BODY)


def _ok_response(title: str = "Some Movie - Official Trailer") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "items": [
            {
                "id": {"videoId": "abc123"},
                "snippet": {"title": title, "channelTitle": "Some Channel", "publishedAt": "2023-01-01T00:00:00Z"},
            }
        ]
    }
    return resp


def test_rotates_to_next_key_when_first_is_exhausted():
    client = YouTubeSearchClient(_FakeConfig(["key1", "key2"]))
    client._session = MagicMock()
    client._session.get.side_effect = [_quota_response(), _ok_response()]

    results = client.search_videos("some query")

    assert len(results) == 1
    assert results[0]["video_id"] == "abc123"
    assert client._session.get.call_count == 2
    # second call used the second key, not the first (exhausted) one again
    assert client._session.get.call_args_list[1].kwargs["params"]["key"] == "key2"


def test_raises_only_after_every_key_is_exhausted():
    client = YouTubeSearchClient(_FakeConfig(["key1", "key2", "key3"]))
    client._session = MagicMock()
    client._session.get.side_effect = [_quota_response(), _quota_response(), _quota_response()]

    try:
        client.search_videos("some query")
        assert False, "expected YouTubeQuotaExceededError"
    except YouTubeQuotaExceededError:
        pass

    assert client._session.get.call_count == 3  # tried every key, not just the first


def test_uses_first_key_when_it_is_healthy():
    client = YouTubeSearchClient(_FakeConfig(["key1", "key2"]))
    client._session = MagicMock()
    client._session.get.side_effect = [_ok_response()]

    results = client.search_videos("some query")

    assert len(results) == 1
    assert client._session.get.call_count == 1
    assert client._session.get.call_args_list[0].kwargs["params"]["key"] == "key1"


def test_raises_plain_runtime_error_when_no_keys_configured():
    try:
        YouTubeSearchClient(_FakeConfig([]))
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "No YouTube API key" in str(exc)
