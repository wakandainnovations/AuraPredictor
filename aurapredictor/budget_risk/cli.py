"""Budget_Opportunity_Risk from the command line: the max budget a planned movie can
carry and still be expected to earn HIT_RATIO x back, at each assumed audience rating.

    python -m aurapredictor.budget_risk.cli --release-date 2026-12-25 --language telugu \\
        --genre "Action, Drama" --lead-actor "Prabhas" --supporting-actors "A, B, C, D" \\
        --director "Some Director" --music-director "Some Composer" --budget 30000000

--budget is optional: with it, each rating also says whether that planned budget
is an OPPORTUNITY or a RISK.
"""

from __future__ import annotations

import argparse
import sys

from aurapredictor.budget_risk import MODEL_NAME
from aurapredictor.budget_risk.predict import (
    MAX_BUDGET_HIT_PROBABILITY,
    SAFE_BUDGET_HIT_PROBABILITY,
    BudgetAssessment,
    assess,
    breakout_row,
    load_context,
    movie_row,
)
from aurapredictor.scenarios.cli import _parse_ratings
from aurapredictor.scenarios.engine import ArtifactsNotFoundError
from aurapredictor.scenarios.handlers import RATING_RANGE_VALUES


def _money(value: float | None, capped: bool) -> str:
    if value is None:
        return "none (risky at any budget)"
    return f"{'>= ' if capped else ''}{value:,.0f}"


def _print_assessments(assessments: list[BudgetAssessment], hit_ratio: float, notes: list[str]) -> None:
    print(
        f"Max budget = RISK line ({MAX_BUDGET_HIT_PROBABILITY:.0%} chance of >= {hit_ratio:g}x revenue); "
        f"safe budget = {SAFE_BUDGET_HIT_PROBABILITY:.0%} chance."
    )
    for a in assessments:
        line = (
            f"  rating {a.rating:g}: max budget {_money(a.max_budget, a.max_budget_capped)}, "
            f"safe budget {_money(a.safe_budget, a.safe_budget_capped)}"
        )
        if a.verdict:
            line += (
                f"\n      planned {a.planned_budget:,.0f} -> {a.verdict}, {a.verdict_confidence:.0%} confident "
                f"(expected ~{a.planned_ratio:.2f}x, {a.planned_hit_probability:.0%} chance of a hit)"
            )
        print(line)
    for note in notes:
        print(f"  note: {note}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release-date", required=True, help="Planned release date, e.g. 2026-12-25")
    parser.add_argument("--language", required=True, help='Original language first: "telugu" or "telugu, hindi"')
    parser.add_argument("--genre", help='e.g. "Action, Drama, Romance"')
    parser.add_argument("--production-company", help='One or more, comma-separated: "Mythri Movie Makers, T-Series"')
    parser.add_argument("--cbfc-rating", help="Target certificate: U, UA or A")
    parser.add_argument("--release-event-type", help="e.g. Festival, Holiday, Normal (default: looked up from the date)")
    parser.add_argument("--release-event-name", help="e.g. Diwali, Christmas, Normal")
    parser.add_argument("--remake", action="store_true", help="The movie is a remake")
    parser.add_argument(
        "--rating-range",
        type=int,
        choices=RATING_RANGE_VALUES,
        help="Expected audience vote-count band (default: from the cast's recent films, lead weighted 4x; "
        "else 10000 for Hindi, 1000 otherwise)",
    )
    parser.add_argument("--lead-actor")
    parser.add_argument("--supporting-actors", help="Up to 4, comma-separated, in billing order")
    parser.add_argument("--director")
    parser.add_argument("--music-director")
    parser.add_argument("--ratings", help="Comma-separated assumed ratings out of 10 (default 4,6,8)")
    parser.add_argument("--budget", type=float, help="Planned budget to classify, same currency as the budget column")
    parser.add_argument("--hit-ratio", type=float, help="Revenue:budget multiple that counts as a hit, 1 to 2.5 (default 2)")
    args = parser.parse_args()

    try:
        context = load_context()
        row, notes = movie_row(
            context,
            release_date=args.release_date,
            language=args.language,
            genre=args.genre,
            production_company=args.production_company,
            cbfc_rating=args.cbfc_rating,
            release_event_type=args.release_event_type,
            release_event_name=args.release_event_name,
            is_remake=args.remake,
            rating_range=args.rating_range,
            lead_actor=args.lead_actor,
            supporting_actors=[a.strip() for a in (args.supporting_actors or "").split(",") if a.strip()],
            director=args.director,
            music_director=args.music_director,
        )
        assessments = assess(context, row, _parse_ratings(args.ratings), args.budget, args.hit_ratio)
    except (ArtifactsNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    hit_ratio = args.hit_ratio or context.layout["hit_ratio"]
    print(f"{MODEL_NAME} ({args.release_date}, {args.language}, rating range {row['rating_range']}):")
    _print_assessments(assessments, hit_ratio, notes)
    if args.rating_range is None and (found := breakout_row(context, row)):
        row, note = found
        print(f"\nBreakout case (rating range {row['rating_range']}):")
        _print_assessments(assess(context, row, _parse_ratings(args.ratings), args.budget, args.hit_ratio), hit_ratio, [note])


if __name__ == "__main__":
    main()
