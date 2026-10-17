"""One function per what-if question. Each handler copies a movie's real raw
feature row, overrides a few fields using the same history/people logic used at
training time (aurapredictor.history / aurapredictor.people), and scores baseline
vs. scenario through engine.score_row(). Adding a new question type the user asks
for later means adding one more small function here, not new infrastructure.

Two honesty rules followed throughout:
1. Never override a field the model doesn't actually use as a trained feature --
   if the underlying data isn't there yet (see NOT_YET_SUPPORTED handlers below),
   say so plainly instead of fabricating a number.
2. Every override recomputes real history (via aurapredictor.history against that
   person's actual filmography), never a guessed constant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from aurapredictor.constants import PRIOR_FILMS_WINDOW
from aurapredictor.dates import release_year as parse_release_year
from aurapredictor.history import hit_flop_ratio_last_n, prior_films_count
from aurapredictor.model.dataset import split_languages
from aurapredictor.scenarios.engine import ScenarioContext, get_movie_row, score_row


@dataclass
class ScenarioResult:
    description: str
    baseline_revenue: float | None
    scenario_revenue: float | None
    supported: bool = True
    note: str | None = None

    @property
    def delta(self) -> float | None:
        if self.baseline_revenue is None or self.scenario_revenue is None:
            return None
        return self.scenario_revenue - self.baseline_revenue

    @property
    def delta_pct(self) -> float | None:
        if not self.baseline_revenue:
            return None
        delta = self.delta
        return None if delta is None else 100 * delta / self.baseline_revenue


def _unsupported(description: str, note: str) -> ScenarioResult:
    return ScenarioResult(description=description, baseline_revenue=None, scenario_revenue=None, supported=False, note=note)


def _load_baseline(movie_name: str, release_date: str, language: str) -> dict[str, Any] | None:
    return get_movie_row(movie_name, release_date, language)


def _person_history_fields(
    prefix: str, person_name: str | None, filmography_index: dict[str, list[dict]], as_of_date: str
) -> dict[str, Any]:
    if not person_name:
        return {
            f"{prefix}_prior_films_count": None,
            f"{prefix}_hit_flop_ratio_last3": None,
        }
    history = filmography_index.get(person_name, [])
    hit_flop = hit_flop_ratio_last_n(history, as_of_date, n=PRIOR_FILMS_WINDOW)
    return {
        f"{prefix}_prior_films_count": prior_films_count(history, as_of_date),
        f"{prefix}_hit_flop_ratio_last3": hit_flop["hit_flop_ratio"],
    }


# Placeholder name for a debutant: a value the model never saw in training, so CatBoost
# scores it with its neutral prior rather than as any real person (or as "Unknown",
# which it learned from rows where the credit simply wasn't captured).
DEBUT_PERSON_NAME = "(debut)"


def person_movie_count_as_of(context: ScenarioContext, role: str, person_name: str, as_of_date: str) -> int | None:
    """How many films person_name had released *before* as_of_date, the same convention
    as MOVIES_TABLE's *_movie_count columns (a debut film is 0). None when the person
    isn't in the data at all -- the count columns cover films outside MOVIES_TABLE too,
    so absence here doesn't mean a debut."""
    films = context.people_movie_counts.get(role, {}).get(person_name)
    if not films:
        return None
    earlier = [count for date, count in films if date < as_of_date]
    if earlier:
        # The latest earlier film had `count` films before it, plus itself.
        return max(earlier) + 1
    # Only later films: the earliest of them had min(count) films before it, so at most
    # that many could precede as_of_date.
    return min(count for _, count in films)


def _person_override_fields(
    context: ScenarioContext, slot: str, role: str, person_name: str | None, as_of_date: str
) -> dict[str, Any]:
    """Name + career-count columns for one billing slot (e.g. lead_actor_1, director)."""
    if not person_name:
        return {slot: None, f"{slot}_movie_count": None}
    return {
        slot: person_name,
        f"{slot}_movie_count": person_movie_count_as_of(context, role, person_name, as_of_date),
    }


