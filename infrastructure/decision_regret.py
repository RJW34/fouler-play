#!/usr/bin/env python3
"""decision_regret.py — dense, transfer-valid offline gate for the learning loop.

ROOT-CAUSE FIX (learning loop, signal side). The acceptance gate measured
NEW-vs-OLD *mirror self-play* win-rate. That signal is (a) low-information
(one noisy bit per multi-minute battle), (b) high-variance, and (c) MIRROR-
BIASED: two copies of the same engine share the same blind spots, so a mirror
cancels exactly the edge you are trying to detect. The live ELO record is a
random walk (50.0% lifetime; per-cycle slope swings +10/-10 per game), so it is
also too noisy to gate on over short windows. Result: no change could ever be
*certified* as a real improvement, which is half of why the loop never accepts.

This module gates on a DENSE, DETERMINISTIC, TRANSFER-VALID signal instead:
per-decision REGRET against the engine's own reference MCTS search, measured on
a FROZEN suite of real decision positions harvested from LOST battles.

Substrate: every live turn is logged to replay_analysis/evidence_traces/*.json
with `mcts_policy_raw` (the Rust MCTS visit policy = the reference search),
`mcts_meta`, `eval.policy_pre_penalty` / `policy_post_penalty`, the final
`choice`, and `legalOptions`. On DECISIVE-MCTS turns (the search clearly
preferred one move) the well-known failure mode is the homegrown eval/penalty
cascade OVERRIDING that move. Regret on such a turn = 1 if the engine's final
choice differs from the decisive-MCTS top move, weighted by how decisive MCTS
was. Lower aggregate regret => the policy stops sabotaging its own search =>
the change that actually moves ladder ELO. Hundreds-to-thousands of labeled
datapoints, no ladder RNG, no mirror cancellation.

The suite is frozen to a file so OLD and NEW are scored on the IDENTICAL
positions. This module computes regret from STORED per-turn policies (fast, no
engine needed); the agent harness can additionally re-run the live engine over
the frozen `showdownRequest`/`snapshot` for an even stronger signal, but the
stored-policy regret already discriminates the override pathology directly.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACES_DIR = PROJECT_ROOT / "replay_analysis" / "evidence_traces"
SUITE_PATH = PROJECT_ROOT / "eval_results" / "regret_suite.jsonl"

# A turn counts as "decisive MCTS" when the top MCTS move holds at least this
# fraction of the visit mass over the runner-up margin. Tunable.
DECISIVE_TOP_MASS = float(os.getenv("REGRET_DECISIVE_TOP_MASS", "0.60"))
DECISIVE_MARGIN = float(os.getenv("REGRET_DECISIVE_MARGIN", "0.15"))


@dataclass
class RegretCase:
    battle_tag: str
    turn: int
    mcts_policy: dict          # move -> normalized visit weight (reference search)
    legal_moves: list          # list of legal move ids
    choice: str                # the final choice the live engine made
    decisive: bool             # whether MCTS was decisive on this turn
    mcts_top: str              # MCTS's top move
    mcts_top_mass: float       # fraction of mass on the top move


def _normalize(policy: dict) -> dict:
    out = {}
    total = 0.0
    for k, v in (policy or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            out[str(k)] = fv
            total += fv
    if total <= 0:
        return {}
    return {k: v / total for k, v in out.items()}


def _ranked(policy: dict):
    return sorted(policy.items(), key=lambda x: x[1], reverse=True)


def case_from_trace(trace: dict) -> RegretCase | None:
    mcts_raw = _normalize(trace.get("mcts_policy_raw") or {})
    if not mcts_raw or len(mcts_raw) < 2:
        return None
    ranked = _ranked(mcts_raw)
    top_move, top_mass = ranked[0]
    second_mass = ranked[1][1] if len(ranked) > 1 else 0.0
    decisive = (top_mass >= DECISIVE_TOP_MASS) and ((top_mass - second_mass) >= DECISIVE_MARGIN)
    choice = str(trace.get("choice") or "").strip()
    legal = []
    lo = trace.get("legalOptions") or {}
    for m in (lo.get("legalMoves") or []):
        if isinstance(m, dict) and m.get("id"):
            legal.append(str(m["id"]))
    return RegretCase(
        battle_tag=str(trace.get("battle_tag") or ""),
        turn=int(trace.get("turn") or 0),
        mcts_policy=mcts_raw,
        legal_moves=legal,
        choice=choice,
        decisive=decisive,
        mcts_top=top_move,
        mcts_top_mass=round(top_mass, 4),
    )


def harvest_suite(loss_battle_tags: set[str] | None = None,
                  max_cases: int = 400,
                  decisive_only: bool = True) -> list[RegretCase]:
    """Scan evidence_traces and build a frozen suite of decisive-MCTS cases.

    If loss_battle_tags is provided, restrict to turns from those (lost) battles
    — the positions where overriding the search most plausibly cost the game.
    """
    cases: list[RegretCase] = []
    if not TRACES_DIR.exists():
        return cases
    # Newest-first so the suite reflects current play.
    files = sorted(TRACES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for fp in files:
        if len(cases) >= max_cases:
            break
        try:
            trace = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        tag = str(trace.get("battle_tag") or "")
        if loss_battle_tags is not None and tag not in loss_battle_tags:
            continue
        case = case_from_trace(trace)
        if case is None:
            continue
        if decisive_only and not case.decisive:
            continue
        cases.append(case)
    return cases


def write_suite(cases: list[RegretCase], path: Path = SUITE_PATH) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps({
                "battle_tag": c.battle_tag,
                "turn": c.turn,
                "mcts_policy": c.mcts_policy,
                "legal_moves": c.legal_moves,
                "choice": c.choice,
                "decisive": c.decisive,
                "mcts_top": c.mcts_top,
                "mcts_top_mass": c.mcts_top_mass,
            }) + "\n")
    return len(cases)


def load_suite(path: Path = SUITE_PATH) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def regret_of_choice(case: dict, choice: str) -> float:
    """Regret of picking `choice` on this case, given the reference MCTS policy.

    Regret = (mass MCTS put on its top move) - (mass MCTS put on `choice`),
    clamped at 0. On a decisive-MCTS turn, choosing the MCTS top move => 0
    regret; choosing a move MCTS de-prioritized => regret up to top_mass.
    """
    pol = case.get("mcts_policy") or {}
    top = float(case.get("mcts_top_mass") or 0.0)
    got = float(pol.get(choice, 0.0))
    return max(0.0, top - got)


def score_choices_against_suite(choices_by_key: dict, suite: list[dict]) -> dict:
    """Aggregate regret of a candidate's choices over the frozen suite.

    choices_by_key maps "<battle_tag>#<turn>" -> chosen move id. Cases with no
    provided choice fall back to the suite's recorded live choice (so a partial
    re-run still scores). Returns a metrics dict; lower mean_regret is better.
    """
    n = 0
    total = 0.0
    overrides = 0          # decisive turns where choice != mcts_top
    matched = 0            # decisive turns where choice == mcts_top
    per_case = []
    for case in suite:
        key = f"{case.get('battle_tag')}#{case.get('turn')}"
        choice = choices_by_key.get(key, case.get("choice"))
        if not choice:
            continue
        r = regret_of_choice(case, choice)
        n += 1
        total += r
        if case.get("mcts_top") == choice:
            matched += 1
        else:
            overrides += 1
        per_case.append({"key": key, "choice": choice, "regret": round(r, 4),
                         "mcts_top": case.get("mcts_top")})
    mean = (total / n) if n else 0.0
    return {
        "cases": n,
        "mean_regret": round(mean, 5),
        "total_regret": round(total, 4),
        "override_rate": round(overrides / n, 4) if n else 0.0,
        "match_rate": round(matched / n, 4) if n else 0.0,
        "per_case": per_case[:50],
    }


def baseline_regret_from_suite(suite: list[dict]) -> dict:
    """Regret of the LIVE-recorded choices on the suite — the incumbent's score.
    This is the OLD number a candidate must beat (lower is better)."""
    choices = {f"{c.get('battle_tag')}#{c.get('turn')}": c.get("choice") for c in suite}
    return score_choices_against_suite(choices, suite)


def candidate_override_rate_from_recent_traces(since_epoch: float,
                                               decisive_only: bool = True) -> dict:
    """Score traces WRITTEN AFTER since_epoch (i.e. produced by a candidate eval
    run) by their decisive-MCTS override rate / mean regret. This is the
    candidate's number to compare against the frozen-suite baseline."""
    if not TRACES_DIR.exists():
        return {"cases": 0, "note": "no traces dir"}
    n = matched = overrides = 0
    total_regret = 0.0
    for fp in TRACES_DIR.glob("*.json"):
        try:
            if fp.stat().st_mtime < since_epoch:
                continue
            trace = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        case = case_from_trace(trace)
        if case is None:
            continue
        if decisive_only and not case.decisive:
            continue
        n += 1
        r = regret_of_choice({"mcts_policy": case.mcts_policy,
                              "mcts_top_mass": case.mcts_top_mass}, case.choice)
        total_regret += r
        if case.choice == case.mcts_top:
            matched += 1
        else:
            overrides += 1
    return {
        "cases": n,
        "decisive_override_rate": round(overrides / n, 4) if n else None,
        "match_rate": round(matched / n, 4) if n else None,
        "mean_regret": round(total_regret / n, 5) if n else None,
    }


