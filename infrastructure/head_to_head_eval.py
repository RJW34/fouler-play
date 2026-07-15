#!/usr/bin/env python3
"""Discriminating candidate-vs-frozen evaluation for Fouler engine changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.offline_eval import (  # noqa: E402
    _terminate_process_tree,
    build_eval_env,
    configured_showdown_dir,
    resolve_fouler_python,
    showdown_server_reachable,
    start_managed_showdown_server,
)
from infrastructure.head_to_head_authority import (  # noqa: E402
    DEFAULT_AUTHORITY_PATH,
    DEFAULT_LEDGER_PATH,
    LedgerAuthority,
    canonical_path,
    durable_attempt_anchor_status,
    initialize_evaluation_ledger as _initialize_evaluation_ledger,
    initialize_ledger_authority,
    load_ledger_authority,
    open_evaluation_ledger,
)
from infrastructure.head_to_head_proof import (  # noqa: E402
    MIN_PROMOTION_BATTLES,
    PROMOTABLE_ENGINE_FILES,
    REQUIRED_ROLES,
    REQUIRED_TEAMS,
)


RESULTS_ROOT = PROJECT_ROOT / "eval_results" / "head_to_head"
LATEST_RESULT = RESULTS_ROOT / "latest.json"
DEFAULT_TEAMS = REQUIRED_TEAMS
MAX_ONE_SIDED_P = 0.01
FAMILY_WISE_ALPHA = 0.05
MAX_ATTEMPTS_PER_BASELINE = 5
MIN_EFFECT = 0.10
MIN_EVAL_SEARCH_TIME_MS = 1200
MIN_EVAL_PER_BATTLE_TIMEOUT_SECONDS = 240.0
ROLES = REQUIRED_ROLES
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
SAFE_CHILD_ENV_KEYS = (
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
)
LOCAL_ISOLATION_BLOCKER = (
    "promotion requires externally attested DEKU isolation; local evaluation arms share "
    "one OS identity and can access controller, peer, or ledger state"
)


class EvaluationContainmentError(RuntimeError):
    """An evaluation arm could not be proven stopped and isolated."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_team(team: str) -> str:
    return Path(str(team).replace("\\", "/")).name


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluation_runtime_limit_blockers(
    search_time_ms: int,
    per_battle_timeout: float,
) -> list[str]:
    blockers: list[str] = []
    if search_time_ms < MIN_EVAL_SEARCH_TIME_MS:
        blockers.append(
            f"evaluation search time {search_time_ms}ms is below the "
            f"{MIN_EVAL_SEARCH_TIME_MS}ms promotion floor"
        )
    if per_battle_timeout < MIN_EVAL_PER_BATTLE_TIMEOUT_SECONDS:
        blockers.append(
            f"per-battle timeout {per_battle_timeout:g}s is below the "
            f"{MIN_EVAL_PER_BATTLE_TIMEOUT_SECONDS:g}s promotion floor"
        )
    return blockers


