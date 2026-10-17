"""Loads DB connection string and third-party API credentials.

Reads config/secrets.properties (gitignored, copy of config/secrets.example.properties)
if present; falls back to environment variables for any key not found there. Never
hardcode credentials into a script -- same rule AuraService/python-batch-jobs follows
for AURA_DB_URL.
"""

from __future__ import annotations

import os
from pathlib import Path

_SECRETS_PATH = Path(__file__).resolve().parent.parent / "config" / "secrets.properties"

_KEY_TO_ENV_VAR = {
    "aura.db.url": "AURA_DB_URL",
    "tmdb.api.read.access.token": "TMDB_API_READ_ACCESS_TOKEN",
    "tmdb.api.key": "TMDB_API_KEY",
    "youtube.api.key": "YOUTUBE_API_KEY",
}

# Headroom past the 7 (youtube.api.key.2 .. .7) actually configured as of
# 2026-09-18 -- a numbered key just needs adding to secrets.properties (or
# YOUTUBE_API_KEY_<n>) to be picked up, no code change.
_MAX_YOUTUBE_API_KEYS = 20


def _read_properties_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


class Config:
    def __init__(self, properties: dict[str, str]):
        self._properties = properties

    def get(self, properties_key: str, *, required: bool = False) -> str | None:
        value = self._properties.get(properties_key)
        if not value:
            env_var = _KEY_TO_ENV_VAR.get(properties_key)
            value = os.environ.get(env_var) if env_var else None
        if required and not value:
            raise RuntimeError(
                f"Missing required config '{properties_key}': set it in "
                f"config/secrets.properties or the {_KEY_TO_ENV_VAR.get(properties_key)} "
                "environment variable."
            )
        return value or None

    @property
    def db_url(self) -> str:
        return self.get("aura.db.url", required=True)

    @property
    def tmdb_read_access_token(self) -> str | None:
        return self.get("tmdb.api.read.access.token")

    @property
    def tmdb_api_key(self) -> str | None:
        return self.get("tmdb.api.key")

    @property
    def youtube_api_key(self) -> str | None:
        return self.get("youtube.api.key")

    @property
    def youtube_api_keys(self) -> list[str]:
        """Every configured YouTube Data API key, in order: youtube.api.key
        (YOUTUBE_API_KEY), then youtube.api.key.2 (YOUTUBE_API_KEY_2), .3, etc,
        for however many are actually set. Each Google Cloud project has its
        own independent search.list quota (confirmed 100/day per key on
        2026-09-18), so youtube_search_client.py rotates through this list --
        one key's exhaustion just moves on to the next rather than stopping the
        whole batch, multiplying the effective daily budget by key count."""
        keys = []
        first = self.get("youtube.api.key")
        if first:
            keys.append(first)
        for i in range(2, _MAX_YOUTUBE_API_KEYS + 1):
            value = self._properties.get(f"youtube.api.key.{i}") or os.environ.get(f"YOUTUBE_API_KEY_{i}")
            if value:
                keys.append(value)
        return keys


def load_config() -> Config:
    return Config(_read_properties_file(_SECRETS_PATH))
