from aurapredictor.people import (
    cast_rows,
    is_crew_row,
    is_music_director_row,
    lead_actor_for_movie,
    music_director_for_movie,
    primary_director,
)

RRR_ROWS = [
    {"actor_name": "Ram Charan", "character_name": "Alluri Sitarama Raju", "role_position": 1},
    {"actor_name": "N.T. Rama Rao Jr.", "character_name": "Komaram Bheem", "role_position": 2},
    {"actor_name": "Alia Bhatt", "character_name": "Sita", "role_position": 3},
    {"actor_name": "S.S. Rajamouli", "character_name": "Director", "role_position": None},
    {"actor_name": "M.M. Keeravaani", "character_name": "Original Music", "role_position": None},
    {"actor_name": "D.V.V. Danayya", "character_name": "Producer", "role_position": None},
]


def test_is_crew_row():
    assert is_crew_row("Director") is True
    assert is_crew_row("Producer") is True
    assert is_crew_row("  original music  ") is True
    assert is_crew_row("Sita") is False
    assert is_crew_row(None) is False
    assert is_crew_row("") is False


def test_is_music_director_row_case_insensitive():
    assert is_music_director_row("Original Music") is True
    assert is_music_director_row("original music") is True
    assert is_music_director_row("Director") is False


def test_cast_rows_excludes_crew():
    cast = cast_rows(RRR_ROWS)
    names = {row["actor_name"] for row in cast}
    assert names == {"Ram Charan", "N.T. Rama Rao Jr.", "Alia Bhatt"}


def test_lead_actor_uses_lowest_role_position():
    assert lead_actor_for_movie(RRR_ROWS) == "Ram Charan"


def test_lead_actor_falls_back_to_first_cast_row_without_role_position():
    rows = [
        {"actor_name": "Someone", "character_name": "A Character", "role_position": None},
        {"actor_name": "Director Person", "character_name": "Director", "role_position": None},
    ]
    assert lead_actor_for_movie(rows) == "Someone"


def test_lead_actor_none_when_no_cast_rows():
    assert lead_actor_for_movie([{"actor_name": "X", "character_name": "Producer"}]) is None


def test_music_director_for_movie():
    assert music_director_for_movie(RRR_ROWS) == "M.M. Keeravaani"
    assert music_director_for_movie([{"actor_name": "X", "character_name": "Sita"}]) is None


def test_primary_director_single():
    assert primary_director("S.S. Rajamouli") == "S.S. Rajamouli"


def test_primary_director_co_directed_takes_first():
    assert primary_director("Raj, DK") == "Raj"


def test_primary_director_none():
    assert primary_director(None) is None
    assert primary_director("") is None
