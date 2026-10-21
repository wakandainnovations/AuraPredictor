# AuraPredictor HTTP API

Two prediction endpoints for a movie that isn't in the database yet. Both are served
by one FastAPI app (`aurapredictor/api/app.py`) that AuraService calls.

| Endpoint | Question it answers | Model |
|---|---|---|
| `POST /v1/predict/upcoming` | How much will this movie gross? | Revenue model (`aurapredictor/model/`) |
| `POST /v1/budget-risk/upcoming` | What's the highest budget this movie can carry and still be a hit? | Budget_Opportunity_Risk (`aurapredictor/budget_risk/`) |

## Running it

```bash
source venv/bin/activate
python scripts/train_model.py         # revenue model      -> aurapredictor/artifacts/
python scripts/train_budget_risk.py   # budget-risk model  -> aurapredictor/artifacts/budget_opportunity_risk/
python scripts/serve_api.py           # http://localhost:8082
```

- **Port and host:** default `127.0.0.1:8082`, overridable with `--port` / `--host` or the
  `AURAPREDICTOR_PORT` / `AURAPREDICTOR_HOST` env vars. (AuraService is 8080, AuraMath 8081.)
- **Interactive docs:** `http://localhost:8082/docs` lists every field and lets you send
  requests from the browser.
- **DB:** both models load their lookups (cast history, release events, GDP) from the
  `aura` Postgres DB once at startup, which takes a minute or two. A **retrain only takes
  effect after a restart**.
- **Model files aren't in git** (`*.cbm`, `*.pkl` are gitignored). A fresh clone has to run
  both training scripts first. Until a model is trained, its endpoint returns `503` and the
  other endpoint keeps working.

### Health check

`GET /healthz`

```json
{"status": "ok", "modelLoaded": true, "budgetRiskModelLoaded": true}
```

`modelLoaded` is the revenue model and `budgetRiskModelLoaded` the budget-risk model.

## Conventions (both endpoints)

- **JSON field names are camelCase** (`movieName`). snake_case (`movie_name`) is accepted
  too, for curl users. Responses are always camelCase.
- **Money** (`budget`, `revenue`, `maxBudget`…) is in the same units as `budget` /
  `revenue` in `india_movies_data_collection`; for example, Pushpa 2 is stored as 52,900,000.
- **`releaseDate`** must be `YYYY-MM-DD`. Anything else is a `422`.
- **`ratings`**: the audience rating out of 10 isn't known before release, so every answer
  is given per assumed rating. Default `[4, 6, 8]` (4 = bad, 6 = good, 8 = blockbuster).
  1 to 10 values, each between 0 and 10.
- **Ratings above about 8 all score the same.** The models are tree ensembles and only one
  training film is rated 9 or more, so the highest rating split is about 8.05. 8.1, 9 and 10
  give the same answer; 8 is the realistic top scenario.
- **Names** (actors, director, music director, production company) are matched exactly
  against the DB. Someone not found is treated as unknown, and a `notes` entry says so.
- **`language`, `genre`, `cbfcRating`** aren't checked against a fixed list. An unseen
  value is accepted and treated as unknown, so check spelling (`"telugu"`, not `"Telegu"`).
  For `language`, put the original language first: `"telugu, hindi"`.
- **`notes`** in every response lists each value that had to be assumed or approximated.
  Read it.

### Error responses

| Status | When | Body |
|---|---|---|
| `422` | Missing required field, wrong type, value out of range, bad date, unknown `ratingRange`, more than 4 `supportingActors` | `{"detail": ...}`: a list of field errors from validation, or a message string |
| `503` | That endpoint's model hasn't been trained yet | `{"detail": "No ... model found ... Run scripts/train_....py first."}` |

Example `422`:

```json
{"detail": [{"type": "value_error", "loc": ["body", "releaseDate"],
             "msg": "Value error, must be a date as YYYY-MM-DD, got '25-12-2026'", "input": "25-12-2026"}]}
```

---

## `POST /v1/budget-risk/upcoming`

For a planned movie's pre-investment parameters, returns per assumed rating:

- the **max budget**: the RISK line, where the chance of reaching `hitRatio` × budget in
  revenue drops below 50%;
- the **safe budget**: the same line at a 75% chance;
- if `budget` is sent, an **OPPORTUNITY / RISK verdict** on that budget, with the chance the
  verdict is right.

Revenue is never an input. Only things a producer knows before investing are used.

### Request

