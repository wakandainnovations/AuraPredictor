"""Interactive (and scriptable) what-if CLI.

    python -m aurapredictor.scenarios.cli                       # interactive prompts
    python -m aurapredictor.scenarios.cli --movie RRR --list    # non-interactive: list matches
    python -m aurapredictor.scenarios.cli --movie RRR --release-date 2022-03-24 --language telugu \\
        --scenario replace_actor --new-actor "Someone Else"
    python -m aurapredictor.scenarios.cli --movie RRR --release-date 2022-03-24 --language telugu \
        --scenario ratings --ratings 4,6,8

    # A movie that isn't in the DB yet: revenue at audience ratings 4 / 6 / 8
    python -m aurapredictor.scenarios.cli --upcoming --movie "New Film" --release-date 2026-12-25 \
        --language telugu --budget 40000000 --runtime 150 --genre "Action, Drama" --lead-actor "Some Actor" \
        --director "Some Director" --cbfc-rating UA
"""

from __future__ import annotations

import argparse
import sys

from aurapredictor.scenarios import handlers
from aurapredictor.scenarios.engine import ArtifactsNotFoundError, find_movies, get_movie_row, load_context

SCENARIOS = {
    "replace_actor": ("Replace lead actor", ["new_actor"]),
    "replace_music_director": ("Replace music director", ["new_music_director"]),
    "new_music_director": ("Use a debut music director", []),
    "replace_director": ("Replace director", ["new_director"]),
    "set_release_date": ("Change release date", ["new_release_date"]),
    "rating_band": ("IMDb rating band", ["band"]),
    "ratings": ("Revenue at specific ratings (default 4, 6, 8)", []),
    "add_song_by_actor": ("Add a song by an actor", ["actor_name"]),
    "actor_promotes": ("Actor promotes the movie", ["actor_name"]),
}


def _print_result(result: handlers.ScenarioResult) -> None:
    print(f"\n-- {result.description} --")
    if not result.supported:
        print(f"  Not supported yet: {result.note}")
        return
    print(f"  Baseline revenue:  {result.baseline_revenue:,.0f}")
    print(f"  Scenario revenue:  {result.scenario_revenue:,.0f}")
    sign = "+" if result.delta >= 0 else ""
    print(f"  Delta:             {sign}{result.delta:,.0f} ({sign}{result.delta_pct:.1f}%)")
    if result.note:
        print(f"  Note: {result.note}")


def _print_rating_predictions(predictions: list[handlers.RatingPrediction], notes: list[str] | None = None) -> None:
    print(f"\n{'Rating':>7}  {'Predicted revenue':>18}  {'Typical range':>31}")
    for p in predictions:
        print(f"{p.rating:>7.1f}  {p.revenue:>18,.0f}  {p.low:>14,.0f} - {p.high:<14,.0f}")
    for note in notes or []:
        print(f"  Note: {note}")


def _parse_ratings(raw: str | None) -> tuple[float, ...]:
    if not raw:
        return handlers.DEFAULT_SCENARIO_RATINGS
    return tuple(float(part) for part in raw.split(","))


def run_scenario(context, movie_name: str, release_date: str, language: str, scenario: str, **kwargs) -> None:
    if scenario == "replace_actor":
        _print_result(handlers.replace_actor(context, movie_name, release_date, language, kwargs["new_actor"]))
    elif scenario == "replace_music_director":
        _print_result(
            handlers.replace_music_director(context, movie_name, release_date, language, kwargs["new_music_director"])
        )
    elif scenario == "new_music_director":
        _print_result(handlers.new_music_director(context, movie_name, release_date, language))
    elif scenario == "replace_director":
        _print_result(handlers.replace_director(context, movie_name, release_date, language, kwargs["new_director"]))
    elif scenario == "set_release_date":
        _print_result(
            handlers.set_release_date(context, movie_name, release_date, language, kwargs["new_release_date"])
        )
    elif scenario == "rating_band":
        for result in handlers.rating_band_scenario(context, movie_name, release_date, language, kwargs["band"]).values():
            _print_result(result)
    elif scenario == "ratings":
        row = get_movie_row(movie_name, release_date, language)
        if row is None:
            print("Movie not found in movie_prediction_features.")
            return
        _print_rating_predictions(handlers.rating_scenarios(context, row, _parse_ratings(kwargs.get("ratings"))))
    elif scenario == "add_song_by_actor":
        _print_result(handlers.add_song_by_actor(context, movie_name, release_date, language, kwargs["actor_name"]))
    elif scenario == "actor_promotes":
        _print_result(handlers.actor_promotes_movie(context, movie_name, release_date, language, kwargs["actor_name"]))
    else:
        raise ValueError(f"Unknown scenario '{scenario}'")


