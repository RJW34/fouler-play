#!/usr/bin/env python3
"""Score fouler-play against its done registry, from battle records only.

Reports:

    FOULER   0 / 13 complete   9 incomplete   4 unverifiable

WHY IT CANNOT BE FOOLED
-----------------------
`truth/latest-elo-proof.json` is a report this project generates about its own
performance. **This scorer does not read its verdict.** It recomputes every ELO
predicate from `battle_stats.json` -- the per-battle records carrying the rating
Showdown returned -- using the thresholds in the registry. A process must never
grade its own homework; recomputing from the observation is what makes this an
external check rather than a restatement.

The learn-loop half is scored the same way: against hypothesis artifacts and the
battle store, never against the closer's own `latest.json` transition log. That
log currently reports `open -> implemented` on every run for the same three
hypotheses, because a sync rewrites them back to `open` afterwards. A transition
count is not progress if the transition never sticks.

THREE STATES
------------
  complete      the predicate holds against the battle records
  incomplete    the predicate fails
  unverifiable  the evidence needed is absent (no battle store, no ledger)

READ-ONLY. Does not touch the running batch.

Usage:
    python scripts/done_registry_score.py
    python scripts/done_registry_score.py --state-root /path/to/fouler/state
    python scripts/done_registry_score.py --json out.json --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for candidate in (str(ROOT), str(SCRIPTS)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import loss_hypothesis_burndown as BD  # noqa: E402

COMPLETE = "complete"
INCOMPLETE = "incomplete"
UNVERIFIABLE = "unverifiable"

REGISTRY_PATH = ROOT / "data" / "completion" / "done_registry.json"


def _rated(battles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for battle in battles:
        rating = battle.get("rating")
        if isinstance(rating, (int, float)):
            out.append(battle)
    return out


def _sustain_window(battles: list[dict[str, Any]], floor: float) -> list[dict[str, Any]]:
    """Games played at or above the floor -- the sustain window."""
    return [b for b in _rated(battles) if float(b["rating"]) >= float(floor)]


def _evaluate(check: dict[str, Any], *, battles: list[dict], burndown: dict,
              battle_store: Path | None) -> dict[str, Any]:
    kind = check.get("kind")

    if kind in ("peak_rating_at_least", "games_at_or_above_floor",
                "sustain_win_rate_at_least", "team_coverage_at_least",
                "max_drawdown_within"):
        rated = _rated(battles)
        if not rated:
            return {"passed": None, "evidence": "no rated battle records available"}

        if kind == "peak_rating_at_least":
            peak = max(float(b["rating"]) for b in rated)
            want = float(check["rating"])
            return {"passed": peak >= want,
                    "evidence": f"peak rating {peak:.1f} over {len(rated)} rated games "
                                f"(need >= {want:.0f})"}

        floor = float(check["floor"])
        window = _sustain_window(battles, floor)

        if kind == "games_at_or_above_floor":
            need = int(check["minimum"])
            return {"passed": len(window) >= need,
                    "evidence": f"{len(window)} game(s) at or above {floor:.0f} "
                                f"(need {need})"}

        if not window:
            return {"passed": False,
                    "evidence": f"sustain window is empty — 0 games at or above {floor:.0f}"}

        if kind == "sustain_win_rate_at_least":
            wins = sum(1 for b in window if str(b.get("result")).lower() == "win")
            rate = wins / len(window)
            need = float(check["minimum"])
            return {"passed": rate >= need,
                    "evidence": f"sustain win rate {rate:.0%} over {len(window)} games "
                                f"(need {need:.0%})"}

        if kind == "team_coverage_at_least":
            counts = Counter(str(b.get("team_file")) for b in window)
            per_team = int(check["minimumPerTeam"])
            short = {t: counts.get(t, 0) for t in check["teams"]
                     if counts.get(t, 0) < per_team}
            return {"passed": not short,
                    "evidence": f"team coverage {dict((t, counts.get(t, 0)) for t in check['teams'])} "
                                f"(need {per_team} each)"}

        if kind == "max_drawdown_within":
            peak = None
            worst = 0.0
            for battle in window:
                rating = float(battle["rating"])
                peak = rating if peak is None else max(peak, rating)
                worst = max(worst, peak - rating)
            allowed = float(check["maximum"])
            return {"passed": worst <= allowed,
                    "evidence": f"max drawdown {worst:.1f} inside the sustain window "
                                f"(allowed {allowed:.0f})"}

    counts = burndown["counts"]

    if kind == "losses_without_hypothesis":
        if not counts["losses"]:
            return {"passed": None, "evidence": "no loss records available"}
        value = counts["lossesWithoutHypothesis"]
        return {"passed": value <= int(check["maximum"]),
                "evidence": f"{value} of {counts['losses']} losses have no hypothesis"}

    if kind == "hypotheses_not_terminal":
        if not counts["hypotheses"]:
            return {"passed": None, "evidence": "no hypothesis ledger entries found"}
        value = counts["hypothesesUnconverted"]
        return {"passed": value <= int(check["maximum"]),
                "evidence": f"{value} of {counts['hypotheses']} hypotheses never "
                            f"reached a terminal state"}

    if kind == "implementation_predates_hypothesis":
        offenders = []
        checked = 0
        for hypothesis in BD.load_hypotheses(Path(burndown["stateRoot"]), []):
            implementation = hypothesis.get("implementation") or {}
            committed = implementation.get("committedAt")
            opened = hypothesis.get("openedAt")
            if not committed or not opened:
                continue
            checked += 1
            if str(committed)[:10] < str(opened)[:10]:
                offenders.append(hypothesis.get("id"))
        if not checked:
            return {"passed": None,
                    "evidence": "no hypothesis carries an implementation commit to check"}
        return {"passed": len(offenders) <= int(check["maximum"]),
                "evidence": f"{len(offenders)} of {checked} implementing commits "
                            f"predate their hypothesis"}

    if kind == "battle_store_resolvable":
        if battle_store is None:
            return {"passed": False,
                    "evidence": "battle store not found — terminal states are "
                                "unreachable by construction"}
        return {"passed": counts["battles"] >= int(check["minimumBattles"]),
                "evidence": f"battle store {battle_store} has {counts['battles']} records"}

    if kind == "grounded_detectors_represented":
        histogram = burndown.get("failureClassHistogram") or {}
        if not histogram:
            return {"passed": None, "evidence": "no hypotheses to classify"}
        grounded = [d for d in check["groundedDetectors"] if histogram.get(d)]
        return {"passed": len(grounded) >= int(check["minimumRepresented"]),
                "evidence": f"grounded detectors in the ledger: {grounded or 'none'}; "
                            f"present classes: {sorted(histogram)}"}

    return {"passed": None, "evidence": f"no evaluator for check kind {kind!r}"}


def score(registry: dict[str, Any], state_root: Path,
          extra_hypothesis_dirs: list[Path]) -> dict[str, Any]:
    battles, battle_store = BD.load_battles(state_root)
    burndown = BD.build_burndown(state_root, extra_hypothesis_dirs)

    rows: list[dict[str, Any]] = []
    counts = {COMPLETE: 0, INCOMPLETE: 0, UNVERIFIABLE: 0}
    per_group: dict[str, dict[str, int]] = {}

    for entry in registry.get("entries", []):
        group = entry.get("group", "other")
        bucket = per_group.setdefault(group, {COMPLETE: 0, INCOMPLETE: 0, UNVERIFIABLE: 0})
        results = [
            {"kind": check.get("kind"), "derivedFrom": check.get("derivedFrom"),
             **_evaluate(check, battles=battles, burndown=burndown,
                         battle_store=battle_store)}
            for check in entry.get("checks", [])
        ]
        if not results:
            state, reason = UNVERIFIABLE, "no checks derived"
        elif any(r["passed"] is False for r in results):
            state = INCOMPLETE
            reason = "; ".join(r["evidence"] for r in results if r["passed"] is False)
        elif any(r["passed"] is None for r in results):
            state = UNVERIFIABLE
            reason = "; ".join(r["evidence"] for r in results if r["passed"] is None)
        else:
            state = COMPLETE
            reason = "; ".join(r["evidence"] for r in results)

        counts[state] += 1
        bucket[state] += 1
        rows.append({"id": entry["id"], "group": group, "title": entry.get("title"),
                     "state": state, "reason": reason, "checks": results})

    return {
        "schemaVersion": "fouler-play-done-score/v1",
        "registrySchema": registry.get("schemaVersion"),
        "stateRoot": str(state_root),
        "battleStore": str(battle_store) if battle_store else None,
        "total": len(rows),
        "counts": counts,
        "perGroup": per_group,
        "burndownCounts": burndown["counts"],
        "entries": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=str(REGISTRY_PATH))
    ap.add_argument("--state-root", default=str(BD.default_state_root()))
    ap.add_argument("--hypothesis-dir", action="append", default=[])
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    report = score(registry, Path(args.state_root),
                   [Path(p) for p in args.hypothesis_dir])
    counts = report["counts"]

    print(f"\n=== FOULER — {counts[COMPLETE]}/{report['total']} complete ===")
    print(f"  state root   : {report['stateRoot']}")
    print(f"  battle store : {report['battleStore'] or 'NOT FOUND'}")
    print(f"  complete     : {counts[COMPLETE]}")
    print(f"  incomplete   : {counts[INCOMPLETE]}")
    print(f"  unverifiable : {counts[UNVERIFIABLE]}")
    print("\n  per group:")
    for group, bucket in report["perGroup"].items():
        print(f"    {group:<14} {bucket[COMPLETE]:>2} complete  "
              f"{bucket[INCOMPLETE]:>2} incomplete  {bucket[UNVERIFIABLE]:>2} unverifiable")

    print("\n  entries:")
    for row in report["entries"]:
        print(f"    [{row['state']:<12}] {row['id']}")
        if args.verbose or row["state"] != COMPLETE:
            print(f"                   {row['reason'][:150]}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")

    if args.strict and counts[COMPLETE] != report["total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
