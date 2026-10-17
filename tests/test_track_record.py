from aurapredictor.track_record import build_people_films, track_record, track_record_fields


def _movie(name, date, revenue, rating, **people):
    return {"movie_name": name, "release_date": date, "revenue": revenue, "rating_10": rating, **people}


MOVIES = [
    _movie("Film A", "2018-01-01", 100, 6.0, director="Dir", lead_actor_1="Star"),
    _movie("Film B", "2019-01-01", 200, 7.0, director="Dir", lead_actor_3="Star"),
    # Same film again under a punctuation variant -- must not count twice.
    _movie("Film: B", "2019-01-01", 999, 1.0, director="Dir"),
    _movie("Film C", "2020-01-01", 300, 8.0, director="Dir"),
]


def test_track_record_uses_only_films_before_release_date():
    films = build_people_films(MOVIES)["director"]["Dir"]
    assert track_record(films, "2020-01-01") == {
        "last5_avg_revenue": 150.0,
        "prev_revenue": 200.0,
        "last5_avg_rating": 6.5,
        "prev_rating": 7.0,
    }


def test_actor_history_is_pooled_across_billing_slots():
    fields = track_record_fields({"release_date": "2021-01-01", "lead_actor_2": "Star"}, build_people_films(MOVIES))
    assert fields["lead_actor_2_last5_avg_revenue"] == 150.0
    assert fields["lead_actor_2_prev_rating"] == 7.0


def test_debut_or_unknown_person_has_no_track_record():
    fields = track_record_fields({"release_date": "2018-01-01", "director": "Dir"}, build_people_films(MOVIES))
    assert fields["director_prev_revenue"] is None and fields["director_last5_avg_rating"] is None
