"""Handler tests never touch a live DB or a trained model: get_movie_row/score_row
are monkeypatched so we can verify each handler builds the *correct overridden
feature row* -- the actual mechanism every what-if question relies on -- in
isolation."""

from __future__ import annotations

import pytest

from aurapredictor.scenarios import handlers
from aurapredictor.scenarios.engine import ScenarioContext

BASELINE_ROW = {
    "movie_name": "Test Movie",
    "release_date": "2022-06-01",
    "language": "telugu",
    "revenue": 500,
    "budget": 100,
    "genre": "Action, Drama",
    "production_companies": "Some Studio",
    "rating_10": 7,
    "runtime": 140,
    "cbfc_rating": "UA",
    "gdp_usd_billions": 3000,
    "inflation_rate_pct": 5,
    "lead_actor_prior_films_count": 5,
    "lead_actor_hit_flop_ratio_last3": 1.5,
    "director_prior_films_count": 3,
    "director_hit_flop_ratio_last3": 2.0,
    "music_director_prior_films_count": None,
    "music_director_hit_flop_ratio_last3": None,
}


def fake_score_row(context, row):
    """A stand-in "model": deterministic function of a couple of fields, so tests
    can assert on exact numbers without loading CatBoost."""
    base = 1000.0
    base += (row.get("lead_actor_prior_films_count") or 0) * 10
    base += (row.get("director_prior_films_count") or 0) * 5
    base += (row.get("music_director_prior_films_count") or 0) * 2
    base += (row.get("rating_10") or 0) * 100
    return base


@pytest.fixture(autouse=True)
def patch_engine(monkeypatch):
    monkeypatch.setattr(handlers, "get_movie_row", lambda name, date, lang: dict(BASELINE_ROW))
    monkeypatch.setattr(handlers, "score_row", fake_score_row)


def make_context(**overrides) -> ScenarioContext:
    defaults = dict(
        model=None,
        binarizer=None,
        feature_layout={},
        keep_companies=set(),
        actor_index={},
        actor_filmography={},
        director_filmography={},
        year_macro={},
    )
    defaults.update(overrides)
    return ScenarioContext(**defaults)


def test_replace_actor_uses_new_actors_real_history():
    context = make_context(
        actor_filmography={
            "New Actor": [
                {"release_date": "2020-01-01", "revenue": 300, "budget": 100},  # blockbuster
                {"release_date": "2021-01-01", "revenue": 300, "budget": 100},  # blockbuster
            ]
        }
    )
    result = handlers.replace_actor(context, "Test Movie", "2022-06-01", "telugu", "New Actor")

    assert result.supported
    assert result.baseline_revenue == fake_score_row(context, BASELINE_ROW)
    # New Actor has 2 prior films by 2022-06-01, both hits -> +20 vs baseline's +50 (5 prior films)
    assert result.scenario_revenue == fake_score_row(
        context, {**BASELINE_ROW, "lead_actor_prior_films_count": 2, "lead_actor_hit_flop_ratio_last3": 2.0}
    )
    assert result.delta == result.scenario_revenue - result.baseline_revenue


def test_replace_actor_unknown_person_gets_zero_history():
    context = make_context()
    result = handlers.replace_actor(context, "Test Movie", "2022-06-01", "telugu", "Nobody Heard Of")
    assert result.scenario_revenue == fake_score_row(
        context, {**BASELINE_ROW, "lead_actor_prior_films_count": 0, "lead_actor_hit_flop_ratio_last3": None}
    )


def test_new_music_director_sets_debutant_profile():
    context = make_context()
    result = handlers.new_music_director(context, "Test Movie", "2022-06-01", "telugu")
    assert result.supported
    assert result.note is not None  # low-confidence caveat always attached
    assert result.scenario_revenue == fake_score_row(
        context, {**BASELINE_ROW, "music_director_prior_films_count": 0, "music_director_hit_flop_ratio_last3": None}
    )


def test_replace_director_uses_director_filmography_not_actor_filmography():
    context = make_context(
        director_filmography={"New Director": [{"release_date": "2021-01-01", "revenue": 100, "budget": 100}]},
        actor_filmography={"New Director": [{"release_date": "2021-01-01", "revenue": 999, "budget": 100}] * 5},
    )
    result = handlers.replace_director(context, "Test Movie", "2022-06-01", "telugu", "New Director")
    assert result.scenario_revenue == fake_score_row(
        context, {**BASELINE_ROW, "director_prior_films_count": 1, "director_hit_flop_ratio_last3": 0.0}
    )


