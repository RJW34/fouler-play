import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import devstream_health


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_optional_stale_analytics_do_not_fail_runtime_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 0, "losses": 0})
    stale = tmp_path / "stability_report.json"
    _write_json(stale, {"generated_at": "old"})
    old = time.time() - 90000
    os.utime(stale, (old, old))

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is True
    assert payload["readiness"]["runtimeReady"] is True
    assert payload["readiness"]["analyticsFresh"] is False
    assert "stale truth file: stability_report.json" in payload["warnings"]
    assert not payload["blockers"]


def test_active_slot_readiness_uses_local_state_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {"battle_id": "battle-gen9ou-1"} if path == "/slot/1/state" else {},
        },
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_showdown_battle_title",
        lambda battle_id: {
            "url": devstream_health.showdown_battle_url(battle_id),
            "ok": False,
            "statusCode": 200,
            "title": "Showdown!",
        },
    )

    _write_json(
        tmp_path / "active_battles.json",
        {"battles": [{"id": "battle-gen9ou-1", "slot": 1, "opponent": "Opponent"}], "count": 1},
    )
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 0, "losses": 0})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is True
    assert payload["readiness"]["streamReady"] is True
    assert payload["slotReadiness"]["checks"][0]["localStateOk"] is True
    assert payload["slotReadiness"]["checks"][0]["showdownPage"]["title"] == "Showdown!"
    assert not payload["blockers"]


def test_active_slot_blocks_when_local_state_does_not_match_active_battle(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {"battle_id": None} if path == "/slot/1/state" else {},
        },
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_showdown_battle_title",
        lambda battle_id: {"url": devstream_health.showdown_battle_url(battle_id), "ok": False, "title": "Showdown!"},
    )

    _write_json(
        tmp_path / "active_battles.json",
        {"battles": [{"id": "battle-gen9ou-1", "slot": 1, "opponent": "Opponent"}], "count": 1},
    )
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 0, "losses": 0})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is False
    assert payload["readiness"]["streamReady"] is False
    assert any("slot 1 is not battle-ready" in blocker for blocker in payload["blockers"])
