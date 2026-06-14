#!/usr/bin/env python3
"""learn_climb_monitor.py — single-pane health for "is fouler actually learning
and climbing?". Read-only. Run on JIGGLY from repo root:

    python learn_climb_monitor.py            # human summary
    python learn_climb_monitor.py --json     # machine summary

Reports three signals, each tied to a root cause:
  1. CLIMB  — live ELO trend (battle_stats.json): last-N win-rate + rating drift.
  2. LEARN  — improve_ledger.jsonl: accepts vs reverts/agent_fails, apply-rate.
  3. PLAY   — decisive-MCTS OVERRIDE rate on recent traces (Root #1 health): how
              often the engine still overrides a decisive search. Lower = better;
              the Phase-0 flatness gate should drive this toward ~0.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

BATTLE_STATS = ROOT / "battle_stats.json"
LEDGER = ROOT / "eval_results" / "improve_ledger.jsonl"


def climb_signal(last_n: int = 200) -> dict:
    if not BATTLE_STATS.exists():
        return {"error": "battle_stats.json missing"}
    try:
        battles = json.loads(BATTLE_STATS.read_text(encoding="utf-8")).get("battles", [])
    except Exception as e:
        return {"error": f"battle_stats parse: {e}"}
    n = len(battles)
    recent = battles[-last_n:]
    wins = sum(1 for b in recent if b.get("result") == "win")
    rated = [b.get("rating") for b in recent if b.get("rating") is not None]
    elos = [b.get("elo_after") for b in recent if b.get("elo_after") is not None]
    last_ts = battles[-1].get("timestamp") if battles else None
    life_w = sum(1 for b in battles if b.get("result") == "win")
    return {
        "total_battles": n,
        "lifetime_win_rate": round(life_w / n, 4) if n else None,
        "last_n": len(recent),
        "recent_win_rate": round(wins / len(recent), 4) if recent else None,
        "rating_first_last": [rated[0], rated[-1]] if rated else None,
        "rating_min_max": [min(rated), max(rated)] if rated else None,
        "elo_drift_last_n": round(elos[-1] - elos[0], 1) if len(elos) >= 2 else None,
        "last_battle_ts": last_ts,
    }


def learn_signal() -> dict:
    if not LEDGER.exists():
        return {"error": "improve_ledger.jsonl missing", "accepts": 0}
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    accepts = sum(1 for r in rows if r.get("outcome") in ("accepted", "committed")
                  or (r.get("selfplay_verdict") or {}).get("ACCEPT") is True
                  or r.get("committed") is True)
    reverts = sum(1 for r in rows if r.get("outcome") == "reverted")
    agent_fails = sum(1 for r in rows if r.get("outcome") in ("agent_failed", "error"))
    lease_blocks = sum(1 for r in rows if r.get("outcome") == "blocked_runtime_lease")
    no_patch = sum(1 for r in rows if r.get("outcome") == "no_patch_applied")
    last = rows[-1] if rows else {}
    return {
        "ledger_entries": len(rows),
        "ACCEPTS": accepts,
        "reverts": reverts,
        "agent_failed_or_error": agent_fails,
        "no_patch_applied": no_patch,
        "lease_blocked": lease_blocks,
        "last_outcome": last.get("outcome"),
        "last_issue": last.get("issue"),
    }


def play_signal(window_s: int = 6 * 3600) -> dict:
    try:
        from infrastructure import decision_regret as dr
    except Exception as e:
        return {"error": f"decision_regret import: {e}"}
    since = time.time() - window_s
    cand = dr.candidate_override_rate_from_recent_traces(since_epoch=since)
    suite = dr.load_suite()
    base = dr.baseline_regret_from_suite(suite) if suite else {}
    return {
        "recent_window_hours": round(window_s / 3600, 1),
        "recent_decisive_cases": cand.get("cases"),
        "recent_override_rate": cand.get("decisive_override_rate"),
        "recent_mean_regret": cand.get("mean_regret"),
        "frozen_suite_cases": len(suite),
        "frozen_baseline_override_rate": base.get("override_rate"),
    }


def main():
    as_json = "--json" in sys.argv
    out = {
        "CLIMB": climb_signal(),
        "LEARN": learn_signal(),
        "PLAY": play_signal(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if as_json:
        print(json.dumps(out, indent=2))
        return 0
    c, l, p = out["CLIMB"], out["LEARN"], out["PLAY"]
    print("=== fouler-play learn-and-climb monitor ===")
    print(f"CLIMB: {c.get('total_battles')} battles | lifetime WR {c.get('lifetime_win_rate')} | "
          f"last-{c.get('last_n')} WR {c.get('recent_win_rate')} | "
          f"rating {c.get('rating_first_last')} (min/max {c.get('rating_min_max')}) | "
          f"drift {c.get('elo_drift_last_n')} | last {c.get('last_battle_ts')}")
    print(f"LEARN: {l.get('ledger_entries')} ledger | ACCEPTS={l.get('ACCEPTS')} | "
          f"reverts={l.get('reverts')} | agent_fail={l.get('agent_failed_or_error')} | "
          f"no_patch={l.get('no_patch_applied')} | last={l.get('last_outcome')}")
    print(f"PLAY:  decisive-MCTS override (last {p.get('recent_window_hours')}h): "
          f"{p.get('recent_override_rate')} over {p.get('recent_decisive_cases')} cases | "
          f"frozen baseline {p.get('frozen_baseline_override_rate')} "
          f"({p.get('frozen_suite_cases')} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
