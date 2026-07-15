#!/usr/bin/env python3
"""Judge the current immutable Fouler deployment using exact-identity battles."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from infrastructure.deployment_state import (  # noqa: E402
    build_judgment_receipt,
    default_state_root,
    deployment_battles,
    current_deployment_context,
    judgment_receipt_blockers,
    judgment_receipt_path,
    read_battle_rows,
    write_immutable_receipt,
)
from infrastructure.runtime_paths import resolve_runtime_paths  # noqa: E402


BATTLE_STATS_PATH = resolve_runtime_paths(REPO_DIR).battle_stats_path
GUARDRAILS_PATH = SCRIPT_DIR / "guardrails.json"
MIN_BATTLES_FOR_JUDGMENT = 30
PILOT_BATTLES_PER_TEAM = 10
OWNER_LOCKED_PILOT_TEAMS = (
    "fat-team-1-stall",
    "fat-team-2-balance",
    "fat-team-3-dondozo",
)
MAX_GLICKO_DEVIATION_FOR_ELO = float(os.getenv("ELO_WATCHDOG_MAX_RD", "50"))


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_elo_threshold() -> float:
    guardrails = load_json(GUARDRAILS_PATH)
    try:
        return float(guardrails["safety"]["max_elo_drop_before_revert"])
    except (KeyError, TypeError, ValueError):
        return 50.0


def _battle_id(row: dict[str, Any]) -> str:
    return str(row.get("battle_id") or row.get("battle_tag") or row.get("replay_id") or "").strip()


def pilot_sample_blockers(rows: list[dict[str, Any]], *, require_complete: bool) -> tuple[list[str], dict[str, int]]:
    """Validate the immutable 30-battle owner-team judgment schedule."""

    blockers: list[str] = []
    counts = {team: 0 for team in OWNER_LOCKED_PILOT_TEAMS}
    battle_ids = [_battle_id(row) for row in rows]
    if any(not battle_id for battle_id in battle_ids):
        blockers.append("pilot judgment rows contain a missing battle ID")
    if len(battle_ids) != len(set(battle_ids)):
        blockers.append("pilot judgment rows contain duplicate battle IDs")
    for index, row in enumerate(rows, start=1):
        team = str(row.get("team_file") or "").strip().replace("\\", "/")
        if "/" in team:
            blockers.append(f"pilot row {index} team provenance is not the exact owner-locked team ID")
            continue
        if team not in counts:
            blockers.append(f"pilot row {index} has unknown or missing owner-locked team provenance")
            continue
        counts[team] += 1
        if str(row.get("result") or "").lower() not in {"win", "loss"}:
            blockers.append(f"pilot row {index} is not decisive")

    overfilled = [team for team, count in counts.items() if count > PILOT_BATTLES_PER_TEAM]
    if overfilled:
        blockers.append("pilot team allocation exceeded 10 battles: " + ", ".join(sorted(overfilled)))
    if require_complete:
        if len(rows) != MIN_BATTLES_FOR_JUDGMENT:
            blockers.append(
                f"pilot judgment requires exactly {MIN_BATTLES_FOR_JUDGMENT} decisive battles; found {len(rows)}"
            )
        wrong = [
            f"{team}={count}"
            for team, count in counts.items()
            if count != PILOT_BATTLES_PER_TEAM
        ]
        if wrong:
            blockers.append("pilot judgment requires exactly 10 battles per owner-locked team: " + ", ".join(wrong))
    return list(dict.fromkeys(blockers)), counts


def pilot_judgment_receipt_blockers(
    judgment: dict[str, Any],
    exact_rows: list[dict[str, Any]],
) -> tuple[list[str], dict[str, int]]:
    evidence = judgment.get("battleEvidence") if isinstance(judgment.get("battleEvidence"), list) else []
    evidence_ids = [str(item.get("battleId") or "") for item in evidence if isinstance(item, dict)]
    rows_by_id = {_battle_id(row): row for row in exact_rows if _battle_id(row)}
    selected = [rows_by_id[battle_id] for battle_id in evidence_ids if battle_id in rows_by_id]
    blockers: list[str] = []
    if judgment.get("minimumBattles") != MIN_BATTLES_FOR_JUDGMENT:
        blockers.append("judgment minimumBattles must be exactly 30")
    if len(evidence_ids) != MIN_BATTLES_FOR_JUDGMENT or len(selected) != len(evidence_ids):
        blockers.append("judgment receipt does not identify exactly 30 available deployment battles")
    sample_blockers, counts = pilot_sample_blockers(selected, require_complete=True)
    blockers.extend(sample_blockers)
    return list(dict.fromkeys(blockers)), counts


def check_and_judge(
    *,
    battle_stats_path: Path | None = None,
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Create or validate one immutable judgment for the current activation."""
    battle_stats_path = battle_stats_path or BATTLE_STATS_PATH
    state_root = state_root or default_state_root()
    context = current_deployment_context(
        state_root=state_root,
        verify_checkout=True,
        battle_stats_path=battle_stats_path,
        expected_runtime_identity={
            "sourceCommit": os.getenv("FOULER_SOURCE_COMMIT", ""),
            "changeId": os.getenv("FOULER_CHANGE_ID", ""),
            "deploymentId": os.getenv("FOULER_DEPLOYMENT_ID", ""),
            "runtimeLeaseId": os.getenv("FOULER_RUNTIME_LEASE_ID", ""),
            "runtimeAuthorizationSha256": os.getenv("FOULER_RUNTIME_AUTHORIZATION_SHA256", ""),
            "sessionId": os.getenv("FOULER_SESSION_ID", ""),
        },
    )
    activation = context.get("activation") if isinstance(context.get("activation"), dict) else {}
    blockers = [
        blocker
        for blocker in context.get("blockers") or []
        if not str(blocker).startswith("judgment receipt:")
    ]
    if blockers or not activation:
        return {
            "ok": False,
            "status": "blocked-activation",
            "blockers": blockers or ["current deployment activation is missing"],
            "codeMutationPerformed": False,
        }

    rows = read_battle_rows(battle_stats_path)
    # Preserve every exact-identity result until the pilot schedule has been
    # validated. Filtering first would hide ties/disconnects and allow a larger
    # deployment sample to masquerade as the exact 30-battle protocol.
    exact = deployment_battles(rows, activation, decisive_only=False)
    receipt_path = judgment_receipt_path(activation["activationId"], state_root)
    if receipt_path.exists():
        judgment, judgment_blockers = judgment_receipt_blockers(
            receipt_path,
            activation=activation,
            battle_rows=rows,
        )
        pilot_blockers, team_counts = pilot_judgment_receipt_blockers(judgment, exact)
        judgment_blockers = list(dict.fromkeys([*judgment_blockers, *pilot_blockers]))
        return {
            "ok": not judgment_blockers,
            "status": str(judgment.get("status") or "blocked-judgment"),
            "activationId": activation["activationId"],
            "deploymentId": activation["deploymentId"],
            "exactIdentityBattles": len(exact),
            "pilotTeamCounts": team_counts,
            "judgmentReceiptPath": str(receipt_path),
            "judgment": judgment,
            "blockers": judgment_blockers,
            "codeMutationPerformed": False,
        }

    partial_blockers, team_counts = pilot_sample_blockers(exact, require_complete=False)
    if partial_blockers:
        return {
            "ok": False,
            "status": "blocked-pilot-sample",
            "activationId": activation["activationId"],
            "deploymentId": activation["deploymentId"],
            "exactIdentityBattles": len(exact),
            "minimumBattles": MIN_BATTLES_FOR_JUDGMENT,
            "pilotTeamCounts": team_counts,
            "blockers": partial_blockers,
            "codeMutationPerformed": False,
        }
    if len(exact) < MIN_BATTLES_FOR_JUDGMENT:
        return {
            "ok": True,
            "status": "waiting-for-sample",
            "activationId": activation["activationId"],
            "deploymentId": activation["deploymentId"],
            "exactIdentityBattles": len(exact),
            "minimumBattles": MIN_BATTLES_FOR_JUDGMENT,
            "pilotTeamCounts": team_counts,
            "blockers": [],
            "codeMutationPerformed": False,
        }

    sample_blockers, team_counts = pilot_sample_blockers(exact, require_complete=True)
    if sample_blockers:
        return {
            "ok": False,
            "status": "blocked-pilot-sample",
            "activationId": activation["activationId"],
            "deploymentId": activation["deploymentId"],
            "exactIdentityBattles": len(exact),
            "minimumBattles": MIN_BATTLES_FOR_JUDGMENT,
            "pilotTeamCounts": team_counts,
            "blockers": sample_blockers,
            "codeMutationPerformed": False,
        }

    try:
        judgment = build_judgment_receipt(
            activation=activation,
            battle_rows=rows,
            min_battles=MIN_BATTLES_FOR_JUDGMENT,
            max_elo_drop=get_elo_threshold(),
            max_glicko_deviation=MAX_GLICKO_DEVIATION_FOR_ELO,
        )
        try:
            write_immutable_receipt(receipt_path, judgment)
        except FileExistsError:
            pass
        judgment, judgment_blockers = judgment_receipt_blockers(
            receipt_path,
            activation=activation,
            battle_rows=rows,
        )
        pilot_blockers, team_counts = pilot_judgment_receipt_blockers(judgment, exact)
        judgment_blockers = list(dict.fromkeys([*judgment_blockers, *pilot_blockers]))
    except Exception as exc:
        judgment = {}
        judgment_blockers = [str(exc)]
        team_counts = {team: 0 for team in OWNER_LOCKED_PILOT_TEAMS}
    return {
        "ok": not judgment_blockers,
        "status": str(judgment.get("status") or "blocked-judgment"),
        "activationId": activation["activationId"],
        "deploymentId": activation["deploymentId"],
        "exactIdentityBattles": len(exact),
        "pilotTeamCounts": team_counts,
        "judgmentReceiptPath": str(receipt_path),
        "judgment": judgment,
        "blockers": judgment_blockers,
        # An immutable release is never dirtied in place. A regressed judgment
        # is an input to a separately authorized rollback deployment.
        "codeMutationPerformed": False,
    }


def check_and_revert() -> bool:
    """Compatibility wrapper: report regression without mutating the release."""
    result = check_and_judge()
    return result.get("status") == "regressed"


def main() -> int:
    result = check_and_judge()
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("ok"):
        return 2
    if result.get("status") == "regressed":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
