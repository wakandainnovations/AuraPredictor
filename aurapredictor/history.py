"""Prior-filmography math shared by actors, directors, and music directors alike --
the same two questions ("how many movies has this person done as of a date" and
"were their last N movies hits or flops") apply to any of the three, just pointed at
a different person's filmography. Reused identically by feature building at training
time and by the what-if scenario engine (replacing/adding a person recomputes these
from that person's *real* filmography, not a guess).

Each `filmography` argument is a list of dicts with at least `release_date` (text,
possibly year-only -- see aurapredictor/dates.py), `revenue`, and `budget` for one
person's other movies (already fetched by the caller; this module has no DB access
so it's trivially unit-testable).
"""

from __future__ import annotations

from typing import Any, TypedDict

from aurapredictor.constants import BLOCKBUSTER, HIT, PRIOR_FILMS_WINDOW, performance_tier
from aurapredictor.dates import is_before, parse_release_date

# Tiers counted as a "hit" for the hits:flops ratio. Blockbuster is included since
# "was it a hit or a flop" is the natural binary reading of the 3-way tier -- a
# blockbuster is a hit by any informal definition, just an especially big one. See
# performance_tier() in constants.py for the 3-way classification itself.
HIT_LIKE_TIERS = frozenset({HIT, BLOCKBUSTER})


class HitFlopSummary(TypedDict):
    hits: int
    flops: int
    hit_flop_ratio: float | None


def prior_films(filmography: list[dict[str, Any]], as_of_date: str | None) -> list[dict[str, Any]]:
    """Films strictly before `as_of_date`, most recent first. Rows with an
    unparseable release_date are excluded (is_before() never treats them as prior)."""
    prior = [row for row in filmography if is_before(row.get("release_date"), as_of_date)]
    prior.sort(key=lambda row: parse_release_date(row.get("release_date")), reverse=True)
    return prior


def prior_films_count(filmography: list[dict[str, Any]], as_of_date: str | None) -> int:
    """"Popularity index": total number of movies this person had done as of the
    given release date."""
    return len(prior_films(filmography, as_of_date))


def hit_flop_ratio_last_n(
    filmography: list[dict[str, Any]],
    as_of_date: str | None,
    n: int = PRIOR_FILMS_WINDOW,
) -> HitFlopSummary:
    recent = prior_films(filmography, as_of_date)[:n]

    hits = flops = 0
    for row in recent:
        revenue, budget = row.get("revenue"), row.get("budget")
        if not budget:
            continue
        tier = performance_tier(float(revenue or 0) / float(budget))
        if tier in HIT_LIKE_TIERS:
            hits += 1
        elif tier is not None:
            flops += 1

    if flops > 0:
        ratio: float | None = hits / flops
    elif hits > 0:
        ratio = float(hits)  # all hits, no flops in the window
    else:
        ratio = None

    return {"hits": hits, "flops": flops, "hit_flop_ratio": ratio}
