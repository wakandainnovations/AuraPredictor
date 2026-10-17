"""Fallback trailer-video resolution for movies TMDB simply has no video entry
for -- true for a large share of regional Indian catalog titles (TMDB's videos
endpoint is thin outside major-market releases). enrich_movies.py's TMDB pass
already tried and left these rows with youtube_last_checked set but no matching
row in movie_video_snapshots; this is the automated equivalent of the step a
human curator does by hand from here -- searching YouTube directly for the
trailer -- so it's still an API call, never a browser, and still writes nothing
without a same confidence check a human applies at a glance.

Quota: YouTube's search.list costs 100 units/call -- what daily budget that
actually buys varies by project (documented default is 10,000 units/day, but
the real key's limit could differ), so this doesn't guess a call-count ceiling
up front. It runs until either the movie list is exhausted or the API itself
reports the quota is gone (YouTubeQuotaExceededError, see
youtube_search_client.py) -- at which point run() stops immediately and
reports how far it got. Meant to be re-run once the quota resets (once daily,
typically midnight Pacific time) for as many days as it takes to clear a large
backlog, same idiom as the other *_scan_once flags elsewhere in this codebase
family -- resumable by construction (a movie already recorded in
movie_youtube_search_status, found or not, is never re-searched).

Confidence check, applied before ever writing a match (never guesses). Tuned
against two real false positives an early manual spot-check caught (verified via
YouTube's oEmbed endpoint) before this ever ran at scale: a "25 Day's Success
Trailer" post-release recut, and a fan "WhatsApp status" edit -- both contained
the movie's exact name and the word "trailer" and passed every check below
*except* the two that were added because of them:
  * title must contain "trailer" (case-insensitive) and none of
    NON_OFFICIAL_TITLE_MARKERS ("review", "success", "status", "whatsapp",
    "box office", "compilation", ... -- see the constant for the full list) --
    a review/recap/status-edit video's title routinely contains both the movie
    name and the word "trailer" without being the trailer itself.
  * channel name must contain none of NON_OFFICIAL_CHANNEL_MARKERS ("editz",
    "fan", "clips", ...) -- catches fan-edit/clip channels even when the title
    itself reads clean.
  * every significant word of the movie's name (punctuation/stopwords stripped)
    must appear in the candidate title -- a plain substring check, not fuzzy,
    so an unrelated same-genre video can't slip through on a partial overlap.
  * the video's publish date must fall within a plausible window of the release
    date: up to 3 years before, but only 7 days after -- a real trailer is
    essentially always published before release, so "published well after
    release" is exactly the success-trailer/recut false-positive's signature
    and is kept deliberately tight rather than matching the "before" side.
  * title must not explicitly name an Indian language OTHER than the catalog
    row's own (_conflicts_with_catalog_language) -- added after a fuller audit
    (2026-09-18) found 9/95 confirmed matches were wrong, mostly a pan-India
    film's trailer in the WRONG language: e.g. "Mark (Hindi) - Official
    Trailer" matching a catalog row whose language is Tamil. A film released
    in several languages at once genuinely has one trailer per language on
    YouTube, and a plain "<name> official trailer" search can easily surface
    the wrong one -- relevanceLanguage (passed to search.list) is only a
    ranking hint, not a filter, so _process_one also now puts the catalog
    row's own language name directly into the query TEXT
    (_language_query_term) to bias the search itself, not just filter after
    the fact. Two of those 9 had already passed an earlier *manual* oEmbed
    spot-check too -- a reminder that "is this a real official trailer"
    isn't the same check as "is this a real official trailer for THIS row",
    and that any future manual re-verification should check language too.

A movie searched with no confident candidate is recorded (found=False) in this
module's own tracking table rather than retried every run -- quota is too scarce
to re-spend on a title that's already been checked and come up empty; those stay
genuinely manual, same as before this existed.
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta
from typing import Any

from aurapredictor.config import load_config
from aurapredictor.constants import MOVIES_TABLE, is_indian_language
from aurapredictor.dates import parse_release_date
from aurapredictor.db import execute, fetch_all
from aurapredictor.enrichment import video_snapshots
from aurapredictor.enrichment.youtube_search_client import YouTubeQuotaExceededError, YouTubeSearchClient

STATUS_TABLE = "movie_youtube_search_status"

_CREATE_STATUS_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {STATUS_TABLE} (
    movie_name text NOT NULL,
    release_date text NOT NULL,
    language text NOT NULL,
    video_type text NOT NULL,
    checked_at timestamp with time zone NOT NULL,
    found boolean NOT NULL,
    matched_video_id text,
    PRIMARY KEY (movie_name, release_date, language, video_type)
)
"""

# ISO 639-1 only -- YouTube's relevanceLanguage param rejects anything else, so a
# language in constants.INDIAN_LANGUAGES with no real 639-1 code (e.g. Bhojpuri,
# Tulu, Konkani) is simply left out; search still runs, just without that nudge.
_ISO_639_1: dict[str, str] = {
    "hindi": "hi", "tamil": "ta", "telugu": "te", "malayalam": "ml", "kannada": "kn",
    "bengali": "bn", "marathi": "mr", "punjabi": "pa", "gujarati": "gu", "urdu": "ur",
    "odia": "or", "oriya": "or", "assamese": "as", "sanskrit": "sa", "kashmiri": "ks",
}

