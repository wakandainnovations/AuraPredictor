"""Config.youtube_api_keys -- the multi-key list youtube_search_client.py
rotates through. No file/env I/O: constructed directly from an in-memory
properties dict."""

from __future__ import annotations

from aurapredictor.config import Config


def test_collects_base_key_plus_numbered_keys_in_order():
    config = Config({
        "youtube.api.key": "k1",
        "youtube.api.key.2": "k2",
        "youtube.api.key.3": "k3",
    })
    assert config.youtube_api_keys == ["k1", "k2", "k3"]


def test_skips_gaps_in_numbering():
    """A numbered key that's blank/missing doesn't break the ones after it --
    every non-empty key still gets collected, gap or not."""
    config = Config({
        "youtube.api.key": "k1",
        "youtube.api.key.3": "k3",
    })
    assert config.youtube_api_keys == ["k1", "k3"]


def test_empty_when_no_keys_configured():
    assert Config({}).youtube_api_keys == []


def test_single_key_when_only_base_key_set():
    assert Config({"youtube.api.key": "k1"}).youtube_api_keys == ["k1"]


def test_falls_back_to_numbered_env_var(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY_2", "from-env")
    config = Config({"youtube.api.key": "k1"})
    assert config.youtube_api_keys == ["k1", "from-env"]
