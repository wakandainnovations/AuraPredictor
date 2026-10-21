# AuraPredictor

Revenue prediction + what-if scenario engine for **Indian movies**, trained on data
in the shared `aura` Postgres DB (same DB AuraService uses).

Primary movie source is `india_movies_data_collection` (`aurapredictor.constants.MOVIES_TABLE`)
-- a curated, actively-growing table with 100% `revenue`/`budget` fill rate, not the
noisier 387K-row global `movies_data_collection` this project started against.
`actors_data_collection` has no equivalent curated counterpart, so cast/crew
history still reads from the original table.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config/secrets.example.properties config/secrets.properties   # fill in aura.db.url
```

`config/secrets.properties` is gitignored. Every value can also come from an env
var of the same name (`AURA_DB_URL`, `TMDB_API_READ_ACCESS_TOKEN`, `TMDB_API_KEY`,
`YOUTUBE_API_KEY`) -- same convention `AuraService/python-batch-jobs` already uses.

## Pipeline

```bash
python scripts/build_features_table.py --dry-run   # read-only sanity check
python scripts/build_features_table.py              # creates/populates movie_prediction_features
python scripts/train_model.py                        # trains + saves the CatBoost model
python -m aurapredictor.scenarios.cli                 # interactive what-if prompts
```

Optional, once TMDB/YouTube keys are filled in:

```bash
python scripts/run_enrichment.py --limit 200          # TMDB/YouTube current-stat backfill; resumable
python scripts/run_wayback_enrichment.py --limit 100  # point-in-time trailer/teaser view counts via Wayback Machine
python scripts/write_predictions.py                    # upserts batch predictions into movie_revenue_predictions
python scripts/run_full_enrichment_cycle.py             # chains all four steps above in one go
```

## Prediction API (for AuraService)

```bash
python scripts/serve_api.py                            # http://localhost:8082, OpenAPI docs at /docs
curl -s localhost:8082/v1/predict/upcoming -H 'Content-Type: application/json' -d '{
  "movieName": "New Film", "releaseDate": "2026-12-25", "language": "telugu",
  "budget": 40000000, "runtime": 150, "genre": "Action, Drama", "leadActor": "Some Actor",
  "supportingActors": ["A", "B", "C", "D"], "director": "Some Director", "cbfcRating": "UA"}'
```

Same inputs and output as the CLI's `--upcoming` mode: one predicted revenue (with a
low/high band from the model's out-of-sample log-MAE) per assumed audience rating
(`ratings`, default 4/6/8), plus `notes` on any value that had to be approximated.
The DB-backed context loads once at startup, so restart after `train_model.py`.
AuraService calls this through `POST /api/predictions/upcoming-movie`
(`AURAPREDICTOR_BASE_URL`, default `http://localhost:8082`).

## Budget_Opportunity_Risk (max budget for a planned movie)

```bash
python scripts/train_budget_risk.py                    # reads india_movies_data_collection directly
python -m aurapredictor.budget_risk.cli --release-date 2026-12-25 --language telugu \
    --genre "Action, Drama" --lead-actor "Some Actor" --supporting-actors "A, B, C, D" \
    --director "Some Director" --music-director "Some Composer" --budget 30000000
```

