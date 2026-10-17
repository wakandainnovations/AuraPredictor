"""Per-person track record as of a release date -- revenue and rating of each
cast/crew member's previous film and the average over their last 5 -- built from
MOVIES_TABLE itself (every row there has a real revenue and rating_10).

Computed from the person's *name* at scoring time rather than stored per movie, so
a what-if that swaps someone in (scenarios/handlers.py) automatically gets the new
person's track record as of that movie's release date.
"""

from __future__ import annotations

import re
from typing import Any

from aurapredictor.constants import MOVIES_TABLE
from aurapredictor.dates import release_year as parse_release_year
from aurapredictor.db import fetch_all

TRACK_RECORD_WINDOW = 5

# Billing slot -> role whose filmography it draws on. Actors are pooled across all
# five lead slots: someone billed 3rd here may have been 1st in their last film.
SLOT_ROLES = {
    "lead_actor_1": "lead_actor",
    "lead_actor_2": "lead_actor",
    "lead_actor_3": "lead_actor",
    "lead_actor_4": "lead_actor",
    "lead_actor_5": "lead_actor",
    "director": "director",
    "music_director": "music_director",
}

TRACK_RECORD_STATS = ("last5_avg_revenue", "prev_revenue", "last5_avg_rating", "prev_rating")

TRACK_RECORD_FEATURES = [f"{slot}_{stat}" for slot in SLOT_ROLES for stat in TRACK_RECORD_STATS]

# Titles are compared on letters/digits only: the same film is stored under
# punctuation variants ("Golmaal - Fun Unlimited" / "Golmaal: Fun Unlimited").
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")

PeopleFilms = dict[str, dict[str, list[dict[str, Any]]]]


def _film_key(movie_name: str | None, release_date: str | None) -> tuple[str, int | None]:
    return _NON_ALNUM_RE.sub("", (movie_name or "").lower()), parse_release_year(release_date)


def build_people_films(movies: list[dict[str, Any]]) -> PeopleFilms:
    """role -> person -> their films (release_date, revenue, rating_10), sorted by
    release_date and deduped by (normalized title, year) so a film stored twice
    (e.g. under two language strings or title spellings) isn't counted as two
    previous films. Which duplicate's revenue wins is arbitrary (first row read)."""
    by_person: dict[str, dict[str, dict[tuple, dict[str, Any]]]] = {role: {} for role in set(SLOT_ROLES.values())}
    for movie in movies:
        if not movie.get("release_date"):
            continue
        film = {
            "release_date": movie["release_date"],
            "revenue": float(movie["revenue"]) if movie.get("revenue") else None,
            "rating_10": float(movie["rating_10"]) if movie.get("rating_10") else None,
        }
        for slot, role in SLOT_ROLES.items():
            person = (movie.get(slot) or "").strip()
            if person:
                by_person[role].setdefault(person, {}).setdefault(_film_key(movie["movie_name"], movie["release_date"]), film)
    return {
        role: {person: sorted(films.values(), key=lambda f: f["release_date"]) for person, films in people.items()}
        for role, people in by_person.items()
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def track_record(films: list[dict[str, Any]], as_of_date: str | None, n: int = TRACK_RECORD_WINDOW) -> dict[str, float | None]:
    """Stats over the person's films released strictly before as_of_date (the film
    being scored itself is never included). All None for a debut / unknown person."""
    prior = [f for f in films if as_of_date and f["release_date"] < as_of_date][-n:]
    previous = prior[-1] if prior else {}
    return {
        "last5_avg_revenue": _mean([f["revenue"] for f in prior if f["revenue"] is not None]),
        "prev_revenue": previous.get("revenue"),
        "last5_avg_rating": _mean([f["rating_10"] for f in prior if f["rating_10"] is not None]),
        "prev_rating": previous.get("rating_10"),
    }


def track_record_fields(row: dict[str, Any], people_films: PeopleFilms | None) -> dict[str, float | None]:
    """All TRACK_RECORD_FEATURES for one movie row, from the names in its slots."""
    fields: dict[str, float | None] = {}
    for slot, role in SLOT_ROLES.items():
        person = (row.get(slot) or "").strip()
        films = (people_films or {}).get(role, {}).get(person, [])
        for stat, value in track_record(films, row.get("release_date")).items():
            fields[f"{slot}_{stat}"] = value
    return fields


def load_people_films() -> PeopleFilms:
    return build_people_films(
        fetch_all(
            f"""
            SELECT movie_name, release_date, revenue, rating_10, {", ".join(SLOT_ROLES)}
            FROM {MOVIES_TABLE}
            """
        )
    )