def test_set_release_date_updates_macro_when_year_known():
    context = make_context(
        year_macro={2025: {"gdp_usd_billions": 4000, "inflation_rate_pct": 3}},
        date_events={"2025-01-15": ("Festival", "Sankranti")},
    )
    result = handlers.set_release_date(context, "Test Movie", "2022-06-01", "telugu", "2025-01-15")
    assert result.note is None
    assert result.scenario_revenue == fake_score_row(
        context,
        {**BASELINE_ROW, "release_date": "2025-01-15", "gdp_usd_billions": 4000, "inflation_rate_pct": 3},
    )


def test_set_release_date_moves_release_event(monkeypatch):
    scored = []
    monkeypatch.setattr(handlers, "score_row", lambda context, row: scored.append(row) or 1.0)
    context = make_context(date_events={"2025-01-15": ("Festival", "Sankranti")})
    handlers.set_release_date(context, "Test Movie", "2022-06-01", "telugu", "2025-01-15")
    assert (scored[-1]["release_event_type"], scored[-1]["release_event_name"]) == ("Festival", "Sankranti")

    result = handlers.set_release_date(context, "Test Movie", "2022-06-01", "telugu", "2025-03-03")
    assert (scored[-1]["release_event_type"], scored[-1]["release_event_name"]) == (None, None)
    assert "event is unknown" in result.note


def test_set_release_date_notes_missing_macro_year():
    context = make_context(year_macro={})
    result = handlers.set_release_date(context, "Test Movie", "2022-06-01", "telugu", "2099-01-15")
    assert result.note is not None and "2099" in result.note
    # Falls back to keeping the baseline's own gdp/inflation values.
    assert result.scenario_revenue == fake_score_row(context, {**BASELINE_ROW, "release_date": "2099-01-15"})


def test_rating_band_scenario_scores_low_mid_high():
    context = make_context()
    results = handlers.rating_band_scenario(context, "Test Movie", "2022-06-01", "telugu", "8_to_10")
    assert set(results) == {"low", "mid", "high"}
    assert results["low"].scenario_revenue == fake_score_row(context, {**BASELINE_ROW, "rating_10": 8.0})
    assert results["mid"].scenario_revenue == fake_score_row(context, {**BASELINE_ROW, "rating_10": 9.0})
    assert results["high"].scenario_revenue == fake_score_row(context, {**BASELINE_ROW, "rating_10": 10.0})


def test_rating_band_scenario_rejects_unknown_band():
    with pytest.raises(ValueError):
        handlers.rating_band_scenario(make_context(), "Test Movie", "2022-06-01", "telugu", "not_a_band")


def test_add_song_by_actor_is_not_supported_yet():
    result = handlers.add_song_by_actor(make_context(), "Test Movie", "2022-06-01", "telugu", "Some Actor")
    assert result.supported is False
    assert result.baseline_revenue is None
    assert "enrichment" in result.note


def test_actor_promotes_movie_is_not_supported_yet():
    result = handlers.actor_promotes_movie(make_context(), "Test Movie", "2022-06-01", "telugu", "Some Actor")
    assert result.supported is False
    assert "enrichment" in result.note


def test_movie_not_found_returns_unsupported_result(monkeypatch):
    monkeypatch.setattr(handlers, "get_movie_row", lambda name, date, lang: None)
    result = handlers.replace_actor(make_context(), "Nope", "2022-06-01", "telugu", "X")
    assert result.supported is False


def test_rating_scenarios_scores_each_rating_with_error_band():
    context = make_context(feature_layout={"log_mae": 0.5})
    predictions = handlers.rating_scenarios(context, dict(BASELINE_ROW))

    assert [p.rating for p in predictions] == [4.0, 6.0, 8.0]
    assert [p.revenue for p in predictions] == [
        fake_score_row(context, {**BASELINE_ROW, "rating_10": r}) for r in (4.0, 6.0, 8.0)
    ]
    for p in predictions:
        assert p.low < p.revenue < p.high


def test_upcoming_movie_row_computes_history_and_flags_gaps():
    context = make_context(
        director_filmography={"Known Director": [{"release_date": "2020-01-01", "revenue": 300, "budget": 100}]},
        year_macro={2025: {"gdp_usd_billions": 4000, "inflation_rate_pct": 4}},
    )
    row, notes = handlers.upcoming_movie_row(
        context,
        movie_name="New Film",
        release_date="2026-12-25",
        language="telugu",
        budget=100,
        lead_actor="Unknown Newcomer",
        director="Known Director",
    )

    assert row["director_prior_films_count"] == 1
    assert row["lead_actor_prior_films_count"] == 0
    assert row["gdp_usd_billions"] == 4000  # fell back to the latest known year
    assert row["rating_10"] is None
    assert any("2025" in n for n in notes)
    assert any("Unknown Newcomer" in n for n in notes)
    assert row["rating_range"] == 1000  # non-Hindi default, flagged in notes
    assert any("rating-range" in n for n in notes)


