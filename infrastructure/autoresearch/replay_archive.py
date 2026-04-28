"""
Replay archive utilities for grounded Fouler Play autoresearch.

Stores local copies of public Showdown replays so batch analysis can depend on
durable evidence instead of ephemeral URLs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIVE_ROOT = PROJECT_ROOT / "data" / "replay_archive"
REPLAY_JSON_DIR = ARCHIVE_ROOT / "json"
REPLAY_PAYLOAD_DIR = ARCHIVE_ROOT / "showdown_payload"
INDEX_FILE = ARCHIVE_ROOT / "index.json"
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
DEFAULT_RECENT_WINDOW = 90


def _normalize_replay_id(value: str) -> str:
    if not value:
        return ""
    tag = value
    if tag.startswith("battle-"):
        tag = tag.replace("battle-", "", 1)
    parts = tag.split("-")
    if len(parts) >= 2:
        tag = f"{parts[0]}-{parts[1]}"
    return tag


def _load_index() -> dict[str, Any]:
    if not INDEX_FILE.exists():
        return {"replays": {}}
    try:
        payload = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("replays"), dict):
            return payload
    except Exception:
        pass
    return {"replays": {}}


def _save_index(payload: dict[str, Any]) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _merge_index_entry(replay_id: str, updates: dict[str, Any]) -> None:
    index = _load_index()
    current = index.setdefault("replays", {}).get(replay_id, {})
    if not isinstance(current, dict):
        current = {}
    current.update({key: value for key, value in updates.items() if value is not None})
    index["replays"][replay_id] = current
    _save_index(index)


def _load_battle_entries() -> list[dict[str, Any]]:
    if not BATTLE_STATS_FILE.exists():
        return []
    try:
        payload = json.loads(BATTLE_STATS_FILE.read_text(encoding="utf-8"))
        battles = payload.get("battles", []) if isinstance(payload, dict) else []
        if isinstance(battles, list):
            return [entry for entry in battles if isinstance(entry, dict)]
    except Exception:
        pass
    return []


async def archive_replay(
    replay_id: str,
    *,
    battle_id: str | None = None,
    team_file: str | None = None,
    result: str | None = None,
    max_attempts: int = 6,
    delay_seconds: float = 2.0,
) -> dict[str, Any]:
    """Fetch and persist a public Showdown replay JSON locally."""
    normalized = _normalize_replay_id(replay_id)
    if not normalized:
        return {"ok": False, "reason": "missing replay id"}

    REPLAY_JSON_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPLAY_JSON_DIR / f"{normalized}.json"
    url = f"https://replay.pokemonshowdown.com/{normalized}.json"
    last_error = None

    timeout = aiohttp.ClientTimeout(total=10)
    headers = {"User-Agent": "FoulerPlay/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        payload = json.loads(text)
                        json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                        _merge_index_entry(normalized, {
                            "replay_id": normalized,
                            "battle_id": battle_id or replay_id,
                            "team_file": team_file,
                            "result": result,
                            "source_url": url,
                            "local_json": str(json_path),
                            "saved_at": datetime.now(timezone.utc).isoformat(),
                        })
                        return {
                            "ok": True,
                            "replay_id": normalized,
                            "path": str(json_path),
                            "source_url": url,
                        }
                    if resp.status in (404, 410):
                        last_error = f"http {resp.status}"
                    else:
                        last_error = f"http {resp.status}"
            except Exception as exc:
                last_error = type(exc).__name__

            if attempt < max_attempts:
                await asyncio.sleep(delay_seconds)

    logger.warning("Replay archive failed for %s: %s", normalized, last_error)
    return {"ok": False, "replay_id": normalized, "reason": last_error or "unavailable"}


def archive_replay_payload(
    replay_data: dict[str, Any],
    *,
    battle_id: str | None = None,
    team_file: str | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    """Persist the raw Showdown savereplay payload locally."""
    normalized = _normalize_replay_id(
        str(replay_data.get("id") or battle_id or "")
    )
    if not normalized:
        return {"ok": False, "reason": "missing replay id"}

    REPLAY_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    payload_path = REPLAY_PAYLOAD_DIR / f"{normalized}.json"
    payload_path.write_text(
        json.dumps(replay_data, indent=2) + "\n",
        encoding="utf-8",
    )
    _merge_index_entry(
        normalized,
        {
            "replay_id": normalized,
            "battle_id": battle_id or replay_data.get("id") or normalized,
            "team_file": team_file,
            "result": result,
            "local_payload_json": str(payload_path),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "ok": True,
        "replay_id": normalized,
        "path": str(payload_path),
    }


async def sync_recent_replays_from_stats(
    *,
    limit: int = 120,
    max_archives: int = 30,
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
) -> dict[str, Any]:
    """Archive recent replay ids from battle_stats.json when missing locally."""
    battles = _load_battle_entries()
    if limit > 0:
        battles = battles[-limit:]

    index = _load_index().get("replays", {})
    missing: list[dict[str, Any]] = []
    for battle in reversed(battles):
        replay_id = _normalize_replay_id(
            str(battle.get("replay_id") or battle.get("battle_id") or "")
        )
        if not replay_id or replay_id in index:
            continue
        missing.append(
            {
                "replay_id": replay_id,
                "battle_id": str(battle.get("battle_id") or replay_id),
                "team_file": battle.get("team_file"),
                "result": battle.get("result"),
            }
        )

    attempted = 0
    archived = 0
    failed: list[dict[str, str]] = []
    for battle in missing[: max(0, max_archives)]:
        attempted += 1
        result = await archive_replay(
            battle["replay_id"],
            battle_id=battle["battle_id"],
            team_file=battle.get("team_file"),
            result=battle.get("result"),
            max_attempts=max_attempts,
            delay_seconds=delay_seconds,
        )
        if result.get("ok"):
            archived += 1
        else:
            failed.append(
                {
                    "replay_id": battle["replay_id"],
                    "reason": str(result.get("reason", "unknown")),
                }
            )

    summary = get_replay_coverage_summary(window=limit)
    summary.update(
        {
            "attempted_archives": attempted,
            "archived_this_run": archived,
            "failed_this_run": len(failed),
            "sample_failed": failed[:10],
        }
    )
    return summary


def get_replay_coverage_summary(window: int = DEFAULT_RECENT_WINDOW) -> dict[str, Any]:
    """Summarize how much of battle_stats has durable replay evidence."""
    index = _load_index().get("replays", {})
    archived = {str(key) for key in index.keys()}
    battles = _load_battle_entries()

    def _coverage_for(entries: list[dict[str, Any]]) -> tuple[int, list[str]]:
        total = len(entries)
        missing_ids: list[str] = []
        for battle in entries:
            replay_id = _normalize_replay_id(
                str(battle.get("replay_id") or battle.get("battle_id") or "")
            )
            if replay_id and replay_id not in archived:
                missing_ids.append(replay_id)
        return total, missing_ids

    total_battles, missing = _coverage_for(battles)
    recent_entries = battles[-window:] if window > 0 else battles
    recent_total, recent_missing = _coverage_for(recent_entries)
    archived_count = max(0, total_battles - len(missing))
    recent_archived = max(0, recent_total - len(recent_missing))
    coverage_pct = round((archived_count / total_battles) * 100, 1) if total_battles else 0.0
    recent_coverage_pct = (
        round((recent_archived / recent_total) * 100, 1) if recent_total else 0.0
    )
    last_saved_at = None
    if index:
        try:
            last_saved_at = max(
                str(item.get("saved_at") or "")
                for item in index.values()
                if isinstance(item, dict)
            )
        except ValueError:
            last_saved_at = None

    return {
        "total_battles": total_battles,
        "archived_replays": archived_count,
        "missing_replays": len(missing),
        "coverage_pct": coverage_pct,
        "recent_window": recent_total,
        "recent_archived_replays": recent_archived,
        "recent_missing_replays": len(recent_missing),
        "recent_coverage_pct": recent_coverage_pct,
        "last_saved_at": last_saved_at,
        "sample_missing": missing[:10],
        "sample_recent_missing": recent_missing[:10],
    }
