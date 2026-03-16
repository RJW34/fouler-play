#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Run with: python -X utf8 infrastructure/autoresearch/discord_report.py
"""Post a useful performance report to the fouler-play Discord channel.

Instead of just W/L + replay, this posts:
- Per-team record with trends
- Top opponent threats
- Problem matchups
- What autoresearch is working on
- Win rate over last 30/100 games

Called by the Hermes cron 'fouler-play-analysis-poster' every 30 min.
Only posts if there are 10+ new battles since last report.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BATTLE_STATS = PROJECT_ROOT / "battle_stats.json"
STATE_FILE = PROJECT_ROOT / ".discord_report_state.json"
CHANNEL_ID = "1466691161363054840"
MIN_NEW_BATTLES = 10


def load_stats() -> dict:
    if not BATTLE_STATS.exists():
        return {}
    return json.loads(BATTLE_STATS.read_text())


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_total": 0, "last_posted_at": None}
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def build_report(stats: dict, last_total: int) -> str | None:
    battles = stats.get("battles", [])
    total = len(battles)
    new_battles = total - last_total

    if new_battles < MIN_NEW_BATTLES:
        return None

    # Overall record
    wins = sum(1 for b in battles if b.get("result") == "win")
    losses = total - wins
    wr = wins / total * 100 if total > 0 else 0

    # Last 30
    recent = battles[-30:]
    r_wins = sum(1 for b in recent if b.get("result") == "win")
    r_losses = len(recent) - r_wins
    r_wr = r_wins / len(recent) * 100 if recent else 0

    # Per-team breakdown
    team_records: dict[str, dict] = {}
    for b in battles:
        team = b.get("team_file", "unknown")
        if team not in team_records:
            team_records[team] = {"wins": 0, "losses": 0}
        if b.get("result") == "win":
            team_records[team]["wins"] += 1
        else:
            team_records[team]["losses"] += 1

    # New batch record
    new_batch = battles[-new_battles:]
    batch_wins = sum(1 for b in new_batch if b.get("result") == "win")
    batch_losses = new_battles - batch_wins
    batch_wr = batch_wins / new_battles * 100 if new_battles > 0 else 0

    # Build message
    lines = [
        f"📊 **Fouler Play Report** — {new_battles} new battles",
        f"",
        f"**This batch:** {batch_wins}W-{batch_losses}L ({batch_wr:.0f}%)",
        f"**Last 30:** {r_wins}W-{r_losses}L ({r_wr:.0f}%)",
        f"**All time:** {wins}W-{losses}L ({wr:.0f}%) across {total} battles",
        f"",
        f"**Per-team:**",
    ]

    for team, record in sorted(team_records.items(), key=lambda x: x[1]["wins"] / max(x[1]["wins"] + x[1]["losses"], 1), reverse=True):
        tw = record["wins"]
        tl = record["losses"]
        t_total = tw + tl
        t_wr = tw / t_total * 100 if t_total > 0 else 0
        icon = "🟢" if t_wr >= 55 else "🟡" if t_wr >= 45 else "🔴"
        lines.append(f"{icon} {team}: {tw}W-{tl}L ({t_wr:.0f}%)")

    # Add competitive analysis if available
    try:
        from infrastructure.autoresearch.matchup_analyzer import analyze_opponent_threats, analyze_team_matchups
        threats = analyze_opponent_threats()
        top_threats = threats.get("top_threats", [])[:3]
        if top_threats:
            lines.append(f"")
            lines.append(f"**Top threats we lose to:**")
            for t in top_threats:
                lines.append(f"- {t['pokemon']}: in {t['losses_against']} losses ({t['loss_rate_pct']}%)")

        matchups = analyze_team_matchups()
        problems = matchups.get("problem_matchups", [])[:3]
        if problems:
            lines.append(f"")
            lines.append(f"**Problem matchups:**")
            for p in problems:
                lines.append(f"- {p['team']} vs {p['opponent_pokemon']}: {p['wins']}W-{p['losses']}L ({p['win_rate_pct']}%)")
    except Exception:
        pass

    return "\n".join(lines)


def send_to_discord(message: str) -> bool:
    # Use WSL-safe path (works on both Windows and Linux)
    if os.name == "nt":
        script = Path(r"D:\deku-workspace\scripts\send_discord_message.py")
    else:
        script = Path("/mnt/d/deku-workspace/scripts/send_discord_message.py")
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--channel", CHANNEL_ID, "--message", message],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception as exc:
        print(f"[DEBUG] Discord post error: {exc}", file=sys.stderr)
        return False


def main() -> int:
    stats = load_stats()
    state = load_state()

    report = build_report(stats, state.get("last_total", 0))
    if not report:
        print(f"Not enough new battles (need {MIN_NEW_BATTLES})")
        return 0

    print(report)
    print()

    if "--dry-run" not in sys.argv:
        ok = send_to_discord(report)
        if ok:
            save_state({
                "last_total": len(stats.get("battles", [])),
                "last_posted_at": datetime.now(timezone.utc).isoformat(),
            })
            print("Posted to Discord")
        else:
            print("Discord post failed")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
