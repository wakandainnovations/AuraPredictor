"""build_update_params's pure value-selection logic -- no network/DB."""

from __future__ import annotations

from aurapredictor.enrichment.copy_snapshots_to_catalog import build_update_params


def _row(catalog_release_date=None, published_at=None, views_on_release=None, views_7d=None):
    return {
        "movie_name": "RRR",
        "release_date": "2022-03-24",
        "language": "telugu",
        "catalog_release_date": catalog_release_date,
        "published_at": published_at,
        "views_on_release": views_on_release,
        "views_7d_before_release": views_7d,
    }


def test_uses_existing_catalog_release_date_when_present():
    row = _row(catalog_release_date="2022-01-05", published_at="2022-01-10T00:00:00Z",
               views_on_release=5000, views_7d=1000)
    params = build_update_params(row)
    assert params[0] == "2022-01-05"  # release date kept, not overwritten by published_at
    assert params[1] == days_between_expected("2022-01-05", "2022-03-24")
    assert params[2] == 5000
    assert params[3] == 1000
    assert params[4:] == ("RRR", "2022-03-24", "telugu")


def test_falls_back_to_snapshot_published_date_when_catalog_release_date_missing():
    row = _row(catalog_release_date=None, published_at="2022-01-10T00:00:00.000Z",
               views_on_release=5000, views_7d=None)
    params = build_update_params(row)
    assert params[0] == "2022-01-10"
    assert params[1] == days_between_expected("2022-01-10", "2022-03-24")
    assert params[2] == 5000
    assert params[3] is None


def test_no_release_date_available_at_all():
    row = _row(catalog_release_date=None, published_at=None, views_on_release=5000, views_7d=None)
    params = build_update_params(row)
    assert params[0] is None
    assert params[1] is None
    assert params[2] == 5000


def days_between_expected(earlier: str, later: str) -> int:
    from datetime import date
    e = date(*(int(x) for x in earlier.split("-")))
    l = date(*(int(x) for x in later.split("-")))
    return (l - e).days