def file_evidence(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    relative = path.relative_to(relative_to).as_posix() if relative_to is not None else path.name
    return {
        "relativePath": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "byteLength": len(data),
    }


def json_document_bytes(payload: Any) -> bytes:
    return json.dumps(payload, indent=2).encode("utf-8")


def bytes_evidence(data: bytes, relative_path: str | Path) -> dict[str, Any]:
    return {
        "relativePath": Path(relative_path).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "byteLength": len(data),
    }


def _runtime_paths(root: Path) -> list[str]:
    tracked = git_text(root, "ls-files").splitlines()
    return sorted(
        path.replace("\\", "/")
        for path in tracked
        if path in RUNTIME_FILES or path.startswith(RUNTIME_PREFIXES)
    )


def runtime_manifest(root: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for relative in _runtime_paths(root):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime input is missing or linked: {relative}")
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {"schemaVersion": "fouler-runtime-files/v1", "files": files}
    return {**payload, "digest": canonical_sha256(payload)}


def protocol_manifest() -> dict[str, Any]:
    files: dict[str, str] = {}
    for relative in PROTOCOL_FILES:
        path = PROJECT_ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"evaluation protocol input is missing or linked: {relative}")
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {"schemaVersion": "fouler-h2h-protocol/v1", "files": files}
    return {**payload, "digest": canonical_sha256(payload)}


def python_runtime_manifest(python_command: list[str]) -> dict[str, Any]:
    probe_code = """
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import sys
import poke_engine

def sha256_file(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None

executable = os.path.realpath(sys.executable)
poke_engine_path = os.path.realpath(poke_engine.__file__)
distributions = sorted(
    (item.metadata.get("Name") or "").lower() + "==" + item.version
    for item in metadata.distributions()
)
print(json.dumps({
    "executable": executable,
    "executableSha256": sha256_file(executable),
    "version": sys.version,
    "platform": platform.platform(),
    "packagesSha256": hashlib.sha256("\\n".join(distributions).encode()).hexdigest(),
    "packageCount": len(distributions),
    "pokeEnginePath": poke_engine_path,
    "pokeEngineSha256": sha256_file(poke_engine_path),
}))
"""
    result = subprocess.run(
        [*python_command, "-c", probe_code],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"runtime Python fingerprint failed: {(result.stderr or result.stdout).strip()}")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    payload["command"] = list(python_command)
    launcher = shutil.which(python_command[0]) or python_command[0]
    launcher_path = Path(launcher).resolve()
    if not launcher_path.is_file():
        raise RuntimeError(f"runtime Python launcher is unavailable: {launcher_path}")
    payload["launcherPath"] = str(launcher_path)
    payload["launcherSha256"] = hashlib.sha256(launcher_path.read_bytes()).hexdigest()
    payload["hostPlatform"] = platform.platform()
    return {**payload, "digest": canonical_sha256(payload)}


def showdown_runtime_manifest() -> dict[str, Any]:
    root = configured_showdown_dir().resolve()
    if not root.is_dir():
        raise RuntimeError(f"Pokemon Showdown checkout is missing: {root}")
    commit = git_text(root, "rev-parse", "HEAD")
    tree = git_text(root, "rev-parse", "HEAD^{tree}")
    status = git_text(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RuntimeError("Pokemon Showdown checkout must be clean for candidate evaluation")
    inputs: dict[str, str] = {}
    for name in ("pokemon-showdown", "package.json", "package-lock.json", "config/config-example.js"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node executable is unavailable for Pokemon Showdown evaluation")
    node_path = Path(node).resolve()
    node_version = subprocess.run(
        [str(node_path), "--version"], capture_output=True, text=True, timeout=30, check=True
    ).stdout.strip()
    payload = {
        "checkout": str(root),
        "commit": commit,
        "tree": tree,
        "clean": True,
        "inputs": inputs,
        "nodeExecutable": str(node_path),
        "nodeExecutableSha256": hashlib.sha256(node_path.read_bytes()).hexdigest(),
        "nodeVersion": node_version,
    }
    return {**payload, "digest": canonical_sha256(payload)}


def _resolve_ledger_configuration(
    *,
    authority_path: Path | None = None,
    ledger_path: Path | None = None,
    ledger_id: str | None = None,
) -> tuple[Path, str, LedgerAuthority | None]:
    """Resolve production authority, or an explicit test-only ledger injection."""
    if ledger_path is not None or ledger_id is not None:
        if ledger_path is None or ledger_id is None:
            raise ValueError("explicit ledger test injection requires both ledger_path and ledger_id")
        configured_path = canonical_path(ledger_path)
        configured_id = str(ledger_id).strip()
        validation = open_evaluation_ledger(
            configured_path,
            configured_id,
            writable=True,
        )
        validation.close()
        return configured_path, configured_id, None
    authority = load_ledger_authority(authority_path or DEFAULT_AUTHORITY_PATH)
    return authority.ledger_path, authority.ledger_id, authority


def ledger_configuration(
    *,
    authority_path: Path | None = None,
    ledger_path: Path | None = None,
    ledger_id: str | None = None,
) -> tuple[Path, str]:
    configured_path, configured_id, _authority = _resolve_ledger_configuration(
        authority_path=authority_path,
        ledger_path=ledger_path,
        ledger_id=ledger_id,
    )
    return configured_path, configured_id


def initialize_evaluation_ledger(path: Path, ledger_id: str) -> dict[str, Any]:
    """Create a direct ledger only for explicit test fixtures."""
    return _initialize_evaluation_ledger(path, ledger_id)


def exact_binomial_upper_tail(successes: int, trials: int) -> float:
    """Return P[X >= successes] for X~Binomial(trials, 0.5)."""
    if trials <= 0:
        return 1.0
    successes = max(0, min(int(successes), int(trials)))
    numerator = sum(math.comb(trials, value) for value in range(successes, trials + 1))
    return numerator / (2**trials)


def build_evaluation_cells(teams: tuple[str, ...], battles: int) -> list[dict[str, Any]]:
    if len(teams) < 2 or len(set(teams)) != len(teams):
        raise ValueError("head-to-head evaluation requires at least two distinct teams")
    ordered_pairs = [(candidate, frozen) for candidate in teams for frozen in teams if candidate != frozen]
    cell_count = len(ordered_pairs) * len(ROLES)
    if battles <= 0 or battles % cell_count:
        raise ValueError(f"battles must be a positive multiple of {cell_count}")
    per_cell = battles // cell_count
    cells: list[dict[str, Any]] = []
    index = 0
    for candidate_team, frozen_team in ordered_pairs:
        for candidate_role in ROLES:
            index += 1
            cells.append(
                {
                    "id": f"cell-{index:02d}",
                    "candidateTeam": candidate_team,
                    "frozenTeam": frozen_team,
                    "candidateRole": candidate_role,
                    "requestedBattles": per_cell,
                }
            )
    return cells


def evaluate_matrix(
    cells: list[dict[str, Any]],
    *,
    requested_battles: int,
    baseline_commit: str,
    candidate_patch_sha256: str,
    minimum_battles: int = MIN_PROMOTION_BATTLES,
) -> dict[str, Any]:
    blockers: list[str] = []
    completed = sum(int(cell.get("completedBattles") or 0) for cell in cells)
    candidate_wins = sum(int(cell.get("candidateWins") or 0) for cell in cells)
    frozen_wins = sum(int(cell.get("frozenWins") or 0) for cell in cells)
    ties = sum(int(cell.get("ties") or 0) for cell in cells)

    if requested_battles < minimum_battles:
        blockers.append(f"requested battle count {requested_battles} is below {minimum_battles}")
    if completed != requested_battles:
        blockers.append(f"matrix completed {completed}/{requested_battles} battles")
    if ties:
        blockers.append(f"matrix contains {ties} tie/disconnect result(s)")
    if candidate_wins + frozen_wins + ties != completed:
        blockers.append("matrix result totals do not equal completed battles")
    if len(baseline_commit) < 7:
        blockers.append("frozen baseline commit is missing")
    if len(candidate_patch_sha256) != 64:
        blockers.append("candidate patch SHA-256 is missing")

    cell_ids = [str(cell.get("id") or "") for cell in cells]
    if not cell_ids or len(cell_ids) != len(set(cell_ids)):
        blockers.append("matrix cell IDs are missing or duplicated")
    expected_per_cell = requested_battles // len(cells) if cells and requested_battles % len(cells) == 0 else None
    if expected_per_cell is None:
        blockers.append("requested battles cannot be allocated equally across matrix cells")

    expected_team_paths = {team.replace("\\", "/") for team in DEFAULT_TEAMS}
    observed_team_paths = {
        str(cell.get("candidateTeam") or "").replace("\\", "/") for cell in cells
    } | {
        str(cell.get("frozenTeam") or "").replace("\\", "/") for cell in cells
    }
    expected_team_names = {normalize_team(team) for team in expected_team_paths}
    if observed_team_paths != expected_team_paths:
        blockers.append(
            "matrix must use exactly the mission benchmark teams: "
            + ", ".join(sorted(expected_team_names))
        )

    observed_pair_roles = {
        (
            normalize_team(str(cell.get("candidateTeam") or "")),
            normalize_team(str(cell.get("frozenTeam") or "")),
            str(cell.get("candidateRole") or ""),
        )
        for cell in cells
    }
    expected_pair_roles = {
        (normalize_team(candidate), normalize_team(frozen), role)
        for candidate in DEFAULT_TEAMS
        for frozen in DEFAULT_TEAMS
        if candidate != frozen
        for role in ROLES
    }
    if observed_pair_roles != expected_pair_roles or len(cells) != len(expected_pair_roles):
        blockers.append("matrix does not cover every ordered benchmark matchup in both connection roles")

    all_battle_ids: list[str] = []
    for cell in cells:
        expected = int(cell.get("requestedBattles") or 0)
        if expected_per_cell is not None and expected != expected_per_cell:
            blockers.append(
                f"{cell.get('id')} requests {expected} battles instead of balanced allocation {expected_per_cell}"
            )
        actual = int(cell.get("completedBattles") or 0)
        if actual != expected:
            blockers.append(f"{cell.get('id')} completed {actual}/{expected} battles")
        if cell.get("error"):
            blockers.append(f"{cell.get('id')} failed: {cell.get('error')}")
        if cell.get("candidateReturncode") != 0 or cell.get("frozenReturncode") != 0:
            blockers.append(f"{cell.get('id')} has nonzero or missing agent return codes")
        battle_ids = cell.get("battleIds") if isinstance(cell.get("battleIds"), list) else []
        normalized_ids = [str(item) for item in battle_ids if str(item)]
        if len(normalized_ids) != actual or len(normalized_ids) != len(set(normalized_ids)):
            blockers.append(f"{cell.get('id')} battle-ID proof is incomplete or duplicated")
        all_battle_ids.extend(normalized_ids)
    if len(all_battle_ids) != completed or len(all_battle_ids) != len(set(all_battle_ids)):
        blockers.append("matrix battle IDs are incomplete or duplicated across cells")

    decisive = candidate_wins + frozen_wins
    win_rate = candidate_wins / decisive if decisive else 0.0
    effect = win_rate - 0.5
    p_value = exact_binomial_upper_tail(candidate_wins, decisive)
    if effect < MIN_EFFECT:
        blockers.append(f"candidate effect {effect:+.1%} is below {MIN_EFFECT:+.0%}")
    if p_value >= MAX_ONE_SIDED_P:
        blockers.append(f"one-sided exact binomial p={p_value:.4f} is not below {MAX_ONE_SIDED_P}")

    role_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "decisive": 0})
    team_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "decisive": 0})
    for cell in cells:
        wins = int(cell.get("candidateWins") or 0)
        losses = int(cell.get("frozenWins") or 0)
        role = str(cell.get("candidateRole") or "unknown")
        team = normalize_team(str(cell.get("candidateTeam") or "unknown"))
        role_totals[role]["wins"] += wins
        role_totals[role]["decisive"] += wins + losses
        team_totals[team]["wins"] += wins
        team_totals[team]["decisive"] += wins + losses

    role_summary: dict[str, Any] = {}
    for role in ROLES:
        values = role_totals.get(role, {"wins": 0, "decisive": 0})
        rate = values["wins"] / values["decisive"] if values["decisive"] else 0.0
        role_summary[role] = {**values, "winRate": round(rate, 4)}
        if values["decisive"] == 0 or rate < 0.5:
            blockers.append(f"candidate regressed as {role}: {values['wins']}/{values['decisive']}")

    team_summary: dict[str, Any] = {}
    expected_teams = {normalize_team(str(cell.get("candidateTeam") or "")) for cell in cells}
    for team in sorted(expected_teams):
        values = team_totals.get(team, {"wins": 0, "decisive": 0})
        rate = values["wins"] / values["decisive"] if values["decisive"] else 0.0
        team_summary[team] = {**values, "winRate": round(rate, 4)}
        if values["decisive"] == 0 or rate < 0.5:
            blockers.append(f"candidate regressed on {team}: {values['wins']}/{values['decisive']}")

    statistical_blockers = list(dict.fromkeys(blockers))
    blockers = [*statistical_blockers, LOCAL_ISOLATION_BLOCKER]
    return {
        "promotionAllowed": False,
        "statisticalPromotionCandidate": not statistical_blockers,
        "statisticalBlockers": statistical_blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "isolationEvidence": {
            "schemaVersion": "fouler-h2h-isolation-evidence/v1",
            "enforced": False,
            "mode": "local-same-identity-evidence-only",
            "candidatePeerFilesystemIsolation": False,
            "controllerStateIsolation": False,
            "durableAttemptAnchorExternal": False,
        },
        "requestedBattles": requested_battles,
        "completedBattles": completed,
        "candidateWins": candidate_wins,
        "frozenWins": frozen_wins,
        "ties": ties,
        "candidateWinRate": round(win_rate, 4),
        "effectOverFrozen": round(effect, 4),
        "oneSidedExactP": round(p_value, 6),
        "minimumPromotionBattles": minimum_battles,
        "requiredCandidateTeams": sorted(expected_team_names),
        "roleSummary": role_summary,
        "candidateTeamSummary": team_summary,
    }


