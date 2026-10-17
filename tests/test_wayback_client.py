"""extract_view_count is verified against real archived YouTube pages (not just
synthetic fixtures) in a one-off check during development -- both the modern
(2022, JSON-embedded) and legacy (2010, watch-view-count element) formats matched
the page's real view count exactly. These tests cover the same two formats plus
edge cases with small synthetic snippets, and choose_closest_snapshot's pure
date-distance logic -- none of this touches the network."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
import requests

from aurapredictor.enrichment import wayback_client
from aurapredictor.enrichment.wayback_client import WaybackClient, choose_closest_snapshot, extract_view_count

_HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


def _row(timestamp: str, original: str = "https://www.youtube.com/watch?v=abc") -> list[str]:
    return ["com,youtube)/watch?v=abc", timestamp, original, "text/html", "200", "DIGEST", "12345"]


def test_extract_view_count_modern_json_format():
    html = '<script>var ytInitialPlayerResponse = {"videoDetails":{"viewCount":"1123832647"}};</script>'
    assert extract_view_count(html) == 1123832647


def test_extract_view_count_legacy_format():
    html = '<div><strong class="watch-view-count">23,419,292</strong></div>'
    assert extract_view_count(html) == 23419292


def test_extract_view_count_prefers_modern_when_both_present():
    html = (
        '<strong class="watch-view-count">999</strong>'
        '<script>{"viewCount":"1123832647"}</script>'
    )
    assert extract_view_count(html) == 1123832647


def test_extract_view_count_returns_none_when_no_match():
    assert extract_view_count("<html><body>nothing here</body></html>") is None


def test_choose_closest_snapshot_picks_nearest_to_target_date():
    rows = [_row("20220101000000"), _row("20220115000000"), _row("20220110000000")]
    result = choose_closest_snapshot(rows, _HEADER, date(2022, 1, 12))
    assert result["timestamp"] == "20220110000000"
    assert result["snapshot_date"] == date(2022, 1, 10)


def test_choose_closest_snapshot_exact_match_wins():
    rows = [_row("20220101000000"), _row("20220112000000")]
    result = choose_closest_snapshot(rows, _HEADER, date(2022, 1, 12))
    assert result["timestamp"] == "20220112000000"


def test_choose_closest_snapshot_empty_rows_returns_none():
    assert choose_closest_snapshot([], _HEADER, date(2022, 1, 1)) is None


def _make_client(monkeypatch) -> WaybackClient:
    monkeypatch.setattr(WaybackClient, "_load_robots", lambda self: MagicMock(can_fetch=lambda *a: True))
    monkeypatch.setattr(wayback_client.time, "sleep", lambda *_: None)
    return WaybackClient()


def _response(status_code: int) -> MagicMock:
    response = MagicMock(status_code=status_code)
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
    else:
        response.raise_for_status.side_effect = None
    return response


def test_get_with_retry_retries_5xx_then_succeeds(monkeypatch):
    client = _make_client(monkeypatch)
    client._session.get = MagicMock(side_effect=[_response(503), _response(503), _response(200)])

    result = client._get_with_retry("http://example.com")

    assert result.status_code == 200
    assert client._session.get.call_count == 3


def test_get_with_retry_gives_up_after_max_attempts_of_5xx(monkeypatch):
    client = _make_client(monkeypatch)
    client._session.get = MagicMock(return_value=_response(503))

    with pytest.raises(requests.exceptions.HTTPError):
        client._get_with_retry("http://example.com")

    assert client._session.get.call_count == wayback_client._MAX_ATTEMPTS


def test_get_with_retry_never_retries_4xx(monkeypatch):
    client = _make_client(monkeypatch)
    client._session.get = MagicMock(return_value=_response(404))

    with pytest.raises(requests.exceptions.HTTPError):
        client._get_with_retry("http://example.com")

    assert client._session.get.call_count == 1


def test_get_with_retry_stops_early_once_deadline_passes(monkeypatch):
    """Regression test for the real overnight stall: attempt count alone must not
    be the only thing bounding how long a single request can take. Simulates
    monotonic time jumping past the deadline after the first failed attempt --
    a 2nd/3rd attempt (allowed by _MAX_ATTEMPTS) must NOT happen once time is up."""
    client = _make_client(monkeypatch)
    client._session.get = MagicMock(return_value=_response(503))

    real_monotonic = wayback_client.time.monotonic()
    # First call establishes the deadline; every call after jumps far past it.
    monkeypatch.setattr(
        wayback_client.time,
        "monotonic",
        MagicMock(side_effect=[real_monotonic, real_monotonic + 9999, real_monotonic + 9999, real_monotonic + 9999]),
    )

    with pytest.raises(requests.exceptions.HTTPError):
        client._get_with_retry("http://example.com")

    assert client._session.get.call_count == 1


def test_get_with_retry_retries_timeout(monkeypatch):
    client = _make_client(monkeypatch)
    client._session.get = MagicMock(side_effect=[requests.exceptions.Timeout(), _response(200)])

    result = client._get_with_retry("http://example.com")

    assert result.status_code == 200
    assert client._session.get.call_count == 2
