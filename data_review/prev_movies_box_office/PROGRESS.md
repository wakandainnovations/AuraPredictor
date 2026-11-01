# india_prev_movies_data_collection — web-verified budget / revenue / verdict

One run = 100 movies, worked in 20 batches of 5. Each batch is written to the DB
as soon as it is verified, so an interrupted run loses at most one batch.

## Files

| File | What it holds |
|---|---|
| `PROGRESS.md` | this file: rules, run log, next-run prompt |
| `runs/run_NNN_movies.json` | the 100 movies chosen for run N, with batch numbers and pre-run values |
| `runs/run_NNN_batch_BB.json` | findings for one batch, as applied (audit trail) |
| `processed.csv` | every movie already reviewed (found or not) — never picked again |
| `changes.csv` | every DB change: old value → new value, ₹ crore, rate, rate date, source URL |
| `missing_data.csv` | **movies where budget / revenue / verdict was not found — for manual follow-up** |
| `deleted_pre2005_rows.csv` | the 850 pre-2005 rows deleted on 2026-11-01 (also in table `india_prev_movies_data_collection_pre2005_backup_20261101`) |

Script: `scripts/prev_movies_bo_review.py` (`next`, `apply`, `status`).

## Rules (every run)

- **Order:** `times_needed DESC, release_date DESC, tmdb_movie_id` — the most-used prev films first. Key is `tmdb_movie_id` (unique; `id` is NULL on many rows).
- **Not found → leave the DB value as is**, and the movie goes into `missing_data.csv`.
- **Only figures read from a source page.** No estimates, no memory. Every number carries a source URL.
- **Budget:** production budget. Ranges ("₹40–50 crore") → midpoint, noted.
- **Revenue:** worldwide gross preferred; if only India gross/net exists use it and set `revenue_scope`.
- **Currency:** INR figures entered in crore; the script converts at the `currency_rate_xe` INR→USD rate nearest the release date (≤120 days away; the corrupt 3.75 rows are ignored). Figures given only in USD are entered as USD.
- **hit_flop_avg** (new text column) from a source's stated verdict or clear verdict wording:
  - `hit`: Semi-Hit, Hit, Super Hit, Blockbuster, All-Time Blockbuster, "commercial success"
  - `avg`: Average, Above Average, "break-even" / "moderate success"
  - `flop`: Below Average, Flop, Disaster, "box-office bomb", "commercial failure"
  - nothing stated → left NULL and listed as missing. A verdict is never guessed from the numbers.
- Changed values get `budget_source` / `revenue_source` = `websearch:<domain>`.

## Run log

| Run | Date | Movies | Budget filled / corrected / confirmed | Revenue filled / corrected / confirmed | Verdict set (hit/avg/flop) | Movies with gaps | Notes |
|---|---|---|---|---|---|---|---|
| 1 | 2026-11-01 | 100 | 2 / 0 / 43 | 14 / 2 / 50 | 59 (7 / 6 / 46) | 66 | Dhammu & Love U Mr Kalakaar revenue corrected; Rabhasa revenue is a distributors' share (flagged) |

Total movies after pre-2005 delete: 7,441 → ~75 runs. Reviewed: 100.

## Lessons from earlier runs (read before searching)

- **Existing Wikipedia-sourced values were converted correctly** — re-deriving them reproduced the stored USD to the dollar. The value of a run is mostly filling gaps, setting verdicts, and catching non-gross figures.
- **Bollywood Hungama year tables** give a verdict + India lifetime nett for nearly every Hindi release:
  `https://www.bollywoodhungama.com/box-office-collections/filterbycountry/IND/<YEAR>/` — fetch once per year and ask only for the titles you need.
- Per movie, fetch `https://en.wikipedia.org/wiki/<Title>` (try `_(<year>_film)` / `_(film)` on 404) and ask for the infobox Budget / Box office verbatim plus any verdict sentence.
- **Watch for "distributors' share"** in infoboxes (common for Telugu/Tamil) — it is not gross; don't store it as revenue.
- Conflicting verdicts between sources (e.g. Wikipedia "commercial success" vs BH "Flop") → leave NULL, note the conflict.
- "Underperformed" / "poor opening" alone is not a verdict → NULL.
- Direct-to-streaming films (Netflix/Prime) have no box office → leave as is, note it.
- Regional films without Wikipedia figures are rarely findable; one targeted search (sacnilk / koimoi) is enough before marking missing.

## Prompt for the next run

```
Continue the india_prev_movies_data_collection budget/revenue/verdict review.
Read data_review/prev_movies_box_office/PROGRESS.md and follow its rules exactly.
Run `venv/bin/python scripts/prev_movies_bo_review.py next` to get this run's 100
movies (it resumes an unfinished run automatically). For each batch of 5: web-search
each movie's budget, worldwide gross and box-office verdict, write the findings to
runs/run_NNN_batch_BB.json and apply with
`venv/bin/python scripts/prev_movies_bo_review.py apply <file> --run N --batch B`.
Leave anything not found as null. When all 20 batches are done, update the run log
in PROGRESS.md and give me the counts.
```
