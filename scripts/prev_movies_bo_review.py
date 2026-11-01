"""Web-verified budget / revenue / verdict review of india_prev_movies_data_collection.

Claude does the web searching; this script does everything deterministic so the
numbers that land in the DB never depend on mental arithmetic:

  next  [--run N]          print (and save) the next 100 unreviewed movies as JSON
  apply <batch.json>       apply one batch of findings (<= 5 movies) to the DB
  status                   counts of reviewed / updated / missing

Tracking files live in data_review/prev_movies_box_office/ (see PROGRESS.md there).

Conversion: INR -> USD with the currency_rate_xe rate nearest the movie's release
date (AuraDataFiller's ExchangeRateService convention), skipping the corrupt 3.75
rows. Crore = 1e7 INR, lakh = 1e5 INR. A movie whose release date has no rate
within MAX_RATE_GAP_DAYS is not converted and goes to the missing list instead.

Batch JSON: a list of objects, one per movie in the batch:
  {
    "tmdb_movie_id": 12345,       # the key (imdb id is NULL on some rows)
    "budget_inr_crore": 12.5,      # or null; ranges -> midpoint (say so in notes)
    "budget_usd": null,            # only when the source states USD directly
    "revenue_inr_crore": 15.2,     # worldwide gross preferred
    "revenue_usd": null,
    "revenue_scope": "worldwide",  # worldwide | india_gross | india_net | unknown
    "verdict": "flop",             # hit | avg | flop | null
    "verdict_label": "Flop",       # wording used by the source
    "sources": {"budget": "url", "revenue": "url", "verdict": "url"},
    "notes": "..."
  }
A null value means "not found": the DB column is left exactly as it is.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurapredictor.db import execute, fetch_all, get_dict_cursor  # noqa: E402

TABLE = "india_prev_movies_data_collection"
RUN_SIZE = 100
BATCH_SIZE = 5
CRORE = 10_000_000
MAX_RATE_GAP_DAYS = 120
RATE_SANE = (0.005, 0.05)  # INR->USD has never left this band since 1995
VERDICTS = {"hit", "avg", "flop"}

DIR = Path(__file__).resolve().parents[1] / "data_review" / "prev_movies_box_office"
PROCESSED = DIR / "processed.csv"
CHANGES = DIR / "changes.csv"
MISSING = DIR / "missing_data.csv"
RUNS = DIR / "runs"

PROCESSED_COLS = ["tmdb_movie_id", "imdb_id", "movie_name", "release_date", "language", "run", "batch",
                  "budget_status", "revenue_status", "verdict_status", "reviewed_at"]
CHANGES_COLS = ["tmdb_movie_id", "imdb_id", "movie_name", "release_date", "field", "old_value", "new_value",
                "inr_value", "rate", "rate_date", "source", "notes", "run", "batch"]
MISSING_COLS = ["tmdb_movie_id", "imdb_id", "movie_name", "release_date", "language",
                "missing_fields", "reason", "notes", "run", "batch"]


def _append(path: Path, cols: list[str], rows: list[dict]) -> None:
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerows(rows)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def ensure_column() -> None:
    execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS hit_flop_avg text")


def _rates() -> list[tuple[date, float]]:
    rows = fetch_all(
        "SELECT rate_date, rate::float AS rate FROM currency_rate_xe "
        "WHERE from_currency='INR' AND to_currency='USD' AND rate BETWEEN %s AND %s "
        "ORDER BY rate_date", RATE_SANE)
    return [(r["rate_date"], r["rate"]) for r in rows]


def nearest_rate(release: str, rates: list[tuple[date, float]]) -> tuple[float, date] | None:
    d = date.fromisoformat(release[:10])
    best = min(rates, key=lambda r: abs((r[0] - d).days))
    return (best[1], best[0]) if abs((best[0] - d).days) <= MAX_RATE_GAP_DAYS else None


def _domain(url: str | None) -> str:
    if not url:
        return "websearch"
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return f"websearch:{host}" if host else "websearch"


def cmd_next(run: int | None) -> None:
    ensure_column()
    done = {int(r["tmdb_movie_id"]) for r in _read(PROCESSED)}
    rows = fetch_all(
        f"SELECT id, movie_name, release_date, language, tmdb_movie_id, budget::float, "
        f"revenue::float, budget_source, revenue_source, hit_flop_avg, director, lead_actor_1, "
        f"times_needed FROM {TABLE} "
        f"ORDER BY times_needed DESC NULLS LAST, release_date DESC, tmdb_movie_id")
    RUNS.mkdir(parents=True, exist_ok=True)
    existing = sorted(RUNS.glob("run_*_movies.json"))
    last = int(existing[-1].stem.split("_")[1]) if existing else 0
    if run is None:
        # Resume the latest run if any of its movies are still unreviewed.
        if existing and any(r["tmdb_movie_id"] not in done for r in json.loads(existing[-1].read_text())):
            run = last
        else:
            run = last + 1
    out = RUNS / f"run_{run:03d}_movies.json"
    if out.exists():
        todo = json.loads(out.read_text())
    else:
        todo = [r for r in rows if r["tmdb_movie_id"] not in done][:RUN_SIZE]
        for i, r in enumerate(todo):
            r["batch"] = i // BATCH_SIZE + 1
        out.write_text(json.dumps(todo, indent=1, default=str))
    pending = [r for r in todo if r["tmdb_movie_id"] not in done]
    print(f"run {run}: {len(todo)} movies ({len(pending)} still to review) -> {out}; "
          f"{len(rows) - len(done) - len(pending)} unreviewed movies outside this run")
    for r in pending:
        print(f"  b{r['batch']:02d} tmdb {r['tmdb_movie_id']:<8} {r['release_date']} {str(r['language']):<9} {r['movie_name']}"
              f" | dir {r['director']} | lead {r['lead_actor_1']}"
              f" | budget {r['budget']} ({r['budget_source']}) revenue {r['revenue']} ({r['revenue_source']})")


def _usd(item: dict, kind: str, rate: tuple[float, date] | None) -> tuple[int | None, str, float | None]:
    """-> (usd, status, inr). status: found_inr | found_usd | not_found | no_rate."""
    crore, usd = item.get(f"{kind}_inr_crore"), item.get(f"{kind}_usd")
    if usd is not None:
        return round(float(usd)), "found_usd", None
    if crore is None:
        return None, "not_found", None
    inr = float(crore) * CRORE
    if rate is None:
        return None, "no_rate", inr
    return round(inr * rate[0]), "found_inr", inr


def cmd_apply(path: Path, run: int, batch: int) -> None:
    ensure_column()
    items = json.loads(path.read_text())
    if len(items) > BATCH_SIZE:
        sys.exit(f"{len(items)} movies in one batch; the limit is {BATCH_SIZE}")
    done = {int(r["tmdb_movie_id"]) for r in _read(PROCESSED)}
    rates = _rates()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    processed, changes, missing = [], [], []
    with get_dict_cursor() as cur:
        for item in items:
            mid = int(item["tmdb_movie_id"])
            if mid in done:
                print(f"skip {mid}: already reviewed")
                continue
            cur.execute(f"SELECT id, movie_name, release_date, language, tmdb_movie_id, budget, revenue, "
                        f"hit_flop_avg FROM {TABLE} WHERE tmdb_movie_id=%s", (mid,))
            row = cur.fetchone()
            if row is None:
                sys.exit(f"{mid} not in {TABLE}")
            rate = nearest_rate(row["release_date"], rates)
            src = item.get("sources") or {}
            notes = item.get("notes") or ""
            base = {"tmdb_movie_id": mid, "imdb_id": row["id"] or "", "movie_name": row["movie_name"], "release_date": row["release_date"],
                    "run": run, "batch": batch}
            statuses, gaps = {}, []
            for kind in ("budget", "revenue"):
                usd, status, inr = _usd(item, kind, rate)
                statuses[kind] = status
                if usd is None:
                    gaps.append(kind if status == "not_found" else f"{kind}(no_rate)")
                    continue
                scope = item.get("revenue_scope") if kind == "revenue" else None
                cur.execute(f"UPDATE {TABLE} SET {kind}=%s, {kind}_source=%s WHERE tmdb_movie_id=%s",
                            (usd, _domain(src.get(kind)), mid))
                changes.append({**base, "field": kind, "old_value": row[kind], "new_value": usd,
                                "inr_value": inr, "rate": rate[0] if inr is not None else "",
                                "rate_date": rate[1] if inr is not None else "",
                                "source": src.get(kind, ""),
                                "notes": "; ".join(x for x in (f"scope={scope}" if scope else "", notes) if x)})
            verdict = (item.get("verdict") or "").lower() or None
            if verdict is not None and verdict not in VERDICTS:
                sys.exit(f"{mid}: verdict must be hit/avg/flop/null, got {verdict!r}")
            if verdict:
                cur.execute(f"UPDATE {TABLE} SET hit_flop_avg=%s WHERE tmdb_movie_id=%s", (verdict, mid))
                changes.append({**base, "field": "hit_flop_avg", "old_value": row["hit_flop_avg"],
                                "new_value": verdict, "inr_value": "", "rate": "", "rate_date": "",
                                "source": src.get("verdict", ""),
                                "notes": f"label={item.get('verdict_label', '')}"})
                statuses["verdict"] = "found"
            else:
                statuses["verdict"] = "not_found"
                gaps.append("verdict")
            if gaps:
                reason = "no INR->USD rate near release date" if any("no_rate" in g for g in gaps) \
                    else "not found in web search"
                missing.append({**base, "language": row["language"],
                                "missing_fields": ";".join(gaps), "reason": reason, "notes": notes})
            processed.append({**base, "language": row["language"],
                              "budget_status": statuses["budget"], "revenue_status": statuses["revenue"],
                              "verdict_status": statuses["verdict"], "reviewed_at": now})
    # DB committed above; now the tracking files.
    _append(PROCESSED, PROCESSED_COLS, processed)
    _append(CHANGES, CHANGES_COLS, changes)
    _append(MISSING, MISSING_COLS, missing)
    for c in changes:
        print(f"  tmdb {c['tmdb_movie_id']} {c['movie_name']}: {c['field']} {c['old_value']} -> {c['new_value']}"
              + (f"  (₹{float(c['inr_value']) / CRORE:g} Cr @ {c['rate']:.6f} on {c['rate_date']})"
                 if c["inr_value"] not in ("", None) else ""))
    for m in missing:
        print(f"  MISSING tmdb {m['tmdb_movie_id']} {m['movie_name']}: {m['missing_fields']}")
    print(f"run {run} batch {batch}: {len(processed)} reviewed, {len(changes)} field updates, "
          f"{len(missing)} with gaps")


def cmd_status() -> None:
    p, c, m = _read(PROCESSED), _read(CHANGES), _read(MISSING)
    total = fetch_all(f"SELECT count(*) AS n FROM {TABLE}")[0]["n"]
    runs = sorted({int(r["run"]) for r in p})
    print(f"reviewed {len(p)}/{total} movies over runs {runs or '-'}; "
          f"{len(c)} field updates; {len(m)} movies with gaps")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("next")
    n.add_argument("--run", type=int)
    a = sub.add_parser("apply")
    a.add_argument("path", type=Path)
    a.add_argument("--run", type=int, required=True)
    a.add_argument("--batch", type=int, required=True)
    sub.add_parser("status")
    args = ap.parse_args()
    DIR.mkdir(parents=True, exist_ok=True)
    if args.cmd == "next":
        cmd_next(args.run)
    elif args.cmd == "apply":
        cmd_apply(args.path, args.run, args.batch)
    else:
        cmd_status()


if __name__ == "__main__":
    main()