def replace_actor(
    context: ScenarioContext, movie_name: str, release_date: str, language: str, new_actor_name: str
) -> ScenarioResult:
    """"What will the revenue be if actor X is replaced by Y" -- and, when the
    movie currently has no identified lead actor, doubles as "what if Y is added
    to the cast"."""
    baseline = _load_baseline(movie_name, release_date, language)
    if baseline is None:
        return _unsupported(f"Replace lead actor with {new_actor_name}", "Movie not found in movie_prediction_features.")

    baseline_revenue = score_row(context, baseline)
    scenario_row = dict(baseline)
    scenario_row.update(_person_history_fields("lead_actor", new_actor_name, context.actor_filmography, release_date))
    scenario_row.update(_person_override_fields(context, "lead_actor_1", "lead_actor", new_actor_name, release_date))
    scenario_revenue = score_row(context, scenario_row)

    return ScenarioResult(
        description=f"Replace lead actor with {new_actor_name}",
        baseline_revenue=baseline_revenue,
        scenario_revenue=scenario_revenue,
    )


def replace_music_director(
    context: ScenarioContext, movie_name: str, release_date: str, language: str, new_music_director_name: str
) -> ScenarioResult:
    baseline = _load_baseline(movie_name, release_date, language)
    if baseline is None:
        return _unsupported(
            f"Replace music director with {new_music_director_name}", "Movie not found in movie_prediction_features."
        )

    baseline_revenue = score_row(context, baseline)
    scenario_row = dict(baseline)
    scenario_row.update(
        _person_history_fields("music_director", new_music_director_name, context.actor_filmography, release_date)
    )
    scenario_row.update(
        _person_override_fields(context, "music_director", "music_director", new_music_director_name, release_date)
    )
    scenario_revenue = score_row(context, scenario_row)

    note = None
    if baseline.get("music_director_prior_films_count") is None:
        note = (
            "Only ~2.6% of movies in this dataset have a music-director credit captured, so this "
            "feature carries very little learned signal in the current model -- treat this delta as low-confidence "
            "until the TMDB/YouTube enrichment pipeline improves coverage."
        )

    return ScenarioResult(
        description=f"Replace music director with {new_music_director_name}",
        baseline_revenue=baseline_revenue,
        scenario_revenue=scenario_revenue,
        note=note,
    )


def new_music_director(context: ScenarioContext, movie_name: str, release_date: str, language: str) -> ScenarioResult:
    """"What if the music director is a new (debut) music director" -- sets the
    music-director history to a true debutant profile (0 prior films, no hit/flop
    ratio) rather than naming anyone specific."""
    baseline = _load_baseline(movie_name, release_date, language)
    if baseline is None:
        return _unsupported("Use a debut (first-time) music director", "Movie not found in movie_prediction_features.")

    baseline_revenue = score_row(context, baseline)
    scenario_row = dict(baseline)
    scenario_row["music_director_prior_films_count"] = 0
    scenario_row["music_director_hit_flop_ratio_last3"] = None
    scenario_row["music_director"] = DEBUT_PERSON_NAME
    scenario_row["music_director_movie_count"] = 0
    scenario_revenue = score_row(context, scenario_row)

    return ScenarioResult(
        description="Use a debut (first-time) music director",
        baseline_revenue=baseline_revenue,
        scenario_revenue=scenario_revenue,
        note="Same low-confidence caveat as replace_music_director -- this feature is sparsely populated today.",
    )


def replace_director(
    context: ScenarioContext, movie_name: str, release_date: str, language: str, new_director_name: str
) -> ScenarioResult:
    baseline = _load_baseline(movie_name, release_date, language)
    if baseline is None:
        return _unsupported(f"Replace director with {new_director_name}", "Movie not found in movie_prediction_features.")

    baseline_revenue = score_row(context, baseline)
    scenario_row = dict(baseline)
    scenario_row.update(_person_history_fields("director", new_director_name, context.director_filmography, release_date))
    scenario_row.update(_person_override_fields(context, "director", "director", new_director_name, release_date))
    scenario_revenue = score_row(context, scenario_row)

    return ScenarioResult(
        description=f"Replace director with {new_director_name}",
        baseline_revenue=baseline_revenue,
        scenario_revenue=scenario_revenue,
    )