**Required: `movieName`, `releaseDate`, `language`.** Everything else is optional.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `movieName` | string | **yes** | | Echoed back; not used by the model. |
| `releaseDate` | string | **yes** | | Planned release date, `YYYY-MM-DD`. Drives release year/month/week, festival lookup and GDP/inflation. |
| `language` | string | **yes** | | Original language first: `"telugu"` or `"telugu, hindi"`. |
| `budget` | number > 0 | no | none | Planned budget to classify. Without it you get the budget lines only, no verdict. |
| `hitRatio` | number, 1 to 2.5 | no | `2` | Revenue ÷ budget that counts as a hit. 1 = gross covers the budget. Changing it needs no retrain. |
| `ratingRange` | int: `1, 10, 100, 1000, 10000, 100000` | no | from the cast (see below) | Expected audience vote-count band. One of the strongest inputs. |
| `genre` | string | no | none | Comma-separated: `"Action, Drama"`. |
| `productionCompany` | string | no | none | One or more, comma-separated. |
| `cbfcRating` | string | no | none | Target certificate: `U`, `UA` or `A`. |
| `releaseEventType` | string | no | looked up from the date | e.g. `Festival`, `Holiday`, `Normal`. |
| `releaseEventName` | string | no | looked up from the date | e.g. `Diwali`, `Sankranti`, `Christmas`. |
| `isRemake` | boolean | no | `false` | The movie is a remake. |
| `leadActor` | string | no | none | Billing slot 1. |
| `supportingActors` | string[] (max 4) | no | `[]` | Billing slots 2–5, in order. Nearly every training movie has all four; fewer gets a note. |
| `director` | string | no | none | |
| `musicDirector` | string | no | none | |
| `ratings` | number[] (1–10 values, 0–10) | no | `[4, 6, 8]` | Assumed audience ratings to score. |

**`ratingRange` default when it's left out:**
1. Each cast member's log-average rating range over their last 3 films that have one.
2. A weighted average across the cast, the lead counting 4× each supporting actor, rounded
   to the nearest band (an exact tie goes to the lower band).
3. If nobody in the cast has history: 10000 for Hindi, 1000 for other languages.

Guessing past films from earlier ones, step 1–2 matched the real band 54% of the time vs
39% for the language-only default. A `notes` entry shows the values used. The response's
top-level `ratingRange` is the value the main `assessments` used.

**Breakout case:** if `ratingRange` was left out and the lead had a 100000 film among their
last 3, the response also includes `breakout`, the same movie scored at 100000. Treat it as
the upside, not the plan: only about 12% of films that followed such a breakout reached 100000
again. Sending `ratingRange` yourself turns this off.

### Response

| Field | Type | Description |
|---|---|---|
| `movieName`, `releaseDate`, `language` | string | Echoed from the request. |
| `modelVersion` | string | Date the budget-risk model was trained. |
| `ratingRange` | int | Rating range used for `assessments`: as sent, or the default above. |
| `hitRatio` | number | Hit bar used (as sent, or 2). |
| `maxBudgetHitProbability` | number | `0.5`: the hit chance at the max-budget line. |
| `safeBudgetHitProbability` | number | `0.75`: the hit chance at the safe-budget line. |
| `assessments` | array | One per requested rating (below). |
| `breakout` | object or `null` | `{ratingRange: 100000, note, assessments}`; see above. |
| `notes` | string[] | Assumptions and approximations made. |

Each assessment:

| Field | Type | Description |
|---|---|---|
| `rating` | number | Assumed audience rating. |
| `maxBudget` | number or `null` | Highest budget where the film is still more likely than not to reach `hitRatio`. Above it is RISK. `null` = risky at any budget the model has seen. |
| `maxBudgetCapped` | boolean | `true` = the line reached the largest training budget (currently 90M, `max_budget` in `layout.json`), so the real line may be higher. |
| `safeBudget` | number or `null` | Highest budget with at least a 75% hit chance. |
| `safeBudgetCapped` | boolean | As `maxBudgetCapped`. |
| `plannedBudget` | number or `null` | The `budget` sent. This and the fields below are `null` without a `budget`. |
| `expectedRatio` | number or `null` | Central forecast of revenue ÷ budget at the planned budget. |
| `hitProbability` | number or `null` | Chance the film reaches `hitRatio` at the planned budget. Never higher than at any smaller budget, so a budget above `maxBudget` is always RISK. |
| `verdict` | string or `null` | `OPPORTUNITY` if `hitProbability` ≥ 0.5, else `RISK`. |
| `verdictConfidence` | number or `null` | Chance the verdict is right: `hitProbability` for OPPORTUNITY, 1 − `hitProbability` for RISK. |

