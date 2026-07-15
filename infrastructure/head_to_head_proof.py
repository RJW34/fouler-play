#!/usr/bin/env python3
"""Read-only validation for Fouler candidate-vs-frozen proof artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
from infrastructure.head_to_head_authority import (  # noqa: E402
    DEFAULT_AUTHORITY_PATH,
    canonical_path,
    load_ledger_authority,
    open_evaluation_ledger,
)


SCHEMA_VERSION = "fouler-head-to-head-eval/v2"
POINTER_SCHEMA_VERSION = "fouler-head-to-head-pointer/v2"
MIN_PROMOTION_BATTLES = 60
MAX_ATTEMPTS_PER_BASELINE = 5
PER_ATTEMPT_ALPHA = 0.01
FAMILY_WISE_ALPHA = 0.05
REQUIRED_TEAMS = (
    "gen9/ou/fat-team-1-stall",
    "gen9/ou/fat-team-2-balance",
    "gen9/ou/fat-team-3-dondozo",
)
REQUIRED_ROLES = ("challenger", "accepter")
PROMOTABLE_ENGINE_FILES = {
    "fp/search/main.py",
    "fp/search/eval.py",
    "fp/search/forced_lines.py",
    "fp/search/endgame.py",
    "fp/playstyle_config.py",
    "fp/team_analysis.py",
    "fp/opponent_model.py",
}
RUNTIME_PREFIXES = ("fp/", "data/", "teams/")
RUNTIME_FILES = {
    "config.py",
    "constants.py",
    "run.py",
    "requirements.txt",
    "requirements-dev.txt",
    "infrastructure/offline_eval_runner.py",
}
PROTOCOL_FILES = (
    "infrastructure/head_to_head_authority.py",
    "infrastructure/head_to_head_eval.py",
    "infrastructure/head_to_head_proof.py",
    "infrastructure/offline_eval.py",
    "infrastructure/offline_eval_runner.py",
)
ROW_PROVENANCE_FIELDS = {
    "account",
    "format",
    "source_commit",
    "session_id",
    "h2h_run_id",
    "h2h_cell_id",
    "h2h_arm",
    "h2h_role",
    "h2h_team",
    "h2h_account",
    "h2h_opponent",
    "h2h_baseline_commit",
    "h2h_candidate_patch_sha256",
    "h2h_engine_digest",
    "h2h_change_id",
}


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _exact_binomial_upper_tail(successes: int, trials: int) -> float:
    if trials <= 0:
        return 1.0
    successes = max(0, min(int(successes), int(trials)))
    numerator = sum(math.comb(trials, value) for value in range(successes, trials + 1))
    return numerator / (2**trials)


def _team_name(path: str) -> str:
    return Path(path.replace("\\", "/")).name


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_path(run_dir: Path, evidence: Mapping[str, Any], label: str, blockers: list[str]) -> Path | None:
    relative_text = str(evidence.get("relativePath") or "").replace("\\", "/")
    relative = Path(relative_text)
    if not relative_text or relative.is_absolute() or ".." in relative.parts:
        blockers.append(f"{label} evidence path is missing or escapes the run directory")
        return None
    candidate = run_dir / relative
    try:
        resolved_run = run_dir.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError:
        blockers.append(f"{label} evidence file is missing")
        return None
    if resolved != resolved_run and resolved_run not in resolved.parents:
        blockers.append(f"{label} evidence path resolves outside the run directory")
        return None
    current = candidate
    while current != run_dir:
        if current.is_symlink():
            blockers.append(f"{label} evidence path contains a symbolic link")
            return None
        current = current.parent
    if not resolved.is_file():
        blockers.append(f"{label} evidence path is not a regular file")
        return None
    return resolved


def _load_raw_rows(
    run_dir: Path,
    evidence: Mapping[str, Any],
    label: str,
    blockers: list[str],
) -> list[dict[str, Any]]:
    path = _evidence_path(run_dir, evidence, label, blockers)
    if path is None:
        return []
    data = path.read_bytes()
    if str(evidence.get("sha256") or "") != hashlib.sha256(data).hexdigest():
        blockers.append(f"{label} SHA-256 does not match the raw file")
    if _as_int(evidence.get("byteLength")) != len(data):
        blockers.append(f"{label} byte length does not match the raw file")
    try:
        payload = json.loads(data)
    except Exception as exc:
        blockers.append(f"{label} is not valid JSON: {exc}")
        return []
    battles = payload.get("battles") if isinstance(payload, dict) else None
    if not isinstance(battles, list) or not all(isinstance(item, dict) for item in battles):
        blockers.append(f"{label} does not contain a battle object list")
        return []
    rows = list(battles)
    if _as_int(evidence.get("rowCount")) != len(rows):
        blockers.append(f"{label} row count does not match the raw file")
    return rows


def _artifact_evidence_blockers(
    run_dir: Path,
    evidence: Mapping[str, Any],
    label: str,
) -> list[str]:
    blockers: list[str] = []
    path = _evidence_path(run_dir, evidence, label, blockers)
    if path is None:
        return blockers
    data = path.read_bytes()
    if str(evidence.get("sha256") or "") != hashlib.sha256(data).hexdigest():
        blockers.append(f"{label} SHA-256 does not match the artifact")
    if _as_int(evidence.get("byteLength")) != len(data):
        blockers.append(f"{label} byte length does not match the artifact")
    return blockers


def _row_provenance_blockers(
    rows: list[dict[str, Any]],
    expected: Mapping[str, Any],
    label: str,
) -> list[str]:
    blockers: list[str] = []
    if set(expected) != ROW_PROVENANCE_FIELDS:
        blockers.append(f"{label} expected provenance does not name the exact required fields")
        return blockers
    for index, row in enumerate(rows, start=1):
        mismatched = [
            field
            for field in sorted(ROW_PROVENANCE_FIELDS)
            if str(row.get(field) or "") != str(expected.get(field) or "")
        ]
        if mismatched:
            blockers.append(f"{label} row {index} provenance mismatch: {','.join(mismatched)}")
    return blockers


def _rows_by_battle_id(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for row in rows:
        battle_id = str(row.get("battle_id") or "").strip()
        if not battle_id:
            continue
        if battle_id in indexed:
            duplicates.add(battle_id)
        indexed[battle_id] = row
    return indexed, duplicates


def structure_blockers(proof: Mapping[str, Any]) -> list[str]:
    """Return every structural reason an artifact cannot authorize promotion."""
    blockers: list[str] = []
    if proof.get("schemaVersion") != SCHEMA_VERSION:
        blockers.append(f"schemaVersion must be {SCHEMA_VERSION}")
    if proof.get("promotionAllowed") is not True:
        blockers.append("promotionAllowed must be true")
    if str(proof.get("status") or "") != "promotion-ready":
        blockers.append("status must be promotion-ready")
    if proof.get("identicalSmoke") is True:
        blockers.append("identical-code smoke cannot authorize promotion")
    reported_blockers = proof.get("blockers")
    if isinstance(reported_blockers, list):
        blockers.extend(f"reported gate blocker: {item}" for item in reported_blockers)
    elif reported_blockers:
        blockers.append(f"reported gate blocker: {reported_blockers}")

    requested = _as_int(proof.get("requestedBattles"))
    completed = _as_int(proof.get("completedBattles"))
    candidate_wins = _as_int(proof.get("candidateWins"))
    frozen_wins = _as_int(proof.get("frozenWins"))
    ties = _as_int(proof.get("ties"))
    if requested is None or requested < MIN_PROMOTION_BATTLES or requested % 12:
        blockers.append("requestedBattles must be a multiple of 12 and at least 60")
    if completed != requested:
        blockers.append(f"completedBattles {completed} does not equal requestedBattles {requested}")
    if ties != 0:
        blockers.append(f"proof contains {ties} tie/disconnect result(s)")
    if None in {candidate_wins, frozen_wins, ties, completed} or (
        (candidate_wins or 0) + (frozen_wins or 0) + (ties or 0) != (completed or 0)
    ):
        blockers.append("result totals do not equal completedBattles")

    reported_effect = _as_float(proof.get("effectOverFrozen"))
    reported_exact_p = _as_float(proof.get("oneSidedExactP"))
    if reported_effect is None:
        blockers.append("candidate effect is missing or non-finite")
    if reported_exact_p is None:
        blockers.append("one-sided exact-binomial p-value is missing or non-finite")

    baseline_commit = str(proof.get("baselineCommit") or "")
    patch_sha = str(proof.get("candidatePatchSha256") or "")
    candidate_file = str(proof.get("candidateFile") or "").replace("\\", "/")
    run_id = str(proof.get("runId") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", baseline_commit):
        blockers.append("baselineCommit is missing or malformed")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", patch_sha):
        blockers.append("candidatePatchSha256 is missing or malformed")
    if candidate_file not in PROMOTABLE_ENGINE_FILES:
        blockers.append("candidateFile is outside the promotable engine allowlist")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", run_id):
        blockers.append("runId is missing or malformed")

    lineage = proof.get("lineage") if isinstance(proof.get("lineage"), dict) else {}
    change_id = str(lineage.get("changeId") or "")
    autoresearch_sha = str(lineage.get("autoresearchSha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", change_id):
        blockers.append("lineage changeId is missing or malformed")
    if not re.fullmatch(r"[0-9a-f]{64}", autoresearch_sha):
        blockers.append("lineage autoresearchSha256 is missing or malformed")
    if lineage.get("baselineCommit") != baseline_commit:
        blockers.append("lineage baseline commit does not match the proof")
    if lineage.get("candidatePatchSha256") != patch_sha:
        blockers.append("lineage candidate patch does not match the proof")
    if str(lineage.get("candidateFile") or "").replace("\\", "/") != candidate_file:
        blockers.append("lineage candidate file does not match the proof")

    runtime_family_id = str(proof.get("runtimeFamilyId") or "")
    candidate_runtime_digest = str(proof.get("candidateRuntimeDigest") or "")
    frozen_runtime_digest = str(proof.get("frozenRuntimeDigest") or "")
    protocol_digest = str(proof.get("protocolDigest") or "")
    for label, value in (
        ("runtimeFamilyId", runtime_family_id),
        ("candidateRuntimeDigest", candidate_runtime_digest),
        ("frozenRuntimeDigest", frozen_runtime_digest),
        ("protocolDigest", protocol_digest),
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            blockers.append(f"{label} is missing or malformed")
    runtime_evidence = proof.get("runtimeEvidence") if isinstance(proof.get("runtimeEvidence"), dict) else {}
    if str(runtime_evidence.get("relativePath") or "") != "runtime-manifest.json":
        blockers.append("runtime evidence must resolve to this run's runtime-manifest.json")
    if not re.fullmatch(r"[0-9a-f]{64}", str(runtime_evidence.get("sha256") or "")):
        blockers.append("runtime evidence SHA-256 is missing or malformed")

    attempt_budget = proof.get("attemptBudget") if isinstance(proof.get("attemptBudget"), dict) else {}
    attempt_ordinal = _as_int(attempt_budget.get("attemptOrdinal"))
    maximum_attempts = _as_int(attempt_budget.get("maximumAttempts"))
    per_attempt_alpha = _as_float(attempt_budget.get("perAttemptAlpha"))
    family_wise_alpha = _as_float(attempt_budget.get("familyWiseAlpha"))
    if attempt_budget.get("registered") is not True:
        blockers.append("promotion attempt was not durably pre-registered")
    if attempt_budget.get("schemaVersion") != "fouler-head-to-head-attempt/v2":
        blockers.append("attempt budget schema is not v2")
    if attempt_budget.get("runId") != run_id:
        blockers.append("attempt budget is not bound to the proof run")
    if attempt_budget.get("runtimeFamilyId") != runtime_family_id:
        blockers.append("attempt budget is not bound to the runtime family")
    if attempt_budget.get("protocolDigest") != protocol_digest:
        blockers.append("attempt budget is not bound to the evaluation protocol")
    if attempt_budget.get("changeId") != change_id:
        blockers.append("attempt budget is not bound to the candidate lineage")
    if not str(attempt_budget.get("ledgerId") or ""):
        blockers.append("attempt budget ledger identity is missing")
    if not re.fullmatch(r"[0-9a-f]{32}", str(attempt_budget.get("attemptId") or "")):
        blockers.append("attempt budget attemptId is missing or malformed")
    if (_as_int(attempt_budget.get("registrationSequence")) or 0) <= 0:
        blockers.append("attempt budget registration sequence is missing")
    if attempt_budget.get("baselineCommit") != baseline_commit:
        blockers.append("attempt budget is not bound to the frozen baseline")
    if attempt_budget.get("candidatePatchSha256") != patch_sha:
        blockers.append("attempt budget is not bound to the candidate patch")
    if str(attempt_budget.get("candidateFile") or "").replace("\\", "/") != candidate_file:
        blockers.append("attempt budget is not bound to the candidate file")
    if attempt_ordinal is None or not 1 <= attempt_ordinal <= MAX_ATTEMPTS_PER_BASELINE:
        blockers.append("attempt ordinal is outside the frozen-baseline budget")
    if maximum_attempts != MAX_ATTEMPTS_PER_BASELINE:
        blockers.append("attempt budget does not enforce exactly five trials per baseline")
    if per_attempt_alpha is None or per_attempt_alpha > PER_ATTEMPT_ALPHA:
        blockers.append("per-attempt alpha exceeds 0.01")
    if family_wise_alpha is None or family_wise_alpha > FAMILY_WISE_ALPHA:
        blockers.append("family-wise alpha exceeds 0.05")
    if (
        per_attempt_alpha is not None
        and maximum_attempts is not None
        and per_attempt_alpha * maximum_attempts > FAMILY_WISE_ALPHA + 1e-12
    ):
        blockers.append("attempt budget does not bound family-wise error to 0.05")

    required_team_names = {_team_name(team) for team in REQUIRED_TEAMS}
    team_summary = proof.get("candidateTeamSummary") if isinstance(proof.get("candidateTeamSummary"), dict) else {}
    if set(team_summary) != required_team_names:
        blockers.append("candidateTeamSummary lacks exact three-team coverage")
    role_summary = proof.get("roleSummary") if isinstance(proof.get("roleSummary"), dict) else {}
    if set(role_summary) != set(REQUIRED_ROLES):
        blockers.append("roleSummary lacks both connection roles")

    cells = proof.get("cells") if isinstance(proof.get("cells"), list) else []
    cell_ids = [str(cell.get("id") or "") for cell in cells if isinstance(cell, dict)]
    expected_pairs = {
        (candidate, frozen, role)
        for candidate in REQUIRED_TEAMS
        for frozen in REQUIRED_TEAMS
        if candidate != frozen
        for role in REQUIRED_ROLES
    }
    observed_pairs: set[tuple[str, str, str]] = set()
    cell_requested = 0
    cell_completed = 0
    cell_candidate_wins = 0
    cell_frozen_wins = 0
    cell_ties = 0
    all_battle_ids: list[str] = []
    role_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "decisive": 0})
    team_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "decisive": 0})
    if len(cells) != len(expected_pairs) or len(cell_ids) != len(set(cell_ids)) or any(not item for item in cell_ids):
        blockers.append("matrix must contain 12 uniquely identified cells")
    expected_per_cell = requested // len(expected_pairs) if requested is not None and requested % len(expected_pairs) == 0 else None
    configuration = proof.get("configuration") if isinstance(proof.get("configuration"), dict) else {}
    if expected_per_cell is None:
        blockers.append("requested battle count cannot be balanced across 12 cells")
    elif _as_int(configuration.get("battlesPerCell")) != expected_per_cell:
        blockers.append("configuration battlesPerCell does not match the balanced allocation")
    for cell in cells:
        if not isinstance(cell, dict):
            blockers.append("matrix contains a malformed cell")
            continue
        candidate_team = str(cell.get("candidateTeam") or "").replace("\\", "/")
        frozen_team = str(cell.get("frozenTeam") or "").replace("\\", "/")
        role = str(cell.get("candidateRole") or "")
        observed_pairs.add((candidate_team, frozen_team, role))
        raw_counts = {
            "requestedBattles": _as_int(cell.get("requestedBattles")),
            "completedBattles": _as_int(cell.get("completedBattles")),
            "candidateWins": _as_int(cell.get("candidateWins")),
            "frozenWins": _as_int(cell.get("frozenWins")),
            "ties": _as_int(cell.get("ties")),
        }
        if any(value is None or value < 0 for value in raw_counts.values()):
            blockers.append(f"{cell.get('id') or 'unknown cell'} contains malformed result counts")
        requested_cell = max(0, raw_counts["requestedBattles"] or 0)
        completed_cell = max(0, raw_counts["completedBattles"] or 0)
        candidate_wins_cell = max(0, raw_counts["candidateWins"] or 0)
        frozen_wins_cell = max(0, raw_counts["frozenWins"] or 0)
        ties_cell = max(0, raw_counts["ties"] or 0)
        cell_requested += requested_cell
        cell_completed += completed_cell
        cell_candidate_wins += candidate_wins_cell
        cell_frozen_wins += frozen_wins_cell
        cell_ties += ties_cell
        role_totals[role]["wins"] += candidate_wins_cell
        role_totals[role]["decisive"] += candidate_wins_cell + frozen_wins_cell
        candidate_team_name = _team_name(candidate_team)
        team_totals[candidate_team_name]["wins"] += candidate_wins_cell
        team_totals[candidate_team_name]["decisive"] += candidate_wins_cell + frozen_wins_cell
        if requested_cell <= 0 or completed_cell != requested_cell:
            blockers.append(f"{cell.get('id') or 'unknown cell'} is incomplete")
        if expected_per_cell is not None and requested_cell != expected_per_cell:
            blockers.append(f"{cell.get('id') or 'unknown cell'} is not allocated exactly {expected_per_cell} battles")
        if candidate_wins_cell + frozen_wins_cell + ties_cell != completed_cell:
            blockers.append(f"{cell.get('id') or 'unknown cell'} result totals are incoherent")
        if cell.get("error"):
            blockers.append(f"{cell.get('id') or 'unknown cell'} reports an execution error")
        if cell.get("candidateReturncode") != 0 or cell.get("frozenReturncode") != 0:
            blockers.append(f"{cell.get('id') or 'unknown cell'} reports a nonzero agent exit")
        expected_provenance = (
            cell.get("expectedProvenance")
            if isinstance(cell.get("expectedProvenance"), dict)
            else {}
        )
        if set(expected_provenance) != {"candidate", "frozen"} or any(
            not isinstance(expected_provenance.get(arm), dict)
            or set(expected_provenance[arm]) != ROW_PROVENANCE_FIELDS
            for arm in ("candidate", "frozen")
        ):
            blockers.append(f"{cell.get('id') or 'unknown cell'} lacks exact per-arm row provenance")
        log_evidence = cell.get("logEvidence") if isinstance(cell.get("logEvidence"), dict) else {}
        if set(log_evidence) != {"candidate", "frozen"}:
            blockers.append(f"{cell.get('id') or 'unknown cell'} lacks both per-arm log artifacts")
        battle_ids = cell.get("battleIds") if isinstance(cell.get("battleIds"), list) else []
        normalized_battle_ids = [str(item) for item in battle_ids if str(item)]
        if len(normalized_battle_ids) != completed_cell or len(normalized_battle_ids) != len(set(normalized_battle_ids)):
            blockers.append(f"{cell.get('id') or 'unknown cell'} battle-ID proof is incomplete or duplicated")
        all_battle_ids.extend(normalized_battle_ids)
    if observed_pairs != expected_pairs:
        blockers.append("matrix lacks every ordered benchmark matchup in both connection roles")
    if requested is not None and cell_requested != requested:
        blockers.append("cell requested-battle totals do not equal requestedBattles")
    if completed is not None and cell_completed != completed:
        blockers.append("cell completed-battle totals do not equal completedBattles")
    if candidate_wins is not None and cell_candidate_wins != candidate_wins:
        blockers.append("cell candidate-win totals do not equal candidateWins")
    if frozen_wins is not None and cell_frozen_wins != frozen_wins:
        blockers.append("cell frozen-win totals do not equal frozenWins")
    if ties is not None and cell_ties != ties:
        blockers.append("cell tie totals do not equal ties")
    if completed is not None and (len(all_battle_ids) != completed or len(all_battle_ids) != len(set(all_battle_ids))):
        blockers.append("matrix battle IDs are incomplete or duplicated across cells")

    decisive = cell_candidate_wins + cell_frozen_wins
    recomputed_win_rate = cell_candidate_wins / decisive if decisive else 0.0
    recomputed_effect = recomputed_win_rate - 0.5
    recomputed_exact_p = _exact_binomial_upper_tail(cell_candidate_wins, decisive)
    if recomputed_effect < 0.10:
        blockers.append("recomputed candidate effect is below +10%")
    if recomputed_exact_p >= PER_ATTEMPT_ALPHA:
        blockers.append("recomputed one-sided exact-binomial p-value is not below 0.01")
    if reported_effect is not None and not math.isclose(
        reported_effect,
        round(recomputed_effect, 4),
        rel_tol=0.0,
        abs_tol=0.00005,
    ):
        blockers.append("reported candidate effect does not match cell results")
    if reported_exact_p is not None and not math.isclose(
        reported_exact_p,
        round(recomputed_exact_p, 6),
        rel_tol=0.0,
        abs_tol=0.0000005,
    ):
        blockers.append("reported exact-binomial p-value does not match cell results")

    for role in REQUIRED_ROLES:
        recomputed = role_totals.get(role, {"wins": 0, "decisive": 0})
        rate = recomputed["wins"] / recomputed["decisive"] if recomputed["decisive"] else 0.0
        reported = role_summary.get(role) if isinstance(role_summary.get(role), dict) else {}
        if (
            _as_int(reported.get("wins")) != recomputed["wins"]
            or _as_int(reported.get("decisive")) != recomputed["decisive"]
            or _as_float(reported.get("winRate")) is None
            or not math.isclose(
                _as_float(reported.get("winRate")) or 0.0,
                round(rate, 4),
                rel_tol=0.0,
                abs_tol=0.00005,
            )
        ):
            blockers.append(f"reported role summary does not match cells for {role}")
        if recomputed["decisive"] <= 0 or rate < 0.5:
            blockers.append(f"candidate regressed as {role}")

    for team in sorted(required_team_names):
        recomputed = team_totals.get(team, {"wins": 0, "decisive": 0})
        rate = recomputed["wins"] / recomputed["decisive"] if recomputed["decisive"] else 0.0
        reported = team_summary.get(team) if isinstance(team_summary.get(team), dict) else {}
        if (
            _as_int(reported.get("wins")) != recomputed["wins"]
            or _as_int(reported.get("decisive")) != recomputed["decisive"]
            or _as_float(reported.get("winRate")) is None
            or not math.isclose(
                _as_float(reported.get("winRate")) or 0.0,
                round(rate, 4),
                rel_tol=0.0,
                abs_tol=0.00005,
            )
        ):
            blockers.append(f"reported team summary does not match cells for {team}")
        if recomputed["decisive"] <= 0 or rate < 0.5:
            blockers.append(f"candidate regressed on {team}")
    return list(dict.fromkeys(blockers))


def _cell_expected_provenance_blockers(
    proof: Mapping[str, Any],
    cell: Mapping[str, Any],
    candidate: Mapping[str, Any],
    frozen: Mapping[str, Any],
) -> list[str]:
    cell_id = str(cell.get("id") or "unknown cell")
    blockers: list[str] = []
    if set(candidate) != ROW_PROVENANCE_FIELDS or set(frozen) != ROW_PROVENANCE_FIELDS:
        return [f"{cell_id} expected provenance does not name the exact required fields for both arms"]
    frozen_role = "accepter" if cell.get("candidateRole") == "challenger" else "challenger"
    required_candidate = {
        "format": "gen9ou",
        "source_commit": proof.get("baselineCommit"),
        "h2h_run_id": proof.get("runId"),
        "h2h_cell_id": cell.get("id"),
        "h2h_arm": "candidate",
        "h2h_role": cell.get("candidateRole"),
        "h2h_team": cell.get("candidateTeam"),
        "h2h_baseline_commit": proof.get("baselineCommit"),
        "h2h_candidate_patch_sha256": proof.get("candidatePatchSha256"),
        "h2h_engine_digest": proof.get("candidateRuntimeDigest"),
        "h2h_change_id": (proof.get("lineage") or {}).get("changeId"),
    }
    required_frozen = {
        "format": "gen9ou",
        "source_commit": proof.get("baselineCommit"),
        "h2h_run_id": proof.get("runId"),
        "h2h_cell_id": cell.get("id"),
        "h2h_arm": "frozen",
        "h2h_role": frozen_role,
        "h2h_team": cell.get("frozenTeam"),
        "h2h_baseline_commit": proof.get("baselineCommit"),
        "h2h_candidate_patch_sha256": proof.get("candidatePatchSha256"),
        "h2h_engine_digest": proof.get("frozenRuntimeDigest"),
        "h2h_change_id": (proof.get("lineage") or {}).get("changeId"),
    }
    for label, observed, required in (
        ("candidate", candidate, required_candidate),
        ("frozen", frozen, required_frozen),
    ):
        mismatched = [field for field, value in required.items() if observed.get(field) != value]
        if mismatched:
            blockers.append(f"{cell_id} {label} expected provenance contradicts proof: {','.join(mismatched)}")
        for field in ("account", "session_id", "h2h_account", "h2h_opponent"):
            if not str(observed.get(field) or "").strip():
                blockers.append(f"{cell_id} {label} expected provenance lacks {field}")
        if observed.get("account") != observed.get("h2h_account"):
            blockers.append(f"{cell_id} {label} account and h2h_account disagree")
    if candidate.get("h2h_opponent") != frozen.get("h2h_account"):
        blockers.append(f"{cell_id} candidate opponent is not the frozen account")
    if frozen.get("h2h_opponent") != candidate.get("h2h_account"):
        blockers.append(f"{cell_id} frozen opponent is not the candidate account")
    if candidate.get("session_id") == frozen.get("session_id"):
        blockers.append(f"{cell_id} candidate and frozen session IDs must differ")
    return blockers


def _raw_artifact_blockers(proof: Mapping[str, Any], run_dir: Path) -> list[str]:
    blockers: list[str] = []
    seen_paths: set[str] = set()
    opposite = {"win": "loss", "loss": "win"}
    cells = proof.get("cells") if isinstance(proof.get("cells"), list) else []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        cell_id = str(cell.get("id") or "unknown cell")
        raw = cell.get("rawEvidence") if isinstance(cell.get("rawEvidence"), dict) else {}
        candidate_evidence = raw.get("candidate") if isinstance(raw.get("candidate"), dict) else {}
        frozen_evidence = raw.get("frozen") if isinstance(raw.get("frozen"), dict) else {}
        for label, evidence in (("candidate", candidate_evidence), ("frozen", frozen_evidence)):
            relative = str(evidence.get("relativePath") or "").replace("\\", "/")
            if relative in seen_paths:
                blockers.append(f"{cell_id} reuses a raw {label} evidence path")
            if relative:
                seen_paths.add(relative)
        candidate_rows = _load_raw_rows(run_dir, candidate_evidence, f"{cell_id} candidate raw", blockers)
        frozen_rows = _load_raw_rows(run_dir, frozen_evidence, f"{cell_id} frozen raw", blockers)
        expected = cell.get("expectedProvenance") if isinstance(cell.get("expectedProvenance"), dict) else {}
        candidate_expected = expected.get("candidate") if isinstance(expected.get("candidate"), dict) else {}
        frozen_expected = expected.get("frozen") if isinstance(expected.get("frozen"), dict) else {}
        blockers.extend(
            _cell_expected_provenance_blockers(proof, cell, candidate_expected, frozen_expected)
        )
        blockers.extend(
            _row_provenance_blockers(candidate_rows, candidate_expected, f"{cell_id} candidate raw")
        )
        blockers.extend(
            _row_provenance_blockers(frozen_rows, frozen_expected, f"{cell_id} frozen raw")
        )
        log_evidence = cell.get("logEvidence") if isinstance(cell.get("logEvidence"), dict) else {}
        for label in ("candidate", "frozen"):
            evidence = log_evidence.get(label) if isinstance(log_evidence.get(label), dict) else {}
            blockers.extend(_artifact_evidence_blockers(run_dir, evidence, f"{cell_id} {label} log"))
        candidate_by_id, candidate_duplicates = _rows_by_battle_id(candidate_rows)
        frozen_by_id, frozen_duplicates = _rows_by_battle_id(frozen_rows)
        if candidate_duplicates or frozen_duplicates:
            blockers.append(f"{cell_id} raw battle IDs contain duplicates")
        if len(candidate_by_id) != len(candidate_rows) or len(frozen_by_id) != len(frozen_rows):
            blockers.append(f"{cell_id} raw rows contain missing battle IDs")
        if set(candidate_by_id) != set(frozen_by_id):
            blockers.append(f"{cell_id} raw arm battle IDs differ")
        candidate_wins = 0
        frozen_wins = 0
        ties = 0
        for battle_id in sorted(set(candidate_by_id) & set(frozen_by_id)):
            candidate_result = str(candidate_by_id[battle_id].get("result") or "").lower()
            frozen_result = str(frozen_by_id[battle_id].get("result") or "").lower()
            if opposite.get(candidate_result) != frozen_result:
                blockers.append(f"{cell_id} raw result perspectives disagree for {battle_id}")
            if candidate_result == "win":
                candidate_wins += 1
            elif candidate_result == "loss":
                frozen_wins += 1
            else:
                ties += 1
        raw_counts = {
            "completedBattles": len(set(candidate_by_id) & set(frozen_by_id)),
            "candidateWins": candidate_wins,
            "frozenWins": frozen_wins,
            "ties": ties,
        }
        for field, value in raw_counts.items():
            if _as_int(cell.get(field)) != value:
                blockers.append(f"{cell_id} {field} does not match both raw arm files")
        reported_ids = cell.get("battleIds") if isinstance(cell.get("battleIds"), list) else []
        if sorted(str(item) for item in reported_ids) != sorted(set(candidate_by_id) & set(frozen_by_id)):
            blockers.append(f"{cell_id} battleIds do not match both raw arm files")
    return blockers


def _section_digest_blockers(section: Mapping[str, Any], label: str) -> list[str]:
    blockers: list[str] = []
    reported = str(section.get("digest") or "")
    payload = {key: value for key, value in section.items() if key != "digest"}
    if not re.fullmatch(r"[0-9a-f]{64}", reported) or reported != _canonical_sha256(payload):
        blockers.append(f"{label} digest does not match its manifest content")
    return blockers


def _host_runtime_blockers(runtime: Mapping[str, Any], project_root: Path) -> list[str]:
    blockers: list[str] = []
    candidate_runtime = runtime.get("candidateRuntime") if isinstance(runtime.get("candidateRuntime"), dict) else {}
    candidate_files = candidate_runtime.get("files") if isinstance(candidate_runtime.get("files"), dict) else {}
    tracked = _git(project_root, "ls-files")
    expected_runtime_paths = {
        path.replace("\\", "/")
        for path in tracked.stdout.splitlines()
        if path in RUNTIME_FILES or path.startswith(RUNTIME_PREFIXES)
    } if tracked.returncode == 0 else set()
    if tracked.returncode or set(candidate_files) != expected_runtime_paths:
        blockers.append("candidate runtime manifest does not cover the current tracked runtime closure")
    for relative, digest in candidate_files.items():
        path = project_root / str(relative)
        if not path.is_file() or path.is_symlink() or _sha256_file(path) != digest:
            blockers.append(f"current candidate runtime differs at {relative}")
    runtime_status = _git(
        project_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *sorted(RUNTIME_FILES),
        *RUNTIME_PREFIXES,
    )
    candidate_file = str(runtime.get("runtimeDifferences", [""])[0] if runtime.get("runtimeDifferences") else "")
    if runtime_status.returncode:
        blockers.append("current runtime working-tree status could not be read")
    else:
        unexpected = [
            line for line in runtime_status.stdout.splitlines()
            if line.strip() and not line.replace("\\", "/").endswith(candidate_file)
        ]
        if unexpected:
            blockers.append("current runtime contains changes outside the proven candidate file")
    protocol = runtime.get("protocol") if isinstance(runtime.get("protocol"), dict) else {}
    protocol_files = protocol.get("files") if isinstance(protocol.get("files"), dict) else {}
    if set(protocol_files) != set(PROTOCOL_FILES):
        blockers.append("protocol manifest does not cover the exact evaluation controller files")
    for relative in PROTOCOL_FILES:
        path = project_root / relative
        if not path.is_file() or path.is_symlink() or protocol_files.get(relative) != _sha256_file(path):
            blockers.append(f"current evaluation protocol differs at {relative}")

    python_runtime = runtime.get("python") if isinstance(runtime.get("python"), dict) else {}
    launcher_path = Path(str(python_runtime.get("launcherPath") or ""))
    if (
        not launcher_path.is_file()
        or launcher_path.is_symlink()
        or python_runtime.get("launcherSha256") != _sha256_file(launcher_path)
    ):
        blockers.append("current Python launcher differs from the evaluated runtime")
    poke_engine_path = Path(str(python_runtime.get("pokeEnginePath") or ""))
    if (
        not poke_engine_path.is_file()
        or poke_engine_path.is_symlink()
        or python_runtime.get("pokeEngineSha256") != _sha256_file(poke_engine_path)
    ):
        blockers.append("current Python runtime differs at pokeEnginePath")
    executable_digest = str(python_runtime.get("executableSha256") or "")
    executable_path = Path(str(python_runtime.get("executable") or ""))
    if executable_digest and (
        not executable_path.is_file()
        or executable_path.is_symlink()
        or executable_digest != _sha256_file(executable_path)
    ):
        blockers.append("current Python executable differs from the evaluated runtime")
    python_command = python_runtime.get("command") if isinstance(python_runtime.get("command"), list) else []
    if python_command:
        probe_code = (
            "import hashlib,importlib.metadata as m,json,os,platform,sys;"
            "d=sorted((x.metadata.get('Name') or '').lower()+'=='+x.version for x in m.distributions());"
            "import poke_engine; pe=os.path.realpath(poke_engine.__file__);"
            "print(json.dumps({'executable':os.path.realpath(sys.executable),'version':sys.version,"
            "'platform':platform.platform(),'packagesSha256':hashlib.sha256(('\\n'.join(d)).encode()).hexdigest(),"
            "'packageCount':len(d),'pokeEnginePath':pe}))"
        )
        try:
            probe = subprocess.run(
                [*(str(item) for item in python_command), "-c", probe_code],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            observed = json.loads(probe.stdout.strip().splitlines()[-1]) if probe.returncode == 0 else {}
        except Exception:
            observed = {}
        for field in ("executable", "version", "platform", "packagesSha256", "packageCount", "pokeEnginePath"):
            if observed.get(field) != python_runtime.get(field):
                blockers.append(f"current Python runtime fingerprint differs at {field}")

    showdown = runtime.get("showdown") if isinstance(runtime.get("showdown"), dict) else {}
    showdown_root = Path(str(showdown.get("checkout") or ""))
    if not showdown_root.is_dir() or showdown_root.is_symlink():
        blockers.append("Pokemon Showdown checkout recorded by the proof is unavailable")
    else:
        head = _git(showdown_root, "rev-parse", "HEAD")
        tree = _git(showdown_root, "rev-parse", "HEAD^{tree}")
        status = _git(showdown_root, "status", "--porcelain=v1", "--untracked-files=all")
        if head.returncode or head.stdout.strip() != showdown.get("commit"):
            blockers.append("Pokemon Showdown commit differs from the evaluated runtime")
        if tree.returncode or tree.stdout.strip() != showdown.get("tree"):
            blockers.append("Pokemon Showdown tree differs from the evaluated runtime")
        if status.returncode or status.stdout.strip():
            blockers.append("Pokemon Showdown checkout is no longer clean")
        inputs = showdown.get("inputs") if isinstance(showdown.get("inputs"), dict) else {}
        for relative, digest in inputs.items():
            path = showdown_root / str(relative)
            if not path.is_file() or path.is_symlink() or _sha256_file(path) != digest:
                blockers.append(f"Pokemon Showdown runtime input differs at {relative}")
    node_path = Path(str(showdown.get("nodeExecutable") or ""))
    if not node_path.is_file() or node_path.is_symlink() or showdown.get("nodeExecutableSha256") != _sha256_file(node_path):
        blockers.append("Node executable differs from the evaluated runtime")
    else:
        try:
            node_version = subprocess.run(
                [str(node_path), "--version"], capture_output=True, text=True, timeout=30, check=True
            ).stdout.strip()
        except Exception:
            node_version = ""
        if node_version != showdown.get("nodeVersion"):
            blockers.append("Node version differs from the evaluated runtime")
    return blockers


def _runtime_artifact_blockers(
    proof: Mapping[str, Any],
    run_dir: Path,
    project_root: Path,
    *,
    verify_host_runtime: bool,
) -> list[str]:
    blockers: list[str] = []
    evidence = proof.get("runtimeEvidence") if isinstance(proof.get("runtimeEvidence"), dict) else {}
    path = _evidence_path(run_dir, evidence, "runtime manifest", blockers)
    if path is None:
        return blockers
    data = path.read_bytes()
    if evidence.get("sha256") != hashlib.sha256(data).hexdigest():
        blockers.append("runtime manifest SHA-256 does not match the canonical file")
    if _as_int(evidence.get("byteLength")) != len(data):
        blockers.append("runtime manifest byte length does not match the canonical file")
    try:
        runtime = json.loads(data)
    except Exception as exc:
        blockers.append(f"runtime manifest is malformed: {exc}")
        return blockers
    if not isinstance(runtime, dict) or runtime.get("schemaVersion") != "fouler-head-to-head-runtime/v2":
        blockers.append("runtime manifest schema is not v2")
        return blockers
    for key, label in (
        ("candidateRuntime", "candidate runtime"),
        ("frozenRuntime", "frozen runtime"),
        ("protocol", "evaluation protocol"),
        ("python", "Python runtime"),
        ("showdown", "Pokemon Showdown runtime"),
    ):
        section = runtime.get(key) if isinstance(runtime.get(key), dict) else {}
        blockers.extend(_section_digest_blockers(section, label))
    candidate = runtime.get("candidateRuntime") if isinstance(runtime.get("candidateRuntime"), dict) else {}
    frozen = runtime.get("frozenRuntime") if isinstance(runtime.get("frozenRuntime"), dict) else {}
    protocol = runtime.get("protocol") if isinstance(runtime.get("protocol"), dict) else {}
    environment = runtime.get("environmentPolicy") if isinstance(runtime.get("environmentPolicy"), dict) else {}
    if proof.get("candidateRuntimeDigest") != candidate.get("digest"):
        blockers.append("proof candidate runtime digest does not match the runtime manifest")
    if proof.get("frozenRuntimeDigest") != frozen.get("digest"):
        blockers.append("proof frozen runtime digest does not match the runtime manifest")
    if proof.get("protocolDigest") != protocol.get("digest"):
        blockers.append("proof protocol digest does not match the runtime manifest")
    values = environment.get("values") if isinstance(environment.get("values"), dict) else {}
    if environment.get("digest") != _canonical_sha256(values) or environment.get("keys") != sorted(values):
        blockers.append("closed child environment policy digest is inconsistent")
    candidate_files = candidate.get("files") if isinstance(candidate.get("files"), dict) else {}
    frozen_files = frozen.get("files") if isinstance(frozen.get("files"), dict) else {}
    invalid_paths = [
        path
        for path in set(candidate_files) | set(frozen_files)
        if path not in RUNTIME_FILES and not str(path).startswith(RUNTIME_PREFIXES)
    ]
    if invalid_paths:
        blockers.append("runtime manifest contains files outside the declared runtime closure")
    differences = sorted(
        path
        for path in set(candidate_files) | set(frozen_files)
        if candidate_files.get(path) != frozen_files.get(path)
    )
    expected = [str(proof.get("candidateFile") or "").replace("\\", "/")]
    if differences != expected or runtime.get("runtimeDifferences") != expected:
        blockers.append("candidate and frozen runtime manifests differ outside the proven candidate file")
    computed_family = _canonical_sha256(
        {
            "frozenRuntimeDigest": frozen.get("digest"),
            "protocolDigest": protocol.get("digest"),
            "pythonRuntimeDigest": (runtime.get("python") or {}).get("digest"),
            "showdownRuntimeDigest": (runtime.get("showdown") or {}).get("digest"),
            "environmentPolicyDigest": environment.get("digest"),
        }
    )
    if proof.get("runtimeFamilyId") != computed_family or runtime.get("runtimeFamilyId") != computed_family:
        blockers.append("runtime family ID does not match the frozen evaluation inputs")
    lineage = proof.get("lineage") if isinstance(proof.get("lineage"), dict) else {}
    computed_change = _canonical_sha256(
        {
            "runtimeFamilyId": computed_family,
            "baselineCommit": proof.get("baselineCommit"),
            "candidateFile": proof.get("candidateFile"),
            "candidatePatchSha256": proof.get("candidatePatchSha256"),
            "candidateRuntimeDigest": candidate.get("digest"),
            "autoresearchSha256": lineage.get("autoresearchSha256"),
        }
    )
    if lineage.get("changeId") != computed_change:
        blockers.append("lineage change ID does not match the runtime and candidate inputs")
    if verify_host_runtime:
        blockers.extend(_host_runtime_blockers(runtime, project_root))
    return blockers


def _ledger_artifact_blockers(
    proof: Mapping[str, Any],
    *,
    result_sha256: str,
    ledger_path: Path | None,
    ledger_id: str | None,
    authority_path: Path | None = None,
) -> list[str]:
    blockers: list[str] = []
    attempt = proof.get("attemptBudget") if isinstance(proof.get("attemptBudget"), dict) else {}
    ledger_authority = None
    if ledger_path is not None or ledger_id is not None:
        if ledger_path is None or ledger_id is None:
            return ["explicit ledger test injection requires both ledger_path and ledger_id"]
        configured_path = canonical_path(ledger_path)
        configured_id = str(ledger_id).strip()
    else:
        try:
            ledger_authority = load_ledger_authority(authority_path or DEFAULT_AUTHORITY_PATH)
        except Exception as exc:
            return [f"external H2H ledger authority is invalid: {exc}"]
        configured_path = ledger_authority.ledger_path
        configured_id = ledger_authority.ledger_id
    if attempt.get("ledgerId") != configured_id:
        blockers.append("proof attempt ledger identity differs from DEKU configuration")
    connection = None
    try:
        connection = open_evaluation_ledger(
            configured_path,
            configured_id,
            authority=ledger_authority,
            writable=False,
        )
        row = connection.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", (str(attempt.get("attemptId") or ""),)
        ).fetchone()
        if row is None:
            blockers.append("external attempt ledger has no exact record for this attempt")
        else:
            expected = {
                "sequence": _as_int(attempt.get("registrationSequence")),
                "ledger_id": attempt.get("ledgerId"),
                "run_id": proof.get("runId"),
                "runtime_family_id": proof.get("runtimeFamilyId"),
                "protocol_digest": proof.get("protocolDigest"),
                "change_id": (proof.get("lineage") or {}).get("changeId"),
                "baseline_commit": proof.get("baselineCommit"),
                "candidate_patch_sha256": proof.get("candidatePatchSha256"),
                "candidate_file": proof.get("candidateFile"),
                "attempt_ordinal": _as_int(attempt.get("attemptOrdinal")),
                "status": proof.get("status"),
                "result_sha256": result_sha256,
            }
            for field, value in expected.items():
                if row[field] != value:
                    blockers.append(f"external attempt ledger field {field} does not match the proof")
            preceding = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE runtime_family_id = ? AND sequence <= ?",
                (proof.get("runtimeFamilyId"), row["sequence"]),
            ).fetchone()[0]
            if int(preceding) != row["attempt_ordinal"]:
                blockers.append("external attempt ordinal does not match durable family history")
            total = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE runtime_family_id = ?", (proof.get("runtimeFamilyId"),)
            ).fetchone()[0]
            if int(total) > MAX_ATTEMPTS_PER_BASELINE:
                blockers.append("external attempt ledger exceeds the five-attempt family budget")
    except Exception as exc:
        blockers.append(f"external attempt ledger could not be verified: {exc}")
    finally:
        if connection is not None:
            connection.close()
    return blockers


def artifact_blockers(
    proof: Mapping[str, Any],
    *,
    run_dir: Path,
    result_sha256: str,
    project_root: Path = PROJECT_ROOT,
    ledger_path: Path | None = None,
    ledger_id: str | None = None,
    authority_path: Path | None = None,
    verify_host_runtime: bool = True,
) -> list[str]:
    blockers = _raw_artifact_blockers(proof, run_dir)
    blockers.extend(
        _runtime_artifact_blockers(
            proof,
            run_dir,
            project_root,
            verify_host_runtime=verify_host_runtime,
        )
    )
    blockers.extend(
        _ledger_artifact_blockers(
            proof,
            result_sha256=result_sha256,
            ledger_path=ledger_path,
            ledger_id=ledger_id,
            authority_path=authority_path,
        )
    )
    return list(dict.fromkeys(blockers))


def load_latest_proof(
    pointer_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    ledger_path: Path | None = None,
    ledger_id: str | None = None,
    authority_path: Path | None = None,
    verify_host_runtime: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Resolve and independently verify the canonical proof selected by latest.json."""
    blockers: list[str] = []
    if not pointer_path.is_file() or pointer_path.is_symlink():
        return {}, ["head-to-head latest pointer is missing or linked"]
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, [f"head-to-head latest pointer is malformed: {exc}"]
    if not isinstance(pointer, dict) or pointer.get("schemaVersion") != POINTER_SCHEMA_VERSION:
        return {}, [f"head-to-head latest pointer schema must be {POINTER_SCHEMA_VERSION}"]
    run_id = str(pointer.get("runId") or "")
    expected_relative = f"{run_id}/result.json"
    if str(pointer.get("resultRelativePath") or "").replace("\\", "/") != expected_relative:
        blockers.append("latest pointer does not select its run's canonical result.json")
    result_path = pointer_path.parent / expected_relative
    if result_path.is_symlink():
        return {}, [*blockers, "canonical head-to-head result is linked"]
    try:
        resolved_parent = pointer_path.parent.resolve(strict=True)
        resolved_result = result_path.resolve(strict=True)
    except OSError:
        return {}, [*blockers, "canonical head-to-head result is missing"]
    if resolved_parent not in resolved_result.parents:
        return {}, [*blockers, "canonical head-to-head result escapes the results root or is linked"]
    result_data = resolved_result.read_bytes()
    result_sha256 = hashlib.sha256(result_data).hexdigest()
    if pointer.get("resultSha256") != result_sha256:
        blockers.append("latest pointer SHA-256 does not match canonical result.json")
    try:
        proof = json.loads(result_data)
    except Exception as exc:
        return {}, [*blockers, f"canonical head-to-head result is malformed: {exc}"]
    if not isinstance(proof, dict):
        return {}, [*blockers, "canonical head-to-head result is not an object"]
    if proof.get("runId") != run_id:
        blockers.append("latest pointer runId does not match canonical result.json")
    blockers.extend(structure_blockers(proof))
    blockers.extend(
        artifact_blockers(
            proof,
            run_dir=resolved_result.parent,
            result_sha256=result_sha256,
            project_root=project_root,
            ledger_path=ledger_path,
            ledger_id=ledger_id,
            authority_path=authority_path,
            verify_host_runtime=verify_host_runtime,
        )
    )
    return proof, list(dict.fromkeys(blockers))


