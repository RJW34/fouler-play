#!/usr/bin/env python3
"""
Hermes Health Check -- single-call runtime truth for Symphony/DEKU monitoring.

Exit codes:
  0 = healthy (runtime is useful and overlay/reporting are fresh)
  1 = degraded (runtime is up but stale, partially broken, or reporting drift exists)
  2 = down (no credible runtime activity)

Outputs JSON to stdout for Symphony consumption.

Usage:
    python infrastructure/hermes_health.py
    python infrastructure/hermes_health.py --brief
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infrastructure.windows.player_loop_watchdog import assess_progress

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATS_FILE = PROJECT_ROOT / "battle_stats.json"
RESEARCH_LOG = PROJECT_ROOT / "data" / "autoresearch" / "research_log.jsonl"
PID_DIR = PROJECT_ROOT / ".pids"
BOT_PID_FILE = PID_DIR / "bot_main.pid"
ACTIVE_BATTLES_FILE = PROJECT_ROOT / "active_battles.json"
STREAM_STATUS_FILE = PROJECT_ROOT / "stream_status.json"
STREAM_SERVER_PORT = 8777

MAX_IDLE_SECONDS = max(120, int(os.getenv("FOULER_HEALTH_MAX_IDLE_SECONDS", "900")))
OVERLAY_MAX_IDLE_SECONDS = max(
    120, int(os.getenv("FOULER_OVERLAY_MAX_IDLE_SECONDS", "600"))
)


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None

    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_file_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _battle_was_win(entry: dict[str, Any]) -> bool | None:
    if "won" in entry:
        won = entry.get("won")
        if isinstance(won, bool):
            return won

    result = entry.get("result")
    if isinstance(result, str):
        lowered = result.strip().lower()
        if lowered == "win":
            return True
        if lowered == "loss":
            return False
    return None


def _normalize_battles(raw_battles: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_battles, list):
        return []
    return [entry for entry in raw_battles if isinstance(entry, dict)]


def _recent_battle_count(battles: list[dict[str, Any]], *, now: datetime, hours: int) -> int:
    cutoff = now - timedelta(hours=hours)
    count = 0
    for battle in battles:
        battle_time = _parse_timestamp(battle.get("timestamp"))
        if battle_time and battle_time >= cutoff:
            count += 1
    return count


def _latest_battle_time(battles: list[dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for battle in battles:
        battle_time = _parse_timestamp(battle.get("timestamp"))
        if battle_time and (latest is None or battle_time > latest):
            latest = battle_time
    return latest


def _extract_elo_from_history(elo_history: Any) -> tuple[int | None, str]:
    if not isinstance(elo_history, list):
        return None, "none"

    values: list[int] = []
    for entry in elo_history:
        if not isinstance(entry, dict):
            continue
        elo = entry.get("elo", entry.get("rating", entry.get("r")))
        if isinstance(elo, (int, float)):
            values.append(int(elo))

    if not values:
        return None, "none"
    return values[-1], "history"


def _compute_elo_trend(elo_history: Any) -> str:
    if not isinstance(elo_history, list):
        return "unknown"

    values: list[int] = []
    for entry in elo_history[-10:]:
        if not isinstance(entry, dict):
            continue
        elo = entry.get("elo", entry.get("rating", entry.get("r")))
        if isinstance(elo, (int, float)):
            values.append(int(elo))

    if len(values) < 3:
        return "unknown"
    if values[-1] > values[0] + 20:
        return "rising"
    if values[-1] < values[0] - 20:
        return "dropping"
    return "stable"


def get_battle_stats(now: datetime | None = None) -> dict[str, Any]:
    """Read battle stats and compute key metrics across legacy and live schemas."""
    if not STATS_FILE.exists():
        return {"exists": False}

    data = _read_json(STATS_FILE, {})
    if not isinstance(data, dict):
        return {"exists": False}

    current_time = _utc_now(now)
    battles = _normalize_battles(data.get("battles"))
    derived_wins = 0
    derived_losses = 0
    completed_battles = 0
    for battle in battles:
        won = _battle_was_win(battle)
        if won is None:
            continue
        completed_battles += 1
        if won:
            derived_wins += 1
        else:
            derived_losses += 1

    wins = derived_wins if battles else int(data.get("win_count", 0) or 0)
    losses = derived_losses if battles else int(data.get("loss_count", 0) or 0)
    total = completed_battles if battles else wins + losses

    elo_history = data.get("elo_history", [])
    elo = data.get("current_elo")
    elo_source = "battle_stats"
    if not isinstance(elo, (int, float)):
        elo, history_source = _extract_elo_from_history(elo_history)
        elo_source = history_source if elo is not None else "none"
    else:
        elo = int(elo)

    latest_battle = _latest_battle_time(battles)
    last_battle_age_seconds = (
        round(max(0.0, (current_time - latest_battle).total_seconds()), 1)
        if latest_battle
        else None
    )
    stats_age_seconds = _safe_file_age_seconds(STATS_FILE)

    return {
        "exists": True,
        "schema": "battles_list" if battles else "summary_fields",
        "current_elo": elo,
        "elo_source": elo_source,
        "target_elo": 1700,
        "elo_gap": (1700 - elo) if isinstance(elo, int) else None,
        "elo_trend": _compute_elo_trend(elo_history),
        "win_count": wins,
        "loss_count": losses,
        "total_games": total,
        "win_rate": round((wins / total) * 100, 1) if total > 0 else 0.0,
        "recent_24h_battles": _recent_battle_count(battles, now=current_time, hours=24),
        "recent_1h_battles": _recent_battle_count(battles, now=current_time, hours=1),
        "last_battle_at": latest_battle.isoformat() if latest_battle else None,
        "last_battle_age_seconds": last_battle_age_seconds,
        "stats_age_seconds": round(stats_age_seconds, 1) if stats_age_seconds is not None else None,
    }


def get_research_status(now: datetime | None = None) -> dict[str, Any]:
    """Check autoresearch activity."""
    if not RESEARCH_LOG.exists():
        return {"active": False, "total_entries": 0}

    try:
        entries = []
        with RESEARCH_LOG.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return {"active": False, "total_entries": 0}

    if not entries:
        return {"active": False, "total_entries": 0}

    last_entry = entries[-1]
    last_dt = _parse_timestamp(last_entry.get("timestamp"))
    current_time = _utc_now(now)
    hours_since = None
    if last_dt is not None:
        hours_since = max(0.0, (current_time - last_dt).total_seconds() / 3600)

    return {
        "active": True,
        "total_entries": len(entries),
        "last_activity": last_dt.isoformat() if last_dt else last_entry.get("timestamp"),
        "hours_since_last": round(hours_since, 1) if hours_since is not None else None,
        "last_type": last_entry.get("type", "unknown"),
    }


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                return False
            output = (result.stdout or "") + (result.stderr or "")
            if "No tasks are running" in output:
                return False
            return str(pid) in output
        return False


def _scan_bot_processes() -> list[dict[str, Any]]:
    username = os.getenv("PS_USERNAME", "").strip()
    if sys.platform == "win32":
        filters = [
            r"$_.Name -match '^(py|python).*\.exe$'",
            "$_.CommandLine",
            "("
            " $_.CommandLine -match 'run\\.py'"
            " -or $_.CommandLine -match 'search_ladder'"
            " -or $_.CommandLine -match 'showdown/websocket'"
            + (f" -or $_.CommandLine -match '{re.escape(username)}'" if username else "")
            + " )",
        ]
        command = (
            "$procs = Get-CimInstance Win32_Process | Where-Object { "
            + " -and ".join(filters)
            + " } | Select-Object ProcessId, Name, CommandLine; "
            "if ($procs) { $procs | ConvertTo-Json -Compress }"
        )
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            return []

        raw = (result.stdout or "").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []
        processes = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("ProcessId")
            if not isinstance(pid, int):
                continue
            processes.append(
                {
                    "pid": pid,
                    "name": entry.get("Name"),
                    "command": entry.get("CommandLine"),
                }
            )
        return processes

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []

    terms = ("run.py", "search_ladder", "showdown/websocket")
    processes = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not any(term in line for term in terms) and (not username or username not in line):
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        processes.append({"pid": pid, "name": "python", "command": parts[1] if len(parts) > 1 else ""})
    return processes


def get_bot_process_state(progress: dict[str, Any]) -> dict[str, Any]:
    """Determine whether the bot is running, preferring live runtime proof over stale PID files."""
    pid_payload = _read_json(BOT_PID_FILE, {})
    pid_file_exists = BOT_PID_FILE.exists()
    pid = pid_payload.get("pid") if isinstance(pid_payload, dict) else None
    pid_alive = isinstance(pid, int) and _pid_exists(pid)

    if pid_alive:
        return {
            "running": True,
            "source": "pid_file",
            "pid": pid,
            "pid_file_exists": pid_file_exists,
            "pid_file_stale": False,
            "processes": [],
        }

    processes = _scan_bot_processes()
    if processes:
        return {
            "running": True,
            "source": "process_scan",
            "pid": processes[0]["pid"],
            "pid_file_exists": pid_file_exists,
            "pid_file_stale": pid_file_exists,
            "processes": processes,
        }

    progress_is_fresh = not bool(progress.get("stale", True))
    return {
        "running": progress_is_fresh,
        "source": "progress_signals" if progress_is_fresh else "none",
        "pid": pid if isinstance(pid, int) else None,
        "pid_file_exists": pid_file_exists,
        "pid_file_stale": pid_file_exists and not pid_alive,
        "processes": [],
    }


def _fetch_json(url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def get_stream_runtime(now: datetime | None = None) -> dict[str, Any]:
    """Check stream server responsiveness and overlay freshness."""
    current_time = _utc_now(now)
    status_payload = _fetch_json(f"http://127.0.0.1:{STREAM_SERVER_PORT}/status")
    state_payload = _fetch_json(f"http://127.0.0.1:{STREAM_SERVER_PORT}/state")

    active_battles_data = _read_json(ACTIVE_BATTLES_FILE, {})
    stream_status_data = _read_json(STREAM_STATUS_FILE, {})

    active_file_age = _safe_file_age_seconds(ACTIVE_BATTLES_FILE)
    status_file_age = _safe_file_age_seconds(STREAM_STATUS_FILE)

    timestamps: list[float] = []
    for candidate in (
        active_battles_data.get("updated") if isinstance(active_battles_data, dict) else None,
        stream_status_data.get("updated") if isinstance(stream_status_data, dict) else None,
        state_payload.get("updated") if isinstance(state_payload, dict) else None,
        state_payload.get("status", {}).get("updated") if isinstance(state_payload, dict) else None,
        status_payload.get("updated") if isinstance(status_payload, dict) else None,
        status_payload.get("elo_updated") if isinstance(status_payload, dict) else None,
    ):
        parsed = _parse_timestamp(candidate)
        if parsed is not None:
            timestamps.append(max(0.0, (current_time - parsed).total_seconds()))

    for age in (active_file_age, status_file_age):
        if age is not None:
            timestamps.append(age)

    freshest_age_seconds = round(min(timestamps), 1) if timestamps else None
    overlay_stale = (
        freshest_age_seconds is None or freshest_age_seconds > OVERLAY_MAX_IDLE_SECONDS
    )

    active_battles = []
    if isinstance(state_payload, dict) and isinstance(state_payload.get("battles"), list):
        active_battles = [battle for battle in state_payload["battles"] if isinstance(battle, dict)]
    elif isinstance(active_battles_data, dict) and isinstance(active_battles_data.get("battles"), list):
        active_battles = [battle for battle in active_battles_data["battles"] if isinstance(battle, dict)]

    status_obj = state_payload.get("status") if isinstance(state_payload, dict) else None
    if not isinstance(status_obj, dict):
        status_obj = status_payload if isinstance(status_payload, dict) else {}

    return {
        "up": bool(status_payload or state_payload),
        "overlay_stale": overlay_stale,
        "freshest_age_seconds": freshest_age_seconds,
        "active_battles": len(active_battles),
        "active_battle_ids": [battle.get("id") for battle in active_battles if battle.get("id")],
        "elo": status_obj.get("elo"),
        "wins": status_obj.get("wins"),
        "losses": status_obj.get("losses"),
        "status_text": status_obj.get("status"),
        "battle_info": status_obj.get("battle_info"),
        "active_file_age_seconds": round(active_file_age, 1) if active_file_age is not None else None,
        "status_file_age_seconds": round(status_file_age, 1) if status_file_age is not None else None,
        "state_endpoint": state_payload,
        "status_endpoint": status_payload,
    }


def run_health_check(now: datetime | None = None) -> dict[str, Any]:
    """Run full health check and return a proof-oriented status dict."""
    current_time = _utc_now(now)
    progress = assess_progress(PROJECT_ROOT, now=current_time.timestamp(), max_idle_seconds=MAX_IDLE_SECONDS)
    process_state = get_bot_process_state(progress)
    stats = get_battle_stats(now=current_time)
    research = get_research_status(now=current_time)
    stream = get_stream_runtime(now=current_time)

    if stats.get("current_elo") is None and isinstance(stream.get("elo"), int):
        stats["current_elo"] = int(stream["elo"])
        stats["elo_source"] = "stream_status"
        stats["elo_gap"] = 1700 - stats["current_elo"]

    if stats.get("win_count", 0) == 0 and stats.get("loss_count", 0) == 0:
        if isinstance(stream.get("wins"), int):
            stats["win_count"] = int(stream["wins"])
        if isinstance(stream.get("losses"), int):
            stats["loss_count"] = int(stream["losses"])
        total = stats["win_count"] + stats["loss_count"]
        stats["total_games"] = total
        stats["win_rate"] = round((stats["win_count"] / total) * 100, 1) if total > 0 else 0.0

    reasons: list[str] = []
    notes: list[str] = []

    useful_activity = (
        not progress.get("stale", True)
        or stream.get("active_battles", 0) > 0
        or (stats.get("last_battle_age_seconds") is not None and stats["last_battle_age_seconds"] <= MAX_IDLE_SECONDS)
    )

    if process_state.get("pid_file_stale"):
        notes.append("bot_main.pid was stale; runtime matched via live process/progress scan")

    if not process_state.get("running") and not useful_activity:
        status = "down"
        exit_code = 2
        reasons.append("no live bot process or fresh progress signals")
    else:
        degraded = False
        if progress.get("stale"):
            degraded = True
            reasons.append(str(progress.get("reason")))
        if not stream.get("up"):
            degraded = True
            reasons.append("stream server is not responding on /status or /state")
        elif stream.get("overlay_stale"):
            degraded = True
            reasons.append(
                f"overlay data is stale ({stream.get('freshest_age_seconds')}s old)"
            )
        if stats.get("exists") and stats.get("recent_24h_battles", 0) == 0 and stream.get("active_battles", 0) == 0:
            degraded = True
            reasons.append("no battles completed in the last 24 hours")
        if stats.get("elo_trend") == "dropping":
            degraded = True
            reasons.append("ELO trend is dropping")

        status = "degraded" if degraded else "healthy"
        exit_code = 1 if degraded else 0

    proof = {
        "progress": {
            "freshest_path": progress.get("freshest_path"),
            "freshest_age_seconds": progress.get("freshest_age_seconds"),
        },
        "last_battle_at": stats.get("last_battle_at"),
        "last_battle_age_seconds": stats.get("last_battle_age_seconds"),
        "active_battle_ids": stream.get("active_battle_ids"),
        "overlay_freshest_age_seconds": stream.get("freshest_age_seconds"),
    }

    return {
        "status": status,
        "exit_code": exit_code,
        "timestamp": current_time.isoformat(),
        "reasons": reasons,
        "notes": notes,
        "bot_process": process_state,
        "progress": progress,
        "stream_server": stream,
        "battle_stats": stats,
        "autoresearch": research,
        "proof": proof,
    }


def _brief(result: dict[str, Any]) -> str:
    stats = result["battle_stats"]
    stream = result["stream_server"]
    process_state = result["bot_process"]
    elo = stats.get("current_elo", "?")
    wr = stats.get("win_rate", 0)
    total = stats.get("total_games", 0)
    active = stream.get("active_battles", 0)
    progress_age = result["progress"].get("freshest_age_seconds")
    overlay_age = stream.get("freshest_age_seconds")
    reasons = "; ".join(result.get("reasons") or []) or "runtime aligned"
    return (
        f"[{result['status'].upper()}] "
        f"Bot:{'UP' if process_state.get('running') else 'DOWN'}({process_state.get('source')}) "
        f"ELO:{elo} WR:{wr}% Games:{total} Active:{active} "
        f"Progress:{progress_age}s Overlay:{overlay_age}s "
        f"Reason:{reasons}"
    )


def main() -> None:
    result = run_health_check()

    if "--brief" in sys.argv:
        print(_brief(result))
    else:
        print(json.dumps(result, indent=2))

    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
