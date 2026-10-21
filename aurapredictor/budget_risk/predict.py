"""Scores a movie that hasn't been made yet with Budget_Opportunity_Risk.

For fixed pre-investment parameters and an assumed audience rating, the model
forecasts revenue at every budget on a grid (more budget never forecasts less
revenue -- a monotone constraint at training time). The *max budget* is where that
forecast stops covering HIT_RATIO x the budget: above it the movie is more likely
than not to miss (RISK), below it it's an OPPORTUNITY. The *safe budget* is the same
line drawn at a stricter SAFE_BUDGET_HIT_PROBABILITY.

Hit probabilities come from the training run's forward-in-time residuals (each year
forecast by a model trained only on earlier years -- what forecasting a planned film
really is): if the model forecasts log ratio p, the raw chance the real ratio clears
the bar is the share of those films whose actual log ratio beat their forecast by at
least log(bar) - p. A per-bar Platt correction fitted on the same forward forecasts
then reins in overconfidence, which is worst near break-even (1x). That still
assumes the error spread is the same for every kind of film, the main
simplification here.
"""

from __future__ import annotations

import json
import math
import pickle
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from aurapredictor.budget_risk import ENCODERS_PATH, LAYOUT_PATH, MODEL_PATH
from aurapredictor.budget_risk.dataset import feature_matrix
from aurapredictor.budget_risk.prev_films import PrevFilms, load_prev_films, prev_film_fields
from aurapredictor.dates import release_year as parse_release_year
from aurapredictor.scenarios.engine import ArtifactsNotFoundError, load_date_events, load_people_movie_counts, load_year_macro
from aurapredictor.scenarios.handlers import (
    DEFAULT_SCENARIO_RATINGS,
    RATING_RANGE_VALUES,
    default_rating_range,
    person_movie_count_as_of,
)

# "More likely than not to be a hit" is the RISK line; the safe budget asks for 3-in-4.
MAX_BUDGET_HIT_PROBABILITY = 0.5
SAFE_BUDGET_HIT_PROBABILITY = 0.75

# Allowed per-request hit bars: producers use 1x (gross covers the budget) to 2x, rarely
# up to 2.5x. Beyond that only a handful of breakouts clear the bar in training.
MIN_HIT_RATIO = 1.0
MAX_HIT_RATIO = 2.5

# Log-spaced budgets scanned between the layout's min/max training budget: ~2% apart.
BUDGET_GRID_POINTS = 400

OPPORTUNITY = "OPPORTUNITY"
RISK = "RISK"


@dataclass
class BudgetRiskContext:
    model: CatBoostRegressor
    encoders: dict[str, Any]
    layout: dict[str, Any]
    # Same lookups the revenue model's upcoming-movie path uses (scenarios/engine.py).
    people_movie_counts: dict[str, dict[str, list[tuple[str, int]]]]
    date_events: dict[str, tuple[str | None, str | None]]
    year_macro: dict[int, dict[str, float]]
    prev_films: PrevFilms


@lru_cache(maxsize=1)
def load_context() -> BudgetRiskContext:
    if not all(path.exists() for path in (MODEL_PATH, ENCODERS_PATH, LAYOUT_PATH)):
        raise ArtifactsNotFoundError(
            f"No Budget_Opportunity_Risk model found in {MODEL_PATH.parent}. Run scripts/train_budget_risk.py first."
        )
    model = CatBoostRegressor()
    model.load_model(str(MODEL_PATH))
    with open(ENCODERS_PATH, "rb") as f:
        encoders = pickle.load(f)
    with open(LAYOUT_PATH) as f:
        layout = json.load(f)
    return BudgetRiskContext(
        model=model,
        encoders=encoders,
        layout=layout,
        people_movie_counts=load_people_movie_counts(),
        date_events=load_date_events(),
        year_macro=load_year_macro(),
        prev_films=load_prev_films(),
    )


