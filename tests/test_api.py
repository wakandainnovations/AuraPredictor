"""API tests never touch a live DB or a trained model: the context dependency is
overridden and the two handlers are monkeypatched, so these only verify request
mapping, validation and the camelCase response contract AuraService relies on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aurapredictor.api import app as api
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
