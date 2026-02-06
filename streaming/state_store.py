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
from pathlib import Path
from datetime import datetime
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_BATTLES_PATH = ROOT_DIR / "active_battles.json"
STREAM_STATUS_PATH = ROOT_DIR / "stream_status.json"
DAILY_STATS_PATH = ROOT_DIR / "daily_stats.json"
NEXT_FIX_PATH = ROOT_DIR / "next_fix.txt"

DEFAULT_NEXT_FIX = "Pending replay review"

DEFAULT_STATUS = {
    "elo": "---",
    "wins": 0,
    "losses": 0,
    "status": "Idle",
    "battle_info": "Waiting for battle...",
    "streaming": False,
    "stream_pid": None,
    "updated": None,
    "next_fix": DEFAULT_NEXT_FIX,
}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


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
    return data


def write_active_battles(payload: dict[str, Any]) -> None:
    if "battles" not in payload:
        payload["battles"] = []
    if "count" not in payload:
        payload["count"] = len(payload["battles"])
    if "updated" not in payload:
        payload["updated"] = datetime.now().isoformat()
    _atomic_write_json(ACTIVE_BATTLES_PATH, payload)


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