def test_upcoming_movie_row_fills_supporting_actor_slots():
    context = make_context(people_movie_counts={"lead_actor": {"Second Star": [("2020-01-01", 7)]}})
    row, notes = handlers.upcoming_movie_row(
        context, movie_name="F", release_date="2026-01-01", language="telugu", budget=1, supporting_actors=["Second Star"]
    )
    assert row["lead_actor_2"] == "Second Star"
    assert row["lead_actor_2_movie_count"] == 8  # 7 films before the 2020 one, plus that one
    assert row["lead_actor_3"] is None and row["lead_actor_5_movie_count"] is None
    assert any("1 of 4 supporting actors" in n for n in notes)
    with pytest.raises(ValueError):
        handlers.upcoming_movie_row(
            context, movie_name="F", release_date="2026-01-01", language="telugu", budget=1, supporting_actors=["a"] * 5
        )


def test_upcoming_movie_row_rating_range_default_and_override():
    context = make_context()
    hindi, _ = handlers.upcoming_movie_row(context, movie_name="F", release_date="2026-01-01", language="hindi", budget=1)
    assert hindi["rating_range"] == 10000
    given, notes = handlers.upcoming_movie_row(
        context, movie_name="F", release_date="2026-01-01", language="hindi", budget=1, rating_range=100
    )
    assert given["rating_range"] == 100
    assert not any("rating-range" in n for n in notes)
    with pytest.raises(ValueError):
        handlers.upcoming_movie_row(context, movie_name="F", release_date="2026-01-01", language="hindi", budget=1, rating_range=500)


def _capture_scored_rows(monkeypatch):
    scored = []

    def capture(context, row):
        scored.append(dict(row))
        return fake_score_row(context, row)

    monkeypatch.setattr(handlers, "score_row", capture)
    return scored


def test_replace_actor_swaps_name_and_career_count_into_lead_slot(monkeypatch):
    scored = _capture_scored_rows(monkeypatch)
    context = make_context(
        people_movie_counts={"lead_actor": {"New Actor": [("2019-01-01", 40), ("2021-01-01", 42), ("2024-01-01", 45)]}}
    )
    handlers.replace_actor(context, "Test Movie", "2022-06-01", "telugu", "New Actor")
    scenario_row = scored[-1]
    assert scenario_row["lead_actor_1"] == "New Actor"
    # The 2021 film had 42 films before it, so 43 came before this one.
    assert scenario_row["lead_actor_1_movie_count"] == 43


def test_person_movie_count_as_of_edge_cases():
    context = make_context(people_movie_counts={"director": {"Late Starter": [("2025-01-01", 0)]}})
    # Only films after the scenario date, the first a debut: no films before this one.
    assert handlers.person_movie_count_as_of(context, "director", "Late Starter", "2022-06-01") == 0
    context = make_context(people_movie_counts={"director": {"Gap Year": [("2025-01-01", 7), ("2026-01-01", 8)]}})
    # 7 films before the earliest later film, so at most 7 before this one.
    assert handlers.person_movie_count_as_of(context, "director", "Gap Year", "2022-06-01") == 7
    # Not in the data at all: unknown, not assumed to be a debut.
    assert handlers.person_movie_count_as_of(context, "director", "Nobody", "2022-06-01") is None


def test_replace_music_director_and_director_swap_names(monkeypatch):
    scored = _capture_scored_rows(monkeypatch)
    context = make_context(people_movie_counts={"music_director": {"A.R. Rahman": [("2020-01-01", 140)]}})
    handlers.replace_music_director(context, "Test Movie", "2022-06-01", "telugu", "A.R. Rahman")
    assert (scored[-1]["music_director"], scored[-1]["music_director_movie_count"]) == ("A.R. Rahman", 141)
    handlers.replace_director(context, "Test Movie", "2022-06-01", "telugu", "Someone New")
    assert (scored[-1]["director"], scored[-1]["director_movie_count"]) == ("Someone New", None)


def test_new_music_director_uses_unseen_debut_name(monkeypatch):
    scored = _capture_scored_rows(monkeypatch)
    handlers.new_music_director(make_context(), "Test Movie", "2022-06-01", "telugu")
    assert scored[-1]["music_director"] == handlers.DEBUT_PERSON_NAME
    assert scored[-1]["music_director_movie_count"] == 0
