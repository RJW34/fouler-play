#!/usr/bin/env python3
"""
Test that /state and /status reflect live active battle state.

Regression test for: Status field stuck on "Searching" during active battles.
"""

import asyncio
import json
import sys
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

    payload = serve_obs_page.build_state_payload()
    status = dict(payload["status"])

    assert status["status"] == "Active", f"Expected 'Active', got {status['status']}"
    assert "TestOpponent" in status["battle_info"], f"Expected opponent in battle_info, got {status['battle_info']}"
    status_response = asyncio.run(serve_obs_page.handle_status(None))
    status_payload = json.loads(status_response.text)
    assert status_payload["status"] == "Active", f"Expected /status to be 'Active', got {status_payload['status']}"
    assert "TestOpponent" in status_payload["battle_info"], (
        f"Expected /status battle_info to include opponent, got {status_payload['battle_info']}"
    )

    # Test empty battles
    state_store.write_active_battles({"battles": [], "count": 0})
    status = dict(serve_obs_page.build_state_payload()["status"])
    assert status["status"] == "Searching", f"Expected 'Searching', got {status['status']}"
    assert status["battle_info"] == "Searching...", f"Expected 'Searching...', got {status['battle_info']}"
    status_response = asyncio.run(serve_obs_page.handle_status(None))
    status_payload = json.loads(status_response.text)
    assert status_payload["status"] == "Searching", (
        f"Expected /status to be 'Searching', got {status_payload['status']}"
    )
    assert status_payload["battle_info"] == "Searching...", (
        f"Expected /status battle_info to be 'Searching...', got {status_payload['battle_info']}"
    )

    print("All status field tests passed")


if __name__ == "__main__":
    test_status_reflects_active_battles()
