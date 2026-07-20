#!/usr/bin/env python3
"""Derive the fouler-play done registry from the in-repo ladder contract.

WHY THIS EXISTS
---------------
The owner's bar for fouler-play is two things: **1700 ELO sustained**, and the
**learn-from-losses loop demonstrably closed** (losses -> hypotheses -> tested ->
applied). Neither existed as a predicate anything could score. The ELO half was
implemented inside a report generator; the learn-loop half was not implemented
at all, so "the loop is closed" was being inferred from the ledger count moving
from 0 to 3 -- which is satisfiable without any learning occurring.

This script derives both halves into `data/completion/done_registry.json` from
constants that already live in this repo:

  scripts/devstream_cycle_report.py   ELO_SUSTAIN_* -- the sustain contract
  scripts/fouler_mission_monitor.py   CANONICAL_TARGET_RATING, LADDER_STAGE_POLICY,
                                      SUSTAIN_* -- the ladder and its floors

Nothing here invents a criterion. Edit those constants and the registry follows;
a test fails if the committed file drifts.

THE LEARN-LOOP PREDICATES
-------------------------
These are the half that did not exist. Each is externally checkable against
artifacts the loop itself does not author, and each was chosen because it is
currently violated in a way that a naive "ledger has entries" check misses:

  loss-coverage        every rated loss is cited by a hypothesis, or explicitly
                       triaged as not worth one. Uncited losses are lost signal.
  hypotheses-terminal  hypotheses reach kept/reverted, not just 'open'. A
                       hypothesis that never resolves was never tested.
  implementation-after-hypothesis
                       a hypothesis's implementing commit must be dated AFTER
                       the hypothesis opened. A commit that predates it cannot
                       be a fix for it -- it is usually the commit that added
                       the detector, certifying its own output.
  measurement-store-resolvable
                       the battle store the closer measures against must exist
                       and be non-empty, or terminal states are unreachable by
                       construction and the loop can never close.
  detector-diversity   grounded detectors must be represented, not only the
                       templated one. Ranking on raw frequency hands the queue
                       to whichever detector fires most, which is the loosest
                       predicate, not the most informative one.

READ-ONLY and OFFLINE.

Usage:
    python scripts/build_done_registry.py
    python scripts/build_done_registry.py --check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for candidate in (str(ROOT), str(SCRIPTS)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

SCHEMA_VERSION = "fouler-play-done-registry/v1"
REGISTRY_PATH = ROOT / "data" / "completion" / "done_registry.json"

# Detectors in replay_analysis/autoresearch.py that resolve against source
# artifacts (decision traces, team files, policy blocks) rather than a bare
# turn counter. Kept as data so the registry states which is which.
GROUNDED_DETECTORS = (
    "hazard_pressure",
    "decision_instability",
    "search_regret",
    "magic_bounce_reflected_hazard",
)
TEMPLATED_DETECTORS = ("endgame_conversion",)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract() -> dict[str, Any]:
    import devstream_cycle_report as R  # noqa: PLC0415
    import fouler_mission_monitor as M  # noqa: PLC0415

    return {
        "targetRating": M.CANONICAL_TARGET_RATING,
        "sustainMinimumGames": R.ELO_SUSTAIN_MINIMUM_GAMES,
        "sustainMinimumGamesPerTeam": R.ELO_SUSTAIN_MINIMUM_GAMES_PER_TEAM,
        "sustainMinimumWinRate": R.ELO_SUSTAIN_MINIMUM_WIN_RATE,
        "requiredTeams": list(R.ELO_REQUIRED_TEAMS),
        "sustainMaxDrawdown": M.SUSTAIN_MAX_DRAWDOWN,
        "ladderStages": [
            {"id": s["id"], "ratingFloor": s["ratingFloor"],
             "targetRating": s["targetRating"], "requiredProof": s["requiredProof"]}
            for s in M.LADDER_STAGE_POLICY
        ],
    }


def build_registry() -> dict[str, Any]:
    contract = _contract()
    target = contract["targetRating"]
    entries: list[dict[str, Any]] = []

    # --- half one: 1700 sustained ------------------------------------------
    for stage in contract["ladderStages"]:
        entries.append({
            "id": f"elo/{stage['id']}",
            "group": "elo",
            "title": f"Ladder stage {stage['id']} (floor {stage['ratingFloor']}, "
                     f"target {stage['targetRating']})",
            "requiredProof": stage["requiredProof"],
            "checks": [{
                "kind": "peak_rating_at_least",
                "rating": stage["targetRating"],
                "derivedFrom": "fouler_mission_monitor.LADDER_STAGE_POLICY",
            }],
        })

    entries.append({
        "id": "elo/sustain-window-games",
        "group": "elo",
        "title": f"{contract['sustainMinimumGames']} rated games at or above {target}",
        "checks": [{
            "kind": "games_at_or_above_floor",
            "floor": target,
            "minimum": contract["sustainMinimumGames"],
            "derivedFrom": "devstream_cycle_report.ELO_SUSTAIN_MINIMUM_GAMES",
        }],
    })
    entries.append({
        "id": "elo/sustain-win-rate",
        "group": "elo",
        "title": f"Win rate at or above {contract['sustainMinimumWinRate']:.0%} "
                 f"across the sustain window",
        "checks": [{
            "kind": "sustain_win_rate_at_least",
            "floor": target,
            "minimum": contract["sustainMinimumWinRate"],
            "derivedFrom": "devstream_cycle_report.ELO_SUSTAIN_MINIMUM_WIN_RATE",
        }],
    })
    entries.append({
        "id": "elo/sustain-team-coverage",
        "group": "elo",
        "title": f"Each required team plays at least "
                 f"{contract['sustainMinimumGamesPerTeam']} games in the sustain window",
        "checks": [{
            "kind": "team_coverage_at_least",
            "floor": target,
            "teams": contract["requiredTeams"],
            "minimumPerTeam": contract["sustainMinimumGamesPerTeam"],
            "derivedFrom": "devstream_cycle_report.ELO_REQUIRED_TEAMS",
        }],
    })
    entries.append({
        "id": "elo/sustain-drawdown",
        "group": "elo",
        "title": f"No drawdown greater than {contract['sustainMaxDrawdown']} "
                 f"points inside the sustain window",
        "checks": [{
            "kind": "max_drawdown_within",
            "floor": target,
            "maximum": contract["sustainMaxDrawdown"],
            "derivedFrom": "fouler_mission_monitor.SUSTAIN_MAX_DRAWDOWN",
        }],
    })

    # --- half two: the learn-from-losses loop, closed ----------------------
    entries.append({
        "id": "learn-loop/loss-coverage",
        "group": "learn-loop",
        "title": "Every rated loss is cited by a hypothesis or explicitly triaged",
        "checks": [{
            "kind": "losses_without_hypothesis",
            "maximum": 0,
            "derivedFrom": "join on hypothesis.evidence[] battle id prefix",
        }],
    })
    entries.append({
        "id": "learn-loop/hypotheses-terminal",
        "group": "learn-loop",
        "title": "Hypotheses reach a terminal state (kept or reverted), not just open",
        "checks": [{
            "kind": "hypotheses_not_terminal",
            "maximum": 0,
            "terminalStates": ["kept", "reverted"],
            "derivedFrom": "replay_analysis/hypothesis_ledger.py status vocabulary",
        }],
    })
    entries.append({
        "id": "learn-loop/implementation-after-hypothesis",
        "group": "learn-loop",
        "title": "Every implementing commit is dated after the hypothesis opened",
        "notes": [
            "A commit that predates the hypothesis cannot be a fix for it. The "
            "closer's git --grep has no --since filter, so a detector's own birth "
            "commit can certify every hypothesis that detector will ever emit."
        ],
        "checks": [{
            "kind": "implementation_predates_hypothesis",
            "maximum": 0,
            "derivedFrom": "hypothesis.implementation.committedAt vs openedAt",
        }],
    })
    entries.append({
        "id": "learn-loop/measurement-store-resolvable",
        "group": "learn-loop",
        "title": "The battle store the closer measures against exists and is non-empty",
        "notes": [
            "If this store is missing, len(window) >= 30 is never true and no "
            "hypothesis can ever reach kept or reverted. Terminal states become "
            "unreachable by construction and the loop cannot close."
        ],
        "checks": [{
            "kind": "battle_store_resolvable",
            "minimumBattles": 1,
            "derivedFrom": "hermes-fouler-hypothesis-closer BATTLE_STATS path",
        }],
    })
    entries.append({
        "id": "learn-loop/detector-diversity",
        "group": "learn-loop",
        "title": "Grounded detectors are represented in the ledger, not only templated ones",
        "notes": [
            "Ranking issues on raw frequency hands the queue to whichever detector "
            "fires most often, which is the loosest predicate rather than the most "
            "informative one. Rank on evidence that resolves against source artifacts."
        ],
        "checks": [{
            "kind": "grounded_detectors_represented",
            "groundedDetectors": list(GROUNDED_DETECTORS),
            "templatedDetectors": list(TEMPLATED_DETECTORS),
            "minimumRepresented": 1,
            "derivedFrom": "replay_analysis/autoresearch.py detector inventory",
        }],
    })

    sources = {}
    for rel in ("scripts/devstream_cycle_report.py", "scripts/fouler_mission_monitor.py"):
        path = ROOT / rel
        if path.exists():
            sources[rel] = _sha256(path)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedBy": "scripts/build_done_registry.py",
        "doctrine": (
            "Every check is evaluated against battle records and hypothesis "
            "artifacts, never against a report the project generated about itself. "
            "A process must never grade its own homework. Checks whose evidence is "
            "absent report 'unverifiable', never 'complete'."
        ),
        "definitionSource": (
            "owner goal: 1700 ELO sustained with a closed learn-from-losses loop; "
            "thresholds from devstream_cycle_report.ELO_SUSTAIN_* and "
            "fouler_mission_monitor.LADDER_STAGE_POLICY"
        ),
        "contract": contract,
        "generatedFrom": sources,
        "entryCount": len(entries),
        "entries": entries,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()

    registry = build_registry()
    rendered = json.dumps(registry, indent=2, sort_keys=True) + "\n"

    if args.stdout:
        sys.stdout.write(rendered)
        return 0
    if args.check:
        if not REGISTRY_PATH.exists():
            print(f"MISSING {REGISTRY_PATH.relative_to(ROOT)}")
            return 1
        if REGISTRY_PATH.read_text(encoding="utf-8") != rendered:
            print(f"STALE {REGISTRY_PATH.relative_to(ROOT)} -- regenerate")
            return 1
        print(f"up to date: {REGISTRY_PATH.relative_to(ROOT)}")
        return 0

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(rendered, encoding="utf-8")
    elo = sum(1 for e in registry["entries"] if e["group"] == "elo")
    loop = sum(1 for e in registry["entries"] if e["group"] == "learn-loop")
    print(f"wrote {REGISTRY_PATH.relative_to(ROOT)}: {registry['entryCount']} entries "
          f"({elo} ELO, {loop} learn-loop)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
