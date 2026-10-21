"""HTTP API over the upcoming-movie scorer, so AuraService (Java) can predict revenue
for a movie that isn't in the DB yet without re-implementing feature building.

    python scripts/serve_api.py            # http://localhost:8082, docs at /docs

POST /v1/predict/upcoming takes the same inputs as the CLI's --upcoming mode and
returns one prediction per assumed audience rating (rating_10 is a post-release
signal, so there is no single "the" revenue -- see handlers.rating_scenarios).

load_context() reads every Indian movie/actor row from the DB, so it runs once at
startup, not per request. A retrain only takes effect after a restart.

POST /v1/budget-risk/upcoming serves Budget_Opportunity_Risk (aurapredictor/budget_risk/)
the same way: for each assumed rating, the max and safe budget for these
pre-investment parameters, plus an OPPORTUNITY/RISK call on `budget` if one is given,
and a `breakout` set at rating range 100000 when the lead's recent films earn one.
It has its own model and context, so either model can be missing without taking the
other endpoint down.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from aurapredictor.budget_risk import predict as budget_risk
from aurapredictor.scenarios import handlers
from aurapredictor.scenarios.engine import ArtifactsNotFoundError, ScenarioContext, load_context


class _CamelModel(BaseModel):
    # AuraService sends/reads camelCase JSON; accept snake_case too for curl users.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UpcomingMovieRequest(_CamelModel):
    movie_name: str = Field(min_length=1)
    release_date: str = Field(min_length=1, description="e.g. 2026-12-25")
    language: str = Field(min_length=1, description='e.g. "telugu" or "hindi, tamil"')
    budget: float = Field(gt=0, description="Same currency as the training revenue column")
    # Required: runtime is filled for every training movie, so a blank would read as
    # "shorter than any real film" and drag the prediction down ~2.5x.
    runtime: float = Field(gt=0, description="Minutes")
    genre: str | None = Field(default=None, description='e.g. "Action, Drama"')
    cbfc_rating: str | None = Field(default=None, description="U, UA or A")
    number_of_screens: int | None = Field(default=None, gt=0)
    release_event_type: str | None = None
    release_event_name: str | None = None
    is_remake: bool = False
    rating_range: int | None = Field(default=None, description=f"One of {handlers.RATING_RANGE_VALUES}")
    production_company: str | None = Field(default=None, description="One or more, comma-separated")
    lead_actor: str | None = None
    supporting_actors: list[str] = Field(default_factory=list, max_length=4, description="Billing order, slots 2-5")
    director: str | None = None
    music_director: str | None = None
    ratings: list[float] = Field(
        default_factory=lambda: list(handlers.DEFAULT_SCENARIO_RATINGS), min_length=1, max_length=10
    )


class RatingPredictionOut(_CamelModel):
    rating: float
    revenue: float
    low: float
    high: float


class UpcomingMovieResponse(_CamelModel):
    movie_name: str
    release_date: str
    language: str
    model_version: str
    predictions: list[RatingPredictionOut]
    notes: list[str]


class BudgetRiskRequest(_CamelModel):
    movie_name: str = Field(min_length=1)
    release_date: str = Field(min_length=1, description="Planned release date, e.g. 2026-12-25")
    language: str = Field(min_length=1, description='Original language first: "telugu" or "telugu, hindi"')
    # Optional: without it the response has budget lines only, no verdict.
    budget: float | None = Field(default=None, gt=0, description="Planned budget to classify")
    # Applied after the forecast, so changing it needs no retrain.
    hit_ratio: float | None = Field(
        default=None,
        ge=budget_risk.MIN_HIT_RATIO,
        le=budget_risk.MAX_HIT_RATIO,
        description="Revenue:budget multiple that counts as a hit; default 2",
    )
    genre: str | None = Field(default=None, description='e.g. "Action, Drama"')
    cbfc_rating: str | None = Field(default=None, description="Target certificate: U, UA or A")
    release_event_type: str | None = Field(default=None, description="Default: looked up from the date")
    release_event_name: str | None = None
    is_remake: bool = False
    rating_range: int | None = Field(
        default=None,
        description=f"One of {handlers.RATING_RANGE_VALUES}; default from the cast's recent films (lead weighted 4x), "
        "else 10000 for Hindi, 1000 otherwise",
    )
    production_company: str | None = Field(default=None, description="One or more, comma-separated")
    lead_actor: str | None = None
    supporting_actors: list[str] = Field(default_factory=list, max_length=4, description="Billing order, slots 2-5")
    director: str | None = None
    music_director: str | None = None
    ratings: list[float] = Field(
        default_factory=lambda: list(handlers.DEFAULT_SCENARIO_RATINGS), min_length=1, max_length=10
    )


class BudgetAssessmentOut(_CamelModel):
    rating: float
    # null: risky at any budget the model has seen.
    max_budget: float | None
    # true: the line is at the largest training budget -- the real line may be higher.
    max_budget_capped: bool
    safe_budget: float | None
    safe_budget_capped: bool
    # The rest are null unless the request had a budget.
    planned_budget: float | None = None
    expected_ratio: float | None = None
    hit_probability: float | None = None
    verdict: str | None = Field(default=None, description="OPPORTUNITY or RISK")
    verdict_confidence: float | None = Field(default=None, description="Chance the verdict is right")


class BreakoutScenarioOut(_CamelModel):
    rating_range: int
    note: str
    assessments: list[BudgetAssessmentOut]


class BudgetRiskResponse(_CamelModel):
    movie_name: str
    release_date: str
    language: str
    model_version: str
    # The one used for `assessments`: as sent, or the cast-based default.
    rating_range: int
    hit_ratio: float
    max_budget_hit_probability: float
    safe_budget_hit_probability: float
    assessments: list[BudgetAssessmentOut]
    # Only when ratingRange was left out and the lead had a 100000 film among their
    # last 3: the same movie scored at 100000, the upside case.
    breakout: BreakoutScenarioOut | None = None
    notes: list[str]


def _assessment_out(a: budget_risk.BudgetAssessment) -> BudgetAssessmentOut:
    return BudgetAssessmentOut(
        rating=a.rating,
        max_budget=a.max_budget,
        max_budget_capped=a.max_budget_capped,
        safe_budget=a.safe_budget,
        safe_budget_capped=a.safe_budget_capped,
        planned_budget=a.planned_budget,
        expected_ratio=a.planned_ratio,
        hit_probability=a.planned_hit_probability,
        verdict=a.verdict,
        verdict_confidence=a.verdict_confidence,
    )


def _check_ratings(ratings: list[float]) -> None:
    for rating in ratings:
        if not 0 <= rating <= 10:
            raise HTTPException(status_code=422, detail=f"ratings must be between 0 and 10, got {rating}")


def get_context() -> ScenarioContext:
    try:
        return load_context()
    except ArtifactsNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def get_budget_risk_context() -> budget_risk.BudgetRiskContext:
    try:
        return budget_risk.load_context()
    except ArtifactsNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Warm the cache so the first real request doesn't pay the DB load. A missing model
    # shouldn't stop the server from booting -- requests get a 503 until it's trained.
    for load in (load_context, budget_risk.load_context):
        try:
            load()
        except ArtifactsNotFoundError:
            pass
    yield


app = FastAPI(title="AuraPredictor", lifespan=_lifespan)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "modelLoaded": load_context.cache_info().currsize > 0,
        "budgetRiskModelLoaded": budget_risk.load_context.cache_info().currsize > 0,
    }


@app.post("/v1/predict/upcoming", response_model=UpcomingMovieResponse, response_model_by_alias=True)
def predict_upcoming(
    request: UpcomingMovieRequest, context: ScenarioContext = Depends(get_context)
) -> UpcomingMovieResponse:
    _check_ratings(request.ratings)
    try:
        row, notes = handlers.upcoming_movie_row(
            context,
            movie_name=request.movie_name,
            release_date=request.release_date,
            language=request.language,
            budget=request.budget,
            genre=request.genre,
            runtime=request.runtime,
            cbfc_rating=request.cbfc_rating,
            number_of_screens=request.number_of_screens,
            release_event_type=request.release_event_type,
            release_event_name=request.release_event_name,
            is_remake=request.is_remake,
            rating_range=request.rating_range,
            production_company=request.production_company,
            lead_actor=request.lead_actor,
            supporting_actors=[a.strip() for a in request.supporting_actors if a.strip()],
            director=request.director,
            music_director=request.music_director,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    predictions = handlers.rating_scenarios(context, row, tuple(request.ratings))
    return UpcomingMovieResponse(
        movie_name=request.movie_name,
        release_date=request.release_date,
        language=request.language,
        model_version=context.feature_layout.get("model_version", "unknown"),
        predictions=[RatingPredictionOut(rating=p.rating, revenue=p.revenue, low=p.low, high=p.high) for p in predictions],
        notes=notes,
    )


@app.post("/v1/budget-risk/upcoming", response_model=BudgetRiskResponse, response_model_by_alias=True)
def predict_budget_risk(
    request: BudgetRiskRequest, context: budget_risk.BudgetRiskContext = Depends(get_budget_risk_context)
) -> BudgetRiskResponse:
    _check_ratings(request.ratings)
    try:
        row, notes = budget_risk.movie_row(
            context,
            release_date=request.release_date,
            language=request.language,
            genre=request.genre,
            production_company=request.production_company,
            cbfc_rating=request.cbfc_rating,
            release_event_type=request.release_event_type,
            release_event_name=request.release_event_name,
            is_remake=request.is_remake,
            rating_range=request.rating_range,
            lead_actor=request.lead_actor,
            supporting_actors=[a.strip() for a in request.supporting_actors if a.strip()],
            director=request.director,
            music_director=request.music_director,
        )
        assessments = budget_risk.assess(context, row, tuple(request.ratings), request.budget, request.hit_ratio)
        breakout = None
        if request.rating_range is None and (found := budget_risk.breakout_row(context, row)):
            breakout_row, breakout_note = found
            breakout = BreakoutScenarioOut(
                rating_range=breakout_row["rating_range"],
                note=breakout_note,
                assessments=[
                    _assessment_out(a)
                    for a in budget_risk.assess(
                        context, breakout_row, tuple(request.ratings), request.budget, request.hit_ratio
                    )
                ],
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return BudgetRiskResponse(
        movie_name=request.movie_name,
        release_date=request.release_date,
        language=request.language,
        model_version=context.layout.get("model_version", "unknown"),
        hit_ratio=request.hit_ratio or context.layout["hit_ratio"],
        max_budget_hit_probability=budget_risk.MAX_BUDGET_HIT_PROBABILITY,
        safe_budget_hit_probability=budget_risk.SAFE_BUDGET_HIT_PROBABILITY,
        rating_range=row["rating_range"],
        assessments=[_assessment_out(a) for a in assessments],
        breakout=breakout,
        notes=notes,
    )
