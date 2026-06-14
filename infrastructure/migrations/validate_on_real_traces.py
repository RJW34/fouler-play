#!/usr/bin/env python3
"""Validate flatness + regret modules against the REAL evidence traces.
Run on JIGGLY from repo root. Read-only; samples up to N traces."""
from __future__ import annotations
import json, sys, random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from fp.search import flatness as fl
from infrastructure import decision_regret as dr

TRACES = ROOT / "replay_analysis" / "evidence_traces"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3000

files = list(TRACES.glob("*.json"))
random.seed(7)
random.shuffle(files)
files = files[:N]

flat = decisive = mixed = 0
override_decisive = matched_decisive = 0     # on decisive turns: did final choice match MCTS top?
eval_flips_mcts = 0                           # final choice != mcts top (any regime)
turns = 0
regret_cases = []

for fp in files:
    try:
        t = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        continue
    mcts = dr._normalize(t.get("mcts_policy_raw") or {})
    if not mcts or len(mcts) < 2:
        continue
    turns += 1
    _, meta = fl.flatness_gated_alpha(mcts)
    reg = meta["regime"]
    if reg == "flat": flat += 1
    elif reg == "decisive": decisive += 1
    else: mixed += 1

    ranked = sorted(mcts.items(), key=lambda x: x[1], reverse=True)
    mcts_top = ranked[0][0]
    choice = str(t.get("choice") or "").strip()
    if choice and choice != mcts_top:
        eval_flips_mcts += 1

    case = dr.case_from_trace(t)
    if case and case.decisive:
        if case.choice == case.mcts_top:
            matched_decisive += 1
        else:
            override_decisive += 1
            regret_cases.append(case)

print(json.dumps({
    "traces_with_mcts_policy": turns,
    "regime_counts": {"flat": flat, "decisive": decisive, "mixed": mixed},
    "regime_pct": {
        "flat": round(100*flat/turns, 1) if turns else 0,
        "decisive": round(100*decisive/turns, 1) if turns else 0,
        "mixed": round(100*mixed/turns, 1) if turns else 0,
    },
    "eval_flips_mcts_top_pct": round(100*eval_flips_mcts/turns, 1) if turns else 0,
    "decisive_turns": matched_decisive + override_decisive,
    "decisive_OVERRIDE_count": override_decisive,
    "decisive_MATCH_count": matched_decisive,
    "decisive_override_rate_pct": round(100*override_decisive/(matched_decisive+override_decisive), 1) if (matched_decisive+override_decisive) else 0,
}, indent=2))
