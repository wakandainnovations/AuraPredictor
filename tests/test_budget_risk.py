from __future__ import annotations

import math

import numpy as np
import pytest

from aurapredictor.budget_risk.dataset import row_to_record
from aurapredictor.budget_risk.prev_films import build_prev_films, prev_film_stats
from aurapredictor.budget_risk.predict import (
    OPPORTUNITY,
    RISK,
    BudgetAssessment,
    BudgetRiskContext,
    budget_line,
    hit_probability,
    movie_row,
)

BUDGETS = np.array([1.0, 2.0, 3.0, 4.0])

# Residuals spread evenly over [-1, 1]: a forecast exactly at log(2) is a coin flip.
LAYOUT = {"hit_ratio": 2.0, "residual_quantiles": list(np.linspace(-1, 1, 101))}


def test_budget_line_stops_at_first_failing_budget():
    assert budget_line(BUDGETS, np.array([True, True, False, True])) == (2.0, False)


def test_budget_line_none_when_even_smallest_budget_fails():
    assert budget_line(BUDGETS, np.array([False, True, True, True])) == (None, False)


def test_budget_line_capped_when_every_budget_passes():
    assert budget_line(BUDGETS, np.array([True, True, True, True])) == (4.0, True)


def test_hit_probability_from_residual_quantiles():
    assert hit_probability(math.log(2), LAYOUT) == pytest.approx(0.5)
    assert hit_probability(math.log(2) + 0.5, LAYOUT) == pytest.approx(0.75)
    assert hit_probability(math.log(2) - 2, LAYOUT) == pytest.approx(0.0)


def test_verdict_follows_hit_probability():
    base = dict(rating=6.0, max_budget=1.0, safe_budget=1.0, max_budget_capped=False, safe_budget_capped=False)
    assert BudgetAssessment(**base).verdict is None
    assert BudgetAssessment(**base, planned_hit_probability=0.5).verdict == OPPORTUNITY
    assert BudgetAssessment(**base, planned_hit_probability=0.49).verdict == RISK


def test_verdict_confidence_is_the_chance_the_call_is_right():
    base = dict(rating=6.0, max_budget=1.0, safe_budget=1.0, max_budget_capped=False, safe_budget_capped=False)
    assert BudgetAssessment(**base).verdict_confidence is None
    assert BudgetAssessment(**base, planned_hit_probability=0.8).verdict_confidence == pytest.approx(0.8)
    assert BudgetAssessment(**base, planned_hit_probability=0.1).verdict_confidence == pytest.approx(0.9)


def test_row_to_record_never_reads_revenue():
    row = {"release_date": "2024-08-15", "language": "telugu, hindi", "budget": 5e6, "revenue": 9e9, "remake_of": "REMAKE"}
    record = row_to_record(row, keep_companies=set())
    assert 9e9 not in record.values()
    assert record["primary_language"] == "telugu"
    assert record["num_languages"] == 2
    assert record["is_remake"] == 1
    assert record["release_day"] == "Thursday"


def test_row_to_record_treats_zero_prev_budget_as_missing():
    row = {"release_date": "2024-08-15", "lead_actor_1_prev_movie_budget": 0, "lead_actor_1_prev_movie_rating_range": 1}
    record = row_to_record(row, keep_companies=set())
    assert record["lead_actor_1_prev_movie_budget"] is None
    assert record["lead_actor_1_prev_movie_rating_range"] == 1.0


def test_prev_film_stats_follow_the_java_fill_conventions():
    films = [("2019-01-01", 1.0, 2.0, 100.0), ("2020-01-01", 3.0, 4.0, None), ("2021-01-01", 5.0, 6.0, 1000.0),
             ("2022-01-01", 7.0, 8.0, 10.0), ("2023-01-01", 9.0, 9.0, 10.0)]
    stats = prev_film_stats(films, "2023-01-01")  # the 2023 film itself is not a previous film
    assert stats["prev3_avg_budget"] == 5.0  # 2020, 2021, 2022
    assert stats["prev3_avg_rating_range"] == pytest.approx((1 + 1000 + 10) / 3)  # NULL counts as 1
    assert (stats["prev_movie_budget"], stats["prev_movie_rating_range"]) == (7.0, 10.0)


def test_build_prev_films_pools_actor_slots_and_prefers_india_row():
    india = [{"movie_name": "Film: One", "release_date": "2020-05-01", "budget": 1, "revenue": 2, "rating_range": 100,
              "lead_actor_3": "Star"}]
    collected = [{"movie_name": "Film - One", "release_date": "2020-04-20", "budget": 9, "revenue": 9,
                  "lead_actor_1": "Star"}]
    films = build_prev_films(india, collected)["lead_actor"]["Star"]
    assert films == [("2020-05-01", 1.0, 2.0, 100.0)]


CONTEXT = BudgetRiskContext(
    model=None,
    encoders={},
    layout=LAYOUT,
    people_movie_counts={"lead_actor": {"Star": [("2020-01-01", 10)]}, "director": {}, "music_director": {}},
    date_events={"2026-11-08": ("Festival", "Diwali")},
    year_macro={2025: {"gdp_usd_billions": 4000.0, "inflation_rate_pct": 4.0}},
    prev_films={"lead_actor": {"Star": [("2020-01-01", 5e6, 9e6, 10000.0)]}, "director": {}, "music_director": {}},
)


def test_movie_row_fills_event_macro_and_career_counts():
    row, notes = movie_row(
        CONTEXT, release_date="2026-11-08", language="hindi", lead_actor="Star", director="Newcomer",
    )
    assert (row["release_event_type"], row["release_event_name"]) == ("Festival", "Diwali")
    assert row["gdp_usd_billions"] == 4000.0  # 2026 missing -> latest year
    assert row["lead_actor_1_movie_count"] == 11  # 10 films before their 2020 film, plus that one
    assert row["director_movie_count"] is None
    assert row["rating_range"] == 10000  # Hindi default
    assert row["lead_actor_1_prev_movie_budget"] == 5e6
    assert row["lead_actor_1_prev3_avg_rating_range"] == pytest.approx((10000 + 2) / 3)
    assert row["director_prev_movie_budget"] == 0.0  # no earlier film: the fill's 0 / 1 markers
    assert row["director_prev3_avg_rating_range"] == 1.0
    assert row["music_director_prev_movie_budget"] is None  # empty slot
    assert "budget" not in row and "rating_10" not in row
    assert any("Newcomer" in note for note in notes)


def test_movie_row_rejects_unknown_rating_range():
    with pytest.raises(ValueError):
        movie_row(CONTEXT, release_date="2026-11-08", language="hindi", rating_range=5000)


def test_movie_row_rejects_more_than_four_supporting_actors():
    with pytest.raises(ValueError):
        movie_row(CONTEXT, release_date="2026-11-08", language="hindi", supporting_actors=list("ABCDE"))
