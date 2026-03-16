#!/usr/bin/env python3
"""
Hermes Health Check — single-call status for Symphony/DEKU monitoring.

Exit codes:
  0 = healthy (bot running, recent battles, ELO stable)
  1 = degraded (bot running but no recent battles, or ELO dropping)
  2 = down (bot not running, or critical failure)

Outputs JSON to stdout for Symphony consumption.

Usage:
    python infrastructure/hermes_health.py          # JSON output
    python infrastructure/hermes_health.py --brief   # one-line summary
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATS_FILE = PROJECT_ROOT / "battle_stats.json"
RESEARCH_LOG = PROJECT_ROOT / "data" / "autoresearch" / "research_log.jsonl"
PID_DIR = PROJECT_ROOT / ".pids"
BOT_PID_FILE = PID_DIR / "bot_main.pid"
STREAM_SERVER_PORT = 8777


def bot_is_running() -> bool:
    """Check if bot process is alive via PID file."""
    try:
        if not BOT_PID_FILE.exists():
            return False
        data = json.loads(BOT_PID_FILE.read_text())
        pid = data.get("pid")
        if not pid:
            return False
        import platform
        if platform.system() == "Windows":
            import subprocess
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
            return str(pid) in r.stdout
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, json.JSONDecodeError):
        return False
    except Exception:
        return False


def get_battle_stats() -> dict:
    """Read battle stats and compute key metrics."""
    if not STATS_FILE.exists():
        return {"exists": False}

    try:
        data = json.loads(STATS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"exists": False}

    battles = data.get("battles", [])
    wins = data.get("win_count", 0)
    losses = data.get("loss_count", 0)
    total = wins + losses
    elo = data.get("current_elo")
    elo_history = data.get("elo_history", [])

    # Recent battles (last 24h)
    recent_count = 0
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    for b in battles:
        ts = b.get("timestamp", "")
        if ts >= cutoff:
            recent_count += 1

    # ELO trend (last 10 entries)
    elo_trend = "stable"
    if len(elo_history) >= 3:
        recent_elos = [e["elo"] for e in elo_history[-10:] if "elo" in e]
        if len(recent_elos) >= 3:
            if recent_elos[-1] > recent_elos[0] + 20:
                elo_trend = "rising"
            elif recent_elos[-1] < recent_elos[0] - 20:
                elo_trend = "dropping"

    # Stats file age
    stats_age_hours = (
        time.time() - STATS_FILE.stat().st_mtime
    ) / 3600

    return {
        "exists": True,
        "current_elo": elo,
        "target_elo": 1700,
        "elo_gap": (1700 - elo) if isinstance(elo, int) else None,
        "elo_trend": elo_trend,
        "win_count": wins,
        "loss_count": losses,
        "total_games": total,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        "recent_24h_battles": recent_count,
        "stats_age_hours": round(stats_age_hours, 1),
    }


def get_research_status() -> dict:
    """Check autoresearch activity."""
    if not RESEARCH_LOG.exists():
        return {"active": False, "total_entries": 0}

    try:
        entries = []
        with RESEARCH_LOG.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return {"active": False, "total_entries": 0}

    if not entries:
        return {"active": False, "total_entries": 0}

    last_entry = entries[-1]
    last_ts = last_entry.get("timestamp", "")
    hours_since = None
    if last_ts:
        try:
            dt = datetime.fromisoformat(last_ts)
            hours_since = (datetime.now() - dt).total_seconds() / 3600
        except ValueError:
            pass

    return {
        "active": True,
        "total_entries": len(entries),
        "last_activity": last_ts,
        "hours_since_last": round(hours_since, 1) if hours_since else None,
        "last_type": last_entry.get("type", "unknown"),
    }


def stream_server_up() -> bool:
    """Check if streaming server is responding."""
    try:
        import urllib.request
        urllib.request.urlopen(
            f"http://127.0.0.1:{STREAM_SERVER_PORT}/status", timeout=3
        )
        return True
    except Exception:
        return False


def run_health_check() -> dict:
    """Run full health check, return status dict."""
    bot_running = bot_is_running()
    stats = get_battle_stats()
    research = get_research_status()
    stream_up = stream_server_up()

    # Determine overall status
    if not bot_running:
        status = "down"
        exit_code = 2
    elif stats["exists"] and stats.get("recent_24h_battles", 0) == 0:
        status = "degraded"
        exit_code = 1
    elif stats.get("elo_trend") == "dropping":
        status = "degraded"
        exit_code = 1
    else:
        status = "healthy"
        exit_code = 0

    return {
        "status": status,
        "exit_code": exit_code,
        "timestamp": datetime.now().isoformat(),
        "bot_running": bot_running,
        "stream_server": stream_up,
        "battle_stats": stats,
        "autoresearch": research,
    }


def main():
    result = run_health_check()

    if "--brief" in sys.argv:
        stats = result["battle_stats"]
        elo = stats.get("current_elo", "?")
        wr = stats.get("win_rate", 0)
        total = stats.get("total_games", 0)
        print(
            f"[{result['status'].upper()}] ELO:{elo} WR:{wr}% "
            f"Games:{total} Bot:{'UP' if result['bot_running'] else 'DOWN'} "
            f"Stream:{'UP' if result['stream_server'] else 'DOWN'}"
        )
    else:
        print(json.dumps(result, indent=2))

    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()

