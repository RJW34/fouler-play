from __future__ import annotations

import importlib


def _load_state_store(monkeypatch, tmp_path):
    from streaming import state_store

    module = importlib.reload(state_store)
    monkeypatch.setattr(module, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(module, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(module, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")
    monkeypatch.setattr(module, "NEXT_FIX_PATH", tmp_path / "next_fix.txt")
    monkeypatch.setattr(
        module,
        "STATE_STORE_WRITE_FAILURE_PATH",
        tmp_path / "devstream" / "truth" / "state-store-write-failure.json",
    )
    return module


def test_write_active_battles_syncs_stream_status_for_file_consumers(monkeypatch, tmp_path):
    state_store = _load_state_store(monkeypatch, tmp_path)
    state_store.write_status({"wins": 7, "losses": 4, "elo": 1355})

    state_store.write_active_battles(
        {
            "battles": [
                {
                    "id": "battle-gen9ou-2635353458",
                    "opponent": "soumatou_story",
                    "status": "active",
                }
            ],
            "count": 1,
        }
    )

    status = state_store.read_status()
    assert status["status"] == "Active"
    assert status["battle_info"] == "vs soumatou_story"
    assert status["active_battles"] == ["battle-gen9ou-2635353458"]
    assert status["wins"] == 7
    assert status["losses"] == 4
    assert status["elo"] == 1355


def test_write_active_battles_empty_syncs_searching_without_clearing_stats(monkeypatch, tmp_path):
    state_store = _load_state_store(monkeypatch, tmp_path)
    state_store.write_status({"wins": 8, "losses": 5, "status": "Active", "battle_info": "vs player"})

    state_store.write_active_battles({"battles": [], "count": 0})

    status = state_store.read_status()
    assert status["status"] == "Searching"
    assert status["battle_info"] == "Searching..."
    assert status["active_battles"] == []
    assert status["wins"] == 8
    assert status["losses"] == 5


def test_write_active_battles_empty_preserves_bounded_session_ready(monkeypatch, tmp_path):
    state_store = _load_state_store(monkeypatch, tmp_path)
    state_store.write_status(
        {
            "status": "Searching",
            "battle_info": "Searching...",
            "runtime_mode": "bounded_session_complete",
        }
    )

    state_store.write_active_battles({"battles": [], "count": 0})

    status = state_store.read_status()
    assert status["status"] == "Ready"
    assert status["battle_info"] == (
        "Bounded session complete; ready for the next finite batch."
    )
    assert status["runtime_mode"] == "bounded_session_complete"


def test_write_active_battles_does_not_clear_runtime_blocked_empty_state(monkeypatch, tmp_path):
    state_store = _load_state_store(monkeypatch, tmp_path)
    state_store.write_status(
        {
            "status": "Credential blocked",
            "battle_info": "Showdown login failed",
            "runtime_blocked": True,
            "blocker_code": "showdown_credential_blocked",
        }
    )

    state_store.write_active_battles({"battles": [], "count": 0})

    status = state_store.read_status()
    assert status["runtime_blocked"] is True
    assert status["status"] == "Credential blocked"
    assert status["battle_info"] == "Showdown login failed"


def test_write_active_battles_keeps_default_devstream_surface_count(monkeypatch, tmp_path):
    state_store = _load_state_store(monkeypatch, tmp_path)

    state_store.write_active_battles({"battles": [], "count": 0, "max_slots": 1})

    active = state_store.read_active_battles()
    assert active["max_slots"] == 3


def test_read_active_battles_normalizes_legacy_single_slot_file(monkeypatch, tmp_path):
    state_store = _load_state_store(monkeypatch, tmp_path)
    state_store.ACTIVE_BATTLES_PATH.write_text(
        '{"battles":[],"count":0,"max_slots":1}',
        encoding="utf-8",
    )

    active = state_store.read_active_battles()
    assert active["max_slots"] == 3


def test_expected_devstream_surface_count_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("FP_EXPECTED_DEVSTREAM_BATTLE_SURFACES", "4")
    state_store = _load_state_store(monkeypatch, tmp_path)

    state_store.write_active_battles({"battles": [], "count": 0})

    active = state_store.read_active_battles()
    assert active["max_slots"] == 4
