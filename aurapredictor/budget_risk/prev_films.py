"""The *_prev3_* / *_prev_movie_* person-history columns for a movie that isn't in
the DB yet, rebuilt the way AuraDataFiller's IndiaPrevMovieStatsFillService fills
them on MOVIES_TABLE:

- the person's last PREV_WINDOW films before the release date, with values from
  MOVIES_TABLE or PREV_MOVIES_TABLE (the films collected for exactly this purpose);
- prev3_avg_budget / _revenue: average over those films, 0 when there are none;
- prev3_avg_rating_range: sum over the PREV_WINDOW slots, a missing film or a NULL
  rating_range counting as 1, divided by PREV_WINDOW;
- prev_movie_*: the latest film alone (budget/revenue 0, rating_range 1 when none).

One difference: the Java side finds "last 3 films" from full TMDB filmographies, so a
film missing from both tables leaves a gap; here only films in the tables are known,
so the next-older one takes its place. The fewer films are missing (the
india_prev_movies_missing to-do list), the closer the two agree.
"""

from __future__ import annotations

from typing import Any

from aurapredictor.constants import MOVIES_TABLE
from aurapredictor.db import fetch_all
from aurapredictor.model.dataset import PEOPLE_FEATURES
from aurapredictor.track_record import SLOT_ROLES, _film_key

PREV_MOVIES_TABLE = "india_prev_movies_data_collection"
PREV_WINDOW = 3

PREV_STATS = (
    "prev3_avg_budget",
    "prev3_avg_revenue",
    "prev3_avg_rating_range",
    "prev_movie_budget",
    "prev_movie_revenue",
    "prev_movie_rating_range",
)

PREV_FILM_FEATURES = [f"{slot}_{stat}" for slot in PEOPLE_FEATURES for stat in PREV_STATS]

# role -> person -> [(release_date, budget, revenue, rating_range)], oldest first.
PrevFilms = dict[str, dict[str, list[tuple[str, float | None, float | None, float | None]]]]


def _value(raw: Any) -> float | None:
    return float(raw) if raw is not None else None


def build_prev_films(india_rows: list[dict[str, Any]], prev_rows: list[dict[str, Any]]) -> PrevFilms:
    """One film per (normalized title, year) per person; an India row wins over a
    collected one for the same film, same as the Java fill's tie-break."""
    by_person: dict[str, dict[str, dict[tuple, tuple]]] = {role: {} for role in set(SLOT_ROLES.values())}
    for row in [*india_rows, *prev_rows]:
        if not row.get("release_date"):
            continue
        film = (row["release_date"], _value(row.get("budget")), _value(row.get("revenue")), _value(row.get("rating_range")))
        key = _film_key(row.get("movie_name"), row["release_date"])
        for slot, role in SLOT_ROLES.items():
            person = (row.get(slot) or "").strip()
            if person:
                by_person[role].setdefault(person, {}).setdefault(key, film)
    return {
        role: {person: sorted(films.values(), key=lambda film: film[0]) for person, films in people.items()}
        for role, people in by_person.items()
    }


def prev_film_stats(films: list[tuple], as_of_date: str) -> dict[str, float]:
    prior = [film for film in films if film[0] < as_of_date][-PREV_WINDOW:]
    budgets = [film[1] for film in prior if film[1] is not None]
    revenues = [film[2] for film in prior if film[2] is not None]
    rating_ranges = [film[3] for film in prior if film[3] is not None]
    latest = prior[-1] if prior else (None, None, None, None)
    return {
        "prev3_avg_budget": sum(budgets) / len(budgets) if budgets else 0.0,
        "prev3_avg_revenue": sum(revenues) / len(revenues) if revenues else 0.0,
        "prev3_avg_rating_range": (sum(rating_ranges) + PREV_WINDOW - len(rating_ranges)) / PREV_WINDOW,
        "prev_movie_budget": latest[1] or 0.0,
        "prev_movie_revenue": latest[2] or 0.0,
        "prev_movie_rating_range": latest[3] if latest[3] is not None else 1.0,
    }


def prev_film_fields(row: dict[str, Any], prev_films: PrevFilms) -> dict[str, float | None]:
    """PREV_FILM_FEATURES for one movie row, from the names in its slots (NULL for an
    empty slot, like the DB columns)."""
    fields: dict[str, float | None] = {}
    for slot, role in SLOT_ROLES.items():
        person = (row.get(slot) or "").strip()
        stats = prev_film_stats(prev_films.get(role, {}).get(person, []), row["release_date"]) if person else {}
        for stat in PREV_STATS:
            fields[f"{slot}_{stat}"] = stats.get(stat)
    return fields


def load_prev_films() -> PrevFilms:
    columns = f"movie_name, release_date, budget, revenue, rating_range, {', '.join(SLOT_ROLES)}"
    return build_prev_films(
        fetch_all(f"SELECT {columns} FROM {MOVIES_TABLE}"),
        fetch_all(f"SELECT {columns} FROM {PREV_MOVIES_TABLE}"),
    )