`expectedRatio` can sit just under the bar while the verdict is OPPORTUNITY, because the
errors are lopsided: a few films beat their forecast by a lot.

### Example

Minimal request:

```json
{"movieName": "New Film", "releaseDate": "2026-12-25", "language": "telugu"}
```

Full request:

```json
{
  "movieName": "Sample Telugu Action Film",
  "releaseDate": "2027-01-14",
  "language": "telugu",
  "budget": 15000000,
  "hitRatio": 2,
  "genre": "Action, Drama",
  "productionCompany": "Mythri Movie Makers",
  "cbfcRating": "UA",
  "isRemake": false,
  "leadActor": "Allu Arjun",
  "supportingActors": ["Rashmika Mandanna", "Fahadh Faasil", "Jagapathi Babu", "Sunil"],
  "director": "Sukumar",
  "musicDirector": "Devi Sri Prasad",
  "ratings": [6, 8]
}
```

Response (numbers rounded; model of 2026-10-21):

```json
{
  "movieName": "Sample Telugu Action Film",
  "releaseDate": "2027-01-14",
  "language": "telugu",
  "modelVersion": "2026-10-21",
  "ratingRange": 10000,
  "hitRatio": 2.0,
  "maxBudgetHitProbability": 0.5,
  "safeBudgetHitProbability": 0.75,
  "assessments": [
    {"rating": 6.0, "maxBudget": 6809010.49, "maxBudgetCapped": false,
     "safeBudget": 465373.53, "safeBudgetCapped": false, "plannedBudget": 15000000.0,
     "expectedRatio": 1.585, "hitProbability": 0.461, "verdict": "RISK", "verdictConfidence": 0.539},
    {"rating": 8.0, "maxBudget": 19200242.83, "maxBudgetCapped": false,
     "safeBudget": 2899428.16, "safeBudgetCapped": false, "plannedBudget": 15000000.0,
     "expectedRatio": 2.162, "hitProbability": 0.63, "verdict": "OPPORTUNITY", "verdictConfidence": 0.63}
  ],
  "breakout": {
    "ratingRange": 100000,
    "note": "Breakout case: Allu Arjun had a 100000 film among their last 3 (10000, 100000, 10000), so this is also scored at rating range 100000. Only ~12% of films after such a breakout reached it again -- treat it as the upside, not the plan.",
    "assessments": [
      {"rating": 6.0, "maxBudget": 17344717.83, "maxBudgetCapped": false,
       "safeBudget": 839094.11, "safeBudgetCapped": false, "plannedBudget": 15000000.0,
       "expectedRatio": 1.964, "hitProbability": 0.59, "verdict": "OPPORTUNITY", "verdictConfidence": 0.59},
      {"rating": 8.0, "maxBudget": 30027587.62, "maxBudgetCapped": false,
       "safeBudget": 6809010.49, "safeBudgetCapped": false, "plannedBudget": 15000000.0,
       "expectedRatio": 2.719, "hitProbability": 0.722, "verdict": "OPPORTUNITY", "verdictConfidence": 0.722}
    ]
  },
  "notes": [
    "No rating range given -- assumed 10000 from the cast's recent films, lead weighted 4x (Allu Arjun: 10000, 100000, 10000; Rashmika Mandanna: 10000, 1000, 10000; Fahadh Faasil: 10000, 1000, 1000; Sunil: 1000).",
    "No GDP/inflation data for 2027 yet -- used 2026's values.",
    "'Jagapathi Babu' isn't in the data -- the model treats them as an unknown lead actor."
  ]
}
```

How to read it: at 15M, the film is a RISK if audiences rate it 6 (line 6.8M) and an
OPPORTUNITY at 8 (line 19.2M). If it repeats a Pushpa 1-scale breakout, 15M is an
OPPORTUNITY even at 6.

### How far to trust it

- **5-fold cross-validation** (1,937 films): hit/flop AUC 0.81. Of films whose real budget
  was under their line, 82% were hits, vs 36% over it.
- **2026 test** (trained on films before 2026, judged 72 releases from 2026): 81% of verdicts
  right, vs 71% for always saying RISK. All eleven 15M+ films were called correctly; almost
  every miss was a sub-5M film.
- **`verdictConfidence`** holds up at 2×: 70–90% calls were right 89% of the time. Near
  break-even (`hitRatio` 1–1.25) the top end is optimistic: a stated 90%+ is about 80%.
