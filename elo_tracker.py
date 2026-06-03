#!/usr/bin/env python3
"""
ELO Tracker - Comprehensive battle stats and ELO monitoring for Fouler Play
Tracks: ELO over time, win/loss, opponent teams, battle duration, common loss patterns
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests

# Use path relative to this file
PROJECT_ROOT = Path(__file__).parent
STATS_FILE = PROJECT_ROOT / "battle_stats.json"
USERNAME = os.getenv("PS_USERNAME", "npctypebeat")
TARGET_ELO = 1700
LOG_FILES = [
    "monitor.log",
    "bot_monitor_output.log",
    "monitor_console.log",
    "monitor_live.log",
]


def load_stats():
    if STATS_FILE.exists():
        return json.loads(STATS_FILE.read_text())
    return {
        "elo_history": [],
        "battles": [],
        "win_count": 0,
        "loss_count": 0,
        "current_elo": None,
        "target_elo": TARGET_ELO,
        "started_at": datetime.now().isoformat(),
    }


def save_stats(stats):
    STATS_FILE.write_text(json.dumps(stats, indent=2))


def get_current_elo():
    """Return current gen9ou ELO from the canonical ladder JSON API.

    DEPRECATED ELO SOURCE NOTE (P1 single-source-of-truth fix):
    This previously scraped ELO out of the profile HTML with a brittle regex
    (``gen9ou.*?<strong>(\\d+)</strong>``) that silently broke whenever Showdown
    changed its page markup. The ONE authoritative ELO source is now the ladder
    JSON endpoint (``/users/<id>.json`` -> ratings[fmt].elo), the same path used by
    ``fp/run_battle.py::_fetch_elo``. We delegate here so there is exactly one ELO
    parser in the codebase.
    """
    try:
        uid = USERNAME.lower().replace(" ", "")
        r = requests.get(
            f"https://pokemonshowdown.com/users/{uid}.json", timeout=10
        )
        if r.status_code != 200:
            return None
        data = r.json()
        ratings = data.get("ratings", {})
        rating = ratings.get("gen9ou")
        if rating:
            elo = rating.get("elo")
            return int(round(float(elo))) if elo is not None else None
    except Exception:
        pass
    return None


def scan_logs_for_battles():
    """Scan log files for completed battles"""
    battles = []
    for logfile in LOG_FILES:
        path = PROJECT_ROOT / logfile
        if not path.exists():
            continue
        content = path.read_text()
        # Find winner lines
        for match in re.finditer(r'Winner: (.+)', content):
            winner = match.group(1).strip()
            won = USERNAME.lower() in winner.lower()
            battles.append({
                "winner": winner,
                "won": won,
                "source": logfile,
            })
    return battles


def get_replay_stats():
    """Analyze saved replays for detailed stats"""
    losses_dir = PROJECT_ROOT / "replay_analysis" / "losses"
    loss_details = []
    if losses_dir.exists():
        for f in sorted(losses_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                loss_details.append({
                    "file": f.name,
                    "replay_id": d.get("replay_id", ""),
                    "mistakes": d.get("mistakes_found", 0),
                    "timestamp": d.get("timestamp", ""),
                })
            except:
                pass
    return loss_details


def print_dashboard(stats):
    elo = stats.get("current_elo", "?")
    wins = stats["win_count"]
    losses = stats["loss_count"]
    total = wins + losses
    wr = f"{wins/total*100:.1f}%" if total > 0 else "N/A"
    gap = (TARGET_ELO - elo) if isinstance(elo, int) else "?"
    
    print("=" * 50)
    print("  FOULER PLAY - ELO DASHBOARD")
    print("=" * 50)
    print(f"  Current ELO:  {elo}")
    print(f"  Target ELO:   {TARGET_ELO}")
    print(f"  Gap:          {gap}")
    print(f"  Win Rate:     {wr} ({wins}W / {losses}L)")
    print(f"  Total Games:  {total}")
    print("=" * 50)
    
    # Recent ELO history
    if stats.get("elo_history"):
        print("\n  ELO History (last 10):")
        for entry in stats["elo_history"][-10:]:
            print(f"    {entry['time']}: {entry['elo']}")
    print()


if __name__ == "__main__":
    stats = load_stats()
    
    # Update ELO
    elo = get_current_elo()
    if elo:
        stats["current_elo"] = elo
        stats["elo_history"].append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "elo": elo,
        })
    
    # Scan for battles
    battles = scan_logs_for_battles()
    stats["win_count"] = sum(1 for b in battles if b["won"])
    stats["loss_count"] = sum(1 for b in battles if not b["won"])
    
    # Get loss details
    stats["loss_details"] = get_replay_stats()
    
    save_stats(stats)
    print_dashboard(stats)
