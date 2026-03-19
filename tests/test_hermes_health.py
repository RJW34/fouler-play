from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infrastructure"
    / "hermes_health.py"
)
SPEC = importlib.util.spec_from_file_location("hermes_health", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_get_battle_stats_derives_live_counts_from_battles_only_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stats_path = tmp_path / "battle_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "timestamp": "2026-03-19T19:00:00+00:00",
                        "result": "win",
                    },
                    {
                        "timestamp": "2026-03-19T18:30:00+00:00",
                        "result": "loss",
                    },
                    {
                        "timestamp": "2026-03-18T17:00:00+00:00",
                        "result": "win",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(MODULE, "STATS_FILE", stats_path)
    now = datetime(2026, 3, 19, 20, 0, 0, tzinfo=UTC)

    result = MODULE.get_battle_stats(now=now)

    assert result["exists"] is True
    assert result["schema"] == "battles_list"
    assert result["win_count"] == 2
    assert result["loss_count"] == 1
    assert result["total_games"] == 3
    assert result["win_rate"] == 66.7
    assert result["recent_24h_battles"] == 2
    assert result["last_battle_at"] == "2026-03-19T19:00:00+00:00"
    assert result["last_battle_age_seconds"] == 3600.0


def test_run_health_check_uses_stream_fallback_when_pid_file_is_stale(monkeypatch) -> None:
    monkeypatch.setattr(
        MODULE,
        "assess_progress",
        lambda *args, **kwargs: {
            "stale": False,
            "reason": "freshest signal 2.5s old",
            "freshest_path": "logs/battle.log",
            "freshest_age_seconds": 2.5,
        },
    )
    monkeypatch.setattr(
        MODULE,
        "get_bot_process_state",
        lambda progress: {
            "running": True,
            "source": "process_scan",
            "pid": 70120,
            "pid_file_exists": True,
            "pid_file_stale": True,
            "processes": [{"pid": 70120, "command": "python run.py --bot-mode search_ladder"}],
        },
    )
    monkeypatch.setattr(
        MODULE,
        "get_battle_stats",
        lambda now=None: {
            "exists": True,
            "schema": "battles_list",
            "current_elo": None,
            "elo_source": "none",
            "elo_trend": "unknown",
            "win_count": 0,
            "loss_count": 0,
            "total_games": 0,
            "win_rate": 0.0,
            "recent_24h_battles": 178,
            "last_battle_at": "2026-03-19T20:08:32+00:00",
            "last_battle_age_seconds": 30.0,
        },
    )
    monkeypatch.setattr(
        MODULE,
        "get_research_status",
        lambda now=None: {"active": True, "total_entries": 10, "hours_since_last": 14.0},
    )
    monkeypatch.setattr(
        MODULE,
        "get_stream_runtime",
        lambda now=None: {
            "up": True,
            "overlay_stale": False,
            "freshest_age_seconds": 8.0,
            "active_battles": 1,
            "active_battle_ids": ["battle-gen9ou-2562798970"],
            "elo": 1185,
            "wins": 90,
            "losses": 88,
            "status_text": "Active",
            "battle_info": "vs AbSolitaire",
        },
    )

    result = MODULE.run_health_check(now=datetime(2026, 3, 19, 20, 9, 0, tzinfo=UTC))

    assert result["status"] == "healthy"
    assert result["exit_code"] == 0
    assert result["battle_stats"]["current_elo"] == 1185
    assert result["battle_stats"]["elo_source"] == "stream_status"
    assert result["battle_stats"]["win_count"] == 90
    assert result["battle_stats"]["loss_count"] == 88
    assert "stale" in result["notes"][0]


def test_run_health_check_marks_overlay_staleness_as_degraded(monkeypatch) -> None:
    monkeypatch.setattr(
        MODULE,
        "assess_progress",
        lambda *args, **kwargs: {
            "stale": False,
            "reason": "freshest signal 20.0s old",
            "freshest_path": "logs/init.log",
            "freshest_age_seconds": 20.0,
        },
    )
    monkeypatch.setattr(
        MODULE,
        "get_bot_process_state",
        lambda progress: {
            "running": True,
            "source": "pid_file",
            "pid": 12345,
            "pid_file_exists": True,
            "pid_file_stale": False,
            "processes": [],
        },
    )
    monkeypatch.setattr(
        MODULE,
        "get_battle_stats",
        lambda now=None: {
            "exists": True,
            "schema": "battles_list",
            "current_elo": 1185,
            "elo_source": "stream_status",
            "elo_trend": "stable",
            "win_count": 90,
            "loss_count": 88,
            "total_games": 178,
            "win_rate": 50.6,
            "recent_24h_battles": 178,
            "last_battle_at": "2026-03-19T20:08:32+00:00",
            "last_battle_age_seconds": 60.0,
        },
    )
    monkeypatch.setattr(
        MODULE,
        "get_research_status",
        lambda now=None: {"active": True, "total_entries": 10, "hours_since_last": 14.0},
    )
    monkeypatch.setattr(
        MODULE,
        "get_stream_runtime",
        lambda now=None: {
            "up": True,
            "overlay_stale": True,
            "freshest_age_seconds": 700.0,
            "active_battles": 0,
            "active_battle_ids": [],
            "elo": 1185,
            "wins": 90,
            "losses": 88,
            "status_text": "Searching",
            "battle_info": "Searching...",
        },
    )

    result = MODULE.run_health_check(now=datetime(2026, 3, 19, 20, 9, 0, tzinfo=UTC))

    assert result["status"] == "degraded"
    assert result["exit_code"] == 1
    assert any("overlay data is stale" in reason for reason in result["reasons"])
