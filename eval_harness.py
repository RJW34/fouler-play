#!/usr/bin/env python3
"""
eval_harness.py — Karpathy-style eval harness for fouler-play.

Reads battle_stats.json, computes win rate over the last N battles,
optionally filters by team, outputs JSON + human-readable summary,
and saves baseline snapshots to eval_results/.

Usage:
    python eval_harness.py                    # last 30 battles
    python eval_harness.py --last 50          # last 50 battles
    python eval_harness.py --team fat-team-1-stall  # filter by team
    python eval_harness.py --all              # all battles
    python eval_harness.py --json             # JSON-only output
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
EVAL_RESULTS_DIR = PROJECT_ROOT / "eval_results"


def load_battles() -> list[dict]:
    """Load all battles from battle_stats.json."""
    if not BATTLE_STATS_FILE.exists():
        print(f"Error: {BATTLE_STATS_FILE} not found", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(BATTLE_STATS_FILE.read_text(encoding="utf-8"))
        battles = data.get("battles", [])
        if not isinstance(battles, list):
            print("Error: 'battles' key is not a list", file=sys.stderr)
            sys.exit(1)
        return battles
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error reading battle_stats.json: {e}", file=sys.stderr)
        sys.exit(1)


def compute_stats(battles: list[dict], team_filter: str | None = None) -> dict:
    """Compute win rate and team breakdown for a list of battles."""
    if team_filter:
        battles = [b for b in battles if team_filter in b.get("team_file", "")]

    total = len(battles)
    if total == 0:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "baseline_date": datetime.now(timezone.utc).isoformat(),
            "team_breakdown": {},
        }

    wins = sum(1 for b in battles if b.get("result") == "win")
    losses = sum(1 for b in battles if b.get("result") == "loss")
    win_rate = round(wins / total, 4) if total > 0 else 0.0

    # Team breakdown
    team_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0})
    for b in battles:
        team = b.get("team_file", "unknown")
        if b.get("result") == "win":
            team_stats[team]["wins"] += 1
        elif b.get("result") == "loss":
            team_stats[team]["losses"] += 1

    team_breakdown = {}
    for team, stats in sorted(team_stats.items()):
        t_total = stats["wins"] + stats["losses"]
        team_breakdown[team] = {
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": round(stats["wins"] / t_total, 4) if t_total > 0 else 0.0,
        }

    return {
        "n": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "baseline_date": datetime.now(timezone.utc).isoformat(),
        "team_breakdown": team_breakdown,
    }


def print_summary(result: dict) -> None:
    """Print human-readable summary to stdout."""
    n = result["n"]
    wins = result["wins"]
    losses = result["losses"]
    wr = result["win_rate"]

    print("=" * 50)
    print("  fouler-play Eval Harness — Baseline Report")
    print("=" * 50)
    print(f"  Date:      {result['baseline_date'][:19]}")
    print(f"  Battles:   {n}")
    print(f"  Wins:      {wins}")
    print(f"  Losses:    {losses}")
    print(f"  Win Rate:  {wr:.1%}")
    print("-" * 50)

    if result["team_breakdown"]:
        print("  Team Breakdown:")
        for team, stats in result["team_breakdown"].items():
            tw = stats["wins"]
            tl = stats["losses"]
            twr = stats["win_rate"]
            bar_len = 20
            filled = int(twr * bar_len)
            bar = "#" * filled + "-" * (bar_len - filled)
            print(f"    {team:<25} {tw}W/{tl}L  {twr:.0%}  [{bar}]")

    print("=" * 50)


def save_result(result: dict) -> Path:
    """Save result to eval_results/YYYY-MM-DD-HH-baseline.json."""
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    filename = f"{now.strftime('%Y-%m-%d-%H')}-baseline.json"
    filepath = EVAL_RESULTS_DIR / filename
    filepath.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return filepath


def main():
    parser = argparse.ArgumentParser(description="fouler-play eval harness")
    parser.add_argument(
        "--last", type=int, default=30,
        help="Number of recent battles to evaluate (default: 30)"
    )
    parser.add_argument(
        "--team", type=str, default=None,
        help="Filter by team file name (substring match)"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Evaluate all battles (ignore --last)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON only (no human-readable summary)"
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Don't save result to eval_results/"
    )
    args = parser.parse_args()

    battles = load_battles()

    if not args.all:
        battles = battles[-args.last:]

    result = compute_stats(battles, team_filter=args.team)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result)
        print(f"\n  JSON:\n{json.dumps(result, indent=2)}")

    if not args.no_save:
        filepath = save_result(result)
        if not args.json:
            print(f"\n  Saved to: {filepath}")


if __name__ == "__main__":
    main()