A second, independent model (`aurapredictor/budget_risk/`, artifacts in
`aurapredictor/artifacts/budget_opportunity_risk/`) for the producer's question *before*
investing: for these parameters, what's the highest budget that still returns 2x
(`constants.FLOP_MAX_RATIO`, the same hit bar as the revenue model's tiers)? For each
assumed audience rating (default 4/6/8) it reports a **max budget** -- the RISK line,
where the chance of reaching 2x drops below 50% -- and a **safe budget** (75% chance).
With `--budget`, it also calls that planned budget an OPPORTUNITY or a RISK.

The same API server exposes it for AuraService (same inputs as the CLI, camelCase;
`budget` optional). Each assessment has `maxBudget`/`safeBudget` (null = risky at any
budget, `*Capped` = at the largest training budget) and, with a budget,
`expectedRatio`, `hitProbability`, `verdict` and `verdictConfidence`:

```bash
curl -s localhost:8082/v1/budget-risk/upcoming -H 'Content-Type: application/json' -d '{
  "movieName": "New Film", "releaseDate": "2026-12-25", "language": "telugu",
  "genre": "Action, Drama", "leadActor": "Some Actor", "supportingActors": ["A", "B", "C", "D"],
  "director": "Some Director", "musicDirector": "Some Composer", "budget": 30000000}'
```

Only pre-investment columns are features (see `budget_risk/dataset.py` for what's
excluded and why): no revenue, runtime, YouTube stats, `number_of_screens` or the
`*_diff_*`/`*_ratio_*` person-history columns that embed the movie's own numbers.
`--rating-range` is an input like the rating (default 10000 for Hindi, 1000
otherwise). Cast/crew `*_prev3_*`/`*_prev_movie_*` columns are used as filled by
AuraDataFiller's `IndiaPrevMovieStatsFillService`; for a new movie they're rebuilt
from `india_movies_data_collection` + `india_prev_movies_data_collection`
(`budget_risk/prev_films.py`, matching the stored values on 90-95% of rows) -- so
retrain after the fill is re-run with more collected films. It forecasts the film's
return (`revenue_budget_ratio` x budget) with budget as a monotone input, then scans
budget for where the forecast stops covering 2x. 5-fold CV: hit/flop AUC 0.81; of
held-out films whose real budget sat under their line, 82% were hits, vs 36% over
it. Hit probabilities are calibrated within every budget band, but individual
blockbusters are under-forecast, so lines for star vehicles lean conservative.

## Relationship to AuraDataFiller

A separate, pre-existing Java pipeline (`../AuraDataFiller`) already fills much of
`movies_data_collection` (TMDB details, India-specific box-office/runtime crawlers,
YouTube trailer/teaser/song stats) on its own schedule. `scripts/run_enrichment.py`
is a deliberate **second, independent** source (TMDB is more accurate than
AuraDataFiller's other sources for some fields, and it won't cover every movie) --
it targets rows by checking which specific fields (`revenue`, `budget`, `genre`,
`production_companies`, `directors`, `runtime`, `trailer_views`, `teaser_views`)
are still actually empty, not a shared bookkeeping column, so it's safe to run
regardless of what AuraDataFiller has or hasn't gotten to. Every write uses
`COALESCE`/`NULLIF`-only-if-missing, matching AuraDataFiller's own convention, so
neither pipeline ever clobbers the other's value.

## Point-in-time trailer/teaser views (Wayback Machine)

The YouTube API only ever returns a video's *current* cumulative view count --
for an already-released movie that includes every post-release rewatch, not just
pre-release marketing reach, which is a real leakage risk for a model predicting
revenue from pre-release signals. `scripts/run_wayback_enrichment.py` fixes this
by reconstructing the actual view count as it stood on two specific dates (7 days
before release, and on release day) from archived Wayback Machine snapshots of
each trailer/teaser's YouTube page, storing them in a new `movie_video_snapshots`
table (verified against real archive.org data during development, both for the
old pre-2017 page format and the modern JSON-embedded one). It checks
`web.archive.org/robots.txt` at runtime before every request rather than
hardcoding an assumption about what's allowed.

## A scheduled one-time run

A crontab entry (see `crontab -l`) fires `run_full_enrichment_cycle.py` once,
~6 hours after this was set up, to give AuraDataFiller time to make progress
first; it self-removes from the crontab afterward. Safe to re-run manually at any
time in the meantime -- everything here is idempotent by construction (a row/video
that's already fully filled just won't match any pending-work query).

## Current data reality (as of the switch to india_movies_data_collection)

- `india_movies_data_collection` had 741 rows with real `revenue`/`budget` at
  rebuild time and is actively growing (it grew ~100 rows during this session
  alone) -- re-run `build_features_table.py` + `train_model.py` periodically to
  pick up more.
- Trailer/teaser/song view counts are still largely unpopulated; both
  AuraDataFiller and `scripts/run_enrichment.py`/`run_wayback_enrichment.py` are
  filling this in over time.
- Within `movie_prediction_features`, `lead_actor_prior_films_count`/
  `director_prior_films_count` are computed only against this same table's own
  (currently smaller) universe of movies, so ~48%/35% of rows have a nonzero prior
  count today -- expected to improve as the table grows. **Music director fill
  rate is separately low** (the `Original Music` role label is rarely captured in
  actors_data_collection) -- music-director what-if questions carry very little
  learned signal today; every handler that touches it says so explicitly.
- First baseline model on this cleaner-but-smaller data: median absolute
  percentage error ~49% on held-out movies (n=149 test rows) -- noisier than the
  ~42%/n=510 figure from the old, larger, noisier table, illustrating exactly the
  "data-bound, not model-bound, single-split metrics are noisy at this size" point
  from the model-comparison discussion. `budget`'s feature importance jumped from
  ~46% to ~72%, consistent with cleaner labels letting the model lean on it more.
- `movie_revenue_predictions` is also written to by at least one other, unrelated
  system (`model_name` values `xgboost`/`gbr`/`catboost`, likely AuraService's own
  box-office-backtest) -- this project's writes are scoped to
  `model_name = 'aura_predictor_catboost'` and never touch those rows.

## Layout

See `aurapredictor/` for the package (db/config/constants/dates/people/history are
shared building blocks; `enrichment/`, `features/`, `model/`, `scenarios/` are the
four pipeline stages) and `tests/` for unit coverage of the pure-Python logic
(role classification, date handling, hit/flop math, scenario handlers) -- none of
it needs a live DB or trained model to run: `python -m pytest`.
