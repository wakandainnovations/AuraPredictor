# india_movies_data_collection — web-verified budget / revenue / verdict (correction review)

Sibling of `data_review/prev_movies_box_office/` (read its PROGRESS.md "Lessons", "Box Office India" and
source notes — they apply here too). One run = 100 movies in 20 batches of 5; each batch is written to the DB
as soon as it is verified.

Unlike the prev table, **every row here already has a budget, revenue and verdict, many corrected by hand**,
and there are no `*_source` columns. So this is a correction review.

## Files

| File | What it holds |
|---|---|
| `PROGRESS.md` | this file |
| `runs/run_NNN_movies.json` / `runs/run_NNN_batch_BB.json` | the run's movies (with pre-run values) / one batch's findings as applied |
| `processed.csv` | every movie reviewed, with budget / revenue / verdict status (confirmed, updated, flagged, not_found, no_rate) |
| `changes.csv` | every DB change: old → new, % diff, ₹ value, rate, rate date, source URL (run 0 = 'Avg' → 'avg' case fix) |
| `flagged.csv` | **sourced figures that differ but were not written** (3–25% off, wrong scope, or source conflicts) — for manual review |

Script: `scripts/movies_bo_review.py` (`next`, `apply <file> --run N --batch B`, `normalise-verdicts`, `status`).
Backup taken before run 1: table `india_movies_data_collection_backup_20261112` (1,943 rows).

## Rules (agreed with the user 2026-11-12)

- **Key:** `(id, movie_name)` — `id` is the IMDb id. tt5310090 is shared by Irudhi Suttru / Saala Khadoos (bilingual);
  Singam 3 (tt5323640) is an exact duplicate row and is updated together with its twin. 1,942 movies → 20 runs.
- **Order:** `release_date DESC, id, movie_name`.
- **Source priority:** Wikipedia first, then Box Office India, then sacnilk / BH / Koimoi / news — as in the prev review.
  Only figures read from a source page; every number carries a URL. Not found → leave as is.