@dataclass
class BudgetAssessment:
    rating: float
    # None: even the smallest budget the model has seen misses HIT_RATIO at this rating.
    max_budget: float | None
    safe_budget: float | None
    # True: the line is at least the largest training budget -- no data beyond it.
    max_budget_capped: bool
    safe_budget_capped: bool
    planned_budget: float | None = None
    planned_ratio: float | None = None
    planned_hit_probability: float | None = None

    @property
    def verdict(self) -> str | None:
        if self.planned_hit_probability is None:
            return None
        return OPPORTUNITY if self.planned_hit_probability >= MAX_BUDGET_HIT_PROBABILITY else RISK

    @property
    def verdict_confidence(self) -> float | None:
        """Chance the verdict turns out right: the hit probability for an OPPORTUNITY,
        the flop probability for a RISK. On the 2025-26 time-split backtest at 2x,
        90%+ calls were right 92% of the time; near break-even (1-1.25x) they were
        right ~81%, so treat the top end at low bars as somewhat optimistic."""
        if self.planned_hit_probability is None:
            return None
        p = self.planned_hit_probability
        return p if self.verdict == OPPORTUNITY else 1 - p


def movie_row(
    context: BudgetRiskContext,
    *,
    release_date: str,
    language: str,
    genre: str | None = None,
    production_company: str | None = None,
    cbfc_rating: str | None = None,
    release_event_type: str | None = None,
    release_event_name: str | None = None,
    is_remake: bool = False,
    rating_range: int | None = None,
    lead_actor: str | None = None,
    supporting_actors: list[str] | None = None,
    director: str | None = None,
    music_director: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """A MOVIES_TABLE-shaped row for a planned movie, (row, notes). budget and
    rating_10 are left unset -- assess() scans them. Cast/crew previous-film columns
    are rebuilt from each person's films before release_date (budget_risk/prev_films.py)."""
    notes: list[str] = []
    row: dict[str, Any] = {
        "release_date": release_date,
        "language": language,
        "genre": genre,
        "production_companies": production_company,
        "cbfc_rating": cbfc_rating,
        "remake_of": "REMAKE" if is_remake else "NOT_REMAKE",
    }

    if rating_range is None:
        rating_range = default_rating_range(language)
        notes.append(f"No rating range given -- assumed {rating_range}, typical for a {language} film.")
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

    supporting_actors = supporting_actors or []
    if len(supporting_actors) > 4:
        raise ValueError(f"At most 4 supporting actors (billing slots 2-5), got {len(supporting_actors)}")
    people = [
        ("lead_actor_1", "lead_actor", lead_actor),
        *((f"lead_actor_{i + 2}", "lead_actor", name) for i, name in enumerate(supporting_actors)),
        ("director", "director", director),
        ("music_director", "music_director", music_director),
    ]
    for slot, role, name in people:
        if not name:
            continue
        row[slot] = name
        row[f"{slot}_movie_count"] = person_movie_count_as_of(context, role, name, release_date)
        if row[f"{slot}_movie_count"] is None:
            notes.append(f"'{name}' isn't in the data -- the model treats them as an unknown {role.replace('_', ' ')}.")
    row.update(prev_film_fields(row, context.prev_films))
    if len(supporting_actors) < 4:
        notes.append(
            f"Only {len(supporting_actors)} of 4 supporting actors given -- nearly every training movie has all four."
        )
    return row, notes


def budget_grid(min_budget: float, max_budget: float) -> np.ndarray:
    return np.geomspace(min_budget, max_budget, BUDGET_GRID_POINTS)


def log_ratio_curves(model: CatBoostRegressor, X: pd.DataFrame, budgets: np.ndarray) -> np.ndarray:
    """(len(X), len(budgets)) forecast log revenue:budget ratios -- each row of X
    re-scored with its budget set to every grid value."""
    repeated = X.loc[X.index.repeat(len(budgets))].copy()
    repeated["budget"] = np.tile(budgets, len(X))
    log_revenue = model.predict(repeated).reshape(len(X), len(budgets))
    return log_revenue - np.log(budgets)[None, :]


def budget_line(budgets: np.ndarray, passes: np.ndarray) -> tuple[float | None, bool]:
    """Largest budget with every budget up to it passing, and whether that's the top
    of the grid. budgets ascending. Stops at the *first* failure rather than the last
    pass: a revenue step can briefly lift the ratio back over the bar at a bigger
    budget, and "fine again if you spend more" isn't a line anyone should plan on."""
    if not passes[0]:
        return None, False
    if passes.all():
        return float(budgets[-1]), True
    return float(budgets[int(np.argmin(passes)) - 1]), False


def logit(p: float | np.ndarray) -> float | np.ndarray:
    # Clipped so a raw 0% / 100% stays finite and the Platt correction can still move it.
    p = np.clip(p, 1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


def raw_hit_probability(
    log_ratio: float | np.ndarray, layout: dict[str, Any], hit_ratio: float | None = None
) -> float | np.ndarray:
    """Uncalibrated P(actual ratio >= hit_ratio) from the residual percentiles saved
    at training time."""
    quantiles = np.asarray(layout["residual_quantiles"])
    needed_residual = math.log(hit_ratio or layout["hit_ratio"]) - np.asarray(log_ratio)
    share_below = np.interp(needed_residual, quantiles, np.linspace(0, 1, len(quantiles)))
    return 1 - share_below


def planned_hit_probability(budgets: np.ndarray, probabilities: np.ndarray, planned_budget: float, own: float) -> float:
    """The planned budget's hit chance, never above the chance at any smaller grid
    budget. Revenue forecasts rise in steps, so the chance can dip under the bar and
    recover a little further up; budget_line() already stops at the first dip, and a
    budget past that dip must be a RISK too, or the verdict contradicts the line."""
    smaller = probabilities[budgets <= planned_budget]
    return float(min(own, smaller.min())) if len(smaller) else float(own)


def hit_probability(
    log_ratio: float | np.ndarray, layout: dict[str, Any], hit_ratio: float | None = None
) -> float | np.ndarray:
    """P(actual ratio >= hit_ratio) given a forecast log ratio. hit_ratio defaults to
    the training bar (layout["hit_ratio"]); the model forecasts the return itself, not
    hit/flop, so any bar works without retraining. With layout["calibration"], the
    raw probability goes through that bar's Platt correction, interpolated between
    the calibrated bars. Slopes are positive, so more budget still never means a
    higher hit chance, and budget lines stay well defined."""
    hit_ratio = hit_ratio or layout["hit_ratio"]
    raw = raw_hit_probability(log_ratio, layout, hit_ratio)
    calibration = layout.get("calibration")
    if not calibration:
        return raw
    intercept = np.interp(hit_ratio, calibration["hit_ratios"], calibration["intercepts"])
    slope = np.interp(hit_ratio, calibration["hit_ratios"], calibration["slopes"])
    return 1 / (1 + np.exp(-(intercept + slope * logit(raw))))


def _feature_rows(context: BudgetRiskContext, row: dict[str, Any], rating: float) -> pd.DataFrame:
    X = feature_matrix([{**row, "rating_10": rating}], **context.encoders)
    X = X.reindex(columns=context.layout["columns"], fill_value=0)
    for col in context.layout["categorical_features"]:
        X[col] = X[col].fillna("Unknown").astype(str)
    return X


def assess(
    context: BudgetRiskContext,
    row: dict[str, Any],
    ratings: tuple[float, ...] = DEFAULT_SCENARIO_RATINGS,
    planned_budget: float | None = None,
    hit_ratio: float | None = None,
) -> list[BudgetAssessment]:
    """hit_ratio: the revenue:budget multiple that counts as a hit; defaults to the
    training bar (HIT_RATIO, 2x)."""
    if hit_ratio is not None and not MIN_HIT_RATIO <= hit_ratio <= MAX_HIT_RATIO:
        raise ValueError(f"hit_ratio must be between {MIN_HIT_RATIO:g} and {MAX_HIT_RATIO:g}, got {hit_ratio}")
    layout = context.layout
    budgets = budget_grid(layout["min_budget"], layout["max_budget"])
    assessments = []
    for rating in ratings:
        X = _feature_rows(context, row, rating)
        probabilities = hit_probability(log_ratio_curves(context.model, X, budgets)[0], layout, hit_ratio)
        max_budget, max_capped = budget_line(budgets, probabilities >= MAX_BUDGET_HIT_PROBABILITY)
        safe_budget, safe_capped = budget_line(budgets, probabilities >= SAFE_BUDGET_HIT_PROBABILITY)
        assessment = BudgetAssessment(
            rating=rating,
            max_budget=max_budget,
            safe_budget=safe_budget,
            max_budget_capped=max_capped,
            safe_budget_capped=safe_capped,
        )
        if planned_budget is not None:
            planned_log_ratio = float(log_ratio_curves(context.model, X, np.array([planned_budget]))[0, 0])
            assessment.planned_budget = planned_budget
            assessment.planned_ratio = math.exp(planned_log_ratio)
            assessment.planned_hit_probability = planned_hit_probability(
                budgets, probabilities, planned_budget, float(hit_probability(planned_log_ratio, layout, hit_ratio))
            )
        assessments.append(assessment)
    return assessments
