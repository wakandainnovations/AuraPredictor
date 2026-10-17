"""actors_data_collection mixes cast and crew credits in one table (per the project
brief: "it has data for music directors, producers, directors, etc. as well"), with
character_name doing double duty as an actual character name for cast rows and as a
role/job label for crew rows. This module is the single place that tells them apart,
so training-time feature building and what-if scenario recomputation never diverge
on the definition of "lead actor" or "music director".

This is inherently heuristic on scraped data (see CREW_ROLE_LABELS in constants.py)
-- not guaranteed correct on every row, which is why it's covered by tests/test_people.py
fixtures rather than assumed correct.
"""

from __future__ import annotations

from typing import Any, Iterable

from aurapredictor.constants import CREW_ROLE_LABELS, MUSIC_DIRECTOR_LABEL


def _normalize(label: str | None) -> str:
    return (label or "").strip().lower()


def is_crew_row(character_name: str | None) -> bool:
    return _normalize(character_name) in CREW_ROLE_LABELS


def is_music_director_row(character_name: str | None) -> bool:
    return _normalize(character_name) == MUSIC_DIRECTOR_LABEL


def cast_rows(actor_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in actor_rows if not is_crew_row(row.get("character_name"))]


def lead_actor_for_movie(actor_rows: Iterable[dict[str, Any]]) -> str | None:
    """Lead actor = lowest role_position among the movie's cast rows. Falls back to
    the first cast row encountered when no row has a role_position (present on
    ~62% of actors_data_collection rows)."""
    candidates = cast_rows(actor_rows)
    if not candidates:
        return None

    positioned = [row for row in candidates if row.get("role_position") is not None]
    if positioned:
        best = min(positioned, key=lambda row: row["role_position"])
        return best.get("actor_name")

    return candidates[0].get("actor_name")


def music_director_for_movie(actor_rows: Iterable[dict[str, Any]]) -> str | None:
    for row in actor_rows:
        if is_music_director_row(row.get("character_name")):
            return row.get("actor_name")
    return None


def primary_director(directors_field: str | None) -> str | None:
    """movies_data_collection.directors is a clean, comma-separated field (unlike
    actors_data_collection) -- take the first named director for co-directed films."""
    if not directors_field:
        return None
    first = directors_field.split(",")[0].strip()
    return first or None