- **Budget / revenue** (INR in crore, converted at the `currency_rate_xe` rate nearest the release date):
  - within 3 % of stored → confirmed, not written
  - 3–25 % off → `flagged.csv`, not written
  - more than 25 % off → **overwritten** (`changes.csv`), `revenue_budget_ratio` recomputed
  - revenue with scope `india_net` / `unknown`, or an India gross *below* the stored value (stored may include
    overseas) → flagged only
  - **BOI budgets may overwrite** (user's choice, 2026-11-12) even though they include P&A and run ~29 % above the
    stored budgets on average (pre-run check: 597 of 992 Hindi budgets >25 % off BOI). Use BOI only when Wikipedia
    has no usable budget.
- **Verdict:** set in the batch only when the sources agree (BOI / Wiki / BH / sacnilk, mapped with the prev review's
  hit / avg / flop rules); a different agreed verdict **replaces** the stored one (user's choice, 2026-11-12 — the
  stored verdicts mostly follow the revenue/budget ratio, and match BOI only ~50 %). Sources that conflict with each
  other → `verdict_flag` (flagged), stored verdict kept. Never set to NULL.
- **Trigger `trg_india_movies_hit_flop_avg`** (on the table): an UPDATE that changes budget/revenue but not the label
  re-derives `hit_flop_avg` from the ratio (> 2 hit, < 1 flop, else avg). The script therefore writes the figures
  first and the sourced/confirmed verdict in a second statement; with no sourced verdict it keeps the
  trigger-derived label and logs it in `changes.csv` (status `trigger`). `revenue_budget_ratio` is a generated column.

## Pre-run check (2026-11-12, stored vs `boi_index.csv`, Hindi rows)

Revenue tracks BOI worldwide gross (median ratio 0.96–1.10 every year except 2009 / 2011 at 1.4 / 1.3) — the rate
convention matches. Budgets are a median 29 % below BOI (0.42–0.73 for 2006–2014, 0.8–1.0 for 2015+).
Verdicts vs ratio thresholds: ratio > 2 → 'hit' 973 of 1,011; ratio ≤ 2 → 'flop' 497 / 'avg' 434.
Largest revenue outliers vs BOI WW: Payback (2010) 89×, Thanks Maa 45×, Manorama Six Feet Under 30×, Rush (2012) 0.01×.

## Run log

| Run | Date | Movies | Budget confirmed / updated / flagged | Revenue confirmed / updated / flagged | Verdict confirmed / updated / flagged | Notes |
|---|---|---|---|---|---|---|
| 1 | 2026-11-12 | 100 | 11 / 14 / 27 (48 not found) | 33 / 9 / 46 (12 not found) | 44 / 10 (+1 trigger) / 5 (41 none) | 2025-10 → 2026-09, newest first: 29 BOI matches (Hindi + big Tamil/Telugu titles), sacnilk pages for ~85 (full runs for most 2026 films), Wiki infoboxes for ~65. Before the run: backup table, 19 'Avg' → 'avg' (run 0 in changes.csv). **Big revenue corrections:** Karuppu ₹32 → ₹296 Cr (BOI WW; stored looked like a dropped digit), Border 2 ₹193 → ₹465 Cr, Aadu 3 ₹40 → ₹120 Cr, Thudakkam ₹9 → ₹45 Cr, Parasakthi ₹53 → ₹93 Cr, O'Romeo ₹63 → ₹95 Cr, Bhooth Bangla ₹187 → ₹247 Cr; Haunted 3D ₹21.6 → ₹16.2 Cr (BOI). **Budgets overwritten (>25%):** MSVPG ₹200 → 125 (BOI), Akhanda 2 ₹201 → 120, Parasakthi ₹217 → 145, Godaan ₹19 → 4 (BOI), The Taj Story ₹25 → 12 (BOI), Aadu 3 ₹45 → 20 (sacnilk), Vrusshabha ₹47 → 30, Chand Mera Dil ₹40 (= BOI) → 27.5 (Wiki, Zoom TV estimate), LIK ₹65 → 92.5, DDPD2 ₹100 → 150, Sarvam Maya ₹20 → 30, Mohiniyattam ₹10 → 5.5, Do Deewane Seher Mein ₹25.8 → 35 (BOI), KVMM ₹3.75 → 5. **Verdicts changed (sources agree):** Karuppu, Border 2, Aadu 3, Bhooth Bangla, MSVPG, Anaganaga Oka Raju, The Girlfriend → hit; Vishnu Vinyasam → avg; De De Pyaar De 2, Haunted 3D → flop. Thudakkam → avg by the trigger (no sourced verdict). **Script bugs found and fixed mid-run:** the ratio column is generated (first apply of 16 batches failed and rolled back cleanly), and the verdict trigger overrode 3 sourced verdicts (Chand Mera Dil, Akhanda 2, The Taj Story) — restored and logged. Reconciled against the backup: 53 changed fields, 0 unexplained. Many stored values equal sacnilk / Wiki exactly (hand-checked). Several small 2026 Kannada rows carry the same unsourced ₹29.9 Cr budget ($3,139,536: Ayogya 2, Graamaayana, Mother Promise, Balaramana Dinagalu, Lo Naveena, Amrutanjan) — nothing found to replace it |

## Prompt for the next run

```
Continue the india_movies_data_collection budget/revenue/verdict correction review.
Read data_review/movies_box_office/PROGRESS.md and follow its rules exactly (and the Lessons /
Box Office India sections of data_review/prev_movies_box_office/PROGRESS.md).
Run `venv/bin/python scripts/movies_bo_review.py next` to get this run's 100 movies.
For each batch of 5: find each movie's budget, worldwide gross and box-office verdict —
Wikipedia first, then Box Office India, then the other sources — write the findings to
data_review/movies_box_office/runs/run_NNN_batch_BB.json and apply with
`venv/bin/python scripts/movies_bo_review.py apply <file> --run N --batch B`.
Leave anything not found as null. When all 20 batches are done, update the run log
in PROGRESS.md and give me the counts.
```
