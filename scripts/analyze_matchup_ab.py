#!/usr/bin/env python3
"""Analyze the matchup-memory A/B experiment (forward-mandate validation, 2026-06-24).

Joins the per-battle A/B arm log (``logs/matchup_ab_log.jsonl``, written by
``fp.matchup_memory`` when ``MATCHUP_MEMORY_AB=1``) against decided results in
``battle_stats.json`` and reports win rate for the bias-ON arm vs the bias-OFF
arm. This is the honest test of whether the matchup-memory lever actually helps:
same bot, same teams, same ladder, randomized per battle.

Usage:
    python scripts/analyze_matchup_ab.py [--min-per-arm 30]

Prints a JSON summary. ``verdict`` is one of:
    "bias-helps"   bias-ON WR is higher by a margin beyond noise (>= ~2 std err)
    "bias-hurts"   bias-ON WR is lower by that margin
    "inconclusive" within noise / not enough samples yet
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AB_LOG = ROOT / "logs" / "matchup_ab_log.jsonl"
BATTLE_STATS = ROOT / "battle_stats.json"


def _norm_id(value: object) -> str:
    """Normalize a battle id/tag to a comparable core (strip 'battle-' prefix)."""
    s = str(value or "").strip().lower()
    s = re.sub(r"^battle-", "", s)
    return s


def load_arms() -> dict[str, str]:
    arms: dict[str, str] = {}
    if not AB_LOG.exists():
        return arms
    with AB_LOG.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            bid = _norm_id(rec.get("battle_id"))
            arm = rec.get("arm")
            if bid and arm in {"on", "off"}:
                arms[bid] = arm  # last write wins (stable per battle anyway)
    return arms


def load_results() -> dict[str, str]:
    results: dict[str, str] = {}
    if not BATTLE_STATS.exists():
        return results
    data = json.loads(BATTLE_STATS.read_text(encoding="utf-8"))
    for row in data.get("battles", []):
        if not isinstance(row, dict):
            continue
        res = str(row.get("result") or "").lower()
        if res not in {"win", "loss"}:
            continue
        for key in ("battle_id", "battle_tag", "replay_id", "id"):
            bid = _norm_id(row.get(key))
            if bid:
                results[bid] = res
                break
    return results


def _wr(wins: int, total: int) -> float | None:
    return round(wins / total, 4) if total else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-per-arm", type=int, default=30,
                        help="Minimum decided battles per arm before a verdict is offered.")
    args = parser.parse_args(argv)

    arms = load_arms()
    results = load_results()

    counts = {"on": {"win": 0, "loss": 0}, "off": {"win": 0, "loss": 0}}
    matched = 0
    for bid, arm in arms.items():
        res = results.get(bid)
        if res in {"win", "loss"}:
            counts[arm][res] += 1
            matched += 1

    on_n = counts["on"]["win"] + counts["on"]["loss"]
    off_n = counts["off"]["win"] + counts["off"]["loss"]
    on_wr = _wr(counts["on"]["win"], on_n)
    off_wr = _wr(counts["off"]["win"], off_n)

    verdict = "inconclusive"
    delta = None
    z = None
    if on_n >= args.min_per_arm and off_n >= args.min_per_arm and on_wr is not None and off_wr is not None:
        delta = round(on_wr - off_wr, 4)
        # two-proportion standard error
        se = math.sqrt(on_wr * (1 - on_wr) / on_n + off_wr * (1 - off_wr) / off_n)
        z = round(delta / se, 2) if se > 0 else None
        if z is not None and z >= 1.64:       # ~95% one-sided
            verdict = "bias-helps"
        elif z is not None and z <= -1.64:
            verdict = "bias-hurts"
        else:
            verdict = "inconclusive"

    summary = {
        "abLogPath": str(AB_LOG),
        "battleStatsPath": str(BATTLE_STATS),
        "armRecords": len(arms),
        "matchedDecidedBattles": matched,
        "biasOn": {"n": on_n, "wins": counts["on"]["win"], "losses": counts["on"]["loss"], "winRate": on_wr},
        "biasOff": {"n": off_n, "wins": counts["off"]["win"], "losses": counts["off"]["loss"], "winRate": off_wr},
        "winRateDelta_onMinusOff": delta,
        "zScore": z,
        "minPerArm": args.min_per_arm,
        "verdict": verdict,
        "note": "verdict requires >= minPerArm decided battles in BOTH arms; "
                "z>=1.64 ~ 95% one-sided that bias-ON beats bias-OFF.",
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
