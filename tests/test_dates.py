from datetime import date

from aurapredictor.dates import (
    days_between,
    is_before,
    parse_release_date,
    release_day_of_week,
    release_day_of_year,
    release_month,
    release_week_of_year,
    release_year,
)


def test_parse_full_date():
    assert parse_release_date("2023-05-05") == date(2023, 5, 5)


def test_parse_year_only():
    assert parse_release_date("2022") == date(2022, 1, 1)


def test_parse_none_or_empty():
    assert parse_release_date(None) is None
    assert parse_release_date("") is None
    assert parse_release_date("TBA") is None


def test_is_before_full_dates():
    assert is_before("2020-01-01", "2021-01-01") is True
    assert is_before("2021-01-01", "2020-01-01") is False


def test_is_before_year_only_treated_as_jan_1():
    assert is_before("2020", "2021") is True
    assert is_before("2021-06-01", "2021") is False  # 2021 == 2021-01-01, not before it


def test_is_before_unparseable_never_prior():
    assert is_before(None, "2021-01-01") is False
    assert is_before("2021-01-01", None) is False


def test_days_between():
    assert days_between("2020-01-01", "2020-01-11") == 10
    assert days_between(None, "2020-01-11") is None


def test_release_month_and_day_of_week():
    assert release_month("2023-05-05") == 5
    assert release_day_of_week("2023-05-05") == "Friday"
    assert release_month("2022") == 1


def test_release_year():
    assert release_year("2023-05-05") == 2023
    assert release_year("2022") == 2022
    assert release_year(None) is None


def test_day_and_week_of_year_fixed_festivals_same_every_year():
    for year in (2023, 2024, 2025):
        assert (release_day_of_year(f"{year}-01-14"), release_week_of_year(f"{year}-01-14")) == (14, 2)
        assert (release_day_of_year(f"{year}-04-14"), release_week_of_year(f"{year}-04-14")) == (104, 15)
        assert (release_day_of_year(f"{year}-10-02"), release_week_of_year(f"{year}-10-02")) == (275, 40)
        assert (release_day_of_year(f"{year}-12-25"), release_week_of_year(f"{year}-12-25")) == (359, 52)


def test_day_and_week_of_year_edges():
    assert (release_day_of_year("2023-01-01"), release_week_of_year("2023-01-01")) == (1, 1)
    assert release_day_of_year("2024-02-29") == release_day_of_year("2024-03-01") == 60
    assert release_day_of_year("2023-03-01") == 60
    assert (release_day_of_year("2024-12-31"), release_week_of_year("2024-12-31")) == (365, 52)
    assert release_day_of_year("2026-07-3") == 184


def test_day_and_week_of_year_none_without_exact_date():
    for raw in (None, "", "2022", "Unknown", "2023-02-30", "2023-13-01"):
        assert release_day_of_year(raw) is None
        assert release_week_of_year(raw) is None
