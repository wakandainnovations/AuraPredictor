from __future__ import annotations

from aurapredictor.model.dataset import (
    fit_director_binarizer,
    fit_production_company_binarizer,
    multi_hot,
    split_directors,
    split_languages,
    has_plausible_revenue,
    normalize_release_event_name,
    prev_film_fields,
    split_production_companies,
)


def test_has_plausible_revenue_drops_scrape_errors():
    assert has_plausible_revenue({"revenue": 3_000_000, "budget": 2_000_000})
    assert not has_plausible_revenue({"revenue": 69, "budget": 11_000_000})  # below the absolute floor
    assert not has_plausible_revenue({"revenue": 43_158, "budget": 50_000_000})  # < 1% of budget


def test_normalize_release_event_name_strips_years():
    assert normalize_release_event_name("2011 ICC Cricket World Cup") == "ICC Cricket World Cup"
    assert normalize_release_event_name("Diwali") == "Diwali"
    assert normalize_release_event_name(None) == "Unknown"


def test_production_company_binarizer_keeps_every_credited_company_above_threshold():
    rows = [{"production_companies": "Big Studio, Small Co"}] * 5 + [{"production_companies": "Small Co, Rare Co"}]
    binarizer = fit_production_company_binarizer(rows)
    assert list(binarizer.classes_) == ["Big Studio", "Small Co"]
    assert split_production_companies("B, A, B") == ["A", "B"]


def test_prev_film_fields_compare_this_movie_to_previous_films():
    fields = prev_film_fields({
        "budget": 30, "rating_range": 1000,
        "lead_actor_1_prev3_avg_budget": 20, "lead_actor_1_prev3_avg_rating_range": 100,
        # The fill's "no previous film" markers.
        "lead_actor_1_prev_movie_budget": 0, "lead_actor_1_prev_movie_revenue": 0, "lead_actor_1_prev_movie_rating_range": 1,
    })
    assert fields["lead_actor_1_budget_diff_prev3_avg"] == 10
    assert fields["lead_actor_1_rating_range_ratio_prev3_avg"] == 10
    assert fields["lead_actor_1_prev_movie_budget"] is None
    assert fields["lead_actor_1_budget_diff_prev_movie"] is None
    assert fields["lead_actor_1_rating_range_ratio_prev_movie"] == 1000
    # Empty slot: everything NULL.
    assert fields["director_budget_diff_prev3_avg"] is None
    # Never the target: revenue diffs would hand the model this movie's own revenue.
    assert not any("revenue_diff" in col for col in fields)


def test_comma_separated_values_get_one_column_each():
    from sklearn.preprocessing import MultiLabelBinarizer

    languages = MultiLabelBinarizer().fit([["hindi"], ["tamil"], ["telugu"]])
    row = multi_hot(languages, [split_languages("tamil, telugu")], "language").iloc[0].to_dict()
    assert row == {"language_hindi": 0, "language_tamil": 1, "language_telugu": 1}

    directors = fit_director_binarizer([{"director": "A, B"}] * 5 + [{"director": "Rare"}])
    assert list(directors.classes_) == ["A", "B"]
    row = multi_hot(directors, [split_directors("B, A")], "directed_by").iloc[0].to_dict()
    assert row == {"directed_by_A": 1, "directed_by_B": 1}
