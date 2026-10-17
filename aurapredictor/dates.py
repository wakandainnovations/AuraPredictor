"""release_date across movies_data_collection / actors_data_collection is stored as
text and is inconsistent grain: sometimes a full ISO date ("2023-05-05"), sometimes
just a year ("2022"). Every comparison in this project goes through here so that
year-only precision is handled the same way everywhere rather than silently drifting
between modules.

A year-only date is treated as January 1st of that year for ordering purposes. This
means "prior movies" computed against a year-only release date can be off by up to
a year at the boundary -- documented here rather than hidden, since it directly
affects prior-films-count / hit-flop-history accuracy for rows lacking a full date.
"""

from __future__ import annotations

import re
from datetime import date

_FULL_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_YEAR_ONLY_RE = re.compile(r"^(\d{4})$")


def parse_release_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip()

    match = _FULL_DATE_RE.match(raw)
    if match:
        year, month, day = (int(g) for g in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, 1, 1)

    match = _YEAR_ONLY_RE.match(raw)
    if match:
        return date(int(match.group(1)), 1, 1)

    return None


def is_before(a: str | None, b: str | None) -> bool:
    """True if release date `a` is strictly before release date `b`. Unparseable
    dates never compare as before anything, so a row we can't date never gets
    treated as this movie's "prior" release."""
    parsed_a, parsed_b = parse_release_date(a), parse_release_date(b)
    if parsed_a is None or parsed_b is None:
        return False
    return parsed_a < parsed_b


def days_between(earlier: str | None, later: str | None) -> int | None:
    parsed_earlier, parsed_later = parse_release_date(earlier), parse_release_date(later)
    if parsed_earlier is None or parsed_later is None:
        return None
    return (parsed_later - parsed_earlier).days


def release_month(raw: str | None) -> int | None:
    parsed = parse_release_date(raw)
    return parsed.month if parsed else None


# Days before each month's 1st in a non-leap year. Day-of-year is computed against these
# fixed offsets so a calendar date always gets the same number (Pongal Jan 14 = 14, Gandhi
# Jayanti Oct 2 = 275, Christmas = 359) regardless of leap years; Feb 29 shares 60 with Mar 1.
# Must stay identical to the india_movies_data_collection.day_of_year/week_of_year generated
# columns (AuraDataFiller), which use the same formula.
_NON_LEAP_MONTH_OFFSETS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def _exact_release_date(raw: str | None) -> date | None:
    """Like parse_release_date, but None for year-only or invalid dates instead of
    falling back to Jan 1st -- day/week-of-year must not pretend a "2022" row
    released on New Year's Day."""
    match = _FULL_DATE_RE.match((raw or "").strip())
    if not match:
        return None
    try:
        return date(*(int(g) for g in match.groups()))
    except ValueError:
        return None


def release_day_of_year(raw: str | None) -> int | None:
    """Leap-normalised day of year, 1-365."""
    parsed = _exact_release_date(raw)
    return _NON_LEAP_MONTH_OFFSETS[parsed.month - 1] + parsed.day if parsed else None


def release_week_of_year(raw: str | None) -> int | None:
    """Fixed 7-day block from Jan 1st (1-52, Dec 31st folded into 52) -- not ISO week,
    which would move a fixed date like Pongal between weeks depending on the weekday."""
    day = release_day_of_year(raw)
    return min((day - 1) // 7 + 1, 52) if day else None


def release_day_of_week(raw: str | None) -> str | None:
    parsed = parse_release_date(raw)
    return parsed.strftime("%A") if parsed else None


def release_year(raw: str | None) -> int | None:
    parsed = parse_release_date(raw)
    return parsed.year if parsed else None
