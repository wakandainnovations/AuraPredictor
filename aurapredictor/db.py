"""Thin psycopg2 connection helper for the shared `aura` Postgres DB."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from aurapredictor.config import load_config


@contextmanager
def get_connection() -> Iterator[psycopg2.extensions.connection]:
    config = load_config()
    conn = psycopg2.connect(config.db_url)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_dict_cursor() -> Iterator[psycopg2.extras.RealDictCursor]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
            conn.commit()


def fetch_all(query: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with get_dict_cursor() as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def execute(query: str, params: tuple[Any, ...] | None = None) -> None:
    with get_dict_cursor() as cur:
        cur.execute(query, params)


def execute_many(query: str, param_list: list[tuple[Any, ...]]) -> None:
    if not param_list:
        return
    with get_dict_cursor() as cur:
        psycopg2.extras.execute_values(cur, query, param_list)