# Both lists grew directly from real false positives caught in manual spot-checks
# (verified against actual YouTube titles via the oEmbed endpoint) before this was
# ever run at scale -- a "25 Day's Success Trailer" re-cut and a fan "WhatsApp
# status" edit both contain the movie's name and the word "trailer" and would
# otherwise pass every other check. Title markers catch what the video IS (a
# recut/reaction/status edit, not a real trailer); channel markers catch WHO
# posted it (a fan-edit or clip channel, not the studio/a news outlet).
NON_OFFICIAL_TITLE_MARKERS = (
    "review", "reaction", "spoof", "parody", "fan made", "fan-made", "recreated", "spoiler",
    "status", "whatsapp", "success", "collection", "box office", "record", "celebration",
    "bgm", "ringtone", "mashup", "compilation", "climax", "audio launch", "pre release event",
    "public review", "public talk", "1st day", "day 1", "first day", "shorts",
    # A generic single-word catalog title (e.g. "Screen Bound") can collide
    # with a same-named video game -- "gameplay" is the one word that reliably
    # never appears in a real film trailer's own title.
    "gameplay",
)
NON_OFFICIAL_CHANNEL_MARKERS = ("editz", "edits", "status", "whatsapp", "fan", "clips", "shorts", "gametrailers")

_STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "for", "to"}
_WORD_RE = re.compile(r"[a-z0-9]+")

# Politeness delay between search.list calls -- YouTube publishes no documented
# rate limit tighter than the daily quota itself, but spacing calls out avoids
# bursting a shared key.
REQUEST_DELAY_SECONDS = 0.2

# A real theatrical trailer is essentially always published before release, or at
# worst days around it -- "published after release" is exactly the signature of
# the recut/success-trailer false positives above, so that side of the window is
# kept deliberately tight rather than matching the "before" side's 3-year slack.
_PUBLISH_WINDOW_BEFORE_DAYS = 3 * 365
_PUBLISH_WINDOW_AFTER_DAYS = 7