- **Blockbusters are under-forecast**, so lines for star films lean conservative. Recent
  big-budget flops (2025–26) weigh on every big film's line.

---

## `POST /v1/predict/upcoming`

Predicts gross revenue for a movie that isn't released yet, per assumed audience rating,
with a low/high band. Same inputs as the scenario CLI's `--upcoming` mode.

### Request

**Required: `movieName`, `releaseDate`, `language`, `budget`, `runtime`.**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `movieName` | string | **yes** | | Echoed back. |
| `releaseDate` | string | **yes** | | `YYYY-MM-DD`. |
| `language` | string | **yes** | | Original language first. |
| `budget` | number > 0 | **yes** | | Production budget. |
| `runtime` | number > 0 | **yes** | | Minutes. Required because every training movie has one; a blank would read as "shorter than any film" and cut the prediction by about 2.5×. |
| `genre` | string | no | none | Comma-separated. |
| `cbfcRating` | string | no | none | `U`, `UA` or `A`. |
| `numberOfScreens` | int > 0 | no | none | Opening screen count, if known. |
| `releaseEventType` | string | no | looked up from the date | |
| `releaseEventName` | string | no | looked up from the date | |
| `isRemake` | boolean | no | `false` | |
| `ratingRange` | int: `1, 10, 100, 1000, 10000, 100000` | no | language default | This endpoint uses the language default (10000 Hindi, 1000 otherwise), not the cast-based one. |
| `productionCompany` | string | no | none | One or more, comma-separated. |
| `leadActor` | string | no | none | |
| `supportingActors` | string[] (max 4) | no | `[]` | Billing order. |
| `director` | string | no | none | |
| `musicDirector` | string | no | none | |
| `ratings` | number[] (1–10 values, 0–10) | no | `[4, 6, 8]` | |

### Response

| Field | Type | Description |
|---|---|---|
| `movieName`, `releaseDate`, `language` | string | Echoed. |
| `modelVersion` | string | Date the revenue model was trained. |
| `predictions` | array | One per rating: `{rating, revenue, low, high}`. `revenue` is the central prediction; `low`/`high` is a band from the model's out-of-sample log error. |
| `notes` | string[] | Assumptions and approximations made. |

### Example

```json
{
  "movieName": "Sample Telugu Action Film",
  "releaseDate": "2027-01-14",
  "language": "telugu",
  "budget": 15000000,
  "runtime": 160,
  "genre": "Action, Drama",
  "productionCompany": "Mythri Movie Makers",
  "cbfcRating": "UA",
  "leadActor": "Allu Arjun",
  "supportingActors": ["Rashmika Mandanna", "Fahadh Faasil", "Jagapathi Babu", "Sunil"],
  "director": "Sukumar",
  "musicDirector": "Devi Sri Prasad",
  "ratings": [6, 8]
}
```

Response (model of 2026-10-19):

```json
{
  "movieName": "Sample Telugu Action Film",
  "releaseDate": "2027-01-14",
  "language": "telugu",
  "modelVersion": "2026-10-19",
  "predictions": [
    {"rating": 6.0, "revenue": 10585771, "low": 5879985, "high": 19057626},
    {"rating": 8.0, "revenue": 23729593, "low": 13180868, "high": 42720524}
  ],
  "notes": [
    "No --rating-range given -- assumed 1000, typical for a well-received telugu film.",
    "No GDP/inflation data for 2027 yet -- used 2026's values.",
    "'Devi Sri Prasad' has no earlier films in the data -- treated as a debut music director."
  ]
}
```

---

## The two endpoints side by side

| | `/v1/predict/upcoming` | `/v1/budget-risk/upcoming` |
|---|---|---|
| Output | Revenue (with band) per rating | Max/safe budget per rating, plus verdict if a budget is sent |
| Required fields | movieName, releaseDate, language, budget, runtime | movieName, releaseDate, language |
| Uses runtime / screens | yes / optional | no / no (not known before investing) |
| `ratingRange` default | language | cast history (lead 4×), then language |
| Configurable hit bar | n/a | `hitRatio` 1–2.5 |
| Model artifacts | `aurapredictor/artifacts/` | `aurapredictor/artifacts/budget_opportunity_risk/` |
| Retrain | `scripts/train_model.py` | `scripts/train_budget_risk.py` |

## AuraService integration

AuraService calls the revenue endpoint through `POST /api/predictions/upcoming-movie`,
using `AURAPREDICTOR_BASE_URL` (default `http://localhost:8082`). The budget-risk endpoint
has no AuraService wrapper yet.
