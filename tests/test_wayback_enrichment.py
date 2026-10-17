from __future__ import annotations

from datetime import date

from aurapredictor.enrichment.wayback_enrichment import _process_one, _published_date


class FakeWaybackClient:
    def __init__(self, results: dict[date, dict]):
        self.results = results
        self.queried_dates: list[date] = []

    def fetch_view_count_near(self, url, target_date, window_days=14):
        self.queried_dates.append(target_date)
        return self.results.get(target_date)


class FakeVideoSnapshots:
    def __init__(self):
        self.saved = None

    def save_wayback_result(self, movie_name, release_date, language, video_type, *args):
        self.saved = (movie_name, release_date, language, video_type, *args)


def test_published_date_parses_iso_prefix():
    assert _published_date("2022-01-15T00:00:00.000Z") == date(2022, 1, 15)


def test_published_date_none_or_invalid():
    assert _published_date(None) is None
    assert _published_date("not-a-date") is None


def test_process_one_queries_both_dates_and_saves(monkeypatch):
    import aurapredictor.enrichment.wayback_enrichment as mod

    fake_snapshots = FakeVideoSnapshots()
    monkeypatch.setattr(mod, "video_snapshots", fake_snapshots)

    client = FakeWaybackClient(
        {
            date(2022, 3, 17): {"views": 1000, "snapshot_date": "2022-03-17"},
            date(2022, 3, 24): {"views": 5000, "snapshot_date": "2022-03-24"},
        }
    )
    target = {
        "movie_name": "RRR",
        "release_date": "2022-03-24",
        "language": "telugu",
        "video_type": "trailer",
        "youtube_video_id": "abc123",
        "published_at": "2022-01-01T00:00:00.000Z",
    }
    _process_one(client, target)

    assert set(client.queried_dates) == {date(2022, 3, 17), date(2022, 3, 24)}
    assert fake_snapshots.saved == ("RRR", "2022-03-24", "telugu", "trailer", 1000, "2022-03-17", 5000, "2022-03-24")


def test_process_one_skips_before_query_when_published_after_that_date(monkeypatch):
    import aurapredictor.enrichment.wayback_enrichment as mod

    fake_snapshots = FakeVideoSnapshots()
    monkeypatch.setattr(mod, "video_snapshots", fake_snapshots)

    client = FakeWaybackClient({date(2022, 3, 24): {"views": 5000, "snapshot_date": "2022-03-24"}})
    target = {
        "movie_name": "Late Trailer Movie",
        "release_date": "2022-03-24",
        "language": "telugu",
        "video_type": "trailer",
        "youtube_video_id": "xyz",
        "published_at": "2022-03-20T00:00:00.000Z",  # published only 4 days before release
    }
    _process_one(client, target)

    # Video didn't exist yet 7 days before release -- must not waste a query on it.
    assert client.queried_dates == [date(2022, 3, 24)]
    assert fake_snapshots.saved[4] is None  # views_7d_before_release
    assert fake_snapshots.saved[6] == 5000  # views_on_release


def test_process_one_skips_unparseable_release_date(monkeypatch):
    import aurapredictor.enrichment.wayback_enrichment as mod

    fake_snapshots = FakeVideoSnapshots()
    monkeypatch.setattr(mod, "video_snapshots", fake_snapshots)

    client = FakeWaybackClient({})
    target = {
        "movie_name": "X",
        "release_date": "Unknown",
        "language": "telugu",
        "video_type": "trailer",
        "youtube_video_id": "abc",
        "published_at": None,
    }
    _process_one(client, target)

    assert client.queried_dates == []
    assert fake_snapshots.saved is None