def git_text(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


@contextmanager
def prepared_worktrees(
    candidate_file: str,
    *,
    allow_identical: bool = False,
) -> Iterator[dict[str, Any]]:
    relative = Path(candidate_file)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("candidate file must be relative to the repository")
    candidate_source = PROJECT_ROOT / relative
    if not candidate_source.is_file():
        raise FileNotFoundError(candidate_source)
    if relative.as_posix() not in PROMOTABLE_ENGINE_FILES:
        raise ValueError(f"candidate file is outside the promotable engine allowlist: {relative.as_posix()}")

    baseline_commit = git_text(PROJECT_ROOT, "rev-parse", "HEAD")
    unstaged = {line for line in git_text(PROJECT_ROOT, "diff", "--name-only").splitlines() if line}
    staged = {line for line in git_text(PROJECT_ROOT, "diff", "--cached", "--name-only").splitlines() if line}
    expected = {relative.as_posix()}
    if staged:
        raise RuntimeError("candidate checkout must not contain staged changes")
    if unstaged != expected and not (allow_identical and not unstaged):
        raise RuntimeError(
            "candidate checkout must contain exactly one unstaged target change; "
            f"found {sorted(unstaged)}"
        )
    runtime_status = git_text(
        PROJECT_ROOT,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *sorted(RUNTIME_FILES),
        *RUNTIME_PREFIXES,
    ).splitlines()
    unexpected_runtime = [
        line
        for line in runtime_status
        if line.strip() and not line.replace("\\", "/").endswith(relative.as_posix())
    ]
    if unexpected_runtime:
        raise RuntimeError(
            "candidate checkout contains runtime changes outside the target file: "
            + "; ".join(unexpected_runtime)
        )
    patch = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "diff", "--binary", "--", relative.as_posix()],
        capture_output=True,
        check=True,
    ).stdout
    if not patch and not allow_identical:
        raise RuntimeError("candidate patch is empty")
    patch_sha = hashlib.sha256(patch or b"identical-smoke").hexdigest()

    temp_parent = Path(tempfile.mkdtemp(prefix="fouler-h2h-stage-"))
    candidate_stage = temp_parent / f"tree-{uuid.uuid4().hex[:12]}"
    frozen_stage = temp_parent / f"tree-{uuid.uuid4().hex[:12]}"
    candidate_root = Path(tempfile.mkdtemp(prefix="fouler-arm-"))
    frozen_root = Path(tempfile.mkdtemp(prefix="fouler-arm-"))
    added: list[Path] = []
    try:
        for root in (candidate_stage, frozen_stage):
            git_text(PROJECT_ROOT, "worktree", "add", "--detach", str(root), baseline_commit)
            added.append(root)
        if patch:
            destination = candidate_stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate_source, destination)
        candidate_runtime = runtime_manifest(candidate_stage)
        frozen_runtime = runtime_manifest(frozen_stage)
        for source, destination in (
            (candidate_stage, candidate_root),
            (frozen_stage, frozen_root),
        ):
            shutil.copytree(
                source,
                destination,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git"),
            )
            if (destination / ".git").exists():
                raise RuntimeError("sanitized evaluation arm retained Git metadata")
        yield {
            "candidateRoot": candidate_root,
            "frozenRoot": frozen_root,
            "candidateRuntime": candidate_runtime,
            "frozenRuntime": frozen_runtime,
            "baselineCommit": baseline_commit,
            "candidatePatchSha256": patch_sha,
            "candidateFile": relative.as_posix(),
            "identicalSmoke": not bool(patch),
        }
    finally:
        cleanup_errors: list[str] = []
        for root in (candidate_root, frozen_root):
            shutil.rmtree(root, ignore_errors=True)
            if root.exists():
                cleanup_errors.append(f"sanitized arm cleanup failed: {root}")
        for root in reversed(added):
            result = subprocess.run(
                ["git", "-C", str(PROJECT_ROOT), "worktree", "remove", "--force", str(root)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode:
                cleanup_errors.append(f"{root}: {(result.stderr or result.stdout).strip()}")
        temp_base = Path(tempfile.gettempdir()).resolve()
        resolved_parent = temp_parent.resolve()
        if not cleanup_errors and temp_base in resolved_parent.parents:
            shutil.rmtree(resolved_parent, ignore_errors=True)
        if cleanup_errors:
            raise RuntimeError("head-to-head worktree cleanup failed: " + "; ".join(cleanup_errors))


FORBIDDEN_ARM_ENV_KEYS = frozenset(
    {
        "FOULER_SOURCE_COMMIT",
        "FOULER_SESSION_ID",
        "FOULER_H2H_RUN_ID",
        "FOULER_H2H_CELL_ID",
        "FOULER_H2H_ARM",
        "FOULER_H2H_ROLE",
        "FOULER_H2H_TEAM",
        "FOULER_H2H_ACCOUNT",
        "FOULER_H2H_OPPONENT",
        "FOULER_H2H_BASELINE_COMMIT",
        "FOULER_H2H_CANDIDATE_PATCH_SHA256",
        "FOULER_H2H_ENGINE_DIGEST",
        "FOULER_H2H_CHANGE_ID",
    }
)


def build_agent_env(
    label: str,
    result_dir: Path,
    showdown_port: int,
    search_time_ms: int,
) -> dict[str, str]:
    if search_time_ms < MIN_EVAL_SEARCH_TIME_MS:
        raise ValueError(
            f"evaluation search time must be at least {MIN_EVAL_SEARCH_TIME_MS}ms"
        )
    generated = build_eval_env(
        label=label,
        showdown_port=showdown_port,
        search_time_ms=search_time_ms,
        extra_env=None,
    )
    env = {key: generated[key] for key in SAFE_CHILD_ENV_KEYS if generated.get(key)}
    result_dir.mkdir(parents=True, exist_ok=True)
    arm_home = result_dir / "home"
    arm_temp = result_dir / "tmp"
    arm_appdata = arm_home / "AppData" / "Roaming"
    arm_local_appdata = arm_home / "AppData" / "Local"
    for directory in (arm_home, arm_temp, arm_appdata, arm_local_appdata):
        directory.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(arm_home),
            "USERPROFILE": str(arm_home),
            "APPDATA": str(arm_appdata),
            "LOCALAPPDATA": str(arm_local_appdata),
            "TEMP": str(arm_temp),
            "TMP": str(arm_temp),
            "TMPDIR": str(arm_temp),
            "XDG_CACHE_HOME": str(arm_home / ".cache"),
            "XDG_CONFIG_HOME": str(arm_home / ".config"),
            "XDG_STATE_HOME": str(arm_home / ".local" / "state"),
            "PYTHONNOUSERSITE": "1",
        }
    )
    paths = {
        "FOULER_OFFLINE_BATTLE_STATS_FILE": result_dir / "battle_stats.json",
        "FOULER_OFFLINE_ACTIVE_BATTLES_FILE": result_dir / "active_battles.json",
        "FOULER_OFFLINE_STREAM_STATUS_FILE": result_dir / "stream_status.json",
        "FOULER_OFFLINE_DAILY_STATS_FILE": result_dir / "daily_stats.json",
        "FOULER_OFFLINE_STABILITY_REPORT_FILE": result_dir / "stability_report.json",
        "FOULER_OFFLINE_STATE_STORE_FAILURE_FILE": result_dir / "state-store-write-failure.json",
        "FOULER_PROCESS_LOCK_FILE": result_dir / "bot.pid",
        "EVENT_QUEUE_FILE": result_dir / "events_queue.json",
        "EVENT_QUEUE_BACKLOG_ARCHIVE_DIR": result_dir / "discord-events",
    }
    env.update({key: str(value) for key, value in paths.items()})
    env.update(
        {
            "PS_PASSWORD": "",
            "FOULER_NO_SECURITY_LOGIN": "1",
            "LOSS_TRIGGERED_DRAIN": "0",
            "MAX_CONCURRENT_BATTLES": "1",
            "SEARCH_TIME_MS": str(search_time_ms),
            "DECISION_POLICY": "eval",
            "FOULER_LOOP_BREAK": "0",
            "FOULER_PENALTY_PIPELINE": "0",
            "MATCHUP_MEMORY_ENABLED": "0",
            "FOULER_BATTLE_RESULT_QUEUE": "0",
            "FOULER_OFFLINE_EVAL_QUEUE_EVENTS": "0",
            "FOULER_STREAM_EVENTS": "0",
            "ENABLE_STREAM_HOOKS": "0",
            "SPECTATOR_USERNAME": "",
            "ENABLE_SPECTATOR_INVITES": "0",
            "FOULER_POST_BATTLE_CHAT_ENABLED": "0",
            "FOULER_DEVSTREAM_LIVE": "0",
            "FOULER_DEVSTREAM_STATUS_JSON": "",
            "FOULER_DEVSTREAM_STATUS_URL": "",
            "POST_BATTLE_LIVE_PROMO_MESSAGE": "",
            "MIN_SEARCH_TIME_MS": str(MIN_EVAL_SEARCH_TIME_MS),
            "SEARCH_PARALLELISM": "2",
            "MAX_MCTS_BATTLES": "3",
        }
    )
    for forbidden in FORBIDDEN_ARM_ENV_KEYS:
        env.pop(forbidden, None)
    return env


