"""Build a local index of Box Office India movie pages from its public sitemap.

One request at a time with a pause (robots.txt allows /movie). Resumable: ids already in
the index are skipped. Output: data_review/prev_movies_box_office/boi_index.csv

    venv/bin/python scripts/boi_index.py            # crawl / resume
    venv/bin/python scripts/boi_index.py find Toss  # look a title up in the index
"""
import csv
import html
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data_review/prev_movies_box_office/boi_index.csv"
SITEMAP = "https://boxofficeindia.com/sitemap?type=movies&p=1"
PAGE = "https://boxofficeindia.com/movie.php?movieid={}"
UA = "AuraPredictorDataReview/1.0 (research script)"
FIELDS = ["movieid", "title", "year", "language", "verdict", "release_date", "budget", "first_day",
          "first_weekend", "total_nett", "india_gross", "overseas_gross", "worldwide_gross", "url"]
PAUSE = 2.0
socket.setdefaulttimeout(45)  # run 22: a request hung for 18+ min despite urlopen(timeout=30)


def get(url: str) -> str | None:
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429, 500, 502, 503, 504):
                time.sleep(30 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(30 * (attempt + 1))
    raise SystemExit(f"giving up on {url} after repeated errors")


def _text(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _field(s: str, label: str) -> str:
    if label == "Release Date":
        m = re.search(r"Release Date:\s*(\d{1,2} \w{3} \d{4})", s)
        return m.group(1) if m else ""
    m = re.search(re.escape(label) + r"\s*:\s*(\$?[\d,.]+|\$?-+|N\.?A\.?)", s)
    return m.group(1).strip() if m else ""


def parse(movieid: int, page: str) -> dict:
    s = _text(page)
    if "Cloudflare" in s[:300] and "Attention Required" in s[:300]:
        raise SystemExit("Cloudflare challenge page returned — stopping (do not work around it)")
    head = re.search(r"^\s*(.*?) Box Office Collection \| Box Office India", s)
    full = head.group(1).strip() if head else ""
    m = re.match(r"(.*) \((\d{4})\) (\S+)$", full)
    title, year, lang = (m.group(1), m.group(2), m.group(3)) if m else (full, "", "")
    verdict = ""
    i = s.find(" Release Date:")
    if i > 0 and title:
        pre = s[max(0, i - 250):i]
        j = pre.rfind(" " + title)
        if j > 0:
            v = pre[:j].split("Advertisement")[-1].strip()
            verdict = v if len(v) <= 30 else ""
    return {"movieid": movieid, "title": title, "year": year, "language": lang, "verdict": verdict,
            "release_date": _field(s, "Release Date"),
            "budget": _field(s, "Budget"), "first_day": _field(s, "First Day"),
            "first_weekend": _field(s, "First Weekend"), "total_nett": _field(s, "Total Nett Gross"),
            "india_gross": _field(s, "India Gross"), "overseas_gross": _field(s, "Overseas Gross"),
            "worldwide_gross": _field(s, "Worldwide Gross"), "url": PAGE.format(movieid)}


def crawl() -> None:
    ids = sorted({int(x) for x in re.findall(r"movieid=(\d+)", get(SITEMAP) or "")})
    done = set()
    if OUT.exists():
        done = {int(r["movieid"]) for r in csv.DictReader(OUT.open())}
    todo = [i for i in ids if i not in done]
    print(f"{len(ids)} ids in sitemap, {len(done)} indexed, {len(todo)} to fetch", flush=True)
    new = not OUT.exists()
    with OUT.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for n, mid in enumerate(todo, 1):
            page = get(PAGE.format(mid))
            row = parse(mid, page) if page else {"movieid": mid, "url": PAGE.format(mid)}
            w.writerow(row)
            fh.flush()
            if n % 250 == 0:
                print(f"{n}/{len(todo)} fetched (last: {mid} {row.get('title')} {row.get('year')})", flush=True)
            time.sleep(PAUSE)
    print("done", flush=True)


def find(q: str) -> None:
    norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
    for r in csv.DictReader(OUT.open()):
        if norm(q) in norm(r["title"] or ""):
            print(r)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "find":
        find(" ".join(sys.argv[2:]))
    else:
        crawl()
