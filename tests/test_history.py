from aurapredictor.history import hit_flop_ratio_last_n, prior_films, prior_films_count

# budget 100, revenue: 250 -> ratio 2.5 (hit), 400 -> ratio 4 (blockbuster), 150 -> ratio 1.5 (flop)
FILMOGRAPHY = [
    {"release_date": "2018-01-01", "revenue": 150, "budget": 100},  # flop
    {"release_date": "2019-01-01", "revenue": 250, "budget": 100},  # hit
    {"release_date": "2020-01-01", "revenue": 400, "budget": 100},  # blockbuster
    {"release_date": "2021-01-01", "revenue": 150, "budget": 100},  # flop
    {"release_date": "2022-01-01", "revenue": None, "budget": None},  # unknown, no financials
]


def test_prior_films_excludes_same_or_future_dates():
    prior = prior_films(FILMOGRAPHY, "2021-01-01")
    assert [f["release_date"] for f in prior] == ["2020-01-01", "2019-01-01", "2018-01-01"]


def test_prior_films_count():
    assert prior_films_count(FILMOGRAPHY, "2021-01-01") == 3
    assert prior_films_count(FILMOGRAPHY, "2018-01-01") == 0
    assert prior_films_count(FILMOGRAPHY, "2023-01-01") == 5


def test_hit_flop_ratio_last_n_mixed():
    # as of 2022-01-01, last 3 prior films are 2021 (flop), 2020 (blockbuster), 2019 (hit)
    result = hit_flop_ratio_last_n(FILMOGRAPHY, "2022-01-01", n=3)
    assert result["hits"] == 2  # hit + blockbuster
    assert result["flops"] == 1
    assert result["hit_flop_ratio"] == 2.0


def test_hit_flop_ratio_all_hits_no_flops():
    all_hits = [
        {"release_date": "2019-01-01", "revenue": 300, "budget": 100},
        {"release_date": "2020-01-01", "revenue": 300, "budget": 100},
    ]
    result = hit_flop_ratio_last_n(all_hits, "2021-01-01", n=3)
    assert result["hits"] == 2
    assert result["flops"] == 0
    assert result["hit_flop_ratio"] == 2.0


def test_hit_flop_ratio_no_prior_films():
    result = hit_flop_ratio_last_n([], "2021-01-01", n=3)
    assert result == {"hits": 0, "flops": 0, "hit_flop_ratio": None}


def test_hit_flop_ratio_skips_rows_missing_budget():
    result = hit_flop_ratio_last_n(FILMOGRAPHY, "2023-01-01", n=1)
    # most recent prior film (2022) has no budget -- contributes to neither count
    assert result == {"hits": 0, "flops": 0, "hit_flop_ratio": None}