def build_agent_command(
    root: Path,
    python_command: list[str],
    *,
    username: str,
    mode: str,
    opponent: str,
    team: str,
    battles: int,
    ws_uri: str,
    search_time_ms: int,
) -> list[str]:
    if search_time_ms < MIN_EVAL_SEARCH_TIME_MS:
        raise ValueError(
            f"evaluation search time must be at least {MIN_EVAL_SEARCH_TIME_MS}ms"
        )
    command = [
        *python_command,
        str(root / "infrastructure" / "offline_eval_runner.py"),
        "run.py",
        "--websocket-uri",
        ws_uri,
        "--ps-username",
        username,
        "--bot-mode",
        mode,
        "--pokemon-format",
        "gen9ou",
        "--team-name",
        team,
        "--run-count",
        str(battles),
        "--max-concurrent-battles",
        "1",
        "--search-time-ms",
        str(search_time_ms),
        "--search-parallelism",
        "2",
        "--max-mcts-battles",
        "3",
        "--decision-policy",
        "eval",
        "--save-replay",
        "never",
        "--log-level",
        "WARNING",
    ]
    if mode == "challenge_user":
        command.extend(["--user-to-challenge", opponent])
    return command


def process_creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def start_agent(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        return subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=process_creation_flags(),
        )


