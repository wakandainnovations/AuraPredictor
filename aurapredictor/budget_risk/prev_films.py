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

import math
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


# Cast-based rating_range default: each actor's log-average over their last
# PREV_WINDOW films that have one, then a weighted log-average across the cast. The
# lead counts 4x each supporting actor (half the weight with a full cast). On 1,913
# films, each guessed from earlier films only, that matched the real band 54% of the
# time vs 52% lead-only and 39% for the language default, and halved the share of
# 2+ bands off. More supporting weight hurt star films: co-stars often come from
# smaller ones.
LEAD_RATING_RANGE_WEIGHT = 4.0
SUPPORTING_RATING_RANGE_WEIGHT = 1.0


def actor_rating_ranges(prev_films: PrevFilms, actor: str, as_of_date: str) -> list[float]:
    """The actor's last PREV_WINDOW known rating ranges before as_of_date. 1 is left
    out: it's also the fill's "no data" marker, so it can't be told apart."""
    known = [film[3] for film in prev_films.get("lead_actor", {}).get(actor, []) if film[0] < as_of_date and film[3] and film[3] > 1]
    return known[-PREV_WINDOW:]


def cast_rating_range(
    prev_films: PrevFilms,
    lead_actor: str | None,
    supporting_actors: list[str],
    as_of_date: str,
    bands: tuple[int, ...],
) -> tuple[int, dict[str, list[float]]] | None:
    """(band, each contributing actor's ranges) from the cast's recent films, or None
    when nobody in the cast has one. Snaps to the nearest band in log terms, ties to
    the lower one -- the cautious side for a risk line."""
    weighted, used = [], {}
    cast = [(lead_actor, LEAD_RATING_RANGE_WEIGHT)] + [(a, SUPPORTING_RATING_RANGE_WEIGHT) for a in supporting_actors]
    for actor, weight in cast:
        ranges = actor_rating_ranges(prev_films, actor, as_of_date) if actor else []
        if ranges:
            used[actor] = ranges
            weighted.append((weight, sum(math.log10(r) for r in ranges) / len(ranges)))
    if not weighted:
        return None
    log_range = sum(w * x for w, x in weighted) / sum(w for w, _ in weighted)
    band = min((b for b in bands if b > 1), key=lambda b: (abs(math.log10(b) - log_range), b))
    return band, used


def load_prev_films() -> PrevFilms:
    columns = f"movie_name, release_date, budget, revenue, rating_range, {', '.join(SLOT_ROLES)}"
    return build_prev_films(
        fetch_all(f"SELECT {columns} FROM {MOVIES_TABLE}"),
        fetch_all(f"SELECT {columns} FROM {PREV_MOVIES_TABLE}"),
    )