def _significant_words(name: str) -> list[str]:
    """movie_name may carry an alternate spelling after a comma (e.g. "Vilaayath
    Budha, Vilayath Budha") -- only the primary spelling before the first comma
    is used for both the search query and the match check."""
    primary = name.split(",")[0]
    words = _WORD_RE.findall(primary.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _relevance_language(language: str | None) -> str | None:
    if not language:
        return None
    first = language.split(",")[0].strip().lower()
    return _ISO_639_1.get(first)


# Names checked against a candidate's title to catch the exact failure class a
# manual audit found (9/95 confirmed matches, 2026-09-18): a pan-India film
# released in several languages at once has a trailer for EACH one on YouTube,
# and a generic "<name> official trailer" search can easily surface a
# same-movie trailer in the WRONG language -- e.g. "Mark (Hindi) - Official
# Trailer" matching a catalog row whose own language is Tamil. relevanceLanguage
# is only a ranking hint, not a filter, so it doesn't stop this by itself.
# "oriya"/"odia" are the same language under two names, normalized together.
_LANGUAGE_NAME_MARKERS = frozenset({
    "hindi", "tamil", "telugu", "malayalam", "kannada", "bengali", "marathi",
    "punjabi", "gujarati", "urdu", "odia", "oriya", "assamese",
})


def _conflicts_with_catalog_language(title_lower: str, language: str | None) -> bool:
    """True only if the title explicitly names a *different* Indian language
    than the catalog row's own -- a title naming no language at all isn't
    flagged, since there's nothing to compare (most titles don't say)."""
    catalog_lang = (language or "").split(",")[0].strip().lower()
    if catalog_lang == "oriya":
        catalog_lang = "odia"
    named = {("odia" if lang == "oriya" else lang) for lang in _LANGUAGE_NAME_MARKERS if lang in title_lower}
    return bool(named) and catalog_lang not in named


def _published_date(published_at: str | None) -> date | None:
    if not published_at:
        return None
    try:
        return date.fromisoformat(published_at[:10])
    except ValueError:
        return None


def pick_best_match(
    candidates: list[dict[str, Any]], movie_name: str, release_date: str, language: str | None = None
) -> dict[str, Any] | None:
    """Pure selection logic (no network) so it's directly unit-testable: returns
    the first candidate (search.list already orders by relevance) that passes
    every confidence check, or None if none do -- see module docstring.
    `language` is optional only so existing tests that don't care about the
    language check don't need updating -- the real caller always has one and
    should always pass it."""
    target = parse_release_date(release_date)
    if target is None:
        return None
    window_start = target - timedelta(days=_PUBLISH_WINDOW_BEFORE_DAYS)
    window_end = target + timedelta(days=_PUBLISH_WINDOW_AFTER_DAYS)

    words = _significant_words(movie_name)
    if not words:
        return None

    for candidate in candidates:
        title_lower = candidate["title"].lower()
        channel_lower = candidate.get("channel_title", "").lower()
        if "trailer" not in title_lower:
            continue
        if any(marker in title_lower for marker in NON_OFFICIAL_TITLE_MARKERS):
            continue
        if any(marker in channel_lower for marker in NON_OFFICIAL_CHANNEL_MARKERS):
            continue
        if _conflicts_with_catalog_language(title_lower, language):
            continue
        if not all(word in title_lower for word in words):
            continue
        published = _published_date(candidate.get("published_at"))
        if published is None or not (window_start <= published <= window_end):
            continue
        return candidate
    return None


def ensure_table() -> None:
    execute(_CREATE_STATUS_TABLE_SQL)


def fetch_pending_movies(limit: int) -> list[dict[str, Any]]:
    """Movies the TMDB pass already visited (youtube_last_checked set) that still
    have no trailer row in movie_video_snapshots, and that this module hasn't
    already searched (successfully or not) -- see module docstring on why a
    failed search isn't retried automatically."""
    rows = fetch_all(
        f"""
        SELECT m.movie_name, m.release_date, m.language
        FROM {MOVIES_TABLE} m
        LEFT JOIN {video_snapshots.TABLE_NAME} s
          ON s.movie_name = m.movie_name AND s.release_date = m.release_date
         AND s.language = m.language AND s.video_type = 'trailer'
        LEFT JOIN {STATUS_TABLE} t
          ON t.movie_name = m.movie_name AND t.release_date = m.release_date
         AND t.language = m.language AND t.video_type = 'trailer'
        WHERE m.youtube_last_checked IS NOT NULL
          AND s.youtube_video_id IS NULL
          AND t.checked_at IS NULL
          AND m.language IS NOT NULL
          AND m.release_date ~ '^[0-9]{{4}}'
        ORDER BY m.release_date DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [row for row in rows if is_indian_language(row["language"])]


def _record_status(movie: dict[str, Any], found: bool, matched_video_id: str | None) -> None:
    execute(
        f"""
        INSERT INTO {STATUS_TABLE}
            (movie_name, release_date, language, video_type, checked_at, found, matched_video_id)
        VALUES (%s, %s, %s, 'trailer', now(), %s, %s)
        ON CONFLICT (movie_name, release_date, language, video_type) DO UPDATE SET
            checked_at = EXCLUDED.checked_at, found = EXCLUDED.found,
            matched_video_id = EXCLUDED.matched_video_id
        """,
        (movie["movie_name"], movie["release_date"], movie["language"], found, matched_video_id),
    )


def _language_query_term(language: str | None) -> str | None:
    """The catalog row's own language name as an extra keyword in the query
    text itself, not just the relevanceLanguage ranking hint below -- biases
    the search directly toward same-language results, attacking the same
    pan-India-multi-language-trailer failure class
    _conflicts_with_catalog_language rejects after the fact. None for a
    language string that isn't a recognized name (nothing useful to add)."""
    if not language:
        return None
    first = language.split(",")[0].strip().lower()
    return first if first in _LANGUAGE_NAME_MARKERS else None


def _process_one(client: YouTubeSearchClient, movie: dict[str, Any]) -> bool:
    words = _significant_words(movie["movie_name"])
    language_term = _language_query_term(movie["language"])
    query = " ".join([" ".join(words), *([language_term] if language_term else []), "official trailer"])
    candidates = client.search_videos(query, relevance_language=_relevance_language(movie["language"]))
    time.sleep(REQUEST_DELAY_SECONDS)

    match = pick_best_match(candidates, movie["movie_name"], movie["release_date"], movie["language"])
    if match is None:
        _record_status(movie, found=False, matched_video_id=None)
        return False

    video_snapshots.upsert_video_id(
        movie["movie_name"], movie["release_date"], movie["language"],
        "trailer", match["video_id"], match["published_at"],
    )
    _record_status(movie, found=True, matched_video_id=match["video_id"])
    return True


def run(limit: int = 2000) -> tuple[int, int, bool]:
    """Returns (matched, unmatched, quota_exceeded). limit is a large default,
    not a guessed daily budget -- see youtube_search_client.py's docstring on
    why call volume isn't self-capped. The real stop signal is the API's own
    quota-exceeded response: caught here and treated as "stop the batch now",
    distinct from a per-row failure (that movie is left unrecorded, so it's
    retried -- not marked a confirmed non-match -- next run)."""
    ensure_table()
    video_snapshots.ensure_table()
    client = YouTubeSearchClient(load_config())

    movies = fetch_pending_movies(limit)
    matched = unmatched = 0
    for movie in movies:
        try:
            if _process_one(client, movie):
                matched += 1
            else:
                unmatched += 1
        except YouTubeQuotaExceededError as exc:
            print(f"  [stop] quota exhausted after {matched + unmatched} movies this run: {exc}")
            return matched, unmatched, True
        except Exception as exc:  # one bad row must never abort the whole batch
            print(f"  [skip] {movie['movie_name']} ({movie['release_date']}): {exc}")
            unmatched += 1
    return matched, unmatched, False
