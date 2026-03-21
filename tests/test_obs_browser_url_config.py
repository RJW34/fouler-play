#!/usr/bin/env python3

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

serve_obs_page = __import__("streaming.serve_obs_page", fromlist=["serve_obs_page"])
scene_builder = __import__("scripts.build_obs_hybrid_scene_collection", fromlist=["build_obs_hybrid_scene_collection"])


def test_obs_browser_base_prefers_explicit_override():
    env = {
        "OBS_BROWSER_BASE_URL": "http://192.168.1.181:8777",
        "OBS_IDLE_URL": "http://localhost:8777/idle",
    }

    assert serve_obs_page._resolve_obs_browser_base_url(env, default_port=8777) == "http://192.168.1.181:8777"
    assert serve_obs_page._resolve_obs_idle_url(env) == "http://192.168.1.181:8777/idle"


def test_obs_browser_base_falls_back_to_legacy_idle_url():
    env = {"OBS_IDLE_URL": "http://localhost:8777/idle"}

    assert serve_obs_page._resolve_obs_browser_base_url(env, default_port=8777) == "http://localhost:8777"
    assert serve_obs_page._resolve_obs_idle_url(env) == "http://localhost:8777/idle"


def test_builder_fallback_matches_runtime_lan_base(monkeypatch):
    monkeypatch.delenv("OBS_BROWSER_BASE_URL", raising=False)
    monkeypatch.delenv("OBS_IDLE_URL", raising=False)
    monkeypatch.setattr(scene_builder, "_detect_lan_ip", lambda: "192.168.1.181")

    assert scene_builder._resolve_base_url(8777, env={}) == "http://192.168.1.181:8777"


def test_build_collection_uses_obs_reachable_base_url():
    data = {
        "sources": [
            {"id": "browser_source", "name": "Battle Slot 1", "settings": {}},
            {"id": "browser_source", "name": "Stats Overlay", "settings": {}},
            {"id": "browser_source", "name": "Debug Overlay", "settings": {}},
        ]
    }

    out = scene_builder.build_collection(data, "Hybrid Test", "http://192.168.1.181:8777")
    sources = {source["name"]: source["settings"] for source in out["sources"]}

    assert sources["Battle Slot 1"]["url"] == "http://192.168.1.181:8777/idle"
    assert sources["Stats Overlay"]["url"] == "http://192.168.1.181:8777/overlay/hybrid"
    assert sources["Debug Overlay"]["url"] == "http://192.168.1.181:8777/dashboard/hybrid"


def test_obs_force_refresh_pause_config_still_defined(monkeypatch):
    class DummyObsClient:
        def __init__(self):
            self.urls = []

        async def set_browser_source_url(self, source_name, url):
            self.urls.append((source_name, url))
            return True

    dummy = DummyObsClient()
    monkeypatch.setattr(serve_obs_page, "_obs_client", dummy)
    monkeypatch.setattr(serve_obs_page, "_obs_sources", ["Battle Slot 1"])
    monkeypatch.setattr(serve_obs_page, "_last_obs_ids", {})
    monkeypatch.setattr(serve_obs_page, "_last_obs_urls", {})
    monkeypatch.setattr(serve_obs_page, "_last_obs_updates", {})
    monkeypatch.setattr(serve_obs_page, "_last_obs_status", {})
    monkeypatch.setattr(serve_obs_page, "OBS_FORCE_REFRESH", True)
    monkeypatch.setattr(serve_obs_page, "OBS_REFRESH_PAUSE_MS", 0)
    monkeypatch.setattr(serve_obs_page, "OBS_IDLE_URL", "http://192.168.1.181:8777/idle")

    asyncio.run(
        serve_obs_page.maybe_update_obs_sources(
            {"battles": [{"id": "battle-gen9ou-123", "slot": 1}]}
        )
    )

    assert len(dummy.urls) == 2
    assert dummy.urls[0][1].startswith("http://192.168.1.181:8777/idle?")
    assert dummy.urls[1][1].startswith("https://play.pokemonshowdown.com/battle-gen9ou-123")