def set_release_date(
    context: ScenarioContext, movie_name: str, release_date: str, language: str, new_release_date: str
) -> ScenarioResult:
    """Re-derives release_month and that target year's GDP/inflation from other
    movies actually released in that year, then re-scores. Does NOT move
    trailer/teaser/song days-to-release yet since those TMDB-derived dates are
    still almost entirely unpopulated in the source data (see project README) --
    once the enrichment pipeline fills them in, this handler should also shift
    those against the real trailer/teaser publish dates."""
    baseline = _load_baseline(movie_name, release_date, language)
    if baseline is None:
        return _unsupported(f"Release on {new_release_date} instead", "Movie not found in movie_prediction_features.")

    baseline_revenue = score_row(context, baseline)
    scenario_row = dict(baseline)
    scenario_row["release_date"] = new_release_date

    year = parse_release_year(new_release_date)
    macro = context.year_macro.get(year)
    if macro:
        scenario_row["gdp_usd_billions"] = macro["gdp_usd_billions"]
        scenario_row["inflation_rate_pct"] = macro["inflation_rate_pct"]

    notes = [] if macro else [f"No GDP/inflation data available for {year} in this dataset -- kept the original values."]
    event = context.date_events.get(new_release_date)
    scenario_row["release_event_type"], scenario_row["release_event_name"] = event or (None, None)
    if event is None:
        notes.append(f"No movie in the data released on {new_release_date} -- its festival/holiday/event is unknown.")

    scenario_revenue = score_row(context, scenario_row)

    note = " ".join(notes) or None

    return ScenarioResult(
        description=f"Release on {new_release_date} instead of {release_date}",
        baseline_revenue=baseline_revenue,
        scenario_revenue=scenario_revenue,
        note=note,
    )


# IMDb rating bands exactly as specified.
RATING_BANDS = {
    "below_5": (1.0, 4.9),
    "5_to_8": (5.0, 8.0),
    "8_to_10": (8.0, 10.0),
}


def rating_band_scenario(
    context: ScenarioContext, movie_name: str, release_date: str, language: str, band: str
) -> dict[str, ScenarioResult]:
    """Reports predicted revenue at the low/mid/high of the requested band rather
    than inventing one fabricated rating value inside it."""
    if band not in RATING_BANDS:
        raise ValueError(f"Unknown rating band '{band}', expected one of {list(RATING_BANDS)}")

    baseline = _load_baseline(movie_name, release_date, language)
    if baseline is None:
        return {"error": _unsupported(f"IMDb rating {band}", "Movie not found in movie_prediction_features.")}

    baseline_revenue = score_row(context, baseline)
    low, high = RATING_BANDS[band]
    mid = (low + high) / 2

    results = {}
    for label, rating in (("low", low), ("mid", mid), ("high", high)):
        scenario_row = dict(baseline)
        scenario_row["rating_10"] = rating
        scenario_revenue = score_row(context, scenario_row)
        results[label] = ScenarioResult(
            description=f"IMDb rating = {rating} ({band} band, {label} point)",
            baseline_revenue=baseline_revenue,
            scenario_revenue=scenario_revenue,
        )
    return results


# Default "how was it received" scenarios: poor / decent / great audience rating.
DEFAULT_SCENARIO_RATINGS = (4.0, 6.0, 8.0)

# rating_range is supplied by hand for an upcoming movie, from how it's being received.
# It's filled for every training movie, so it's never left blank: a blank would read
# to the model as below the lowest bucket. Default is a well-received film's typical
# value -- 10000 for Hindi, 1000 for every other language.
RATING_RANGE_VALUES = (1, 10, 100, 1000, 10000, 100000)
DEFAULT_RATING_RANGE_HINDI = 10000
DEFAULT_RATING_RANGE_OTHER = 1000


def default_rating_range(language: str | None) -> int:
    languages = split_languages(language)
    return DEFAULT_RATING_RANGE_HINDI if languages and languages[0] == "hindi" else DEFAULT_RATING_RANGE_OTHER


@dataclass
class RatingPrediction:
    rating: float
    revenue: float
    low: float
    high: float


