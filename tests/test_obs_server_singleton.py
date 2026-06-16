from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from streaming import state_store
from streaming import serve_obs_page


def test_same_repo_obs_server_duplicate_is_detected() -> None:
    current = os.getpid()
    repo = str(serve_obs_page.ROOT_DIR)
    rows = [
        {"pid": current, "ppid": os.getppid(), "command": "python streaming/serve_obs_page.py"},
        {
            "pid": 424242,
            "ppid": 1,
            "command": f'py -3 "{repo}\\streaming\\serve_obs_page.py"',
        },
    ]

    duplicates = serve_obs_page._find_duplicate_obs_servers(rows)

    assert [process["pid"] for process in duplicates] == [424242]


def test_other_repo_obs_server_is_not_a_duplicate() -> None:
    current = os.getpid()
    rows = [
        {"pid": current, "ppid": os.getppid(), "command": "python streaming/serve_obs_page.py"},
        {
            "pid": 424242,
            "ppid": 1,
            "command": r'py -3 "D:\OtherRepo\streaming\serve_obs_page.py"',
        },
    ]

    assert serve_obs_page._find_duplicate_obs_servers(rows) == []


def test_scheduled_task_cmd_wrapper_is_not_a_duplicate() -> None:
    current = os.getpid()
    repo = str(serve_obs_page.ROOT_DIR)
    rows = [
        {"pid": current, "ppid": os.getppid(), "command": "python streaming/serve_obs_page.py"},
        {
            "pid": 424242,
            "ppid": 1,
            "command": (
                f'"C:\\Windows\\System32\\cmd.exe" /d /c ""{repo}\\.venv\\Scripts\\python.exe" '
                f'"streaming\\serve_obs_page.py" 1>>"{repo}\\logs\\jigglypuff-obs-server.log" '
                f'2>>"{repo}\\logs\\jigglypuff-obs-server.err.log""'
            ),
        },
    ]

    assert serve_obs_page._find_duplicate_obs_servers(rows) == []


