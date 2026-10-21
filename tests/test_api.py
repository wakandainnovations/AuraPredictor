"""API tests never touch a live DB or a trained model: the context dependency is
overridden and the handlers are monkeypatched, so these only verify request
mapping, validation and the camelCase response contract AuraService relies on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aurapredictor.api import app as api
from aurapredictor.budget_risk import predict as budget_risk
from aurapredictor.scenarios import handlers
from aurapredictor.scenarios.engine import ArtifactsNotFoundError, ScenarioContext

CONTEXT = ScenarioContext(
    model=None,
    binarizer=None,
    feature_layout={"model_version": "2026-10-20", "log_mae": 0.5},
    keep_companies=set(),
    actor_index={},
    actor_filmography={},
    director_filmography={},
)

REQUEST = {
    "movieName": "New Film",
    "releaseDate": "2026-12-25",
    "language": "telugu",
    "budget": 40_000_000,
    "runtime": 150,
    "leadActor": "Some Actor",
    "supportingActors": ["A", " ", "B"],
}


@pytest.fixture
def client(monkeypatch):
    calls = {}

    def fake_upcoming_movie_row(context, **kwargs):
        calls["row_kwargs"] = kwargs
        return {"budget": kwargs["budget"]}, ["a note"]

    def fake_rating_scenarios(context, row, ratings):
        calls["ratings"] = ratings
        return [handlers.RatingPrediction(rating=r, revenue=r * 100, low=r * 50, high=r * 200) for r in ratings]

    monkeypatch.setattr(handlers, "upcoming_movie_row", fake_upcoming_movie_row)
    monkeypatch.setattr(handlers, "rating_scenarios", fake_rating_scenarios)
    api.app.dependency_overrides[api.get_context] = lambda: CONTEXT
    # No `with`: skips the lifespan hook, which would try to load the real context.
    yield TestClient(api.app), calls
    api.app.dependency_overrides.clear()


def test_predict_upcoming_maps_request_and_returns_camel_case(client):
    test_client, calls = client
    response = test_client.post("/v1/predict/upcoming", json=REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["movieName"] == "New Film"
    assert body["modelVersion"] == "2026-10-20"
    assert body["notes"] == ["a note"]
    assert [p["rating"] for p in body["predictions"]] == [4.0, 6.0, 8.0]
    assert body["predictions"][0] == {"rating": 4.0, "revenue": 400.0, "low": 200.0, "high": 800.0}
    assert calls["ratings"] == (4.0, 6.0, 8.0)
    assert calls["row_kwargs"]["supporting_actors"] == ["A", "B"]
    assert calls["row_kwargs"]["lead_actor"] == "Some Actor"
    assert calls["row_kwargs"]["is_remake"] is False


def test_custom_ratings_are_passed_through(client):
    test_client, calls = client
    response = test_client.post("/v1/predict/upcoming", json={**REQUEST, "ratings": [7.5]})

    assert response.status_code == 200
    assert calls["ratings"] == (7.5,)


@pytest.mark.parametrize(
    "override",
    [
        {"runtime": None},
        {"budget": 0},
        {"ratings": [11]},
        {"ratings": []},
        {"supportingActors": ["a", "b", "c", "d", "e"]},
    ],
)
def test_invalid_requests_return_422(client, override):
    test_client, _ = client
    response = test_client.post("/v1/predict/upcoming", json={**REQUEST, **override})
    assert response.status_code == 422


def test_handler_value_error_returns_422(client, monkeypatch):
    test_client, _ = client

    def raise_value_error(context, **kwargs):
        raise ValueError("rating_range must be one of ...")

    monkeypatch.setattr(handlers, "upcoming_movie_row", raise_value_error)
    response = test_client.post("/v1/predict/upcoming", json=REQUEST)
    assert response.status_code == 422
    assert "rating_range" in response.json()["detail"]


def test_missing_model_returns_503(monkeypatch):
    def no_artifacts():
        raise ArtifactsNotFoundError("No trained model found")

    monkeypatch.setattr(api, "load_context", no_artifacts)
    response = TestClient(api.app).post("/v1/predict/upcoming", json=REQUEST)
    assert response.status_code == 503


BUDGET_RISK_CONTEXT = budget_risk.BudgetRiskContext(
    model=None,
    encoders={},
    layout={"model_version": "2026-10-21", "hit_ratio": 2.0},
    people_movie_counts={},
    date_events={},
    year_macro={},
    prev_films={},
)

BUDGET_RISK_REQUEST = {
    "movieName": "New Film",
    "releaseDate": "2026-12-25",
    "language": "telugu",
    "leadActor": "Some Actor",
    "supportingActors": ["A", " ", "B"],
    "ratingRange": 1000,
}


@pytest.fixture
def budget_client(monkeypatch):
    calls = {}

    def fake_movie_row(context, **kwargs):
        calls["row_kwargs"] = kwargs
        return {}, ["a note"]

    def fake_assess(context, row, ratings, planned_budget):
        calls["ratings"], calls["planned_budget"] = ratings, planned_budget
        return [
            budget_risk.BudgetAssessment(
                rating=r,
                max_budget=r * 1e6,
                safe_budget=None,
                max_budget_capped=r == 8,
                safe_budget_capped=False,
                **(
                    {"planned_budget": planned_budget, "planned_ratio": 1.5, "planned_hit_probability": 0.3}
                    if planned_budget
                    else {}
                ),
            )
            for r in ratings
        ]

    monkeypatch.setattr(budget_risk, "movie_row", fake_movie_row)
    monkeypatch.setattr(budget_risk, "assess", fake_assess)
    api.app.dependency_overrides[api.get_budget_risk_context] = lambda: BUDGET_RISK_CONTEXT
    yield TestClient(api.app), calls
    api.app.dependency_overrides.clear()


def test_budget_risk_without_budget_returns_lines_only(budget_client):
    test_client, calls = budget_client
    response = test_client.post("/v1/budget-risk/upcoming", json=BUDGET_RISK_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["modelVersion"] == "2026-10-21"
    assert (body["hitRatio"], body["maxBudgetHitProbability"], body["safeBudgetHitProbability"]) == (2.0, 0.5, 0.75)
    assert body["notes"] == ["a note"]
    assert body["assessments"][0] == {
        "rating": 4.0, "maxBudget": 4e6, "maxBudgetCapped": False, "safeBudget": None, "safeBudgetCapped": False,
        "plannedBudget": None, "expectedRatio": None, "hitProbability": None, "verdict": None,
        "verdictConfidence": None,
    }
    assert body["assessments"][2]["maxBudgetCapped"] is True
    assert calls["planned_budget"] is None
    assert calls["row_kwargs"]["supporting_actors"] == ["A", "B"]
    assert calls["row_kwargs"]["rating_range"] == 1000
    assert "movie_name" not in calls["row_kwargs"]


def test_budget_risk_with_budget_returns_verdict_and_confidence(budget_client):
    test_client, calls = budget_client
    response = test_client.post("/v1/budget-risk/upcoming", json={**BUDGET_RISK_REQUEST, "budget": 3e7, "ratings": [6]})

    assert response.status_code == 200
    (assessment,) = response.json()["assessments"]
    assert assessment["plannedBudget"] == 3e7
    assert assessment["expectedRatio"] == 1.5
    assert assessment["verdict"] == "RISK"
    assert assessment["verdictConfidence"] == pytest.approx(0.7)
    assert calls["ratings"] == (6.0,)


@pytest.mark.parametrize(
    "override",
    [{"budget": 0}, {"ratings": [11]}, {"ratings": []}, {"supportingActors": ["a", "b", "c", "d", "e"]}, {"language": ""}],
)
def test_invalid_budget_risk_requests_return_422(budget_client, override):
    test_client, _ = budget_client
    response = test_client.post("/v1/budget-risk/upcoming", json={**BUDGET_RISK_REQUEST, **override})
    assert response.status_code == 422


def test_budget_risk_value_error_returns_422(budget_client, monkeypatch):
    test_client, _ = budget_client

    def raise_value_error(context, **kwargs):
        raise ValueError("rating_range must be one of ...")

    monkeypatch.setattr(budget_risk, "movie_row", raise_value_error)
    response = test_client.post("/v1/budget-risk/upcoming", json=BUDGET_RISK_REQUEST)
    assert response.status_code == 422
    assert "rating_range" in response.json()["detail"]


def test_missing_budget_risk_model_returns_503(monkeypatch):
    def no_artifacts():
        raise ArtifactsNotFoundError("No Budget_Opportunity_Risk model found")

    monkeypatch.setattr(budget_risk, "load_context", no_artifacts)
    response = TestClient(api.app).post("/v1/budget-risk/upcoming", json=BUDGET_RISK_REQUEST)
    assert response.status_code == 503