def upcoming_movie_row(
    context: ScenarioContext,
    *,
    movie_name: str,
    release_date: str,
    language: str,
    budget: float,
    genre: str | None = None,
    runtime: float | None = None,
    cbfc_rating: str | None = None,
    number_of_screens: int | None = None,
    release_event_type: str | None = None,
    release_event_name: str | None = None,
    is_remake: bool = False,
    rating_range: int | None = None,
    production_company: str | None = None,
    lead_actor: str | None = None,
    supporting_actors: list[str] | None = None,
    director: str | None = None,
    music_director: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Builds a raw feature-source row (same shape as engine.get_movie_row) for a
    movie that isn't in the DB yet, computing cast/crew history against each
    person's real filmography as of the release date. Returns (row, notes); notes
    explain any value that had to be approximated. rating_10 is left unset --
    rating_scenarios() supplies it."""
    notes: list[str] = []
    row: dict[str, Any] = {
        "movie_name": movie_name,
        "release_date": release_date,
        "language": language,
        "budget": budget,
        "genre": genre,
        "runtime": runtime,
        "cbfc_rating": cbfc_rating,
        "number_of_screens": number_of_screens,
        "remake_of": "REMAKE" if is_remake else "NOT_REMAKE",
        "production_companies": production_company,
        "rating_10": None,
    }

    if rating_range is None:
        rating_range = default_rating_range(language)
        notes.append(f"No --rating-range given -- assumed {rating_range}, typical for a well-received {language} film.")
    elif rating_range not in RATING_RANGE_VALUES:
        raise ValueError(f"rating_range must be one of {RATING_RANGE_VALUES}, got {rating_range}")
    row["rating_range"] = rating_range

    if release_event_type is None and release_event_name is None:
        event = context.date_events.get(release_date)
        if event:
            release_event_type, release_event_name = event
            notes.append(f"Release event taken from other movies released {release_date}: {event[0]} / {event[1]}.")
    row["release_event_type"], row["release_event_name"] = release_event_type, release_event_name

    year = parse_release_year(release_date)
    macro = context.year_macro.get(year)
    if macro is None and context.year_macro:
        latest = max(context.year_macro)
        macro = context.year_macro[latest]
        notes.append(f"No GDP/inflation data for {year} yet -- used {latest}'s values.")
    row.update(macro or {"gdp_usd_billions": None, "inflation_rate_pct": None})

    for prefix, slot, name, filmography in (
        ("lead_actor", "lead_actor_1", lead_actor, context.actor_filmography),
        ("director", "director", director, context.director_filmography),
        ("music_director", "music_director", music_director, context.actor_filmography),
    ):
        row.update(_person_history_fields(prefix, name, filmography, release_date))
        row.update(_person_override_fields(context, slot, prefix, name, release_date))
        if name and name not in filmography:
            notes.append(f"'{name}' has no earlier films in the data -- treated as a debut {prefix.replace('_', ' ')}.")

    # Billing slots 2-5. Nearly every training movie has all five filled, so leaving
    # them blank makes a star vehicle look like an obscure film (halved Pushpa 2's score).
    supporting_actors = supporting_actors or []
    if len(supporting_actors) > 4:
        raise ValueError(f"At most 4 supporting actors (billing slots 2-5), got {len(supporting_actors)}")
    for slot_number in range(2, 6):
        name = supporting_actors[slot_number - 2] if slot_number - 2 < len(supporting_actors) else None
        row.update(_person_override_fields(context, f"lead_actor_{slot_number}", "lead_actor", name, release_date))
    if len(supporting_actors) < 4:
        notes.append(
            f"Only {len(supporting_actors)} of 4 supporting actors given -- empty billing slots lower the prediction."
        )
    return row, notes


def rating_scenarios(
    context: ScenarioContext, raw_row: dict[str, Any], ratings: tuple[float, ...] = DEFAULT_SCENARIO_RATINGS
) -> list[RatingPrediction]:
    """Predicted revenue for the same movie under each assumed audience rating.
    The low/high range is the model's typical (out-of-sample, log-space) error
    around each prediction, same as write_predictions.py's confidence band."""
    log_mae = context.feature_layout.get("log_mae", 0.0)
    predictions = []
    for rating in ratings:
        revenue = score_row(context, {**raw_row, "rating_10": rating})
        log_revenue = math.log1p(revenue)
        predictions.append(
            RatingPrediction(
                rating=rating,
                revenue=revenue,
                low=math.expm1(log_revenue - log_mae),
                high=math.expm1(log_revenue + log_mae),
            )
        )
    return predictions


def add_song_by_actor(context: ScenarioContext, movie_name: str, release_date: str, language: str, actor_name: str) -> ScenarioResult:
    return _unsupported(
        f"Add a song featuring {actor_name}",
        "song_views/song_comments are populated for essentially none of the source data "
        "(TMDB doesn't track songs at all; YouTube song-video identification needs manual curation per the project "
        "README) and aren't wired into the trained model as a result. Run the enrichment pipeline and retrain to "
        "unlock this scenario -- fabricating a number without a signal the model actually learned from would be "
        "misleading.",
    )


def actor_promotes_movie(context: ScenarioContext, movie_name: str, release_date: str, language: str, actor_name: str) -> ScenarioResult:
    return _unsupported(
        f"{actor_name} actively promotes the movie",
        "trailer_views/trailer_comments (the closest available promotional-reach signal) are "
        "populated for only a handful of rows today and aren't wired into the trained model. Same fix as "
        "add_song_by_actor -- run the YouTube enrichment pipeline, retrain, then this becomes a real, "
        "mechanically-grounded scenario instead of a guess.",
    )