def _interactive() -> None:
    context = load_context()
    print("AuraPredictor what-if CLI. Ctrl+C to quit.\n")
    while True:
        query = input("Search for a movie by name: ").strip()
        if not query:
            continue
        matches = find_movies(query)
        if not matches:
            print("No matches in movie_prediction_features. Try another name.\n")
            continue

        for i, m in enumerate(matches):
            print(f"  [{i}] {m['movie_name']} ({m['release_date']}, {m['language']})")
        choice = input("Pick a movie index: ").strip()
        if not choice.isdigit() or int(choice) not in range(len(matches)):
            print("Invalid choice.\n")
            continue
        movie = matches[int(choice)]

        print("\nScenarios:")
        keys = list(SCENARIOS)
        for i, key in enumerate(keys):
            print(f"  [{i}] {SCENARIOS[key][0]}")
        scenario_choice = input("Pick a scenario index: ").strip()
        if not scenario_choice.isdigit() or int(scenario_choice) not in range(len(keys)):
            print("Invalid choice.\n")
            continue
        scenario = keys[int(scenario_choice)]

        kwargs = {}
        for param in SCENARIOS[scenario][1]:
            kwargs[param] = input(f"  {param}: ").strip()

        try:
            run_scenario(context, movie["movie_name"], movie["release_date"], movie["language"], scenario, **kwargs)
        except Exception as exc:  # keep the REPL alive on a bad input
            print(f"Error: {exc}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--movie", help="Movie name (exact, as stored in movie_prediction_features)")
    parser.add_argument("--release-date", help="release_date as stored (e.g. 2022-03-24 or 2022)")
    parser.add_argument("--language", help="language as stored (e.g. telugu)")
    parser.add_argument("--list", action="store_true", help="List movie_prediction_features matches for --movie and exit")
    parser.add_argument("--scenario", choices=list(SCENARIOS))
    parser.add_argument("--new-actor")
    parser.add_argument("--new-music-director")
    parser.add_argument("--new-director")
    parser.add_argument("--new-release-date")
    parser.add_argument("--band", choices=["below_5", "5_to_8", "8_to_10"])
    parser.add_argument("--actor-name")
    parser.add_argument("--ratings", help="Comma-separated ratings out of 10 for the ratings scenario (default 4,6,8)")

    upcoming = parser.add_argument_group("upcoming movie (not in the DB yet)")
    upcoming.add_argument("--upcoming", action="store_true", help="Predict revenue at each --ratings value for a new movie")
    upcoming.add_argument("--budget", type=float, help="Budget, same currency as the revenue column")
    upcoming.add_argument("--genre", help='e.g. "Action, Drama, Romance"')
    upcoming.add_argument("--runtime", type=float, help="Minutes (required with --upcoming)")
    upcoming.add_argument("--cbfc-rating", help="U, UA or A")
    upcoming.add_argument("--production-company")
    upcoming.add_argument("--lead-actor")
    upcoming.add_argument("--director")
    upcoming.add_argument("--music-director")
    args = parser.parse_args()

    try:
        if args.list:
            for m in find_movies(args.movie or ""):
                print(f"{m['movie_name']} | {m['release_date']} | {m['language']}")
            return

        if args.upcoming:
            # runtime is filled for every training movie, so leaving it blank would read as
            # "shorter than any real film" to the model and drag the prediction down ~2.5x.
            missing = [f for f in ("movie", "release_date", "language", "budget", "runtime") if getattr(args, f) is None]
            if missing:
                parser.error(f"--upcoming requires: {', '.join('--' + f.replace('_', '-') for f in missing)}")
            context = load_context()
            row, notes = handlers.upcoming_movie_row(
                context,
                movie_name=args.movie,
                release_date=args.release_date,
                language=args.language,
                budget=args.budget,
                genre=args.genre,
                runtime=args.runtime,
                cbfc_rating=args.cbfc_rating,
                production_company=args.production_company,
                lead_actor=args.lead_actor,
                director=args.director,
                music_director=args.music_director,
            )
            print(f"{args.movie} ({args.release_date}, {args.language}) -- predicted revenue by audience rating:")
            _print_rating_predictions(handlers.rating_scenarios(context, row, _parse_ratings(args.ratings)), notes)
            return

        if not args.scenario:
            _interactive()
            return

        context = load_context()
        kwargs = {
            "new_actor": args.new_actor,
            "new_music_director": args.new_music_director,
            "new_director": args.new_director,
            "new_release_date": args.new_release_date,
            "band": args.band,
            "actor_name": args.actor_name,
            "ratings": args.ratings,
        }
        needed = SCENARIOS[args.scenario][1]
        missing = [p for p in needed if not kwargs.get(p)]
        if missing:
            parser.error(f"--scenario {args.scenario} requires: {', '.join('--' + p.replace('_', '-') for p in missing)}")

        run_scenario(
            context, args.movie, args.release_date, args.language, args.scenario,
            **{k: v for k, v in kwargs.items() if k in needed or k == "ratings"},
        )
    except ArtifactsNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
