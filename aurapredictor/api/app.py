"""HTTP API over the upcoming-movie scorer, so AuraService (Java) can predict revenue
for a movie that isn't in the DB yet without re-implementing feature building.

    python scripts/serve_api.py            # http://localhost:8082, docs at /docs

POST /v1/predict/upcoming takes the same inputs as the CLI's --upcoming mode and
returns one prediction per assumed audience rating (rating_10 is a post-release
signal, so there is no single "the" revenue -- see handlers.rating_scenarios).

load_context() reads every Indian movie/actor row from the DB, so it runs once at
startup, not per request. A retrain only takes effect after a restart.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

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


def get_context() -> ScenarioContext:
    try:
        return load_context()
    except ArtifactsNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Warm the cache so the first real request doesn't pay the DB load. A missing model
    # shouldn't stop the server from booting -- requests get a 503 until it's trained.
    try:
        load_context()
    except ArtifactsNotFoundError:
        pass
    yield


app = FastAPI(title="AuraPredictor", lifespan=_lifespan)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "modelLoaded": load_context.cache_info().currsize > 0}


@app.post("/v1/predict/upcoming", response_model=UpcomingMovieResponse, response_model_by_alias=True)
def predict_upcoming(
    request: UpcomingMovieRequest, context: ScenarioContext = Depends(get_context)
) -> UpcomingMovieResponse:
    for rating in request.ratings:
        if not 0 <= rating <= 10:
            raise HTTPException(status_code=422, detail=f"ratings must be between 0 and 10, got {rating}")
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
