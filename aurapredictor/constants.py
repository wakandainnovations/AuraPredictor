"""Fixed vocabularies and thresholds shared across feature building, modeling, and
the what-if scenario engine. Keeping these in one place means a definition only
needs to change once for training and scenario-recomputation to stay consistent.
"""

from __future__ import annotations

# Primary movie source table. Switched from the global `movies_data_collection`
# (387K rows, ~9% Indian-language fill rate for revenue/budget) to this curated,
# actively-growing table (100% revenue/budget fill rate as of the switch) --
# single source of truth so every query site stays consistent if this ever moves
# again. actors_data_collection has no equivalent curated counterpart, so cast/
# crew history still reads from the original table.
MOVIES_TABLE = "india_movies_data_collection"

# Matched against MOVIES_TABLE.language, which can be a single language
# or a comma-separated multi-language string (e.g. "hindi, tamil"). A movie counts
# as Indian if ANY of its listed languages is in this set. Chosen over the `country`
# column, which was found to be noisy (co-production country lists sometimes wrongly
# include India for movies that aren't Indian productions, e.g. global animated films).
INDIAN_LANGUAGES: frozenset[str] = frozenset(
    {
        "hindi",
        "tamil",
        "telugu",
        "malayalam",
        "kannada",
        "bengali",
        "marathi",
        "punjabi",
        "gujarati",
        "urdu",
        "bhojpuri",
        "odia",
        "oriya",
        "assamese",
        "tulu",
        "konkani",
        "sanskrit",
        "kashmiri",
        "manipuri",
        "rajasthani",
        "haryanvi",
        "chhattisgarhi",
    }
)

# actors_data_collection.character_name doubles as a "role/job label" for crew rows
# and as an actual character name for cast rows. Any row whose character_name
# (case-insensitive, trimmed) matches one of these is treated as crew, not cast --
# see aurapredictor/people.py. Music-director rows use the dedicated MUSIC_DIRECTOR_LABEL
# constant below rather than this generic crew set, since they need to be identified
# specifically (not just excluded from the cast pool).
MUSIC_DIRECTOR_LABEL = "original music"

CREW_ROLE_LABELS: frozenset[str] = frozenset(
    {
        "director",
        "producer",
        "executive producer",
        "screenwriter",
        "writer",
        "story",
        "dialogue",
        "cinematographer",
        "director of photography",
        "editor",
        "film editor",
        "costume design",
        "casting",
        "choreographer",
        "sound",
        "sound designer",
        "narrator",
        "self",
        "himself",
        "herself",
        "tba",
        "director, screenwriter",
        "director, writer",
        MUSIC_DIRECTOR_LABEL,
    }
)

# revenue / budget ratio thresholds, exactly as specified: blockbuster > 3,
# hit strictly between 2 and 3, flop otherwise (<= 2).
FLOP_MAX_RATIO = 2.0
HIT_MAX_RATIO = 3.0

BLOCKBUSTER = "blockbuster"
HIT = "hit"
FLOP = "flop"

PRIOR_FILMS_WINDOW = 3  # "past 3 movies" for hit/flop ratio history


def performance_tier(revenue_budget_ratio: float | None) -> str | None:
    if revenue_budget_ratio is None:
        return None
    if revenue_budget_ratio > HIT_MAX_RATIO:
        return BLOCKBUSTER
    if revenue_budget_ratio > FLOP_MAX_RATIO:
        return HIT
    return FLOP


def is_indian_language(language: str | None) -> bool:
    if not language:
        return False
    parts = [p.strip().lower() for p in language.split(",")]
    return any(p in INDIAN_LANGUAGES for p in parts)