def regret_gate(since_epoch: float, tolerance: float = 0.0) -> tuple[bool, dict]:
    """Dense pre-gate: ACCEPT-contribution iff the candidate's decisive-MCTS
    override rate (on traces it just produced) is <= the frozen-suite baseline
    override rate (+ tolerance). Lower override of a DECISIVE search is strictly
    better play. SKIPS (returns True) if the suite or candidate traces are
    missing, so it never blocks the pipeline on absent data.

    Returns (accept, detail).
    """
    # Read SUITE_PATH dynamically so callers/tests can repoint it at runtime.
    import infrastructure.decision_regret as _self
    suite = load_suite(_self.SUITE_PATH)
    if not suite:
        return True, {"skipped": "no frozen regret suite (run decision_regret.py --build)"}
    base = baseline_regret_from_suite(suite)
    cand = candidate_override_rate_from_recent_traces(since_epoch)
    if not cand.get("cases"):
        return True, {"skipped": "no candidate traces since gate start",
                      "baseline_override_rate": base.get("override_rate")}
    base_or = float(base.get("override_rate") or 0.0)
    cand_or = float(cand.get("decisive_override_rate") or 0.0)
    accept = cand_or <= (base_or + tolerance)
    return accept, {
        "rule": "candidate decisive-MCTS override_rate <= baseline + tolerance",
        "baseline_override_rate": round(base_or, 4),
        "candidate_override_rate": round(cand_or, 4),
        "tolerance": tolerance,
        "candidate_cases": cand.get("cases"),
        "baseline": base,
        "candidate": cand,
        "ACCEPT": accept,
    }


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Build/inspect the decision-regret suite.")
    p.add_argument("--build", action="store_true", help="harvest + write the frozen suite")
    p.add_argument("--max-cases", type=int, default=400)
    p.add_argument("--losses-only", action="store_true",
                   help="restrict to turns from lost battles (uses replay_analysis/losses)")
    p.add_argument("--baseline", action="store_true",
                   help="print incumbent regret on the existing suite")
    args = p.parse_args(argv)

    if args.build:
        loss_tags = None
        if args.losses_only:
            loss_tags = set()
            losses_dir = PROJECT_ROOT / "replay_analysis" / "losses"
            for fp in losses_dir.glob("*.json"):
                # filenames embed the gen9ou-<id>; map to battle-<id> tag form too
                name = fp.stem
                for token in name.split("_"):
                    if token.startswith("gen9ou-"):
                        loss_tags.add(token)
                        loss_tags.add("battle-" + token)
        cases = harvest_suite(loss_battle_tags=loss_tags, max_cases=args.max_cases)
        n = write_suite(cases)
        print(json.dumps({"built": n, "path": str(SUITE_PATH),
                          "decisive_top_mass": DECISIVE_TOP_MASS}))
        return 0

    if args.baseline:
        suite = load_suite()
        print(json.dumps({"suite_cases": len(suite),
                          "baseline": baseline_regret_from_suite(suite)}))
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