def _git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    command = ["git", "-C", str(root), *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        empty = "" if text else b""
        error = str(exc) if text else str(exc).encode("utf-8", errors="replace")
        return subprocess.CompletedProcess(command, 127, stdout=empty, stderr=error)


def checkout_provenance(root: Path, proof: Mapping[str, Any]) -> dict[str, Any]:
    """Bind an accepted artifact to the currently checked-out engine diff."""
    blockers: list[str] = []
    baseline = str(proof.get("baselineCommit") or "")
    candidate_file = str(proof.get("candidateFile") or "").replace("\\", "/")
    expected_patch_sha = str(proof.get("candidatePatchSha256") or "")

    head_result = _git(root, "rev-parse", "HEAD")
    current_commit = head_result.stdout.strip() if head_result.returncode == 0 else ""
    if not current_commit:
        blockers.append("current checkout commit is unavailable")

    baseline_result = _git(root, "cat-file", "-e", f"{baseline}^{{commit}}") if baseline else None
    if baseline_result is None or baseline_result.returncode:
        blockers.append("frozen baseline commit is not present in this repository")
    elif current_commit:
        ancestor = _git(root, "merge-base", "--is-ancestor", baseline, current_commit)
        if ancestor.returncode:
            blockers.append("frozen baseline commit is not an ancestor of the current checkout")

    status = _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *sorted(RUNTIME_FILES),
        *RUNTIME_PREFIXES,
    )
    dirty_engine = [line for line in status.stdout.splitlines() if line.strip()] if status.returncode == 0 else []
    if status.returncode:
        blockers.append("current runtime working-tree status could not be read")
    elif dirty_engine:
        blockers.append("current runtime working tree has uncommitted or untracked changes")

    changed_engine_files: list[str] = []
    actual_patch_sha = ""
    if baseline_result is not None and baseline_result.returncode == 0 and current_commit:
        changed = _git(
            root,
            "diff",
            "--name-only",
            baseline,
            current_commit,
            "--",
            *sorted(RUNTIME_FILES),
            *RUNTIME_PREFIXES,
        )
        if changed.returncode:
            blockers.append("runtime paths changed since the frozen baseline could not be read")
        else:
            changed_engine_files = [line.replace("\\", "/") for line in changed.stdout.splitlines() if line]
            if changed_engine_files != [candidate_file]:
                blockers.append(
                    "current runtime diff does not contain exactly the proven candidate file"
                )
        patch = _git(root, "diff", "--binary", baseline, current_commit, "--", candidate_file, text=False)
        if patch.returncode:
            blockers.append("current candidate patch could not be hashed")
        else:
            actual_patch_sha = hashlib.sha256(patch.stdout).hexdigest()
            if actual_patch_sha != expected_patch_sha:
                blockers.append("current candidate patch SHA-256 does not match the accepted proof")

    return {
        "ready": not blockers,
        "blockers": blockers,
        "baselineCommit": baseline,
        "currentCommit": current_commit or None,
        "candidateFile": candidate_file,
        "expectedCandidatePatchSha256": expected_patch_sha,
        "actualCandidatePatchSha256": actual_patch_sha or None,
        "changedEngineFiles": changed_engine_files,
        "dirtyEngineEntries": dirty_engine,
        "noRuntimeActions": True,
    }