def test_acquire_singleton_removes_stale_pid_file(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "obs_server.pid"
    pid_file.write_text(json.dumps({"pid": 999999, "name": "obs_server"}), encoding="utf-8")
    monkeypatch.setattr(serve_obs_page, "PID_FILE", pid_file)
    monkeypatch.setattr(serve_obs_page, "_pid_exists", lambda _pid: False)
    monkeypatch.setattr(serve_obs_page, "_collect_process_rows", lambda: [])
    monkeypatch.delenv("FOULER_OBS_SERVER_ALLOW_DUPLICATE", raising=False)

    serve_obs_page._acquire_singleton_or_exit()

    data = json.loads(pid_file.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["name"] == "obs_server"


def test_acquire_singleton_refuses_live_pid_file(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "obs_server.pid"
    pid_file.write_text(json.dumps({"pid": 12345, "name": "obs_server"}), encoding="utf-8")
    monkeypatch.setattr(serve_obs_page, "PID_FILE", pid_file)
    monkeypatch.setattr(serve_obs_page, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(
        serve_obs_page,
        "_collect_process_rows",
        lambda: [
            {
                "pid": 12345,
                "ppid": 1,
                "command": f'py -3 "{serve_obs_page.ROOT_DIR}\\streaming\\serve_obs_page.py"',
            }
        ],
    )
    monkeypatch.delenv("FOULER_OBS_SERVER_ALLOW_DUPLICATE", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        serve_obs_page._acquire_singleton_or_exit()

    assert exc_info.value.code == 78


def test_acquire_singleton_replaces_pid_file_reused_by_unrelated_process(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "obs_server.pid"
    pid_file.write_text(json.dumps({"pid": 12345, "name": "obs_server"}), encoding="utf-8")
    monkeypatch.setattr(serve_obs_page, "PID_FILE", pid_file)
    monkeypatch.setattr(serve_obs_page, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(
        serve_obs_page,
        "_collect_process_rows",
        lambda: [{"pid": 12345, "ppid": 1, "command": r"C:\Windows\System32\notepad.exe"}],
    )
    monkeypatch.delenv("FOULER_OBS_SERVER_ALLOW_DUPLICATE", raising=False)

    serve_obs_page._acquire_singleton_or_exit()

    data = json.loads(pid_file.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()


def test_acquire_singleton_refuses_discovered_duplicate(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "obs_server.pid"
    monkeypatch.setattr(serve_obs_page, "PID_FILE", pid_file)
    monkeypatch.setattr(serve_obs_page, "_pid_exists", lambda _pid: False)
    monkeypatch.setattr(
        serve_obs_page,
        "_collect_process_rows",
        lambda: [
            {
                "pid": 424242,
                "ppid": 1,
                "command": f'py -3 "{serve_obs_page.ROOT_DIR}\\streaming\\serve_obs_page.py"',
            }
        ],
    )
    monkeypatch.delenv("FOULER_OBS_SERVER_ALLOW_DUPLICATE", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        serve_obs_page._acquire_singleton_or_exit()

    assert exc_info.value.code == 78
    assert not pid_file.exists()


@pytest.mark.asyncio
async def test_health_default_uses_fast_public_surface(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(serve_obs_page, "DEEP_HEALTH_DEFAULT", False)
    monkeypatch.setattr(serve_obs_page, "_build_singleton_status", lambda: {"duplicateCount": 0, "duplicates": []})
    monkeypatch.setattr(serve_obs_page, "recent_showdown_credential_failure", lambda _root: {"found": False})
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")

    state_store.write_status({"status": "Searching"})
    state_store.update_daily_stats(0, 0)
    state_store.write_active_battles({"battles": [], "count": 0})

    response = await serve_obs_page.handle_health(make_mocked_request("GET", "/health"))
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["devstreamHealthProbe"]["method"] == "skipped"
    assert payload["readiness"]["streamReady"] is True


@pytest.mark.asyncio
async def test_health_default_skips_singleton_subprocess_probe(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(serve_obs_page, "DEEP_HEALTH_DEFAULT", False)

    def fail_singleton_probe():
        raise KeyboardInterrupt

    monkeypatch.setattr(serve_obs_page, "_build_singleton_status", fail_singleton_probe)
    monkeypatch.setattr(serve_obs_page, "recent_showdown_credential_failure", lambda _root: {"found": False})
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")

    state_store.write_status({"status": "Searching"})
    state_store.update_daily_stats(0, 0)
    state_store.write_active_battles({"battles": [], "count": 0})

    response = await serve_obs_page.handle_health(make_mocked_request("GET", "/health"))
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["obsServerSingleton"]["skipped"] is True
    assert payload["devstreamHealthProbe"]["method"] == "skipped"


@pytest.mark.asyncio
async def test_health_deep_query_uses_in_process_devstream_probe(monkeypatch) -> None:
    monkeypatch.setattr(serve_obs_page, "DEEP_HEALTH_DEFAULT", False)
    monkeypatch.setattr(serve_obs_page, "_build_singleton_status", lambda: {"duplicateCount": 0, "duplicates": []})
    monkeypatch.setattr(
        serve_obs_page,
        "_build_devstream_health_payload",
        lambda: {
            "schemaVersion": "devstream-health/v1",
            "projectId": "fouler-play",
            "status": "running",
            "healthy": True,
            "readiness": {"runtimeReady": True, "streamReady": True, "proofHandoffReady": False},
        },
    )

    response = await serve_obs_page.handle_health(make_mocked_request("GET", "/health?deep=1"))
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["devstreamHealthProbe"] == {"ok": True, "method": "in-process"}
    assert payload["healthy"] is True


@pytest.mark.asyncio
async def test_health_fallback_keeps_serving_active_public_surface(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(serve_obs_page, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(serve_obs_page, "_build_singleton_status", lambda: {"duplicateCount": 0, "duplicates": []})
    monkeypatch.setattr(
        serve_obs_page,
        "_build_devstream_health_payload",
        lambda: (_ for _ in ()).throw(RuntimeError("probe failed")),
    )
    monkeypatch.setattr(serve_obs_page, "recent_showdown_credential_failure", lambda _root: {"found": False})
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")

    state_store.write_status({"status": "Battling", "battle_info": "vs Opponent"})
    state_store.update_daily_stats(0, 0)
    state_store.write_active_battles(
        {
            "battles": [
                {
                    "id": "battle-gen9ou-123",
                    "opponent": "Opponent",
                    "url": "https://play.pokemonshowdown.com/battle-gen9ou-123",
                    "slot": 1,
                    "status": "active",
                }
            ],
            "count": 1,
        }
    )

    response = await serve_obs_page.handle_health(make_mocked_request("GET", "/health?deep=1"))
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["status"] == "running"
    assert payload["healthy"] is True
    assert payload["readiness"]["streamReady"] is True
    assert payload["readiness"]["runtimeReady"] is True
    assert payload["readiness"]["proofHandoffReady"] is False
    assert payload["devstreamHealthProbe"]["ok"] is False
    assert payload["activeBattleCount"] == 1
