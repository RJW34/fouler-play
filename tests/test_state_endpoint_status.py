#!/usr/bin/env python3
"""
Test that /state endpoint status field reflects active battle state.

Regression test for: Status field stuck on "Searching" during active battles.
"""

import sys
import asyncio
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

state_store = __import__("streaming.state_store", fromlist=["state_store"])
serve_obs_page = __import__("streaming.serve_obs_page", fromlist=["serve_obs_page"])


def test_status_reflects_active_battles():
    """Status should be 'Active' when battles exist, 'Searching' when empty."""
    
    # Mock active battle data
    active_battle = {
        "battles": [
            {
                "id": "battle-gen9ou-12345678",
                "opponent": "TestOpponent",
                "url": "https://play.pokemonshowdown.com/battle-gen9ou-12345678",
                "started": "2026-02-08T20:00:00.000000",
                "worker_id": 0,
                "status": "active",
                "players": ["BugInTheCode", "TestOpponent"],
                "slot": 1,
            }
        ],
        "count": 1,
    }
    
    # Write test battle data
    state_store.write_active_battles(active_battle)
    
    # Simulate server logic (from serve_obs_page.py build_state_payload)
    status = state_store.read_status()
    battles_data = state_store.read_active_battles()
    battles = battles_data.get("battles", [])
    
    # Apply status update logic
    if battles:
        status["status"] = "Active"
        status["battle_info"] = ", ".join(
            f"vs {b.get('opponent', 'Unknown')}" for b in battles
        )
    else:
        if status.get("status") in ("Active", "Battling"):
            status["status"] = "Searching"
            status["battle_info"] = "Searching..."
    
    assert status["status"] == "Active", f"Expected 'Active', got {status['status']}"
    assert "TestOpponent" in status["battle_info"], f"Expected opponent in battle_info, got {status['battle_info']}"
    
    # Test empty battles
    state_store.write_active_battles({"battles": [], "count": 0})
    battles_data = state_store.read_active_battles()
    battles = battles_data.get("battles", [])
    
    if battles:
        status["status"] = "Active"
    else:
        if status.get("status") in ("Active", "Battling"):
            status["status"] = "Searching"
            status["battle_info"] = "Searching..."
    
    assert status["status"] == "Searching", f"Expected 'Searching', got {status['status']}"
    assert status["battle_info"] == "Searching...", f"Expected 'Searching...', got {status['battle_info']}"
    
    print("All status field tests passed")


if __name__ == "__main__":
    test_status_reflects_active_battles()


def test_status_endpoint_reflects_active_battles(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")

    state_store.write_status({"status": "Searching", "battle_info": "Searching..."})
    state_store.update_daily_stats(0, 0)
    state_store.write_active_battles({
        "battles": [
            {
                "id": "battle-gen9ou-12345678",
                "opponent": "TestOpponent",
                "url": "https://play.pokemonshowdown.com/battle-gen9ou-12345678",
                "started": "2026-02-08T20:00:00.000000",
                "worker_id": 0,
                "status": "active",
                "players": ["BugInTheCode", "TestOpponent"],
                "slot": 1,
            }
        ],
        "count": 1,
    })

    response = asyncio.run(serve_obs_page.handle_status(None))
    payload = json.loads(response.text)

    assert payload["status"] == "Active"
    assert payload["active_battles"] == ["battle-gen9ou-12345678"]
    assert payload["battle_info"] == "vs TestOpponent"


def test_status_endpoint_clears_stale_battle_info_when_searching(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")

    state_store.write_status({"status": "Searching", "battle_info": "vs stale-opponent"})
    state_store.update_daily_stats(0, 0)
    state_store.write_active_battles({"battles": [], "count": 0})

    response = asyncio.run(serve_obs_page.handle_status(None))
    payload = json.loads(response.text)

    assert payload["status"] == "Searching"
    assert payload["battle_info"] == "Searching..."
    assert payload["active_battles"] == []


def test_state_payload_filters_ladder_cache_to_active_account(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")
    monkeypatch.setattr(
        serve_obs_page,
        "recent_showdown_credential_failure",
        lambda _root: {"found": False},
    )
    monkeypatch.setitem(
        serve_obs_page._ladder_cache,
        "accounts",
        {"npctypebeat": 1029, "LEBOTJAMESXD00N": 1143},
    )
    monkeypatch.setitem(serve_obs_page._ladder_cache, "updated", 123.0)

    state_store.write_status({
        "status": "Searching",
        "battle_info": "Searching...",
        "elo": 1029,
        "elo_source": "showdown",
        "accounts_elo": {"npctypebeat": 1029},
    })
    state_store.update_daily_stats(0, 0)
    state_store.write_active_battles({
        "battles": [
            {
                "id": "battle-gen9ou-2632460352",
                "opponent": "killerpanda16",
                "players": ["LEBOTJAMESXD00N", "killerpanda16"],
                "status": "active",
                "slot": 1,
            }
        ],
        "count": 1,
    })

    payload = serve_obs_page.build_state_payload()

    assert payload["status"]["status"] == "Active"
    assert payload["status"]["battle_info"] == "vs killerpanda16"
    assert payload["status"]["elo"] == 1143
    assert payload["accounts_elo"] == {"LEBOTJAMESXD00N": 1143}
    assert payload["status"]["accounts_elo"] == {"LEBOTJAMESXD00N": 1143}


def test_state_payload_suppresses_stale_elo_when_active_account_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")
    monkeypatch.setattr(
        serve_obs_page,
        "recent_showdown_credential_failure",
        lambda _root: {"found": False},
    )
    monkeypatch.setitem(serve_obs_page._ladder_cache, "accounts", {"npctypebeat": 1029})
    monkeypatch.setitem(serve_obs_page._ladder_cache, "updated", 123.0)

    state_store.write_status({
        "status": "Searching",
        "battle_info": "Searching...",
        "elo": 1029,
        "elo_source": "showdown",
        "accounts_elo": {"npctypebeat": 1029},
    })
    state_store.update_daily_stats(0, 0)
    state_store.write_active_battles({
        "battles": [
            {
                "id": "battle-gen9ou-2632460352",
                "opponent": "killerpanda16",
                "players": ["LEBOTJAMESXD00N", "killerpanda16"],
                "status": "active",
                "slot": 1,
            }
        ],
        "count": 1,
    })

    payload = serve_obs_page.build_state_payload()

    assert payload["status"]["elo"] == "---"
    assert "elo_source" not in payload["status"]
    assert payload["accounts_elo"] == {}
    assert payload["status"]["accounts_elo"] == {}


def test_active_battle_atomic_write_retries_windows_replace_lock(tmp_path, monkeypatch):
    active_path = tmp_path / "active_battles.json"
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", active_path)
    monkeypatch.setattr(state_store, "STATE_STORE_WRITE_FAILURE_PATH", tmp_path / "devstream" / "truth" / "state-store-write-failure.json")
    monkeypatch.setattr(state_store.time, "sleep", lambda _seconds: None)

    calls = {"count": 0}
    original_replace = state_store.os.replace

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("simulated Windows file lock")
        return original_replace(src, dst)

    monkeypatch.setattr(state_store.os, "replace", flaky_replace)

    state_store.write_active_battles({"battles": [], "count": 0})

    assert calls["count"] == 2
    assert json.loads(active_path.read_text(encoding="utf-8"))["count"] == 0
    assert not (tmp_path / "active_battles.json.tmp").exists()
    assert not state_store.STATE_STORE_WRITE_FAILURE_PATH.exists()
