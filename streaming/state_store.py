#!/usr/bin/env python3
"""
Shared state store for OBS overlays.

Single source of truth for:
- active_battles.json
- stream_status.json

Provides atomic reads/writes to avoid partial reads in OBS.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_BATTLES_PATH = ROOT_DIR / "active_battles.json"
STREAM_STATUS_PATH = ROOT_DIR / "stream_status.json"
DAILY_STATS_PATH = ROOT_DIR / "daily_stats.json"
NEXT_FIX_PATH = ROOT_DIR / "next_fix.txt"
STABILITY_REPORT_PATH = ROOT_DIR / "stability_report.json"
STATE_STORE_WRITE_FAILURE_PATH = ROOT_DIR / "devstream" / "truth" / "state-store-write-failure.json"

DEFAULT_NEXT_FIX = "Pending replay review"
DEFAULT_DEVSTREAM_BATTLE_SURFACES = 3

DEFAULT_STATUS = {
    "elo": "---",
    "wins": 0,
    "losses": 0,
    "status": "Idle",
    "battle_info": "Waiting for battle...",
    "active_battles": [],
    "streaming": False,
    "stream_pid": None,
    "updated": None,
    "next_fix": DEFAULT_NEXT_FIX,
}


def _status_with_cleared_blocker(status: dict[str, Any]) -> dict[str, Any]:
    cleared = dict(status)
    for key in ("runtime_blocked", "blocker_code", "blocker_summary"):
        cleared.pop(key, None)
    return cleared


def _write_state_store_failure(path: Path, exc: BaseException, attempts: int) -> None:
    failure = {
        "schemaVersion": "fouler-play-state-store-write-failure/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "target": str(path),
        "attempts": attempts,
        "errorType": type(exc).__name__,
        "error": str(exc),
        "safeForPublicLogs": True,
    }
    STATE_STORE_WRITE_FAILURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_STORE_WRITE_FAILURE_PATH.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def expected_battle_surfaces() -> int:
    """Return the minimum number of public OBS battle surfaces to keep live."""
    return max(1, _env_int("FP_EXPECTED_DEVSTREAM_BATTLE_SURFACES", DEFAULT_DEVSTREAM_BATTLE_SURFACES))


def _normalize_max_slots(payload: dict[str, Any]) -> int:
    raw_value = payload.get("max_slots", 0)
    try:
        raw_slots = int(raw_value)
    except (TypeError, ValueError):
        raw_slots = 0
    battles = payload.get("battles")
    battle_count = len(battles) if isinstance(battles, list) else 0
    count_value = payload.get("count", battle_count)
    try:
        payload_count = int(count_value)
    except (TypeError, ValueError):
        payload_count = battle_count
    return max(raw_slots, battle_count, payload_count, expected_battle_surfaces())


def _clear_state_store_failure(path: Path) -> None:
    if not STATE_STORE_WRITE_FAILURE_PATH.exists():
        return
    try:
        failure = json.loads(STATE_STORE_WRITE_FAILURE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if failure.get("target") != str(path):
        return
    try:
        STATE_STORE_WRITE_FAILURE_PATH.unlink()
    except OSError:
        pass


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    attempts = max(1, _env_int("STATE_STORE_WRITE_ATTEMPTS", 30))
    base_sleep = max(0.0, _env_float("STATE_STORE_WRITE_RETRY_BASE_SEC", 0.05))
    max_sleep = max(0.0, _env_float("STATE_STORE_WRITE_RETRY_MAX_SEC", 0.5))
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            os.replace(tmp, path)
            _clear_state_store_failure(path)
            return
        except PermissionError as exc:
            last_error = exc
        except OSError as exc:
            if getattr(exc, "winerror", None) != 5:
                raise
            last_error = exc
        if attempt < attempts:
            time.sleep(min(max_sleep, base_sleep * attempt))
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
    if last_error is None:
        last_error = RuntimeError(f"atomic write failed for {path}")
    _write_state_store_failure(path, last_error, attempts)
    raise last_error


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        # If the file contains a list or other type, fall back to default
        return dict(default)
    except Exception:
        return dict(default)


def _normalize_active_battle_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    battle_id = entry.get("id")
    if not battle_id or not isinstance(battle_id, str):
        return None
    normalized = {
        "id": battle_id,
        "opponent": entry.get("opponent") if isinstance(entry.get("opponent"), str) else "Unknown",
        "url": entry.get("url") if isinstance(entry.get("url"), str) else "",
        "started": entry.get("started"),
        "worker_id": entry.get("worker_id"),
        "status": entry.get("status") if isinstance(entry.get("status"), str) else "active",
        "players": entry.get("players") if isinstance(entry.get("players"), list) else [],
        "slot": entry.get("slot"),
    }
    return normalized


def read_active_battles() -> dict[str, Any]:
    data = _read_json(
        ACTIVE_BATTLES_PATH,
        {"battles": [], "count": 0, "updated": None},
    )
    # Normalize legacy or malformed payloads
    if "battles" not in data or not isinstance(data.get("battles"), list):
        data["battles"] = []
    normalized_battles = []
    for entry in data.get("battles", []):
        normalized = _normalize_active_battle_entry(entry)
        if normalized:
            normalized_battles.append(normalized)
    data["battles"] = normalized_battles
    if "count" not in data or not isinstance(data.get("count"), int):
        data["count"] = len(data["battles"])
    if "updated" not in data:
        data["updated"] = None
    data["max_slots"] = _normalize_max_slots(data)
    return data


def write_active_battles(payload: dict[str, Any]) -> None:
    if "battles" not in payload:
        payload["battles"] = []
    if "count" not in payload:
        payload["count"] = len(payload["battles"])
    if "updated" not in payload:
        payload["updated"] = datetime.now().isoformat()
    payload["max_slots"] = _normalize_max_slots(payload)
    _atomic_write_json(ACTIVE_BATTLES_PATH, payload)
    _sync_stream_status_with_active_battles(payload)


def read_status() -> dict[str, Any]:
    return _read_json(STREAM_STATUS_PATH, DEFAULT_STATUS)


def read_next_fix() -> str:
    if not NEXT_FIX_PATH.exists():
        return DEFAULT_NEXT_FIX
    try:
        text = NEXT_FIX_PATH.read_text(encoding="utf-8").strip()
        return text or DEFAULT_NEXT_FIX
    except Exception:
        return DEFAULT_NEXT_FIX


def write_next_fix(text: str) -> None:
    value = (text or "").strip()
    if not value:
        value = DEFAULT_NEXT_FIX
    NEXT_FIX_PATH.write_text(value + "\n", encoding="utf-8")


def write_status(status: dict[str, Any]) -> None:
    data = dict(DEFAULT_STATUS)
    data.update(status)
    data["updated"] = datetime.now().isoformat()
    _atomic_write_json(STREAM_STATUS_PATH, data)


def _active_battle_summary(battles: list[dict[str, Any]]) -> str:
    opponents = []
    for battle in battles:
        opponent = battle.get("opponent")
        if isinstance(opponent, str) and opponent.strip():
            opponents.append(opponent.strip())
        else:
            opponents.append("Unknown")
    return ", ".join(f"vs {opponent}" for opponent in opponents) if opponents else "Searching..."


def _sync_stream_status_with_active_battles(payload: dict[str, Any]) -> None:
    if not _env_bool("FOULER_SYNC_STREAM_STATUS_WITH_ACTIVE_BATTLES", True):
        return
    try:
        normalized = read_active_battles()
        battles = normalized.get("battles", [])
        status = read_status()
        if status.get("runtime_blocked") and not battles:
            return
        status = _status_with_cleared_blocker(status)
        status["active_battles"] = [battle.get("id") for battle in battles if battle.get("id")]
        if battles:
            status["status"] = "Active"
            status["battle_info"] = _active_battle_summary(battles)
        else:
            status["status"] = "Searching"
            status["battle_info"] = "Searching..."
        write_status(status)
    except Exception as exc:
        _write_state_store_failure(STREAM_STATUS_PATH, exc, 1)


def write_runtime_blocked_status(*, code: str, summary: str) -> dict[str, Any]:
    """Publish a fresh, viewer-safe blocked state for OBS and HERMES."""
    now = datetime.now(timezone.utc).isoformat()
    blocker_summary = (summary or "Runtime blocked.").strip()
    blocker_code = (code or "runtime_blocked").strip()
    active_payload = {
        "battles": [],
        "count": 0,
        "updated": now,
        "runtime_blocked": True,
        "blocker_code": blocker_code,
        "blocker_summary": blocker_summary,
    }
    status_payload = {
        "status": "Credential blocked" if "credential" in blocker_code else "Runtime blocked",
        "battle_info": blocker_summary,
        "streaming": False,
        "stream_pid": None,
        "runtime_blocked": True,
        "blocker_code": blocker_code,
        "blocker_summary": blocker_summary,
        "next_fix": "Refresh Showdown credentials and rerun the login proof." if "credential" in blocker_code else DEFAULT_NEXT_FIX,
    }
    write_active_battles(active_payload)
    write_status(status_payload)
    daily = update_daily_stats(0, 0)
    stability_payload = {
        "generated_at": now,
        "runtime_blocked": True,
        "blocker_code": blocker_code,
        "blocker_summary": blocker_summary,
        "stability": {
            "health": "blocked",
            "summary": blocker_summary,
            "next_fix": status_payload["next_fix"],
        },
        "details": {
            "active_battles": 0,
            "reason": "No stability sample is meaningful while the runtime is blocked before battle launch.",
        },
    }
    _atomic_write_json(STABILITY_REPORT_PATH, stability_payload)
    return {
        "activeBattles": active_payload,
        "status": read_status(),
        "dailyStats": daily,
        "stabilityReport": stability_payload,
    }


def write_runtime_ready_status(*, summary: str, mode: str = "ready") -> dict[str, Any]:
    """Publish fresh, secret-free runtime truth after a safe readiness proof."""
    now = datetime.now(timezone.utc).isoformat()
    clean_summary = (summary or "Runtime ready.").strip()
    clean_mode = (mode or "ready").strip()
    status_label = "Offline rehearsal ready" if clean_mode == "offline_rehearsal" else "Ready"
    active_payload = {
        "battles": [],
        "count": 0,
        "updated": now,
        "runtime_mode": clean_mode,
        "runtime_blocked": False,
    }
    daily = update_daily_stats(0, 0)
    existing_status = _status_with_cleared_blocker(read_status())
    status_payload = {
        **existing_status,
        "wins": daily.get("wins", 0),
        "losses": daily.get("losses", 0),
        "today_wins": daily.get("wins", 0),
        "today_losses": daily.get("losses", 0),
        "status": status_label,
        "battle_info": clean_summary,
        "streaming": False,
        "stream_pid": None,
        "runtime_mode": clean_mode,
        "next_fix": "Start a bounded devstream batch." if clean_mode != "offline_rehearsal" else "Offline rehearsal is available; live ladder still needs a successful executed login proof.",
    }
    write_active_battles(active_payload)
    write_status(status_payload)
    stability_payload = {
        "generated_at": now,
        "runtime_blocked": False,
        "runtime_mode": clean_mode,
        "stability": {
            "health": "ready" if clean_mode != "offline_rehearsal" else "offline_rehearsal",
            "summary": clean_summary,
            "next_fix": status_payload["next_fix"],
        },
        "details": {
            "active_battles": 0,
            "reason": "Fresh readiness truth was published without launching a battle.",
        },
    }
    _atomic_write_json(STABILITY_REPORT_PATH, stability_payload)
    return {
        "activeBattles": active_payload,
        "status": read_status(),
        "dailyStats": daily,
        "stabilityReport": stability_payload,
    }


# Daily stats tracking
DEFAULT_DAILY_STATS = {
    "date": None,
    "wins": 0,
    "losses": 0,
}


def read_daily_stats() -> dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    data = _read_json(DAILY_STATS_PATH, DEFAULT_DAILY_STATS)
    # Reset if date changed
    if data.get("date") != today:
        data = {"date": today, "wins": 0, "losses": 0}
    return data


def update_daily_stats(wins_delta: int = 0, losses_delta: int = 0) -> dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    data = _read_json(DAILY_STATS_PATH, DEFAULT_DAILY_STATS)
    # Reset if date changed
    if data.get("date") != today:
        data = {"date": today, "wins": 0, "losses": 0}
    data["wins"] = max(0, data.get("wins", 0) + wins_delta)
    data["losses"] = max(0, data.get("losses", 0) + losses_delta)
    data["date"] = today
    _atomic_write_json(DAILY_STATS_PATH, data)
    return data
