"""Web-verified budget / revenue / verdict review of india_movies_data_collection.

Sibling of prev_movies_bo_review.py. Every row of this table already has a budget,
revenue and verdict, many of them corrected by hand, so this is a *correction*
review, not gap filling (rules agreed with the user on 2026-11-12):

  budget / revenue   sourced value within CONFIRM_TOL of stored  -> confirmed, no write
                     differs by CONFIRM_TOL .. OVERWRITE_MIN     -> flagged.csv, no write
                     differs by more than OVERWRITE_MIN          -> overwritten (changes.csv)
                     revenue with scope india_net / unknown, or an india_gross figure
                     below the stored value (stored may include overseas) -> flagged only
  verdict            null in the batch -> kept; same -> confirmed; different -> updated.
                     Set a verdict in the batch only when the sources agree; a conflict
                     goes in "verdict_flag" (-> flagged.csv) and the stored verdict stays.
  revenue_budget_ratio is a generated column, so it follows budget / revenue changes by itself.

  next  [--run N]          print (and save) the next 100 unreviewed movies
  apply <batch.json> --run N --batch B
  normalise-verdicts       lower-case stray verdict labels ('Avg' -> 'avg'), logged as run 0
  status

Key: (id, movie_name). id is the IMDb id; it is shared by a bilingual pair
(Irudhi Suttru / Saala Khadoos) and duplicated for one row (Singam 3), so both
columns are matched; an exact duplicate row is updated together with its twin.

Batch JSON: a list (<= 5) of
  {"id": "tt1234567", "movie_name": "...",
   "budget_inr_crore": 12.5, "budget_usd": null,
   "revenue_inr_crore": 15.2, "revenue_usd": null,
   "revenue_scope": "worldwide",      # worldwide | india_gross | india_net | unknown
   "verdict": "flop", "verdict_label": "BOI Disaster",
   "verdict_flag": null,              # text when sources conflict with each other / stored
   "sources": {"budget": "url", "revenue": "url", "verdict": "url"},
   "notes": "..."}
Conversion: INR -> USD at the currency_rate_xe rate nearest the release date
(<= 120 days), same as prev_movies_bo_review.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurapredictor.db import execute, fetch_all, get_dict_cursor  # noqa: E402
from scripts.prev_movies_bo_review import (  # noqa: E402
    BATCH_SIZE, CRORE, RUN_SIZE, VERDICTS, _append, _rates, _read, _usd, nearest_rate)

TABLE = "india_movies_data_collection"
CONFIRM_TOL = 0.03
OVERWRITE_MIN = 0.25

DIR = Path(__file__).resolve().parents[1] / "data_review" / "movies_box_office"
PROCESSED = DIR / "processed.csv"
CHANGES = DIR / "changes.csv"
FLAGGED = DIR / "flagged.csv"
RUNS = DIR / "runs"

PROCESSED_COLS = ["id", "movie_name", "release_date", "language", "run", "batch",
                  "budget_status", "revenue_status", "verdict_status", "reviewed_at"]
CHANGES_COLS = ["id", "movie_name", "release_date", "field", "old_value", "new_value", "pct_diff",
                "inr_value", "rate", "rate_date", "source", "notes", "run", "batch"]
FLAGGED_COLS = ["id", "movie_name", "release_date", "field", "stored", "sourced", "pct_diff",
                "inr_value", "rate_date", "scope", "reason", "source", "notes", "run", "batch"]


def _key(r: dict) -> tuple[str, str]:
    return (r["id"], r["movie_name"])


def cmd_next(run: int | None) -> None:
    done = {_key(r) for r in _read(PROCESSED)}
    rows = fetch_all(
        f"SELECT DISTINCT ON (release_date, id, movie_name) id, movie_name, release_date, language, "
        f"budget::float, revenue::float, hit_flop_avg, director, lead_actor_1 FROM {TABLE} "
        f"ORDER BY release_date DESC, id, movie_name")
    RUNS.mkdir(parents=True, exist_ok=True)
    existing = sorted(RUNS.glob("run_*_movies.json"))
    last = int(existing[-1].stem.split("_")[1]) if existing else 0
    if run is None:
        if existing and any(_key(r) not in done for r in json.loads(existing[-1].read_text())):
            run = last
        else:
            run = last + 1
    out = RUNS / f"run_{run:03d}_movies.json"
    if out.exists():
        todo = json.loads(out.read_text())
    else:
        todo = [r for r in rows if _key(r) not in done][:RUN_SIZE]
        for i, r in enumerate(todo):
            r["batch"] = i // BATCH_SIZE + 1
        out.write_text(json.dumps(todo, indent=1, default=str))
    pending = [r for r in todo if _key(r) not in done]
    print(f"run {run}: {len(todo)} movies ({len(pending)} still to review) -> {out}; "
          f"{len(rows) - len(done) - len(pending)} unreviewed movies outside this run")
    for r in pending:
        print(f"  b{r['batch']:02d} {r['id']:<11} {r['release_date']} {str(r['language'])[:9]:<9} {r['movie_name']}"
              f" | dir {r['director']} | lead {r['lead_actor_1']}"
              f" | budget {r['budget']} revenue {r['revenue']} verdict {r['hit_flop_avg']}")


def _decide(kind: str, stored: float | None, new: int, scope: str | None) -> tuple[str, float | None, str]:
    """-> (status, pct_diff, reason). status: confirmed | updated | flagged."""
    if not stored:
        return "updated", None, ""
    diff = (new - stored) / stored
    if abs(diff) <= CONFIRM_TOL:
        return "confirmed", diff, ""
    if abs(diff) <= OVERWRITE_MIN:
        return "flagged", diff, f"differs by {diff:+.1%} (<= {OVERWRITE_MIN:.0%}: not overwritten)"
    if kind == "revenue" and scope in ("india_net", "unknown", None):
        return "flagged", diff, f"scope {scope or 'unstated'}: not overwritten"
    if kind == "revenue" and scope == "india_gross" and new < stored:
        return "flagged", diff, "India gross below stored (stored may include overseas): not overwritten"
    return "updated", diff, ""


def cmd_apply(path: Path, run: int, batch: int) -> None:
    items = json.loads(path.read_text())
    if len(items) > BATCH_SIZE:
        sys.exit(f"{len(items)} movies in one batch; the limit is {BATCH_SIZE}")
    done = {_key(r) for r in _read(PROCESSED)}
    for item in items:  # validate before touching the DB
        v = (item.get("verdict") or "").lower() or None
        if v is not None and v not in VERDICTS:
            sys.exit(f"{_key(item)}: verdict must be hit/avg/flop/null, got {v!r}")
    rates = _rates()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    processed, changes, flagged = [], [], []
    with get_dict_cursor() as cur:
        for item in items:
            k = _key(item)
            if k in done:
                print(f"skip {k}: already reviewed")
                continue
            cur.execute(f"SELECT id, movie_name, release_date, language, budget::float, revenue::float, "
                        f"hit_flop_avg FROM {TABLE} WHERE id=%s AND movie_name=%s", k)
            rows = cur.fetchall()
            if not rows:
                sys.exit(f"{k} not in {TABLE}")
            if len({(r["budget"], r["revenue"], r["hit_flop_avg"]) for r in rows}) > 1:
                sys.exit(f"{k}: {len(rows)} rows with different values; resolve by hand")
            row = rows[0]
            y, mth, d = (int(x) for x in row["release_date"][:10].split("-"))  # tolerate '2026-07-3'
            rate = nearest_rate(f"{y:04d}-{mth:02d}-{d:02d}", rates)
            src, notes = item.get("sources") or {}, item.get("notes") or ""
            scope = item.get("revenue_scope")
            base = {"id": row["id"], "movie_name": row["movie_name"], "release_date": row["release_date"],
                    "run": run, "batch": batch}
            statuses, sets = {}, {}
            for kind in ("budget", "revenue"):
                usd, st, inr = _usd(item, kind, rate)
                if usd is None:
                    statuses[kind] = st  # not_found | no_rate
                    continue
                sc = scope if kind == "revenue" else None
                status, diff, reason = _decide(kind, row[kind], usd, sc)
                statuses[kind] = status
                common = {"inr_value": inr if inr is not None else "",
                          "rate_date": rate[1] if inr is not None else "",
                          "source": src.get(kind, ""), "pct_diff": "" if diff is None else round(diff * 100, 1)}
                if status == "flagged":
                    flagged.append({**base, **common, "field": kind, "stored": row[kind], "sourced": usd,
                                    "scope": sc or "", "reason": reason, "notes": notes})
                elif status == "updated":
                    sets[kind] = usd
                    changes.append({**base, **common, "field": kind, "old_value": row[kind], "new_value": usd,
                                    "rate": rate[0] if inr is not None else "",
                                    "notes": "; ".join(x for x in (f"scope={sc}" if sc else "", notes) if x)})
            stored_v = (row["hit_flop_avg"] or "").lower() or None
            verdict = (item.get("verdict") or "").lower() or None
            if verdict is None:
                statuses["verdict"] = "not_found"
            elif verdict == stored_v:
                statuses["verdict"] = "confirmed"
            else:
                statuses["verdict"] = "updated"
                sets["hit_flop_avg"] = verdict
                changes.append({**base, "field": "hit_flop_avg", "old_value": row["hit_flop_avg"],
                                "new_value": verdict, "pct_diff": "", "inr_value": "", "rate": "",
                                "rate_date": "", "source": src.get("verdict", ""),
                                "notes": f"label={item.get('verdict_label', '')}; {notes}"})
            if item.get("verdict_flag"):
                statuses["verdict"] += "+flagged"
                flagged.append({**base, "field": "hit_flop_avg", "stored": row["hit_flop_avg"],
                                "sourced": verdict or "", "pct_diff": "", "inr_value": "", "rate_date": "",
                                "scope": "", "reason": item["verdict_flag"], "source": src.get("verdict", ""),
                                "notes": notes})
            # revenue_budget_ratio is a generated column. Trigger trg_india_movies_hit_flop_avg
            # re-derives hit_flop_avg from the ratio whenever budget/revenue change in an UPDATE
            # that does not also change the label. So: write the figures first, then the
            # sourced verdict (or the confirmed one) in a second statement, which the trigger
            # keeps; with no sourced verdict, keep (and log) what the trigger derived.
            money = {c: v for c, v in sets.items() if c in ("budget", "revenue")}
            if money:
                cols = ", ".join(f"{c}=%s" for c in money)
                cur.execute(f"UPDATE {TABLE} SET {cols} WHERE id=%s AND movie_name=%s", (*money.values(), *k))
            want = verdict  # sourced verdict (confirmed or new), else None
            if want is not None:
                cur.execute(f"UPDATE {TABLE} SET hit_flop_avg=%s WHERE id=%s AND movie_name=%s "
                            f"AND hit_flop_avg IS DISTINCT FROM %s", (want, *k, want))
            elif money:
                cur.execute(f"SELECT hit_flop_avg FROM {TABLE} WHERE id=%s AND movie_name=%s", k)
                derived = cur.fetchone()["hit_flop_avg"]
                if (derived or "").lower() != (stored_v or ""):
                    statuses["verdict"] = statuses["verdict"].replace("not_found", "trigger")
                    changes.append({**base, "field": "hit_flop_avg", "old_value": row["hit_flop_avg"],
                                    "new_value": derived, "pct_diff": "", "inr_value": "", "rate": "",
                                    "rate_date": "", "source": "",
                                    "notes": "re-derived by trg_india_movies_hit_flop_avg from the new "
                                             "revenue/budget ratio (no sourced verdict)"})
            processed.append({**base, "language": row["language"], "budget_status": statuses["budget"],
                              "revenue_status": statuses["revenue"], "verdict_status": statuses["verdict"],
                              "reviewed_at": now})
    _append(PROCESSED, PROCESSED_COLS, processed)
    _append(CHANGES, CHANGES_COLS, changes)
    _append(FLAGGED, FLAGGED_COLS, flagged)
    for c in changes:
        extra = f"  (₹{float(c['inr_value']) / CRORE:g} Cr @ {c['rate']:.6f} on {c['rate_date']})" \
            if c["inr_value"] not in ("", None) else ""
        print(f"  UPDATED {c['id']} {c['movie_name']}: {c['field']} {c['old_value']} -> {c['new_value']}"
              f"{' (' + str(c['pct_diff']) + '%)' if c['pct_diff'] != '' else ''}{extra}")
    for f in flagged:
        print(f"  FLAGGED {f['id']} {f['movie_name']}: {f['field']} stored {f['stored']} vs {f['sourced']} — {f['reason']}")
    print(f"run {run} batch {batch}: {len(processed)} reviewed, {len(changes)} updates, {len(flagged)} flags")


def cmd_normalise() -> None:
    rows = fetch_all(f"SELECT id, movie_name, release_date, hit_flop_avg FROM {TABLE} "
                     f"WHERE hit_flop_avg IS NOT NULL AND hit_flop_avg <> lower(hit_flop_avg)")
    if not rows:
        print("nothing to normalise")
        return
    execute(f"UPDATE {TABLE} SET hit_flop_avg = lower(hit_flop_avg) "
            f"WHERE hit_flop_avg IS NOT NULL AND hit_flop_avg <> lower(hit_flop_avg)")
    _append(CHANGES, CHANGES_COLS, [
        {"id": r["id"], "movie_name": r["movie_name"], "release_date": r["release_date"], "field": "hit_flop_avg",
         "old_value": r["hit_flop_avg"], "new_value": r["hit_flop_avg"].lower(), "pct_diff": "", "inr_value": "",
         "rate": "", "rate_date": "", "source": "", "notes": "case normalised", "run": 0, "batch": 0}
        for r in rows])
    print(f"normalised {len(rows)} verdicts")


def cmd_status() -> None:
    p, c, f = _read(PROCESSED), _read(CHANGES), _read(FLAGGED)
    total = fetch_all(f"SELECT count(*) AS n FROM (SELECT DISTINCT id, movie_name FROM {TABLE}) t")[0]["n"]
    runs = sorted({int(r["run"]) for r in p})
    print(f"reviewed {len(p)}/{total} movies over runs {runs or '-'}; {len(c)} updates; {len(f)} flags")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("next")
    n.add_argument("--run", type=int)
    a = sub.add_parser("apply")
    a.add_argument("path", type=Path)
    a.add_argument("--run", type=int, required=True)
    a.add_argument("--batch", type=int, required=True)
    sub.add_parser("normalise-verdicts")
    sub.add_parser("status")
    args = ap.parse_args()
    DIR.mkdir(parents=True, exist_ok=True)
    if args.cmd == "next":
        cmd_next(args.run)
    elif args.cmd == "apply":
        cmd_apply(args.path, args.run, args.batch)
    elif args.cmd == "normalise-verdicts":
        cmd_normalise()
    else:
        cmd_status()


if __name__ == "__main__":
    main()