def read_battle_stats(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    if path.is_symlink():
        raise RuntimeError(f"battle stats artifact is linked: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("battles"), list):
        raise ValueError("battle stats artifact must contain a battles list")
    if any(not isinstance(item, dict) for item in payload["battles"]):
        raise ValueError("battle stats artifact contains a non-object row")
    return [dict(item) for item in payload["battles"]]


def _rows_by_battle_id(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in rows:
        battle_id = str(row.get("battle_id") or "").strip()
        if not battle_id:
            continue
        if battle_id in indexed:
            duplicates.append(battle_id)
        indexed[battle_id] = row
    return indexed, sorted(set(duplicates))


def row_provenance_errors(
    rows: list[dict[str, Any]],
    expected: Mapping[str, Any],
    *,
    label: str,
) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        mismatched = [
            field
            for field, expected_value in expected.items()
            if str(row.get(field) or "") != str(expected_value)
        ]
        if mismatched:
            errors.append(f"{label} row {index} provenance mismatch: {','.join(mismatched)}")
    return errors


CONTROLLER_PROVENANCE_FIELDS = frozenset(
    {
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
)


def _normalized_account(value: object) -> str:
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


def externally_attest_rows(
    rows: list[dict[str, Any]],
    expected: Mapping[str, Any],
    *,
    label: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach hidden arm provenance only after the agent has completed behavior."""

    errors: list[str] = []
    attested: list[dict[str, Any]] = []
    expected_account = _normalized_account(expected.get("account"))
    expected_team = normalize_team(str(expected.get("h2h_team") or ""))
    for index, row in enumerate(rows, start=1):
        supplied_controller_fields = sorted(
            field for field in row if field.startswith("h2h_")
        )
        if supplied_controller_fields:
            errors.append(
                f"{label} row {index} supplied controller-only provenance: "
                + ",".join(supplied_controller_fields)
            )
        if _normalized_account(row.get("account")) != expected_account:
            errors.append(f"{label} row {index} account does not match its opaque process assignment")
        if str(row.get("format") or "").lower() != "gen9ou":
            errors.append(f"{label} row {index} format is not gen9ou")
        if normalize_team(str(row.get("team_file") or "")) != expected_team:
            errors.append(f"{label} row {index} team does not match its controller assignment")
        if not str(row.get("battle_id") or "").strip():
            errors.append(f"{label} row {index} battle ID is missing")
        if str(row.get("result") or "").lower() not in {"win", "loss"}:
            errors.append(f"{label} row {index} result is not decisive")
        clean = {
            key: value
            for key, value in row.items()
            if key not in CONTROLLER_PROVENANCE_FIELDS
        }
        clean.update(expected)
        attested.append(clean)
    return attested, errors


def summarize_cell(
    cell: dict[str, Any],
    *,
    candidate_stats_path: Path,
    frozen_stats_path: Path,
    candidate_returncode: int | None,
    frozen_returncode: int | None,
    candidate_expected_provenance: Mapping[str, Any],
    frozen_expected_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        candidate_raw = read_battle_stats(candidate_stats_path)
    except Exception as exc:
        candidate_raw = []
        errors.append(f"candidate stats are unreadable: {type(exc).__name__}: {exc}")
    try:
        frozen_raw = read_battle_stats(frozen_stats_path)
    except Exception as exc:
        frozen_raw = []
        errors.append(f"frozen stats are unreadable: {type(exc).__name__}: {exc}")
    candidate, candidate_attestation_errors = externally_attest_rows(
        candidate_raw,
        candidate_expected_provenance,
        label="candidate",
    )
    frozen, frozen_attestation_errors = externally_attest_rows(
        frozen_raw,
        frozen_expected_provenance,
        label="frozen",
    )
    errors.extend(candidate_attestation_errors)
    errors.extend(frozen_attestation_errors)

    cell_evidence_dir = Path(str(cell["id"]))
    candidate_evidence_dir = cell_evidence_dir / f"arm-{uuid.uuid4().hex[:12]}"
    frozen_evidence_dir = cell_evidence_dir / f"arm-{uuid.uuid4().hex[:12]}"
    candidate_attested_path = candidate_evidence_dir / "battle_stats.json"
    frozen_attested_path = frozen_evidence_dir / "battle_stats.json"
    deferred_evidence = {
        candidate_attested_path.as_posix(): json_document_bytes({"battles": candidate}),
        frozen_attested_path.as_posix(): json_document_bytes({"battles": frozen}),
    }

    candidate_by_id, candidate_duplicates = _rows_by_battle_id(candidate)
    frozen_by_id, frozen_duplicates = _rows_by_battle_id(frozen)
    candidate_ids = set(candidate_by_id)
    frozen_ids = set(frozen_by_id)
    candidate_wins = sum(str(item.get("result") or "").lower() == "win" for item in candidate_by_id.values())
    candidate_losses = sum(str(item.get("result") or "").lower() == "loss" for item in candidate_by_id.values())
    candidate_other = len(candidate_by_id) - candidate_wins - candidate_losses
    frozen_wins = sum(str(item.get("result") or "").lower() == "win" for item in frozen_by_id.values())
    frozen_losses = sum(str(item.get("result") or "").lower() == "loss" for item in frozen_by_id.values())
    frozen_other = len(frozen_by_id) - frozen_wins - frozen_losses
    errors.extend(row_provenance_errors(candidate, candidate_expected_provenance, label="candidate"))
    errors.extend(row_provenance_errors(frozen, frozen_expected_provenance, label="frozen"))
    requested = int(cell["requestedBattles"])
    if len(candidate) != requested or len(frozen) != requested:
        errors.append(f"stats underfilled: candidate={len(candidate)} frozen={len(frozen)} requested={requested}")
    if candidate_duplicates or frozen_duplicates:
        errors.append(
            "duplicate battle IDs: "
            f"candidate={candidate_duplicates or 'none'} frozen={frozen_duplicates or 'none'}"
        )
    if candidate_ids != frozen_ids:
        errors.append("candidate and frozen battle IDs differ")
    if candidate_wins != frozen_losses or candidate_losses != frozen_wins:
        errors.append("candidate/frozen result perspectives disagree")
    opposite = {"win": "loss", "loss": "win", "tie": "tie", "draw": "draw"}
    mismatched_results = []
    for battle_id in sorted(candidate_ids & frozen_ids):
        candidate_result = str(candidate_by_id[battle_id].get("result") or "").lower()
        frozen_result = str(frozen_by_id[battle_id].get("result") or "").lower()
        if opposite.get(candidate_result) != frozen_result:
            mismatched_results.append(f"{battle_id}:{candidate_result}/{frozen_result}")
    if mismatched_results:
        errors.append("per-battle result perspectives disagree: " + ", ".join(mismatched_results[:5]))
    if candidate_returncode != 0 or frozen_returncode != 0:
        errors.append(f"agent return codes candidate={candidate_returncode} frozen={frozen_returncode}")
    raw_candidate = bytes_evidence(
        deferred_evidence[candidate_attested_path.as_posix()],
        candidate_attested_path,
    )
    raw_candidate["rowCount"] = len(candidate)
    raw_frozen = bytes_evidence(
        deferred_evidence[frozen_attested_path.as_posix()],
        frozen_attested_path,
    )
    raw_frozen["rowCount"] = len(frozen)
    agent_raw_evidence: dict[str, Any] = {}
    for label, source, destination in (
        ("candidate", candidate_stats_path, candidate_evidence_dir / "agent-raw-battle-stats.json"),
        ("frozen", frozen_stats_path, frozen_evidence_dir / "agent-raw-battle-stats.json"),
    ):
        if source.is_file() and not source.is_symlink():
            data = source.read_bytes()
            deferred_evidence[destination.as_posix()] = data
            agent_raw_evidence[label] = bytes_evidence(data, destination)
        else:
            errors.append(f"{label} raw agent stats are missing or linked")
    log_evidence: dict[str, Any] = {}
    for label, source, destination in (
        ("candidate", candidate_stats_path.parent / "agent.log", candidate_evidence_dir / "agent.log"),
        ("frozen", frozen_stats_path.parent / "agent.log", frozen_evidence_dir / "agent.log"),
    ):
        if source.is_file() and not source.is_symlink():
            data = source.read_bytes()
            deferred_evidence[destination.as_posix()] = data
            log_evidence[label] = bytes_evidence(data, destination)
        else:
            errors.append(f"{label} agent log is missing or linked")
    return {
        **cell,
        "completedBattles": len(candidate_ids & frozen_ids),
        "candidateWins": candidate_wins,
        "frozenWins": frozen_wins,
        "ties": max(candidate_other, frozen_other),
        "candidateStatsCount": len(candidate),
        "frozenStatsCount": len(frozen),
        "candidateReturncode": candidate_returncode,
        "frozenReturncode": frozen_returncode,
        "battleIds": sorted(candidate_ids & frozen_ids),
        "expectedProvenance": {
            "candidate": dict(candidate_expected_provenance),
            "frozen": dict(frozen_expected_provenance),
        },
        "rawEvidence": {"candidate": raw_candidate, "frozen": raw_frozen},
        "agentRawEvidence": agent_raw_evidence,
        "logEvidence": log_evidence,
        "error": "; ".join(errors),
        "_deferredEvidence": deferred_evidence,
    }


def run_cell(
    cell: dict[str, Any],
    *,
    candidate_root: Path,
    frozen_root: Path,
    python_command: list[str],
    showdown_port: int,
    search_time_ms: int,
    per_battle_timeout: float,
    startup_wait: float,
    run_id: str,
    baseline_commit: str,
    candidate_patch_sha256: str,
    change_id: str,
    candidate_runtime_digest: str,
    frozen_runtime_digest: str,
) -> dict[str, Any]:
    candidate_dir = Path(tempfile.mkdtemp(prefix="fouler-agent-state-"))
    frozen_dir = Path(tempfile.mkdtemp(prefix="fouler-agent-state-"))
    candidate_user = f"Eval{uuid.uuid4().hex[:12]}"
    frozen_user = f"Eval{uuid.uuid4().hex[:12]}"
    candidate_label = f"arm-{uuid.uuid4().hex[:12]}"
    frozen_label = f"arm-{uuid.uuid4().hex[:12]}"
    candidate_is_challenger = cell["candidateRole"] == "challenger"
    accepter_root = frozen_root if candidate_is_challenger else candidate_root
    challenger_root = candidate_root if candidate_is_challenger else frozen_root
    accepter_user = frozen_user if candidate_is_challenger else candidate_user
    challenger_user = candidate_user if candidate_is_challenger else frozen_user
    accepter_team = cell["frozenTeam"] if candidate_is_challenger else cell["candidateTeam"]
    challenger_team = cell["candidateTeam"] if candidate_is_challenger else cell["frozenTeam"]
    accepter_label = frozen_label if candidate_is_challenger else candidate_label
    challenger_label = candidate_label if candidate_is_challenger else frozen_label
    label_dirs = {candidate_label: candidate_dir, frozen_label: frozen_dir}
    ws_uri = f"ws://127.0.0.1:{showdown_port}/showdown/websocket"
    count = int(cell["requestedBattles"])
    frozen_role = "accepter" if cell["candidateRole"] == "challenger" else "challenger"
    candidate_expected_provenance = {
        "account": candidate_user,
        "format": "gen9ou",
        "source_commit": baseline_commit,
        "session_id": "session-" + uuid.uuid4().hex,
        "h2h_run_id": run_id,
        "h2h_cell_id": cell["id"],
        "h2h_arm": "candidate",
        "h2h_role": cell["candidateRole"],
        "h2h_team": cell["candidateTeam"],
        "h2h_account": candidate_user,
        "h2h_opponent": frozen_user,
        "h2h_baseline_commit": baseline_commit,
        "h2h_candidate_patch_sha256": candidate_patch_sha256,
        "h2h_engine_digest": candidate_runtime_digest,
        "h2h_change_id": change_id,
    }
    frozen_expected_provenance = {
        "account": frozen_user,
        "format": "gen9ou",
        "source_commit": baseline_commit,
        "session_id": "session-" + uuid.uuid4().hex,
        "h2h_run_id": run_id,
        "h2h_cell_id": cell["id"],
        "h2h_arm": "frozen",
        "h2h_role": frozen_role,
        "h2h_team": cell["frozenTeam"],
        "h2h_account": frozen_user,
        "h2h_opponent": candidate_user,
        "h2h_baseline_commit": baseline_commit,
        "h2h_candidate_patch_sha256": candidate_patch_sha256,
        "h2h_engine_digest": frozen_runtime_digest,
        "h2h_change_id": change_id,
    }
    accepter_command = build_agent_command(
        accepter_root,
        python_command,
        username=accepter_user,
        mode="accept_challenge",
        opponent=challenger_user,
        team=accepter_team,
        battles=count,
        ws_uri=ws_uri,
        search_time_ms=search_time_ms,
    )
    challenger_command = build_agent_command(
        challenger_root,
        python_command,
        username=challenger_user,
        mode="challenge_user",
        opponent=accepter_user,
        team=challenger_team,
        battles=count,
        ws_uri=ws_uri,
        search_time_ms=search_time_ms,
    )
    accepter: subprocess.Popen | None = None
    challenger: subprocess.Popen | None = None
    cleanup: list[dict[str, Any]] = []
    try:
        accepter = start_agent(
            accepter_command,
            cwd=accepter_root,
            env=build_agent_env(
                accepter_label,
                label_dirs[accepter_label],
                showdown_port,
                search_time_ms,
            ),
            log_path=label_dirs[accepter_label] / "agent.log",
        )
        time.sleep(startup_wait)
        if accepter.poll() is not None:
            raise RuntimeError(f"accepter exited before challenge with code {accepter.returncode}")
        challenger = start_agent(
            challenger_command,
            cwd=challenger_root,
            env=build_agent_env(
                challenger_label,
                label_dirs[challenger_label],
                showdown_port,
                search_time_ms,
            ),
            log_path=label_dirs[challenger_label] / "agent.log",
        )
        deadline = time.monotonic() + per_battle_timeout * count + 120
        while time.monotonic() < deadline:
            if accepter.poll() is not None and challenger.poll() is not None:
                break
            time.sleep(1)
        if accepter.poll() is None or challenger.poll() is None:
            raise TimeoutError(f"{cell['id']} exceeded its bounded evaluation window")
        result = summarize_cell(
            cell,
            candidate_stats_path=candidate_dir / "battle_stats.json",
            frozen_stats_path=frozen_dir / "battle_stats.json",
            candidate_returncode=(
                challenger.returncode if candidate_is_challenger and challenger else accepter.returncode
            ),
            frozen_returncode=(
                accepter.returncode
                if candidate_is_challenger
                else challenger.returncode if challenger else None
            ),
            candidate_expected_provenance=candidate_expected_provenance,
            frozen_expected_provenance=frozen_expected_provenance,
        )
        result["cleanup"] = cleanup
        return result
    finally:
        containment_failures: list[str] = []
        for name, process in (("challenger", challenger), ("accepter", accepter)):
            if process is not None and process.poll() is None:
                detail = _terminate_process_tree(process, reason=f"head-to-head-{name}-cleanup")
                cleanup.append(detail)
                if detail.get("returncodeAfter") is None:
                    containment_failures.append(f"{name} process tree remained alive")
        shutil.rmtree(candidate_dir, ignore_errors=True)
        shutil.rmtree(frozen_dir, ignore_errors=True)
        if candidate_dir.exists() or frozen_dir.exists():
            containment_failures.append("opaque arm state cleanup failed")
        if containment_failures:
            raise EvaluationContainmentError("; ".join(containment_failures))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json_document_bytes(payload))
    os.replace(temporary, path)


def _write_exclusive_evidence(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def materialize_deferred_evidence(run_dir: Path, cells: list[dict[str, Any]]) -> None:
    """Persist controller evidence only after every opaque arm has stopped."""

    resolved_run_dir = run_dir.resolve(strict=False)
    for cell in cells:
        deferred = cell.pop("_deferredEvidence", None)
        if not isinstance(deferred, dict):
            raise RuntimeError(f"cell {cell.get('id')} is missing deferred controller evidence")
        for relative_text, data in deferred.items():
            relative = Path(str(relative_text))
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.as_posix() != str(relative_text)
                or not isinstance(data, bytes)
            ):
                raise RuntimeError("deferred controller evidence contains an unsafe path or payload")
            destination = (run_dir / relative).resolve(strict=False)
            try:
                destination.relative_to(resolved_run_dir)
            except ValueError as exc:
                raise RuntimeError("deferred controller evidence escapes the judge directory") from exc
            _write_exclusive_evidence(destination, data)


def _open_evaluation_ledger(
    path: Path,
    ledger_id: str,
    *,
    ledger_authority: LedgerAuthority | None = None,
):
    return open_evaluation_ledger(
        path,
        ledger_id,
        authority=ledger_authority,
        writable=True,
    )


def register_evaluation_attempt(
    *,
    ledger_path: Path,
    ledger_id: str,
    runtime_family_id: str,
    protocol_digest: str,
    change_id: str,
    baseline_commit: str,
    candidate_patch_sha256: str,
    candidate_file: str,
    run_id: str,
    ledger_authority: LedgerAuthority | None = None,
    allow_unanchored_test_only: bool = False,
) -> dict[str, Any]:
    """Mirror an attempt only when an external durable anchor is available.

    The current local SQLite authority is useful evidence but is replaceable by
    its owning OS identity. Production therefore fails closed. Unit tests may
    exercise the append-only SQLite mechanics explicitly; those rows cannot be
    produced by ``run_head_to_head`` and are never promotion authority.
    """

    if not allow_unanchored_test_only:
        anchor = durable_attempt_anchor_status(ledger_authority)
        return {
            "registered": False,
            "externalAnchorProven": False,
            "anchorStatus": anchor,
            "blocker": str(anchor["blocker"]),
        }
    try:
        connection = _open_evaluation_ledger(
            ledger_path,
            ledger_id,
            ledger_authority=ledger_authority,
        )
    except Exception as exc:
        return {"registered": False, "blocker": str(exc)}
    try:
        connection.execute("BEGIN IMMEDIATE")
        used = int(
            connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE runtime_family_id = ?", (runtime_family_id,)
            ).fetchone()[0]
        )
        if used >= MAX_ATTEMPTS_PER_BASELINE:
            connection.rollback()
            return {
                "registered": False,
                "blocker": (
                    f"runtime family exhausted its {MAX_ATTEMPTS_PER_BASELINE}-attempt family-wise error budget"
                ),
                "runtimeFamilyId": runtime_family_id,
                "attemptsUsed": used,
                "maximumAttempts": MAX_ATTEMPTS_PER_BASELINE,
                "perAttemptAlpha": MAX_ONE_SIDED_P,
                "familyWiseAlpha": FAMILY_WISE_ALPHA,
            }
        attempt_id = uuid.uuid4().hex
        ordinal = used + 1
        registered_at = utc_now()
        cursor = connection.execute(
            """
            INSERT INTO attempts(
                attempt_id, ledger_id, run_id, registered_at_utc, runtime_family_id,
                protocol_digest, change_id, baseline_commit, candidate_patch_sha256,
                candidate_file, attempt_ordinal, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'registered')
            """,
            (
                attempt_id,
                ledger_id,
                run_id,
                registered_at,
                runtime_family_id,
                protocol_digest,
                change_id,
                baseline_commit,
                candidate_patch_sha256,
                candidate_file,
                ordinal,
            ),
        )
        sequence = int(cursor.lastrowid)
        connection.commit()
        return {
            "registered": True,
            "testOnlyUnanchored": True,
            "externalAnchorProven": False,
            "schemaVersion": "fouler-head-to-head-attempt/v2",
            "ledgerId": ledger_id,
            "attemptId": attempt_id,
            "registrationSequence": sequence,
            "runId": run_id,
            "registeredAtUtc": registered_at,
            "runtimeFamilyId": runtime_family_id,
            "protocolDigest": protocol_digest,
            "changeId": change_id,
            "baselineCommit": baseline_commit,
            "candidatePatchSha256": candidate_patch_sha256,
            "candidateFile": candidate_file,
            "attemptOrdinal": ordinal,
            "maximumAttempts": MAX_ATTEMPTS_PER_BASELINE,
            "perAttemptAlpha": MAX_ONE_SIDED_P,
            "familyWiseAlpha": FAMILY_WISE_ALPHA,
        }
    except Exception as exc:
        connection.rollback()
        return {"registered": False, "blocker": f"attempt ledger registration failed: {exc}"}
    finally:
        connection.close()


def finalize_evaluation_attempt(
    *,
    ledger_path: Path,
    ledger_id: str,
    attempt_id: str,
    result_sha256: str,
    status: str,
    ledger_authority: LedgerAuthority | None = None,
) -> None:
    connection = _open_evaluation_ledger(
        ledger_path,
        ledger_id,
        ledger_authority=ledger_authority,
    )
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE attempts SET status = ?, result_sha256 = ?
            WHERE attempt_id = ? AND ledger_id = ? AND status = 'registered'
            """,
            (status, result_sha256, attempt_id, ledger_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("attempt ledger finalization did not update exactly one registered attempt")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_head_to_head(
    *,
    candidate_file: str,
    battles: int,
    teams: tuple[str, ...],
    showdown_port: int,
    search_time_ms: int,
    per_battle_timeout: float,
    startup_wait: float,
    allow_identical_smoke: bool,
    autoresearch_sha256: str = "",
    authority_path: Path | None = None,
    ledger_path: Path | None = None,
    ledger_id: str | None = None,
) -> dict[str, Any]:
    runtime_limit_blockers = evaluation_runtime_limit_blockers(
        search_time_ms,
        per_battle_timeout,
    )
    if runtime_limit_blockers:
        raise ValueError("; ".join(runtime_limit_blockers))
    cells = build_evaluation_cells(teams, battles)
    configured_ledger_path, configured_ledger_id, ledger_authority = _resolve_ledger_configuration(
        authority_path=authority_path,
        ledger_path=ledger_path,
        ledger_id=ledger_id,
    )
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    managed_server: subprocess.Popen | None = None
    execution_blockers: list[str] = []
    attempt_budget: dict[str, Any] = {}

    with prepared_worktrees(candidate_file, allow_identical=allow_identical_smoke) as prepared:
        candidate_runtime = prepared["candidateRuntime"]
        frozen_runtime = prepared["frozenRuntime"]
        runtime_differences = sorted(
            path
            for path in set(candidate_runtime["files"]) | set(frozen_runtime["files"])
            if candidate_runtime["files"].get(path) != frozen_runtime["files"].get(path)
        )
        expected_differences = [] if prepared["identicalSmoke"] else [prepared["candidateFile"]]
        if runtime_differences != expected_differences:
            raise RuntimeError(
                "candidate/frozen runtime manifests differ outside the candidate file: "
                f"expected={expected_differences} actual={runtime_differences}"
            )
        protocol = protocol_manifest()
        python_command = resolve_fouler_python()
        python_runtime = python_runtime_manifest(python_command)
        showdown_runtime = showdown_runtime_manifest()
        environment_shape_dir = Path(tempfile.mkdtemp(prefix="fouler-env-shape-"))
        try:
            environment_sample = build_agent_env(
                "arm-manifest",
                environment_shape_dir,
                showdown_port,
                search_time_ms,
            )
        finally:
            shutil.rmtree(environment_shape_dir, ignore_errors=True)
        if environment_shape_dir.exists():
            raise RuntimeError("evaluation environment-shape directory cleanup failed")
        normalized_environment = {
            key: value.replace(str(environment_shape_dir), "<ARM_STATE_DIR>").replace(
                "arm-manifest",
                "<ARM_LABEL>",
            )
            for key, value in sorted(environment_sample.items())
        }
        environment_policy = {
            "keys": sorted(normalized_environment),
            "values": normalized_environment,
            "digest": canonical_sha256(normalized_environment),
        }
        runtime_family_id = canonical_sha256(
            {
                "frozenRuntimeDigest": frozen_runtime["digest"],
                "protocolDigest": protocol["digest"],
                "pythonRuntimeDigest": python_runtime["digest"],
                "showdownRuntimeDigest": showdown_runtime["digest"],
                "environmentPolicyDigest": environment_policy["digest"],
            }
        )
        change_id = canonical_sha256(
            {
                "runtimeFamilyId": runtime_family_id,
                "baselineCommit": prepared["baselineCommit"],
                "candidateFile": prepared["candidateFile"],
                "candidatePatchSha256": prepared["candidatePatchSha256"],
                "candidateRuntimeDigest": candidate_runtime["digest"],
                "autoresearchSha256": autoresearch_sha256,
            }
        )
        runtime_payload = {
            "schemaVersion": "fouler-head-to-head-runtime/v2",
            "runtimeFamilyId": runtime_family_id,
            "candidateRuntime": candidate_runtime,
            "frozenRuntime": frozen_runtime,
            "runtimeDifferences": runtime_differences,
            "protocol": protocol,
            "python": python_runtime,
            "showdown": showdown_runtime,
            "environmentPolicy": environment_policy,
        }
        runtime_relative_path = Path("runtime-manifest.json")
        runtime_document = json_document_bytes(runtime_payload)
        runtime_evidence = bytes_evidence(runtime_document, runtime_relative_path)
        attempt_budget = (
            {
                "registered": False,
                "mode": "identical-smoke",
                "consumesAttempt": False,
                "maximumAttempts": MAX_ATTEMPTS_PER_BASELINE,
                "perAttemptAlpha": MAX_ONE_SIDED_P,
                "familyWiseAlpha": FAMILY_WISE_ALPHA,
            }
            if prepared["identicalSmoke"]
            else None
        )
        if attempt_budget is None:
            try:
                attempt_budget = register_evaluation_attempt(
                    ledger_path=configured_ledger_path,
                    ledger_id=configured_ledger_id,
                    runtime_family_id=runtime_family_id,
                    protocol_digest=protocol["digest"],
                    change_id=change_id,
                    baseline_commit=prepared["baselineCommit"],
                    candidate_patch_sha256=prepared["candidatePatchSha256"],
                    candidate_file=prepared["candidateFile"],
                    run_id=run_id,
                    ledger_authority=ledger_authority,
                )
            except Exception as exc:
                attempt_budget = {"registered": False, "blocker": str(exc)}
        report: dict[str, Any] = {
            "schemaVersion": "fouler-head-to-head-eval/v2",
            "runId": run_id,
            "startedAtUtc": utc_now(),
            "status": "running",
            "baselineCommit": prepared["baselineCommit"],
            "candidatePatchSha256": prepared["candidatePatchSha256"],
            "candidateFile": prepared["candidateFile"],
            "identicalSmoke": prepared["identicalSmoke"],
            "attemptBudget": attempt_budget,
            "lineage": {
                "changeId": change_id,
                "baselineCommit": prepared["baselineCommit"],
                "candidateFile": prepared["candidateFile"],
                "candidatePatchSha256": prepared["candidatePatchSha256"],
                "autoresearchSha256": autoresearch_sha256,
            },
            "runtimeEvidence": runtime_evidence,
            "runtimeFamilyId": runtime_family_id,
            "candidateRuntimeDigest": candidate_runtime["digest"],
            "frozenRuntimeDigest": frozen_runtime["digest"],
            "protocolDigest": protocol["digest"],
            "configuration": {
                "requestedBattles": battles,
                "teams": list(teams),
                "showdownPort": showdown_port,
                "searchTimeMs": search_time_ms,
                "perBattleTimeoutSeconds": per_battle_timeout,
                "matrixCellCount": len(cells),
                "battlesPerCell": battles // len(cells),
            },
            "cells": [],
        }
        if not prepared["identicalSmoke"] and not attempt_budget.get("registered"):
            execution_blockers.append(str(attempt_budget.get("blocker") or "attempt budget registration failed"))
        try:
            managed_server = start_managed_showdown_server(showdown_port)
            if managed_server is None:
                raise RuntimeError("head-to-head evaluation refuses to adopt an existing Showdown listener")
            if not showdown_server_reachable(showdown_port):
                raise RuntimeError("managed Showdown server is not reachable")
            for cell in cells:
                result = run_cell(
                    cell,
                    candidate_root=prepared["candidateRoot"],
                    frozen_root=prepared["frozenRoot"],
                    python_command=python_command,
                    showdown_port=showdown_port,
                    search_time_ms=search_time_ms,
                    per_battle_timeout=per_battle_timeout,
                    startup_wait=startup_wait,
                    run_id=run_id,
                    baseline_commit=prepared["baselineCommit"],
                    candidate_patch_sha256=prepared["candidatePatchSha256"],
                    change_id=change_id,
                    candidate_runtime_digest=candidate_runtime["digest"],
                    frozen_runtime_digest=frozen_runtime["digest"],
                )
                report["cells"].append(result)
        except EvaluationContainmentError:
            raise
        except Exception as exc:
            report["status"] = "blocked"
            execution_blockers.append(str(exc))
        finally:
            if managed_server is not None:
                cleanup_detail = _terminate_process_tree(
                    managed_server,
                    reason="head-to-head-eval-complete",
                )
                report["showdownCleanup"] = cleanup_detail
                if cleanup_detail.get("returncodeAfter") is None:
                    execution_blockers.append("managed Showdown process did not terminate")
                deadline = time.monotonic() + 5
                while showdown_server_reachable(showdown_port) and time.monotonic() < deadline:
                    time.sleep(0.25)
                if showdown_server_reachable(showdown_port):
                    execution_blockers.append("managed Showdown listener remained reachable after cleanup")

        judge_parent = Path(tempfile.mkdtemp(prefix="fouler-eval-result-"))
        run_dir = judge_parent / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        materialize_deferred_evidence(run_dir, report["cells"])
        _write_exclusive_evidence(run_dir / runtime_relative_path, runtime_document)

        verdict = evaluate_matrix(
            report["cells"],
            requested_battles=battles,
            baseline_commit=prepared["baselineCommit"],
            candidate_patch_sha256=prepared["candidatePatchSha256"],
        )
        if execution_blockers:
            verdict["promotionAllowed"] = False
            verdict["blockers"] = list(dict.fromkeys([*execution_blockers, *verdict["blockers"]]))
        if prepared["identicalSmoke"]:
            verdict["promotionAllowed"] = False
            verdict["blockers"] = list(
                dict.fromkeys([*verdict["blockers"], "identical-code smoke cannot authorize promotion"])
            )
        report.update(verdict)
        report["status"] = "promotion-ready" if verdict["promotionAllowed"] else "promotion-blocked"
        report["completedAtUtc"] = utc_now()
        write_json(run_dir / "result.json", report)

    result_path = run_dir / "result.json"
    result_sha256 = hashlib.sha256(result_path.read_bytes()).hexdigest()
    if attempt_budget.get("registered"):
        finalize_evaluation_attempt(
            ledger_path=configured_ledger_path,
            ledger_id=configured_ledger_id,
            attempt_id=str(attempt_budget["attemptId"]),
            result_sha256=result_sha256,
            status=str(report["status"]),
            ledger_authority=ledger_authority,
        )
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    final_run_dir = RESULTS_ROOT / run_id
    if final_run_dir.exists():
        raise RuntimeError(f"canonical head-to-head run directory already exists: {final_run_dir}")
    os.replace(run_dir, final_run_dir)
    judge_parent.rmdir()
    pointer = {
        "schemaVersion": "fouler-head-to-head-pointer/v2",
        "runId": run_id,
        "resultRelativePath": f"{run_id}/result.json",
        "resultSha256": result_sha256,
        "completedAtUtc": report["completedAtUtc"],
    }
    write_json(LATEST_RESULT, pointer)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-file")
    parser.add_argument("--battles", type=int, default=MIN_PROMOTION_BATTLES)
    parser.add_argument("--teams", default=",".join(DEFAULT_TEAMS))
    parser.add_argument("--showdown-port", type=int, default=8791)
    parser.add_argument("--search-time-ms", type=int, default=1200)
    parser.add_argument("--per-battle-timeout", type=float, default=240.0)
    parser.add_argument("--startup-wait", type=float, default=8.0)
    parser.add_argument("--allow-identical-smoke", action="store_true")
    parser.add_argument("--autoresearch-sha256", default="")
    parser.add_argument("--initialize-ledger", action="store_true")
    parser.add_argument("--ledger-path")
    parser.add_argument("--ledger-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-promotion", action="store_true")
    args = parser.parse_args()
    if args.initialize_ledger:
        if not str(args.ledger_id or "").strip():
            parser.error("--ledger-id is required with --initialize-ledger")
        authority = initialize_ledger_authority(
            ledger_path=Path(args.ledger_path) if args.ledger_path else DEFAULT_LEDGER_PATH,
            ledger_id=str(args.ledger_id),
        )
        print(json.dumps(authority.as_dict(created=True), indent=2))
        return 0
    if args.ledger_path or args.ledger_id:
        parser.error("--ledger-path and --ledger-id are only valid with --initialize-ledger")
    if not args.candidate_file:
        parser.error("--candidate-file is required unless --initialize-ledger is used")
    teams = tuple(team.strip() for team in args.teams.split(",") if team.strip())
    cells = build_evaluation_cells(teams, args.battles)
    runtime_limit_blockers = evaluation_runtime_limit_blockers(
        args.search_time_ms,
        args.per_battle_timeout,
    )
    if runtime_limit_blockers:
        parser.error("; ".join(runtime_limit_blockers))
    if args.dry_run:
        ledger_configuration()
        print(json.dumps({"requestedBattles": args.battles, "teams": teams, "cells": cells}, indent=2))
        return 0
    report = run_head_to_head(
        candidate_file=args.candidate_file,
        battles=args.battles,
        teams=teams,
        showdown_port=args.showdown_port,
        search_time_ms=args.search_time_ms,
        per_battle_timeout=args.per_battle_timeout,
        startup_wait=args.startup_wait,
        allow_identical_smoke=args.allow_identical_smoke,
        autoresearch_sha256=args.autoresearch_sha256,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("promotionAllowed") or not args.require_promotion else 2


if __name__ == "__main__":
    raise SystemExit(main())
