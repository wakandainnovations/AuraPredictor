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
Rechecking gap movies from an earlier run (e.g. against a new source): `apply <file> --run N --batch B --recheck`.
It only accepts fields listed as gaps in `missing_data.csv`, removes filled gaps from that file, updates the movie's
statuses in `processed.csv` (no new row), and tags the `changes.csv` rows with `recheck`. Name the files `runs/run_NNN_recheck_BB.json`.

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
| 2 | 2026-11-01 | 100 | 5 / 1 / 36 | 15 / 0 / 34 | 42 (12 / 5 / 25) | 79 | Urumi budget corrected (tmdb → ₹20 Cr); Shadow (2013) stored revenue likely a distributors' share (flagged, left); Gali Gali Chor Hai revenue conflict (Wiki 13.07 vs BH 4.83 Cr, left); 9 direct-to-streaming or unreleased; 7 verdict conflicts left NULL |
| 3 | 2026-11-01 | 100 | 3 / 2 / 18 | 8 / 3 / 19 | 35 (15 / 2 / 18) | 93 | Agnyaathavaasi budget/revenue corrected (old revenue was the ₹60 Cr distributors' share → ₹94.6 Cr gross); Panjaa revenue → Wiki ₹32.1 Cr; Ek Ajnabee tmdb revenue → BH India nett; stored revenue is a distributors' share for 100% Love, Don Seenu, Chirutha (flagged, left); 3 verdict conflicts left NULL (Dhobi Ghat, Dus, Page 3); 5 direct-to-streaming/short films; batch 4 split over run_003_batch_04 + 04b, batch 18 over 18a + 18 (usage-limit interruption) |
| 4 | 2026-11-01 | 100 | 7 / 0 / 19 | 11 / 0 / 28 | 46 (19 / 3 / 24) | 84 | First run of mostly 2011–2023 regional films; 10 direct-to-streaming / festival-only / short films (Mumbaikar, Cuttputlli, Ariyippu, Hum Do Hamare Do, Mimi, Putham Pudhu Kaalai, Kriti, Sainma, Sunglass, Michael); flagged, left: Connect revenue (Wiki ₹20 Cr cited to a day-2 article; sacnilk WW 15.57 Cr), Yatra (Wiki infobox ₹14.3 Cr vs body ₹28.5 Cr; verdict self-conflict), Chalo budget (Wiki ₹3 Cr vs Sakshi ₹10.5 Cr); Maha Samudram revenue from sacnilk end-of-run total |
| 5 | 2026-11-01 | 100 | 4 / 0 / 23 | 16 / 1 / 28 | 30 (8 / 4 / 18) | 89 | 2005–2011 small Hindi/regional films + 2023–2026 regional; Footage revenue corrected (tmdb $3,280 → sacnilk ₹0.41 Cr); 10 direct-to-streaming / unreleased / short / TV (Be Happy, The Archies, Apurva, Pippa, Haddi, Kathal, Maruthi Nagar Police Station, Dubai Return, The White Mountain, SRK doc); flagged, left: Adhurs stored revenue is the ₹28–30 Cr distributors' share; Fussclass Dabhade Wiki ₹9.64 Cr vs sacnilk ₹4.25 Cr; Pravinkoodu Shappu Wiki ref says ₹30 Cr budget; verdict conflicts left NULL: Kandahar, Sura, Black (2005); Shakti budget is filmibeat's "touted ₹45 Cr" |

| 6 | 2026-11-01 | 100 | 6 / 0 / 19 | 7 / 2 / 24 | 40 (12 / 1 / 27) | 87 | 2019–2023 regional + Hindi; Box Office India was down (maintenance page, not in search index) → only Wiki / BH / sacnilk used; Yashoda (₹50 → 41 Cr) and Major (₹66 → 65 Cr) revenue corrected to the Wiki range midpoint; 15 direct-to-streaming (Qala, Tadka, Jogi, 12th Man, Saani Kaayidham, Dasvi, Bro Daddy, Maa Oori Polimera, Drushyam 2, Tuck Jagadish, Shershaah, Bhoomi, The Power, Miss India, Nishabdham); flagged, left: Christopher (Wiki gross now ₹2.1 Cr 'dubious' vs stored ≈₹38 Cr), Alone (infobox ₹55 L vs body ₹1.1 Cr), Bheemla Nayak (infobox ₹158.5 Cr vs body ₹192 Cr+, revenue not set), Mosagallu (stored ₹0.5 Cr < sacnilk 3-day ₹1.65 Cr), Action (infobox ₹23.32 Cr vs body '<₹12 Cr'), Tuck Jagadish stored tmdb budget $20M implausible, Agent Sai budget (infobox ₹4 Cr vs body ₹1 Cr), 10 (2022) budget ambiguous; verdict conflict left NULL: Prince (Wiki bomb vs sacnilk Above Average) |
| 7 | 2026-11-01 | 100 | 1 / 0 / 18 | 15 / 0 / 25 | 48 (24 / 6 / 18) | 86 | 2013–2018 regional + Hindi; Box Office India still down → Wiki / BH / filmibeat / Sakshi / News Minute used; Bhai budget ₹42 Cr from m9.news; stored revenue is a distributors' share for Pilla Nuvvu Leni Jeevitham, Run Raja Run, Kotha Janta (flagged, left); stored revenue is a partial running total for Oozham (25-day Kerala) and Rajamma @ Yahoo (21 days) (flagged, left); Papanasam Wiki ₹100 Cr vs filmibeat ₹57.83 Cr and 10 Endrathukulla Wiki ₹28 Cr vs filmibeat ₹35 Cr (Wiki used); verdict conflicts left NULL: Nil Battey Sannata (Wiki success vs BH Flop), Iraivi (average vs failure), Solo (Kerala hit vs 'sank'); Chandrettan Evideya 'moderate hit' → avg; Shuruaat Ka Interval is a shorts anthology |
| 8 | 2026-11-02 | 100 | 0 / 0 / 13 | 13 / 1 / 10 | 39 (17 / 3 / 19) | 94 | 2006–2013 small Hindi + Tamil/Telugu/Kannada; Box Office India still down (HTTP 500) → Wiki / BH year tables / news; ABCD revenue corrected (tmdb $222,000 → BH India nett ₹45.5 Cr); stored revenue is a distributors' share for Mr. Perfect (₹32 Cr), Golimaar (₹12 Cr), Pournami (₹9 Cr) (flagged, left); Jogayya Wiki lifetime ₹8 Cr < filmibeat 3-day ₹9.59 Cr (left); Pappu Can't Dance Saala DB date 2010-12-16 but released 16 Dec 2011 (2010 rate used); Supermen of Malegaon theatrical release 2012 (DB 2008); shorts / festival-only / unreleased: Kirchiyaan, Facebook (Atlee short), Migration, Ek Tho Chance, Team: The Force, Fear (online release); verdict conflict left NULL: Rama Rama Krishna Krishna; Oram Po 'decent success' → avg |

Total movies after pre-2005 delete: 7,441 → ~75 runs. Reviewed: 800.

## Lessons from earlier runs (read before searching)

- **Existing Wikipedia-sourced values were converted correctly** — re-deriving them reproduced the stored USD to the dollar. The value of a run is mostly filling gaps, setting verdicts, and catching non-gross figures.
- **Bollywood Hungama year tables** give a verdict + India lifetime nett for nearly every Hindi release:
  `https://www.bollywoodhungama.com/box-office-collections/filterbycountry/IND/<YEAR>/` — fetch once per year and ask only for the titles you need.
- Per movie, fetch `https://en.wikipedia.org/wiki/<Title>` (try `_(<year>_film)` / `_(film)` on 404) and ask for the infobox Budget / Box office verbatim plus any verdict sentence.
- **Also check Box Office India** (`https://boxofficeindia.com/`) for every movie. It has budget, India nett / worldwide gross and a verdict for many Indian films, regional ones included. Search `site:boxofficeindia.com <Title> <year>` and fetch the movie's page. Use its figures when Wikipedia/BH have none, and cite its URL. If its verdict differs from Wikipedia's or BH's, the conflict rule still applies: leave the verdict NULL.
- **Watch for "distributors' share"** in infoboxes (common for Telugu/Tamil) — it is not gross; don't store it as revenue.
- Conflicting verdicts between sources (e.g. Wikipedia "commercial success" vs BH "Flop") → leave NULL, note the conflict.
- "Underperformed" / "poor opening" alone is not a verdict → NULL.
- Direct-to-streaming films (Netflix/Prime) have no box office → leave as is, note it.
- **Koimoi "Plus"** verdict = profitable but below Hit → mapped to `avg` (Nikka Zaildar 4).
- **Ranges in Wikipedia infoboxes** (budget or gross) are already stored as midpoints — re-deriving the midpoint reproduces them.
- **"Highest-grossing <language> film" / "biggest opening"** are rankings, not verdicts → NULL.
- **Wikipedia vs BH verdict conflicts are common for 2005–2015 Hindi films** (BH tends to be harsher); run 2 had 7 such conflicts.
- Partial running totals ("₹50 Cr in two weeks", opening weekend) are not lifetime gross → don't store.
- **Stored Telugu revenues are often distributors' shares** (run 3: 100% Love, Don Seenu, Chirutha, Agnyaathavaasi). `apply` can't null a value, so a share with no gross to replace it stays flagged in notes.
- **BH year tables omit many small Hindi releases** (shorts, direct-to-video, 2005–2008 small films); don't spend more searches on them.
- **sacnilk movie pages** (`https://www.sacnilk.com/movie/<Title>_<year>`) give budget, WW gross and a verdict (Hit / Blockbuster …) for many 2019+ regional films; their header totals often disagree with their own day-wise table, and the 'figures till' date shows whether the run was complete (run 4).
- **Disambiguation pages are common** for regional titles: retry `<Title>_(<year>_film)`, `_(film)`, `_(<year>_<language>_film)`.
- Regional films without Wikipedia figures are rarely findable; one targeted search (sacnilk / koimoi) is enough before marking missing.
- **sacnilk's "Worldwide gross" header is often wrong for small films** (lower than its own India gross, run 5). When overseas is "—"/0, store the India gross line as `india_gross`. If 'figures till' is the release day or the end of week 1, it is a partial total → don't store.
- **BH 2005–2009 year tables list only the bigger releases**; most small Hindi films from those years have no BH entry (run 5).
- **sacnilk shows a default "Disaster" verdict on pages with ₹0.00 data** (e.g. Pranaya Vilasam, a Wiki "super hit"). Use a sacnilk verdict only when the page has real figures (run 6).
- **Box Office India may be down** (run 6: maintenance page, and `site:boxofficeindia.com` searches return nothing). If so, note it in the run log and continue with Wiki / BH / sacnilk.
- **Wiki infobox ranges are sometimes stored at the range top** instead of the midpoint (run 6: Yashoda, Major). Re-derive the midpoint and correct it.
- **Fetching Wikipedia in parallel trips HTTP 429** (run 7). Query the API one request at a time with a short sleep and back off on 429.
- **filmibeat's 'highest grossing Tamil movies of <year>' list** (e.g. the 2015 one) gives lifetime figures for the year's top 20 Tamil films; scope is usually not stated → `unknown`. Sakshi's 'Hit Kannada Films in <year>' list is a usable verdict source for Kannada films (run 7).
- **BH movie pages** (`/movie/<slug>/box-office/`) for 2006–2008 films mostly say 'No Data Found'; a slug can belong to an older namesake (run 8: `victoria-no-203` is the 1972 film — check `datePublished`). Wiki mirrors (bharatpedia, wikimili) sometimes carry figures removed from the current article as unsourced — don't use them. A redirect to a franchise's first film (My Friend Ganesha 2 → My Friend Ganesha) is not the sequel's data (run 8).
- Wiki raw wikitext (`https://en.wikipedia.org/w/index.php?title=<Title>&action=raw`) and the search API (`/w/api.php?action=query&list=search`) are faster than rendered pages for infobox budget/gross. BH year tables and sacnilk movie pages can also be fetched directly.

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
