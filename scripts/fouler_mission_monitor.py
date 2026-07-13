#!/usr/bin/env python3
"""Mission monitor for the Fouler devstream lane.

This is deliberately deterministic. It refreshes health, classifies mission
violations, writes HERMES-readable tickets, and can restart the bounded ladder
supervisor only when duplicate/process/lease rails make that safe.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TRUTH_DIR = ROOT / "devstream" / "truth"
TICKET_DIR = ROOT / "devstream" / "tickets" / "fouler-play"
MISSION_MONITOR_FILE = TRUTH_DIR / "mission-monitor.json"
HEALTH_FILE = TRUTH_DIR / "health.json"
SUPERVISOR_STATUS_FILE = TRUTH_DIR / "supervisor-status.json"
RUNTIME_LEASE_FILE = TRUTH_DIR / "runtime-lease.json"
LATEST_ELO_PROOF_FILE = TRUTH_DIR / "latest-elo-proof.json"
ACTIVE_IMPROVEMENT_PROOF_FILE = TRUTH_DIR / "post-packet-eval.json"
ACCOUNT_SEASON_FILE = TRUTH_DIR / "account-season.json"
BATTLE_STATS_FILE = ROOT / "battle_stats.json"
STALE_ACTIVE_BATTLE_BACKUP_DIR = TRUTH_DIR / "stale-active-battles-backups"
DRAIN_FILE = ROOT / ".pids" / "drain.request"
SUPERVISOR_STOP_FILE = ROOT / ".pids" / "supervisor.stop"
RECOVERY_PROOF_WINDOW_FILE = ROOT / ".pids" / "recovery-proof-window.json"
SUPERVISOR_STOP_FILE_ISSUE_ID = "fouler-supervisor-stop-file-present"
ABANDONED_BATTLE_ISSUE_ID = "fouler-abandoned-battle-without-result"
RATING_TRUTH_BUILDING_ISSUE_ID = "fouler-rating-truth-building"
LOGS_DIR = ROOT / "logs"
BATTLE_LOG_GLOB = "battle-*.log"
SESSION_LOG_NAME = "init.log"
DECISION_TRACE_DIR = LOGS_DIR / "decision_traces"
# Played-vs-policy divergence gauge (2026-07-04): a decision "diverges" when the
# played move carried < 45% of the search's best raw policy weight. The loop-
# breaker defect (#79 hunt) caused 94% of these inversions; the gauge should sit
# near zero with the breaker guarded/disabled and catches future overrider layers.
DECISION_DIVERGENCE_PLAYED_FRACTION = 0.45
DECISION_DIVERGENCE_MAX_TRACES = 400
LIVE_SIGNAL_MAX_BATTLE_LOG_AGE_SECONDS = 3600
SIGNAL_STATE_LIVE = "LIVE"
SIGNAL_STATE_STALE = "STALE — bot not playing; do not trust gauges"

DEFAULT_ACCOUNT = ""
CANONICAL_TARGET_RATING = 1700
DEFAULT_MONITOR_RUN_COUNT = 5
DEFAULT_MONITOR_MAX_CYCLES = 1
CANONICAL_LOOP_BREAK = "0"
STOP_LOSS_RECOVERY_VALIDATION_MAX_RUN_COUNT = 5
STOP_LOSS_RECOVERY_VALIDATION_MAX_CYCLES = 1
REPAIR_QUEUE_SCHEMA_VERSION = "fouler-play-repair-queue/v1"
REPAIR_PACKET_SCHEMA_VERSION = "fouler-play-skid-repair-packet/v1"
OFFLINE_EVAL_RESULT_PROOF_SCHEMA_VERSION = "fouler-play-offline-eval-result-proof/v1"
OFFLINE_EVAL_RESUME_PROOF_POLICY = "fouler-offline-eval-resume-proof/v1"
ACTIVE_IMPROVEMENT_PROOF_POLICY = "fouler-active-improvement-proof/v1"
ACTIVE_IMPROVEMENT_PROOF_SCHEMA_VERSION = "fouler-play-post-packet-eval/v1"
ACTIVE_IMPROVEMENT_PROOF_MAX_AGE_SECONDS = 21600
ACTIVE_IMPROVEMENT_ISSUE_ID = "fouler-active-improvement-proof-missing"
OFFLINE_EVAL_RESUME_ISSUE_ID = "fouler-offline-eval-resume-proof-missing"
ACCOUNT_AUTHORITY_MISMATCH_ISSUE_ID = "fouler-account-authority-mismatch"
ACCOUNT_TELEMETRY_MISSING_ISSUE_ID = "fouler-runtime-account-telemetry-missing"
SUSTAIN_RUNTIME_CHUNK_MAX_GAMES = 5
LADDER_STAGE_POLICY = (
    {
        "id": "establish-baseline",
        "ratingFloor": 0,
        "targetRating": 1500,
        "maxBatchGames": 5,
        "requiredProof": "first fresh rated proof window without duplicate runners or reporting gaps",
    },
    {
        "id": "prove-1500",
        "ratingFloor": 1,
        "targetRating": 1500,
        "maxBatchGames": 10,
        "requiredProof": "bounded proof batches until current rating is at least 1500 with stop-loss clear",
    },
    {
        "id": "prove-1600",
        "ratingFloor": 1500,
        "targetRating": 1600,
        "maxBatchGames": 8,
        "requiredProof": "shorter proof batches after 1500 so rating drawdown is caught before a full skid",
    },
    {
        "id": "prove-1700",
        "ratingFloor": 1600,
        "targetRating": 1700,
        "maxBatchGames": 5,
        "requiredProof": "very small batches near 1700 plus replay/error analysis between attempts",
    },
    {
        "id": "sustain-1700",
        "ratingFloor": 1700,
        "targetRating": 1700,
        "maxBatchGames": SUSTAIN_RUNTIME_CHUNK_MAX_GAMES,
        "requiredProof": "30-game post-1700 sustain proof accumulated through re-gated five-game chunks with per-team coverage",
    },
)
SUSTAIN_MINIMUM_GAMES = 30
SUSTAIN_MINIMUM_GAMES_PER_TEAM = 10
SUSTAIN_MAX_DRAWDOWN = 75.0
SUSTAIN_MINIMUM_WIN_RATE = 0.5
SUSTAIN_REQUIRED_TEAMS = ("fat-team-1-stall", "fat-team-2-pivot", "fat-team-3-dondozo")
LADDER_STAGE_FLOOR_PROOF_MINIMUM_GAMES = 5
RATING_TRUTH_MIN_RATED_DECISIVE_BATTLES = 20
ANALYSIS_EVIDENCE_PATH_KEYS = (
    "autoresearchJsonPath",
    "autoresearchReportPath",
    "decisionTraceReviewPath",
)
LOSS_WORDS = {"loss", "lost", "timeout", "timed out", "disconnect", "disconnected", "inactive", "forfeit"}
WIN_WORDS = {"win", "won"}
KNOWN_ISSUE_IDS = {
    "fouler-health-stale",
    ABANDONED_BATTLE_ISSUE_ID,
    "fouler-duplicate-ladder-runners",
    "fouler-runtime-idle",
    "fouler-supervisor-max-cycles-complete",
    "fouler-discord-reporting-unhealthy",
    "fouler-loss-streak",
    "fouler-low-recent-win-rate",
    RATING_TRUTH_BUILDING_ISSUE_ID,
    "fouler-rating-truth-insufficient",
    "fouler-rating-drawdown",
    "fouler-elo-target-floor-breach",
    "fouler-ladder-floor-regression",
    "fouler-ladder-batch-too-large-for-stage",
    SUPERVISOR_STOP_FILE_ISSUE_ID,
    ACCOUNT_AUTHORITY_MISMATCH_ISSUE_ID,
    ACCOUNT_TELEMETRY_MISSING_ISSUE_ID,
    OFFLINE_EVAL_RESUME_ISSUE_ID,
    ACTIVE_IMPROVEMENT_ISSUE_ID,
    "fouler-session-stop-loss-breached",
    "fouler-elo-sustain-proof-missing-or-failing",
}
SESSION_STOP_LOSS_ISSUE_IDS = {
    "fouler-loss-streak",
    "fouler-low-recent-win-rate",
    "fouler-rating-truth-insufficient",
    "fouler-rating-drawdown",
    "fouler-elo-target-floor-breach",
    "fouler-ladder-floor-regression",
    "fouler-ladder-batch-too-large-for-stage",
}
START_GATE_BLOCKING_ISSUE_IDS = {
    "fouler-health-stale",
    ABANDONED_BATTLE_ISSUE_ID,
    "fouler-duplicate-ladder-runners",
    "fouler-discord-reporting-unhealthy",
    "fouler-loss-streak",
    "fouler-low-recent-win-rate",
    "fouler-rating-truth-insufficient",
    "fouler-rating-drawdown",
    "fouler-elo-target-floor-breach",
    "fouler-ladder-floor-regression",
    "fouler-ladder-batch-too-large-for-stage",
    SUPERVISOR_STOP_FILE_ISSUE_ID,
    ACCOUNT_AUTHORITY_MISMATCH_ISSUE_ID,
    OFFLINE_EVAL_RESUME_ISSUE_ID,
    ACTIVE_IMPROVEMENT_ISSUE_ID,
    "fouler-session-stop-loss-breached",
}
STOP_LOSS_RECOVERY_VALIDATION_ALLOWED_ISSUE_IDS = {
    "fouler-low-recent-win-rate",
    "fouler-rating-truth-insufficient",
    "fouler-rating-drawdown",
    "fouler-elo-target-floor-breach",
    "fouler-ladder-floor-regression",
    ACTIVE_IMPROVEMENT_ISSUE_ID,
    "fouler-session-stop-loss-breached",
}
REPAIR_QUEUE_TRIGGER_ISSUE_IDS = SESSION_STOP_LOSS_ISSUE_IDS | {
    OFFLINE_EVAL_RESUME_ISSUE_ID,
    ACTIVE_IMPROVEMENT_ISSUE_ID,
    "fouler-session-stop-loss-breached",
    "fouler-elo-sustain-proof-missing-or-failing",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(value: object, *, now: datetime | None = None) -> float | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - parsed).total_seconds())


def recovery_proof_window_status(now: datetime | None = None) -> dict[str, Any]:
    """Describe the one bounded stop-loss recovery proof window, if present."""

    now = now or datetime.now(timezone.utc)
    marker = load_json(RECOVERY_PROOF_WINDOW_FILE, {})
    path = display_path(RECOVERY_PROOF_WINDOW_FILE)
    if not isinstance(marker, dict) or not marker:
        return {
            "schemaVersion": "fouler-play-recovery-proof-window-status/v1",
            "active": False,
            "path": path,
            "reason": "missing",
            "noRuntimeActions": True,
        }

    blockers: list[str] = []
    if marker.get("approved") is not True:
        blockers.append("approved must be true")
    if str(marker.get("purpose") or "") != "stop-loss-recovery-proof-window":
        blockers.append("purpose must be stop-loss-recovery-proof-window")

    try:
        run_count = int(marker.get("runCount") or 0)
    except (TypeError, ValueError):
        run_count = 0
    try:
        max_cycles = int(marker.get("maxCycles") or 0)
    except (TypeError, ValueError):
        max_cycles = 0
    try:
        max_concurrent_battles = int(marker.get("maxConcurrentBattles") or 0)
    except (TypeError, ValueError):
        max_concurrent_battles = 0

    if run_count < 1 or run_count > STOP_LOSS_RECOVERY_VALIDATION_MAX_RUN_COUNT:
        blockers.append(
            f"runCount must be 1-{STOP_LOSS_RECOVERY_VALIDATION_MAX_RUN_COUNT}; got {run_count}"
        )
    if max_cycles < 1 or max_cycles > STOP_LOSS_RECOVERY_VALIDATION_MAX_CYCLES:
        blockers.append(
            f"maxCycles must be 1-{STOP_LOSS_RECOVERY_VALIDATION_MAX_CYCLES}; got {max_cycles}"
        )
    if max_concurrent_battles != 1:
        blockers.append("maxConcurrentBattles must be 1")
    if str(marker.get("loopBreak") or "") != CANONICAL_LOOP_BREAK:
        blockers.append(f"loopBreak must be {CANONICAL_LOOP_BREAK}")
    if marker.get("noStreamStart") is not True:
        blockers.append("noStreamStart must be true")

    launched_at = parse_timestamp(marker.get("launchedAtUtc") or marker.get("createdAtUtc"))
    expires_at = parse_timestamp(marker.get("expiresAtUtc") or marker.get("expiresAt"))
    if expires_at is None:
        blockers.append("expiresAtUtc missing/invalid")
    elif expires_at <= now:
        blockers.append("expired")

    return {
        "schemaVersion": "fouler-play-recovery-proof-window-status/v1",
        "active": not blockers,
        "path": path,
        "marker": marker,
        "blockers": blockers,
        "runCount": run_count,
        "maxCycles": max_cycles,
        "maxConcurrentBattles": max_concurrent_battles,
        "launchedAtUtc": launched_at.isoformat() if launched_at else None,
        "expiresAtUtc": expires_at.isoformat() if expires_at else None,
        "noRuntimeActions": True,
    }


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def runtime_python() -> str:
    configured = os.getenv("FOULER_RUNTIME_PYTHON", "").strip()
    if configured:
        return configured
    for candidate in (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv-eval" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python"


def tail_text(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text[-limit:]


def run_command(command: list[str], *, timeout: int = 60) -> dict[str, Any]:
    started = time.time()
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "returnCode": result.returncode,
            "ok": result.returncode == 0,
            "stdoutTail": tail_text(result.stdout),
            "stderrTail": tail_text(result.stderr),
            "durationSeconds": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returnCode": None,
            "ok": False,
            "timedOut": True,
            "stdoutTail": tail_text(exc.stdout or ""),
            "stderrTail": tail_text(exc.stderr or ""),
            "durationSeconds": round(time.time() - started, 3),
        }
    except Exception as exc:
        return {
            "command": command,
            "returnCode": None,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "durationSeconds": round(time.time() - started, 3),
        }


def current_source_commit() -> str | None:
    result = run_command(["git", "rev-parse", "HEAD"], timeout=5)
    if not result.get("ok"):
        return None
    return str(result.get("stdoutTail") or "").strip() or None


def powershell_exe() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.exists() else "powershell.exe"


def refresh_health(*, skip_http: bool = False, write: bool = False) -> dict[str, Any]:
    command = [runtime_python(), "scripts/devstream_health.py"]
    if write:
        command.append("--write")
    if skip_http:
        command.append("--skip-http")
    return run_command(command, timeout=90)


def supervisor_task_status() -> dict[str, Any]:
    installer = ROOT / "scripts" / "install_battle_supervisor_task.ps1"
    if not installer.exists():
        return {"ok": False, "error": "missing scripts/install_battle_supervisor_task.ps1"}
    result = run_command(
        [
            powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
            "-Status",
        ],
        timeout=45,
    )
    try:
        parsed = json.loads(result.get("stdoutTail") or "{}")
    except json.JSONDecodeError:
        parsed = {}
    result["status"] = parsed
    return result


def newest_battle_log(logs_dir: Path | None = None) -> dict[str, Any]:
    """Newest per-battle/session log under logs/ plus its age in seconds.

    Only run.py's own play output counts as play signal: per-battle
    battle-*.log files and the active init.log session log. Keepalive or
    reporting logs must never refresh this signal — they keep getting written
    while the ladder client is dead, which is exactly the instrument lie this
    field exists to expose.
    """
    logs_dir = LOGS_DIR if logs_dir is None else logs_dir
    candidates = list(logs_dir.glob(BATTLE_LOG_GLOB))
    session_log = logs_dir / SESSION_LOG_NAME
    if session_log.exists():
        candidates.append(session_log)
    newest_path: Path | None = None
    newest_mtime: float | None = None
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if newest_mtime is None or mtime > newest_mtime:
            newest_mtime = mtime
            newest_path = path
    if newest_path is None or newest_mtime is None:
        return {"path": None, "ageSeconds": None}
    return {
        "path": display_path(newest_path),
        "ageSeconds": round(max(0.0, time.time() - newest_mtime), 3),
    }


def ladder_client_top_level_pids(processes: list[Mapping[str, Any]]) -> list[int]:
    """Top-level ladder-client PIDs from a process snapshot.

    Mirrors the singleton detection in scripts/fouler_keepalive.ps1: a ladder
    client is a python process whose cmdline contains BOTH run.py AND
    search_ladder, and a TOP-LEVEL client is a matching process whose parent is
    NOT itself a matching process. The venv launcher shim, its system-python
    child, and the MCTS workers therefore collapse into ONE client — never
    treat the raw match count as a client count.
    """
    matching: dict[int, Any] = {}
    for proc in processes:
        name = str(proc.get("name") or "").lower()
        if name not in {"python.exe", "pythonw.exe", "python", "pythonw", "python3"}:
            continue
        command = " ".join(str(part) for part in (proc.get("cmdline") or ())).lower()
        if "run.py" not in command or "search_ladder" not in command:
            continue
        try:
            matching[int(proc["pid"])] = proc.get("ppid")
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(pid for pid, ppid in matching.items() if ppid not in matching)


def ladder_client_status() -> dict[str, Any]:
    """Liveness of the top-level run.py search_ladder client (bool only)."""
    status: dict[str, Any] = {
        "clientAlive": False,
        "topLevelPids": [],
        "method": (
            "top-level run.py search_ladder cmdline match per scripts/fouler_keepalive.ps1 "
            "(venv parent + system-python child + MCTS workers = one client)"
        ),
    }
    try:
        import psutil
    except ImportError as exc:
        status["error"] = f"psutil unavailable; cannot verify ladder client liveness: {exc}"
        return status
    snapshot: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            snapshot.append(dict(proc.info))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    top_level = ladder_client_top_level_pids(snapshot)
    status["topLevelPids"] = top_level
    status["clientAlive"] = bool(top_level)
    return status


def signal_freshness_status(*, logs_dir: Path | None = None) -> dict[str, Any]:
    """Age-honest top-level truth fields for every gauge this monitor writes.

    Regime law: an instrument must publish the AGE of its underlying signals
    and go loudly STALE when the thing it measures is dead. signal_state is
    LIVE only while a top-level ladder client is running AND a battle/session
    log was written within the last hour; every other combination means the
    gauges describe a bot that is not playing and must not be trusted.
    """
    newest_log = newest_battle_log(logs_dir)
    client = ladder_client_status()
    battle_log_age_s = newest_log.get("ageSeconds")
    client_alive = bool(client.get("clientAlive"))
    live = (
        client_alive
        and battle_log_age_s is not None
        and battle_log_age_s < LIVE_SIGNAL_MAX_BATTLE_LOG_AGE_SECONDS
    )
    return {
        "battle_log_age_s": battle_log_age_s,
        "client_alive": client_alive,
        "signal_state": SIGNAL_STATE_LIVE if live else SIGNAL_STATE_STALE,
        "signalFreshness": {
            "policy": (
                "LIVE only when client_alive AND battle_log_age_s < "
                f"{LIVE_SIGNAL_MAX_BATTLE_LOG_AGE_SECONDS}s; anything else means the bot is not playing"
            ),
            "newestBattleLog": newest_log,
            "ladderClient": client,
            "maxLiveBattleLogAgeSeconds": LIVE_SIGNAL_MAX_BATTLE_LOG_AGE_SECONDS,
            "measuredAt": iso_now(),
        },
    }


def decision_divergence_status(
    *,
    trace_dir: Path | None = None,
    max_traces: int = DECISION_DIVERGENCE_MAX_TRACES,
) -> dict[str, Any]:
    """Played-vs-policy divergence over the newest decision traces.

    A turn diverges when the move actually played carried less than
    DECISION_DIVERGENCE_PLAYED_FRACTION of the search's best raw policy weight,
    i.e. a post-search layer overrode the search. The 2026-07-04 loss-corpus
    audit traced 94% of such inversions to the decision loop-breaker; with the
    breaker guarded/disabled this rate should sit near zero, and a climb flags
    any future overrider layer. Observability only: no issue classification.
    """
    directory = trace_dir if trace_dir is not None else DECISION_TRACE_DIR
    status: dict[str, Any] = {
        "traceDir": display_path(directory),
        "tracesScanned": 0,
        "turnsScored": 0,
        "divergentTurns": 0,
        "divergenceRate": None,
        "loopBreakOverrideTurns": 0,
        "battlesCovered": 0,
        "playedFractionThreshold": DECISION_DIVERGENCE_PLAYED_FRACTION,
        "maxTraces": max_traces,
        "measuredAt": iso_now(),
    }
    if not directory.is_dir():
        status["error"] = "decision trace directory missing"
        return status
    try:
        files = sorted(
            directory.glob("battle-*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:max_traces]
    except OSError as exc:
        status["error"] = str(exc)
        return status
    battles: set[str] = set()
    for path in files:
        payload = load_json(path, None)
        if not isinstance(payload, dict):
            continue
        status["tracesScanned"] += 1
        policy = payload.get("mcts_policy_raw")
        choice = payload.get("choice")
        if not isinstance(policy, dict) or not policy or not isinstance(choice, str):
            continue
        try:
            weights = {str(move): float(weight) for move, weight in policy.items()}
        except (TypeError, ValueError):
            continue
        best_weight = max(weights.values())
        played_weight = weights.get(choice)
        if best_weight <= 0 or played_weight is None:
            continue
        status["turnsScored"] += 1
        battles.add(str(payload.get("battle_tag") or path.name.split("_turn")[0]))
        if played_weight < DECISION_DIVERGENCE_PLAYED_FRACTION * best_weight:
            status["divergentTurns"] += 1
        events: list[Any] = []
        for block_key in ("mcts_only", "eval"):
            block = payload.get(block_key)
            if isinstance(block, dict) and isinstance(block.get("events"), list):
                events.extend(block["events"])
        if any(
            isinstance(event, dict)
            and event.get("source") == "decision_loop_break"
            and event.get("type") == "override"
            for event in events
        ):
            status["loopBreakOverrideTurns"] += 1
    status["battlesCovered"] = len(battles)
    if status["turnsScored"]:
        status["divergenceRate"] = round(
            status["divergentTurns"] / status["turnsScored"], 4
        )
    return status


def normalize_result(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in WIN_WORDS:
        return "win"
    if text in LOSS_WORDS or any(word in text for word in ("timeout", "timed out", "disconnect", "inactive")):
        return "loss"
    if text in {"tie", "draw"}:
        return "tie"
    return text


def read_battles(path: Path = BATTLE_STATS_FILE) -> list[dict[str, Any]]:
    payload = load_json(path, {})
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("battles") or payload.get("battle_results") or []
    else:
        rows = []
    return [dict(row) for row in rows if isinstance(row, dict)]


def battle_identity_set(battles: list[dict[str, Any]]) -> set[str]:
    identities: set[str] = set()
    for row in battles:
        for key in ("battle_id", "battleId", "replay_id", "battle_tag", "id"):
            value = str(row.get(key) or "").strip()
            if value:
                identities.add(value)
    return identities


def newest_battle_stats_time(battles: list[dict[str, Any]]) -> datetime | None:
    newest: datetime | None = None
    for row in battles:
        parsed = parse_timestamp(row.get("timestamp") or row.get("updated") or row.get("endedAt"))
        if parsed is None:
            continue
        if newest is None or parsed > newest:
            newest = parsed
    return newest


def abandoned_battle_cleanup_status(
    battles: list[dict[str, Any]],
    *,
    backup_dir: Path | None = None,
    max_backups: int = 20,
    season_started_at: datetime | None = None,
    season_account: str | None = None,
) -> dict[str, Any]:
    backup_dir = backup_dir or STALE_ACTIVE_BATTLE_BACKUP_DIR
    status: dict[str, Any] = {
        "policy": "fouler-abandoned-active-battle-cleanup/v2",
        "ready": True,
        "status": "clear",
        "backupDir": display_path(backup_dir),
        "latestBattleStatsAtUtc": None,
        "sourceBackupPath": None,
        "battleIds": [],
        "missingBattleIds": [],
        "checkedBackups": 0,
        "skippedPreSeasonBackups": 0,
        "seasonBoundaryAtUtc": season_started_at.isoformat() if season_started_at else None,
        "seasonAccount": season_account,
        "requiredAction": (
            "root-cause why the ladder runner exited before writing a battle_stats result, "
            "then prove the next bounded battle writes a completed result row before opening a larger proof window"
        ),
    }
    if not backup_dir.is_dir():
        status["status"] = "no-backups"
        return status

    known_battle_ids = battle_identity_set(battles)
    latest_stats_time = newest_battle_stats_time(battles)
    if latest_stats_time is not None:
        status["latestBattleStatsAtUtc"] = latest_stats_time.isoformat()

    try:
        backups = sorted(
            backup_dir.glob("active_battles-*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:max_backups]
    except OSError as exc:
        status["ready"] = False
        status["status"] = "backup-scan-error"
        status["error"] = str(exc)
        return status

    for path in backups:
        status["checkedBackups"] += 1
        try:
            backup_mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if season_started_at is not None and backup_mtime < season_started_at:
            status["skippedPreSeasonBackups"] += 1
            continue
        if latest_stats_time is not None and backup_mtime <= latest_stats_time:
            continue
        payload = load_json(path, {})
        entries = payload.get("battles") if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            continue
        battle_ids = [
            str(item.get("id") or item.get("battle_id") or "").strip()
            for item in entries
            if isinstance(item, dict) and str(item.get("id") or item.get("battle_id") or "").strip()
        ]
        if not battle_ids:
            continue
        missing = [battle_id for battle_id in battle_ids if battle_id not in known_battle_ids]
        if not missing:
            continue
        status.update(
            {
                "ready": False,
                "status": "abandoned-active-battle-without-result",
                "sourceBackupPath": display_path(path),
                "sourceBackupMtimeUtc": backup_mtime.isoformat(),
                "battleIds": battle_ids,
                "missingBattleIds": missing,
                "opponents": [
                    str(item.get("opponent") or "").strip()
                    for item in entries
                    if isinstance(item, dict) and str(item.get("opponent") or "").strip()
                ],
                "sourceUpdated": payload.get("updated") if isinstance(payload, dict) else None,
            }
        )
        break
    return status


def recent_result_summary(battles: list[dict[str, Any]], *, window: int = 20) -> dict[str, Any]:
    recent = battles[-window:]
    wins = sum(1 for row in recent if normalize_result(row.get("result")) == "win")
    losses = sum(1 for row in recent if normalize_result(row.get("result")) == "loss")
    decisive = wins + losses
    streak_kind = ""
    streak = 0
    for row in reversed(recent):
        outcome = normalize_result(row.get("result"))
        if outcome not in {"win", "loss"}:
            break
        if not streak_kind:
            streak_kind = outcome
        if outcome != streak_kind:
            break
        streak += 1
    return {
        "windowSize": len(recent),
        "wins": wins,
        "losses": losses,
        "decisive": decisive,
        "winRate": round(wins / decisive, 4) if decisive else None,
        "record": f"last {len(recent)}: {wins}-{losses}" if recent else "last 0: 0-0",
        "streakKind": streak_kind or None,
        "streak": streak,
    }


def rating_value(row: dict[str, Any]) -> float | None:
    for key in ("rating", "ratingAfter", "rating_after", "elo_after", "eloAfter", "elo"):
        value = row.get(key)
        if value is None:
            continue
        try:
            rating = float(value)
        except (TypeError, ValueError):
            continue
        if rating > 0:
            return rating
    return None


def rating_drawdown_summary(battles: list[dict[str, Any]], *, window: int = 60) -> dict[str, Any]:
    rated: list[dict[str, Any]] = []
    for row in battles:
        rating = rating_value(row)
        if rating is None:
            continue
        rated.append({**row, "rating": rating})
    recent = rated[-window:] if window > 0 else rated
    if not recent:
        return {
            "windowSize": 0,
            "ratedBattles": 0,
            "peakRating": None,
            "troughRating": None,
            "currentRating": None,
            "currentDrawdown": None,
            "maxDrawdown": None,
        }

    peak = recent[0]
    max_drawdown = 0.0
    trough_after_peak = recent[0]
    max_peak = recent[0]
    for row in recent:
        if row["rating"] > peak["rating"]:
            peak = row
        drawdown = peak["rating"] - row["rating"]
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            trough_after_peak = row
            max_peak = peak

    current = recent[-1]
    peak_rating = max(row["rating"] for row in recent)
    current_drawdown = peak_rating - current["rating"]
    return {
        "windowSize": window,
        "ratedBattles": len(recent),
        "peakRating": round(max_peak["rating"], 2),
        "peakBattleId": max_peak.get("battle_id") or max_peak.get("id"),
        "troughRating": round(trough_after_peak["rating"], 2),
        "troughBattleId": trough_after_peak.get("battle_id") or trough_after_peak.get("id"),
        "currentRating": round(current["rating"], 2),
        "currentBattleId": current.get("battle_id") or current.get("id"),
        "currentDrawdown": round(current_drawdown, 2),
        "maxDrawdown": round(max_drawdown, 2),
    }


def proof_drawdown_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rated: list[dict[str, Any]] = []
    for row in rows:
        rating = row.get("rating") if isinstance(row.get("rating"), (int, float)) else rating_value(row)
        if rating is None:
            continue
        rated.append({**row, "rating": float(rating)})

    if not rated:
        return {
            "ratedGames": 0,
            "maxDrawdown": None,
            "peakRating": None,
            "peakBattleId": None,
            "troughRating": None,
            "troughBattleId": None,
        }

    peak = rated[0]
    drawdown_peak = rated[0]
    trough_after_peak = rated[0]
    max_drawdown = 0.0
    for row in rated:
        if row["rating"] > peak["rating"]:
            peak = row
        drawdown = peak["rating"] - row["rating"]
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            drawdown_peak = peak
            trough_after_peak = row

    return {
        "ratedGames": len(rated),
        "maxDrawdown": round(max_drawdown, 2),
        "peakRating": round(drawdown_peak["rating"], 2),
        "peakBattleId": drawdown_peak.get("battleId") or drawdown_peak.get("battle_id"),
        "troughRating": round(trough_after_peak["rating"], 2),
        "troughBattleId": trough_after_peak.get("battleId") or trough_after_peak.get("battle_id"),
    }


def rating_truth_summary(battles: list[dict[str, Any]], *, window: int = 20) -> dict[str, Any]:
    recent = battles[-window:] if window > 0 else battles
    decisive = [
        row for row in recent
        if normalize_result(row.get("result")) in {"win", "loss"}
    ]
    rated = [row for row in decisive if rating_value(row) is not None]
    missing = [row for row in decisive if rating_value(row) is None]
    return {
        "policy": "fouler-rating-truth/v1",
        "windowSize": len(recent),
        "decisiveBattles": len(decisive),
        "ratedDecisiveBattles": len(rated),
        "missingRatingBattles": len(missing),
        "minimumRatedDecisiveBattles": RATING_TRUTH_MIN_RATED_DECISIVE_BATTLES,
        "ratingCoverage": round(len(rated) / len(decisive), 4) if decisive else None,
        "ratingTruthReady": (
            len(rated) >= RATING_TRUTH_MIN_RATED_DECISIVE_BATTLES
            and len(missing) == 0
            and bool(decisive)
        ),
        "missingBattleIds": [
            str(row.get("battle_id") or row.get("battleId") or row.get("id") or "")
            for row in missing[:10]
        ],
    }


def ladder_stage_status(
    battles: list[dict[str, Any]],
    *,
    requested_run_count: int | None = None,
    requested_max_cycles: int | None = None,
) -> dict[str, Any]:
    rated: list[dict[str, Any]] = []
    for row in battles:
        rating = rating_value(row)
        if rating is None:
            continue
        rated.append({**row, "rating": rating})

    current = rated[-1] if rated else None
    current_rating = round(current["rating"], 2) if current else None
    current_battle_id = current.get("battle_id") or current.get("id") if current else None
    peak_rating = round(max(row["rating"] for row in rated), 2) if rated else None
    floor_proofs = {
        floor: ladder_floor_proof_status(rated, floor=floor)
        for floor in (1500, 1600, 1700)
    }
    floor_regression = ladder_floor_regression_status(rated)

    if current_rating is None:
        stage = LADDER_STAGE_POLICY[0]
        stage_reason = "no current rated battle proof"
    elif current_rating >= 1700 and floor_proofs[1600]["ready"]:
        stage = LADDER_STAGE_POLICY[4]
        stage_reason = "current rating is at or above 1700 and the 1600 floor has consecutive proof"
    elif current_rating >= 1600 and floor_proofs[1600]["ready"]:
        stage = LADDER_STAGE_POLICY[3]
        stage_reason = "current rating is at or above 1600 with consecutive 1600-floor proof"
    elif current_rating >= 1500 and floor_proofs[1500]["ready"]:
        stage = LADDER_STAGE_POLICY[2]
        stage_reason = "current rating is at or above 1500 with consecutive floor proof"
    elif current_rating >= 1500:
        stage = LADDER_STAGE_POLICY[1]
        stage_reason = "rating crossed 1500 but lacks consecutive 1500-floor proof"
    else:
        stage = LADDER_STAGE_POLICY[1]
        stage_reason = "current rating remains below the next floor"

    max_batch_games = int(stage["maxBatchGames"])
    requested = int(requested_run_count) if requested_run_count is not None else None
    requested_cycles = int(requested_max_cycles) if requested_max_cycles is not None else None
    proof_window_games = (
        requested * max(1, requested_cycles)
        if requested is not None and requested_cycles is not None
        else requested
    )
    batch_size_ok = proof_window_games is None or proof_window_games <= max_batch_games
    return {
        "policy": "fouler-ladder-stage-gate/v1",
        "stageId": stage["id"],
        "currentRating": current_rating,
        "currentBattleId": current_battle_id,
        "peakRating": peak_rating,
        "stageGateReason": stage_reason,
        "floorProofPolicy": "fouler-ladder-floor-proof/v1",
        "floorProofs": floor_proofs,
        "floorRegression": floor_regression,
        "targetRating": int(stage["targetRating"]),
        "ratingFloor": int(stage["ratingFloor"]),
        "maxBatchGames": max_batch_games,
        "requestedRunCount": requested,
        "requestedMaxCycles": requested_cycles,
        "requestedProofWindowGames": proof_window_games,
        "batchSizeOk": batch_size_ok,
        "requiredProof": stage["requiredProof"],
        "sustainProofTargetGames": SUSTAIN_MINIMUM_GAMES if stage["id"] == "sustain-1700" else None,
        "runtimeChunkedProofRequired": stage["id"] == "sustain-1700",
        "nextMilestone": (
            "maintain 1700 sustain proof"
            if stage["id"] == "sustain-1700"
            else f"reach and hold {int(stage['targetRating'])} before promoting to the next stage"
        ),
    }


def floor_window_record(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(1 for row in rows if normalize_result(row.get("result")) == "win")
    losses = sum(1 for row in rows if normalize_result(row.get("result")) == "loss")
    decisive = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "decisive": decisive,
        "winRate": round(wins / decisive, 4) if decisive else None,
        "recordReady": bool(decisive and wins >= losses),
    }


def ladder_floor_proof_status(
    rated: list[dict[str, Any]],
    *,
    floor: int,
    minimum_consecutive_games: int = LADDER_STAGE_FLOOR_PROOF_MINIMUM_GAMES,
) -> dict[str, Any]:
    consecutive: list[dict[str, Any]] = []
    for row in reversed(rated):
        rating = row.get("rating")
        if not isinstance(rating, (int, float)) or rating < floor:
            break
        consecutive.append(row)
    consecutive.reverse()
    battle_ids = [
        str(row.get("battle_id") or row.get("battleId") or row.get("id") or "")
        for row in consecutive
        if str(row.get("battle_id") or row.get("battleId") or row.get("id") or "")
    ]
    proof_window = consecutive[-minimum_consecutive_games:]
    record = floor_window_record(proof_window)
    return {
        "floor": floor,
        "minimumConsecutiveGames": minimum_consecutive_games,
        "consecutiveGamesAtOrAboveFloor": len(consecutive),
        "floorWindowRecord": record,
        "floorWindowRecordReady": record["recordReady"],
        "ready": len(consecutive) >= minimum_consecutive_games and record["recordReady"],
        "battleIds": battle_ids[-minimum_consecutive_games:],
        "currentRating": round(rated[-1]["rating"], 2) if rated else None,
    }


def historical_ladder_floor_proof_status(
    rated: list[dict[str, Any]],
    *,
    floor: int,
    minimum_consecutive_games: int = LADDER_STAGE_FLOOR_PROOF_MINIMUM_GAMES,
) -> dict[str, Any]:
    current_streak: list[dict[str, Any]] = []
    last_proof_window: list[dict[str, Any]] = []
    for row in rated:
        rating = row.get("rating")
        if isinstance(rating, (int, float)) and rating >= floor:
            current_streak.append(row)
            candidate = current_streak[-minimum_consecutive_games:]
            if len(candidate) >= minimum_consecutive_games and floor_window_record(candidate)["recordReady"]:
                last_proof_window = candidate
        else:
            current_streak = []
    battle_ids = [
        str(row.get("battle_id") or row.get("battleId") or row.get("id") or "")
        for row in last_proof_window
        if str(row.get("battle_id") or row.get("battleId") or row.get("id") or "")
    ]
    record = floor_window_record(last_proof_window)
    return {
        "floor": floor,
        "minimumConsecutiveGames": minimum_consecutive_games,
        "historicallyReady": bool(last_proof_window),
        "floorWindowRecord": record,
        "floorWindowRecordReady": record["recordReady"],
        "lastProofBattleIds": battle_ids,
        "lastProofFinalRating": round(last_proof_window[-1]["rating"], 2) if last_proof_window else None,
        "currentRating": round(rated[-1]["rating"], 2) if rated else None,
    }


def ladder_floor_regression_status(rated: list[dict[str, Any]]) -> dict[str, Any]:
    current_rating = rated[-1]["rating"] if rated else None
    historical_proofs = {
        floor: historical_ladder_floor_proof_status(rated, floor=floor)
        for floor in (1500, 1600, 1700)
    }
    regressed_floors = [
        floor
        for floor, proof in historical_proofs.items()
        if proof["historicallyReady"]
        and isinstance(current_rating, (int, float))
        and current_rating < floor
    ]
    highest_regressed_floor = max(regressed_floors) if regressed_floors else None
    return {
        "policy": "fouler-ladder-floor-regression-stop-loss/v1",
        "regressed": bool(regressed_floors),
        "regressedFloors": regressed_floors,
        "highestRegressedFloor": highest_regressed_floor,
        "currentRating": round(current_rating, 2) if isinstance(current_rating, (int, float)) else None,
        "historicalProofs": historical_proofs,
    }


def expected_account(lease: dict[str, Any]) -> str:
    account = (
        str(lease.get("account") or "")
        or str((lease.get("battleScope") or {}).get("account") or "")
        or os.getenv("FOULER_ACTIVE_ACCOUNT", "")
        or os.getenv("PS_USERNAME", "")
        or DEFAULT_ACCOUNT
    )
    return account.strip()


def normalize_account(value: object) -> str:
    return str(value or "").strip().lower()


def account_season_status(
    season: dict[str, Any],
    *,
    lease: dict[str, Any],
    battles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate the explicit account-season boundary used by fresh ladder runs."""

    blockers: list[str] = []
    warnings: list[str] = []
    schema_version = str(season.get("schemaVersion") or "").strip()
    account = str(season.get("account") or "").strip()
    expected = expected_account(lease)
    created_at = parse_timestamp(season.get("createdAtUtc"))
    if schema_version != "fouler-play-account-season/v1":
        blockers.append("account season schemaVersion must be fouler-play-account-season/v1")
    if not account:
        blockers.append("account season account is missing")
    elif expected and normalize_account(account) != normalize_account(expected):
        blockers.append(f"account season account {account} does not match expected account {expected}")
    if created_at is None:
        blockers.append("account season createdAtUtc is missing or invalid")
    if battles and season.get("firstBattleStarted") is not True:
        warnings.append("battle_stats contains season battles but firstBattleStarted is not yet true")
    return {
        "policy": "fouler-account-season-boundary/v1",
        "active": not blockers,
        "schemaVersion": schema_version or None,
        "seasonId": season.get("seasonId"),
        "account": account or None,
        "expectedAccount": expected or None,
        "createdAtUtc": created_at.isoformat() if created_at else None,
        "baselineRating": season.get("baselineRating"),
        "declaredFirstBattleStarted": season.get("firstBattleStarted") is True,
        "firstBattleStarted": season.get("firstBattleStarted") is True or bool(battles),
        "observedBattleCount": len(battles),
        "runtimeStatus": season.get("runtimeStatus"),
        "blockers": blockers,
        "warnings": warnings,
        "noRuntimeActions": True,
    }


def _mapping_value(data: Mapping[str, Any], path: tuple[str, ...]) -> object:
    current: object = data
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _append_account_claim(
    claims: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    *,
    source: str,
    value: object,
) -> None:
    account = str(value or "").strip()
    if not account:
        return
    key = (source, normalize_account(account))
    if key in seen:
        return
    seen.add(key)
    claims.append({"source": source, "account": account})


def health_account_claims(health: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    authority = health.get("accountAuthority") if isinstance(health.get("accountAuthority"), dict) else {}
    authority_claims = authority.get("claims") if isinstance(authority.get("claims"), list) else []
    for index, item in enumerate(authority_claims):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or f"health.accountAuthority.claims[{index}]").strip()
        _append_account_claim(claims, seen, source=source, value=item.get("account"))

    paths = (
        (("accountAuthority", "runtimeAccount"), "health.accountAuthority.runtimeAccount"),
        (("accountAuthority", "envAccount"), "health.accountAuthority.envAccount"),
        (("accountAuthority", "runtimeLeaseAccount"), "health.accountAuthority.runtimeLeaseAccount"),
        (("accountAuthority", "expectedAccount"), "health.accountAuthority.expectedAccount"),
        (("runtimeOwnership", "account"), "health.runtimeOwnership.account"),
        (("runtimeOwnership", "showdownAccount"), "health.runtimeOwnership.showdownAccount"),
        (("runtimeOwnership", "showdownUserId"), "health.runtimeOwnership.showdownUserId"),
        (("runtimeOwnership", "accountAuthority", "runtimeAccount"), "health.runtimeOwnership.accountAuthority.runtimeAccount"),
        (("runtimeOwnership", "accountAuthority", "envAccount"), "health.runtimeOwnership.accountAuthority.envAccount"),
        (("runtimeOwnership", "accountAuthority", "runtimeLeaseAccount"), "health.runtimeOwnership.accountAuthority.runtimeLeaseAccount"),
        (("readiness", "account"), "health.readiness.account"),
        (("readiness", "showdownAccount"), "health.readiness.showdownAccount"),
        (("readiness", "showdownUserId"), "health.readiness.showdownUserId"),
        (("account",), "health.account"),
        (("showdownAccount",), "health.showdownAccount"),
        (("showdownUserId",), "health.showdownUserId"),
    )
    for path, source in paths:
        _append_account_claim(claims, seen, source=source, value=_mapping_value(health, path))
    return claims


def runtime_account_authority_status(health: dict[str, Any], lease: dict[str, Any]) -> dict[str, Any]:
    expected = expected_account(lease)
    claims = health_account_claims(health)
    blockers: list[str] = []
    mismatches = [
        claim
        for claim in claims
        if normalize_account(claim.get("account")) != normalize_account(expected)
    ]
    distinct_claims: dict[str, str] = {}
    for claim in claims:
        account = str(claim.get("account") or "").strip()
        normalized = normalize_account(account)
        if normalized:
            distinct_claims.setdefault(normalized, account)
    if mismatches:
        blockers.extend(
            f"{claim['source']} account {claim['account']} does not match expected account {expected}"
            for claim in mismatches
        )
    if len(distinct_claims) > 1:
        blockers.append(
            "health account authority contains multiple distinct accounts: "
            + ", ".join(sorted(distinct_claims.values(), key=str.lower))
        )
    return {
        "policy": "fouler-runtime-account-authority/v1",
        "ready": not blockers,
        "expectedAccount": expected,
        "observable": bool(claims),
        "claimCount": len(claims),
        "claims": claims,
        "distinctAccounts": sorted(distinct_claims.values(), key=str.lower),
        "blockers": blockers,
        "warnings": (
            ["health payload does not expose runtime account authority; lease/env checks still run at launch"]
            if not claims
            else []
        ),
    }


def normalize_team(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    leaf = text.rstrip("/").split("/")[-1]
    if "." in leaf:
        leaf = leaf.rsplit(".", 1)[0]
    return leaf.lower()


def replay_proof_present(value: object) -> bool:
    text = str(value or "").strip()
    if not re.fullmatch(r"https?://replay\.pokemonshowdown\.com/[A-Za-z0-9][A-Za-z0-9-]*", text):
        return False
    replay_id = text.rstrip("/").rsplit("/", 1)[-1].lower()
    return replay_id not in {"unknown", "none", "null"}


def normalized_battle_id(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("battle-"):
        text = text[len("battle-"):]
    return text


def replay_matches_battle_id(replay_url: object, battle_id: object) -> bool:
    replay = str(replay_url or "").strip()
    normalized = normalized_battle_id(battle_id)
    if not replay or not normalized:
        return False
    replay_id = replay.rstrip("/").rsplit("/", 1)[-1].lower()
    return replay_id == normalized


def decision_trace_proof_present(row: dict[str, Any]) -> bool:
    return bool(decision_trace_proof_id(row))


def decision_trace_proof_id(row: dict[str, Any]) -> str:
    for key in ("decisionTracePath", "decision_trace_path", "decisionTrace", "decisionTraceUrl", "decision_trace_url"):
        text = str(row.get(key) or "").strip()
        if text and text.lower() not in {"unknown", "none", "null"}:
            return text.replace("\\", "/").rstrip("/").lower()
    return ""


def analysis_evidence_status(proof: dict[str, Any]) -> dict[str, Any]:
    analysis = proof.get("analysis") if isinstance(proof.get("analysis"), dict) else {}
    missing = [
        key for key in ANALYSIS_EVIDENCE_PATH_KEYS
        if not str(analysis.get(key) or "").strip()
    ]
    return {
        "policy": "fouler-elo-proof-analysis-evidence/v1",
        "ready": not missing,
        "missingPathKeys": missing,
        "autoresearchJsonPath": analysis.get("autoresearchJsonPath"),
        "autoresearchReportPath": analysis.get("autoresearchReportPath"),
        "decisionTraceReviewPath": analysis.get("decisionTraceReviewPath"),
        "topIssue": analysis.get("topIssue"),
        "reviewedBattleCount": analysis.get("reviewedBattleCount"),
        "lossesAnalyzed": analysis.get("lossesAnalyzed"),
    }


def proof_timestamp(proof: dict[str, Any]) -> str | None:
    summary = proof.get("summary") if isinstance(proof.get("summary"), dict) else {}
    session = proof.get("session") if isinstance(proof.get("session"), dict) else {}
    candidates = [
        proof.get("checkedAtUtc"),
        proof.get("checkedAt"),
        proof.get("generatedAt"),
        proof.get("generatedAtUtc"),
        summary.get("latestBattleAt"),
        session.get("endedAt"),
    ]
    parsed = [parse_timestamp(value) for value in candidates]
    parsed = [value for value in parsed if value is not None]
    if not parsed:
        return None
    return max(parsed).isoformat()


def proof_analysis_timestamp(proof: dict[str, Any]) -> str | None:
    analysis = proof.get("analysis") if isinstance(proof.get("analysis"), dict) else {}
    candidates = [
        analysis.get("generatedAtUtc"),
        analysis.get("checkedAtUtc"),
    ]
    parsed = [parse_timestamp(value) for value in candidates]
    parsed = [value for value in parsed if value is not None]
    if not parsed:
        return None
    return max(parsed).isoformat()


def completed_battle_rows(games: object) -> list[dict[str, Any]]:
    if not isinstance(games, list):
        return []
    completed: list[dict[str, Any]] = []
    for item in games:
        if not isinstance(item, dict):
            continue
        outcome = normalize_result(item.get("result"))
        if outcome in {"win", "loss", "tie", "draw"}:
            completed.append(item)
    return completed


def proof_game_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "endedAt", "ended_at", "createdAt", "created_at", "time"):
        parsed = parse_timestamp(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _numbers_match(actual: object, expected: int | float | None, *, tolerance: float = 0.01) -> bool:
    if expected is None:
        return actual is None
    if isinstance(actual, bool):
        return False
    try:
        return abs(float(actual) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return False


def elo_sustain_proof_status(
    proof: dict[str, Any],
    *,
    lease: dict[str, Any],
    max_age_seconds: int,
    current_checkout_commit: str | None = None,
    target_rating: int = CANONICAL_TARGET_RATING,
    minimum_games: int = SUSTAIN_MINIMUM_GAMES,
    required_teams: tuple[str, ...] = SUSTAIN_REQUIRED_TEAMS,
    minimum_games_per_team: int = SUSTAIN_MINIMUM_GAMES_PER_TEAM,
    max_drawdown: float = SUSTAIN_MAX_DRAWDOWN,
    minimum_win_rate: float = SUSTAIN_MINIMUM_WIN_RATE,
) -> dict[str, Any]:
    """Classify the machine-readable proof for the actual Fouler mission claim."""

    blockers: list[str] = []
    if not isinstance(proof, dict) or not proof:
        return {
            "policy": "fouler-1700-sustain/v1",
            "ready": False,
            "status": "missing",
            "blockers": [f"missing {display_path(LATEST_ELO_PROOF_FILE)}"],
            "targetRating": target_rating,
            "minimumSustainGames": minimum_games,
            "noRuntimeActions": True,
        }

    target = proof.get("target") if isinstance(proof.get("target"), dict) else {}
    account = proof.get("account") if isinstance(proof.get("account"), dict) else {}
    summary = proof.get("summary") if isinstance(proof.get("summary"), dict) else {}
    live_profile = proof.get("liveProfile") if isinstance(proof.get("liveProfile"), dict) else {}
    source = proof.get("source") if isinstance(proof.get("source"), dict) else {}
    source_commit = str(proof.get("sourceCommit") or source.get("sourceCommit") or "").strip()
    source_commit_matches_current = (
        source_commit == current_checkout_commit
        if source_commit and current_checkout_commit
        else None
    )
    games = completed_battle_rows(proof.get("games"))
    missing_battle_timestamp_games = [
        row for row in games if proof_game_timestamp(row) is None
    ]
    out_of_order_battle_timestamp_games: list[dict[str, Any]] = []
    previous_timestamp: datetime | None = None
    for row in games:
        parsed_timestamp = proof_game_timestamp(row)
        if parsed_timestamp is None:
            continue
        if previous_timestamp is not None and parsed_timestamp < previous_timestamp:
            out_of_order_battle_timestamp_games.append(row)
        previous_timestamp = parsed_timestamp
    chronological_battle_order_complete = (
        not missing_battle_timestamp_games
        and not out_of_order_battle_timestamp_games
    )
    analysis = analysis_evidence_status(proof)
    if not analysis["ready"]:
        blockers.append(
            "ELO proof is missing post-window analysis artifact path(s): "
            + ", ".join(analysis["missingPathKeys"])
        )
    if len(source_commit) < 7:
        blockers.append("ELO proof sourceCommit is missing or too short")
    if source_commit and current_checkout_commit and source_commit != current_checkout_commit:
        blockers.append("ELO proof sourceCommit does not match current checkout")
    expected = expected_account(lease)
    proof_account = str(account.get("showdownUserId") or "").strip()
    account_matches = bool(proof_account) and normalize_account(proof_account) == normalize_account(expected)
    if not account_matches:
        blockers.append(f"ELO proof account {proof_account or '<missing>'} does not match expected account {expected}")

    try:
        proof_target = int(target.get("ratingFloor"))
    except (TypeError, ValueError):
        proof_target = None
    if proof_target is None or proof_target < target_rating:
        blockers.append(f"ELO proof target ratingFloor must be at least {target_rating}")

    target_contract_ready = True

    def add_target_blocker(reason: str) -> None:
        nonlocal target_contract_ready
        target_contract_ready = False
        blockers.append(reason)

    target_minimum_completed_games = _as_int(target.get("minimumCompletedGames"))
    if target_minimum_completed_games is None or target_minimum_completed_games < minimum_games:
        add_target_blocker(
            f"ELO proof target.minimumCompletedGames must be at least {minimum_games}"
        )
    target_sustain_minimum_games = _as_int(target.get("sustainMinimumGames"))
    if target_sustain_minimum_games is None or target_sustain_minimum_games < minimum_games:
        add_target_blocker(
            f"ELO proof target.sustainMinimumGames must be at least {minimum_games}"
        )
    target_sustain_minimum_games_per_team = _as_int(target.get("sustainMinimumGamesPerTeam"))
    if (
        target_sustain_minimum_games_per_team is None
        or target_sustain_minimum_games_per_team < minimum_games_per_team
    ):
        add_target_blocker(
            f"ELO proof target.sustainMinimumGamesPerTeam must be at least {minimum_games_per_team}"
        )
    target_maximum_sustain_drawdown = _as_float(target.get("maximumSustainDrawdown"))
    if target_maximum_sustain_drawdown is None or target_maximum_sustain_drawdown > max_drawdown:
        add_target_blocker(
            f"ELO proof target.maximumSustainDrawdown must be no more than {max_drawdown}"
        )
    target_maximum_pre_target_drawdown = _as_float(target.get("maximumPreTargetDrawdown"))
    if target_maximum_pre_target_drawdown is None or target_maximum_pre_target_drawdown > max_drawdown:
        add_target_blocker(
            f"ELO proof target.maximumPreTargetDrawdown must be no more than {max_drawdown}"
        )
    target_minimum_win_rate = _as_float(target.get("minimumSustainWinRate"))
    if target_minimum_win_rate is None or target_minimum_win_rate < minimum_win_rate:
        add_target_blocker(
            f"ELO proof target.minimumSustainWinRate must be at least {minimum_win_rate}"
        )
    declared_required_teams = target.get("requiredTeams")
    declared_required_team_set = {
        normalize_team(item)
        for item in declared_required_teams
        if isinstance(item, str) and normalize_team(item)
    } if isinstance(declared_required_teams, list) else set()
    missing_declared_teams = [
        team for team in required_teams if team not in declared_required_team_set
    ]
    if missing_declared_teams:
        add_target_blocker(
            "ELO proof target.requiredTeams must include fixed team(s): "
            + ", ".join(missing_declared_teams)
        )

    if target.get("noCherryPicking") is not True:
        blockers.append("ELO proof target.noCherryPicking must be true")
    if target.get("uninterruptedPostTargetFloorRequired") is not True:
        add_target_blocker("ELO proof target.uninterruptedPostTargetFloorRequired must be true")

    checked_at = proof_timestamp(proof)
    analysis_checked_at = proof_analysis_timestamp(proof)
    proof_age = age_seconds(checked_at)
    if checked_at is None:
        blockers.append("ELO proof has no checked/generated/session timestamp")
    elif proof_age is None or proof_age > max_age_seconds:
        blockers.append(f"ELO proof is stale: ageSeconds={proof_age}, maxAgeSeconds={max_age_seconds}")

    rated_games: list[dict[str, Any]] = []
    for row in games:
        rating = rating_value(row)
        if rating is None:
            continue
        rated_games.append({**row, "rating": rating})

    if len(games) < minimum_games:
        blockers.append(f"ELO proof completedGames {len(games)} is below required {minimum_games}")
    if len(rated_games) < minimum_games:
        blockers.append(f"ELO proof rated completed games {len(rated_games)} is below required {minimum_games}")

    first_target_index = next(
        (index for index, row in enumerate(rated_games) if row["rating"] >= target_rating),
        None,
    )
    sustain_games: list[dict[str, Any]] = []
    if first_target_index is None:
        blockers.append(f"ELO proof never reaches {target_rating}")
    else:
        sustain_games = rated_games[first_target_index:]
    pre_target_games = rated_games[:first_target_index] if first_target_index is not None else rated_games
    pre_target_drawdown = proof_drawdown_summary(pre_target_games)
    max_pre_target_drawdown = pre_target_drawdown.get("maxDrawdown")
    if isinstance(max_pre_target_drawdown, (int, float)) and max_pre_target_drawdown > max_drawdown:
        blockers.append(
            f"ELO proof pre-target drawdown {max_pre_target_drawdown} exceeds max {max_drawdown} "
            f"before first {target_rating} hit"
        )

    games_at_or_above = [row for row in sustain_games if row["rating"] >= target_rating]
    below_floor = [row for row in sustain_games if row["rating"] < target_rating]
    missing_replay_games = [
        row for row in games_at_or_above if not replay_proof_present(row.get("replayUrl") or row.get("replay"))
    ]
    mismatched_replay_games = [
        row
        for row in games_at_or_above
        if replay_proof_present(row.get("replayUrl") or row.get("replay"))
        and not replay_matches_battle_id(row.get("replayUrl") or row.get("replay"), row.get("battleId") or row.get("battle_id"))
    ]
    unknown_team_games = [
        row
        for row in games_at_or_above
        if normalize_team(row.get("teamFile") or row.get("team") or row.get("teamName")) not in required_teams
    ]
    missing_decision_trace_games = [
        row for row in games_at_or_above if not decision_trace_proof_present(row)
    ]
    sustain_decision_trace_ids = [
        decision_trace_proof_id(row)
        for row in games_at_or_above
        if decision_trace_proof_present(row)
    ]
    duplicate_decision_trace_ids = sorted(
        trace_id
        for trace_id in set(sustain_decision_trace_ids)
        if trace_id and sustain_decision_trace_ids.count(trace_id) > 1
    )
    sustain_battle_ids = [
        normalized_battle_id(row.get("battleId") or row.get("battle_id"))
        for row in games_at_or_above
    ]
    missing_battle_id_games = [
        row
        for row, battle_id in zip(games_at_or_above, sustain_battle_ids)
        if not battle_id or battle_id in {"unknown", "none", "null"}
    ]
    duplicate_battle_ids = sorted(
        battle_id
        for battle_id in set(sustain_battle_ids)
        if battle_id and sustain_battle_ids.count(battle_id) > 1
    )
    sustain_replay_ids = [
        str(row.get("replayUrl") or row.get("replay") or "").strip().rstrip("/").rsplit("/", 1)[-1].lower()
        for row in games_at_or_above
        if replay_proof_present(row.get("replayUrl") or row.get("replay"))
    ]
    duplicate_replay_ids = sorted(
        replay_id
        for replay_id in set(sustain_replay_ids)
        if replay_id and sustain_replay_ids.count(replay_id) > 1
    )
    if len(games_at_or_above) < minimum_games:
        blockers.append(
            f"ELO proof has {len(games_at_or_above)} post-target games at or above {target_rating}; "
            f"requires {minimum_games}"
        )
    if below_floor:
        blockers.append(f"ELO proof dips below {target_rating} after first target hit")
    if missing_replay_games:
        blockers.append(f"ELO proof has {len(missing_replay_games)} sustain-window game(s) without Pokemon Showdown replay proof")
    if mismatched_replay_games:
        blockers.append(f"ELO proof has {len(mismatched_replay_games)} sustain-window replay URL(s) that do not match their battle id")
    if missing_battle_id_games:
        blockers.append(f"ELO proof has {len(missing_battle_id_games)} sustain-window game(s) without a concrete battle id")
    if duplicate_battle_ids:
        blockers.append(f"ELO proof has duplicate sustain-window battle id(s): {', '.join(duplicate_battle_ids[:5])}")
    if duplicate_replay_ids:
        blockers.append(f"ELO proof has duplicate sustain-window replay id(s): {', '.join(duplicate_replay_ids[:5])}")
    if unknown_team_games:
        blockers.append(f"ELO proof has {len(unknown_team_games)} sustain-window game(s) without fixed-team attribution")
    if missing_decision_trace_games:
        blockers.append(
            f"ELO proof has {len(missing_decision_trace_games)} sustain-window game(s) without decision trace proof"
        )
    if duplicate_decision_trace_ids:
        blockers.append(
            "ELO proof has duplicate sustain-window decision trace proof(s): "
            + ", ".join(duplicate_decision_trace_ids[:5])
        )
    if missing_battle_timestamp_games:
        blockers.append(
            f"ELO proof has {len(missing_battle_timestamp_games)} completed game(s) without parseable battle timestamp proof"
        )
    if out_of_order_battle_timestamp_games:
        blockers.append(
            f"ELO proof has {len(out_of_order_battle_timestamp_games)} completed game timestamp(s) out of chronological order"
        )

    team_counts: dict[str, int] = {team: 0 for team in required_teams}
    for row in games_at_or_above:
        team = normalize_team(row.get("teamFile") or row.get("team") or row.get("teamName"))
        if team in team_counts:
            team_counts[team] += 1
    missing_team_minimums = [
        {"team": team, "games": count, "required": minimum_games_per_team}
        for team, count in team_counts.items()
        if count < minimum_games_per_team
    ]
    if missing_team_minimums:
        missing = ", ".join(f"{item['team']}={item['games']}/{item['required']}" for item in missing_team_minimums)
        blockers.append(f"ELO proof does not sustain all three fixed teams: {missing}")

    wins = sum(1 for row in games_at_or_above if normalize_result(row.get("result")) == "win")
    losses = sum(1 for row in games_at_or_above if normalize_result(row.get("result")) == "loss")
    decisive = wins + losses
    win_rate_denominator = len(games_at_or_above)
    win_rate = round(wins / win_rate_denominator, 4) if win_rate_denominator else None
    if win_rate is None or win_rate < minimum_win_rate:
        blockers.append(f"ELO proof sustain-window win rate {win_rate} is below required {minimum_win_rate}")

    max_sustain_drawdown = None
    peak_rating = None
    min_sustain_rating = None
    final_rating = None
    first_target_battle = None
    if sustain_games:
        peak = sustain_games[0]["rating"]
        drawdown = 0.0
        for row in sustain_games:
            rating = row["rating"]
            peak = max(peak, rating)
            drawdown = max(drawdown, peak - rating)
        ratings = [row["rating"] for row in sustain_games]
        peak_rating = round(max(ratings), 2)
        min_sustain_rating = round(min(ratings), 2)
        final_rating = round(sustain_games[-1]["rating"], 2)
        max_sustain_drawdown = round(drawdown, 2)
        first_target_battle = sustain_games[0].get("battleId") or sustain_games[0].get("battle_id")
        if drawdown > max_drawdown:
            blockers.append(f"ELO proof sustain-window drawdown {round(drawdown, 2)} exceeds max {max_drawdown}")
        if final_rating < target_rating:
            blockers.append(f"ELO proof final rating {final_rating} is below {target_rating}")

    completed_wins = sum(1 for row in games if normalize_result(row.get("result")) == "win")
    completed_losses = sum(1 for row in games if normalize_result(row.get("result")) == "loss")
    overall_peak_rating = round(max(row["rating"] for row in rated_games), 2) if rated_games else None
    overall_final_rating = round(rated_games[-1]["rating"], 2) if rated_games else None
    live_profile_rating = _as_float(live_profile.get("rating"))
    summary_current_rating = _as_float(summary.get("currentRating"))
    summary_live_profile_rating = _as_float(summary.get("liveProfileRating"))
    sustain_evidence_shape_complete = bool(
        len(games_at_or_above) >= minimum_games
        and not below_floor
        and not missing_replay_games
        and not mismatched_replay_games
        and not missing_battle_id_games
        and not duplicate_battle_ids
        and not duplicate_replay_ids
        and not unknown_team_games
        and not missing_decision_trace_games
        and not duplicate_decision_trace_ids
        and not missing_team_minimums
        and chronological_battle_order_complete
    )
    sustained_target = bool(
        first_target_index is not None
        and len(games_at_or_above) >= minimum_games
        and not below_floor
        and final_rating is not None
        and final_rating >= target_rating
        and max_sustain_drawdown is not None
        and max_sustain_drawdown <= max_drawdown
        and win_rate is not None
        and win_rate >= minimum_win_rate
    )
    sustain_proof_complete = bool(
        target_contract_ready
        and len(source_commit) >= 7
        and account_matches
        and first_target_index is not None
        and sustained_target
        and sustain_evidence_shape_complete
        and analysis["ready"]
    )
    summary_mismatches: list[str] = []

    def require_summary_number(name: str, expected: int | float | None) -> None:
        if not _numbers_match(summary.get(name), expected):
            summary_mismatches.append(
                f"ELO proof summary.{name}={summary.get(name)!r} does not match derived value {expected!r}"
            )

    def require_summary_bool(name: str, expected: bool) -> None:
        if summary.get(name) is not expected:
            summary_mismatches.append(
                f"ELO proof summary.{name}={summary.get(name)!r} does not match derived value {expected!r}"
            )

    require_summary_number("completedGames", len(games))
    require_summary_number("wins", completed_wins)
    require_summary_number("losses", completed_losses)
    require_summary_number("peakRating", overall_peak_rating)
    require_summary_number("finalRating", overall_final_rating)
    require_summary_bool("passesTarget", first_target_index is not None)
    require_summary_bool("sustainedTarget", sustained_target)
    require_summary_number("sustainWindowGames", len(sustain_games))
    require_summary_number("gamesAtOrAboveFloor", len(games_at_or_above))
    require_summary_number("belowFloorAfterFirstTarget", len(below_floor))
    require_summary_number("maxSustainDrawdown", max_sustain_drawdown)
    require_summary_number("preTargetRatedGames", pre_target_drawdown["ratedGames"])
    require_summary_number("maxPreTargetDrawdown", max_pre_target_drawdown)
    require_summary_number("sustainReplayProofCount", len(games_at_or_above) - len(missing_replay_games))
    require_summary_number("missingSustainReplayCount", len(missing_replay_games))
    require_summary_number("mismatchedSustainReplayCount", len(mismatched_replay_games))
    require_summary_number("missingSustainBattleIdCount", len(missing_battle_id_games))
    require_summary_number("duplicateSustainBattleIdCount", len(duplicate_battle_ids))
    require_summary_number("duplicateSustainReplayIdCount", len(duplicate_replay_ids))
    require_summary_number("unknownSustainTeamCount", len(unknown_team_games))
    require_summary_number("decisionTraceProofCount", len(games_at_or_above) - len(missing_decision_trace_games))
    require_summary_number("missingDecisionTraceCount", len(missing_decision_trace_games))
    require_summary_number("duplicateDecisionTraceProofCount", len(duplicate_decision_trace_ids))
    require_summary_number("missingBattleTimestampCount", len(missing_battle_timestamp_games))
    require_summary_number("outOfOrderBattleTimestampCount", len(out_of_order_battle_timestamp_games))
    require_summary_bool("chronologicalBattleOrderComplete", chronological_battle_order_complete)
    require_summary_bool("analysisEvidenceComplete", bool(analysis["ready"]))
    require_summary_bool("sustainEvidenceShapeComplete", sustain_evidence_shape_complete)
    require_summary_bool("sustainProofComplete", sustain_proof_complete)
    if live_profile_rating is not None:
        if not _numbers_match(summary.get("liveProfileRating"), live_profile_rating):
            summary_mismatches.append(
                "ELO proof summary.liveProfileRating does not match liveProfile.rating"
            )
        if not _numbers_match(summary.get("currentRating"), live_profile_rating):
            summary_mismatches.append(
                "ELO proof summary.currentRating must reflect liveProfile.rating when live profile proof is present"
            )
    summary_team_coverage = summary.get("teamCoverage")
    if not isinstance(summary_team_coverage, dict):
        summary_mismatches.append("ELO proof summary.teamCoverage must be an object")
    else:
        for team, count in team_counts.items():
            if _as_int(summary_team_coverage.get(team)) != count:
                summary_mismatches.append(
                    f"ELO proof summary.teamCoverage.{team}={summary_team_coverage.get(team)!r} "
                    f"does not match derived value {count}"
                )
    blockers.extend(summary_mismatches)

    return {
        "policy": "fouler-1700-sustain/v1",
        "ready": not blockers,
        "status": "passed" if not blockers else "blocked",
        "blockers": blockers,
        "path": display_path(LATEST_ELO_PROOF_FILE),
        "account": {
            "expected": expected,
            "proof": proof_account or None,
            "matched": account_matches,
            "ratingSource": account.get("ratingSource"),
        },
        "target": {
            "canonicalRatingFloor": target_rating,
            "proofRatingFloor": proof_target,
            "minimumSustainGames": minimum_games,
            "minimumGamesPerTeam": minimum_games_per_team,
            "requiredTeams": list(required_teams),
            "maximumSustainDrawdown": max_drawdown,
            "maximumPreTargetDrawdown": max_drawdown,
            "minimumSustainWinRate": minimum_win_rate,
            "noCherryPicking": target.get("noCherryPicking"),
            "uninterruptedPostTargetFloorRequired": target.get("uninterruptedPostTargetFloorRequired"),
        },
        "targetContract": {
            "ready": target_contract_ready,
            "declaredMinimumCompletedGames": target_minimum_completed_games,
            "declaredSustainMinimumGames": target_sustain_minimum_games,
            "declaredSustainMinimumGamesPerTeam": target_sustain_minimum_games_per_team,
            "declaredMaximumSustainDrawdown": target_maximum_sustain_drawdown,
            "declaredMaximumPreTargetDrawdown": target_maximum_pre_target_drawdown,
            "declaredMinimumSustainWinRate": target_minimum_win_rate,
            "declaredRequiredTeams": sorted(declared_required_team_set),
            "missingDeclaredTeams": missing_declared_teams,
            "declaredUninterruptedPostTargetFloorRequired": (
                target.get("uninterruptedPostTargetFloorRequired")
            ),
        },
        "freshness": {
            "checkedAt": checked_at,
            "analysisCheckedAt": analysis_checked_at,
            "ageSeconds": proof_age,
            "maxAgeSeconds": max_age_seconds,
            "freshnessAnchorPolicy": "proof/session/latest-battle timestamps only; replay-analysis timestamps are diagnostic",
        },
        "source": {
            "sourceCommit": source_commit or None,
            "currentCheckoutCommit": current_checkout_commit,
            "sourceCommitMatchesCurrent": source_commit_matches_current,
            "generatedBy": source.get("generatedBy"),
        },
        "counts": {
            "completedGames": len(games),
            "ratedCompletedGames": len(rated_games),
            "preTargetRatedGames": pre_target_drawdown["ratedGames"],
            "sustainWindowGames": len(sustain_games),
            "gamesAtOrAboveFloor": len(games_at_or_above),
            "belowFloorAfterFirstTarget": len(below_floor),
            "winsAtOrAboveFloor": wins,
            "lossesAtOrAboveFloor": losses,
            "decisiveAtOrAboveFloor": decisive,
            "winRateDenominatorAtOrAboveFloor": win_rate_denominator,
            "winRateAtOrAboveFloor": win_rate,
            "sustainReplayProofCount": len(games_at_or_above) - len(missing_replay_games),
            "missingSustainReplayCount": len(missing_replay_games),
            "mismatchedSustainReplayCount": len(mismatched_replay_games),
            "missingSustainBattleIdCount": len(missing_battle_id_games),
            "duplicateSustainBattleIdCount": len(duplicate_battle_ids),
            "duplicateSustainReplayIdCount": len(duplicate_replay_ids),
            "unknownSustainTeamCount": len(unknown_team_games),
            "decisionTraceProofCount": len(games_at_or_above) - len(missing_decision_trace_games),
            "missingDecisionTraceCount": len(missing_decision_trace_games),
            "duplicateDecisionTraceProofCount": len(duplicate_decision_trace_ids),
            "missingBattleTimestampCount": len(missing_battle_timestamp_games),
            "outOfOrderBattleTimestampCount": len(out_of_order_battle_timestamp_games),
        },
        "ratings": {
            "firstTargetBattleId": first_target_battle,
            "peakRating": peak_rating,
            "minSustainRating": min_sustain_rating,
            "finalRating": final_rating,
            "currentRating": summary.get("currentRating"),
            "currentRatingSource": summary.get("currentRatingSource"),
            "liveProfileRating": live_profile_rating,
            "liveProfileStatus": live_profile.get("status"),
            "liveProfileCheckedAtUtc": live_profile.get("checkedAtUtc"),
            "summaryCurrentRating": summary_current_rating,
            "summaryLiveProfileRating": summary_live_profile_rating,
            "maxSustainDrawdown": max_sustain_drawdown,
            "maxPreTargetDrawdown": max_pre_target_drawdown,
            "preTargetDrawdownPeakRating": pre_target_drawdown["peakRating"],
            "preTargetDrawdownPeakBattleId": pre_target_drawdown["peakBattleId"],
            "preTargetDrawdownTroughRating": pre_target_drawdown["troughRating"],
            "preTargetDrawdownTroughBattleId": pre_target_drawdown["troughBattleId"],
            "summaryPeakRating": summary.get("peakRating"),
            "summaryFinalRating": summary.get("finalRating"),
        },
        "summaryConsistency": {
            "ready": not summary_mismatches,
            "mismatches": summary_mismatches,
            "derived": {
                "completedGames": len(games),
                "wins": completed_wins,
                "losses": completed_losses,
                "peakRating": overall_peak_rating,
                "finalRating": overall_final_rating,
                "passesTarget": first_target_index is not None,
                "sustainedTarget": sustained_target,
                "sustainEvidenceShapeComplete": sustain_evidence_shape_complete,
                "sustainProofComplete": sustain_proof_complete,
            },
        },
        "teams": {
            "gamesAtOrAboveFloorByTeam": team_counts,
            "missingTeamMinimums": missing_team_minimums,
            "unknownSustainTeamBattleIds": [
                str(row.get("battleId") or row.get("battle_id") or "") for row in unknown_team_games[:10]
            ],
        },
        "decisionTraces": {
            "missingDecisionTraceBattleIds": [
                str(row.get("battleId") or row.get("battle_id") or "") for row in missing_decision_trace_games[:10]
            ],
            "duplicateDecisionTraceProofs": duplicate_decision_trace_ids[:10],
        },
        "battleOrder": {
            "chronological": chronological_battle_order_complete,
            "missingBattleTimestampBattleIds": [
                str(row.get("battleId") or row.get("battle_id") or "") for row in missing_battle_timestamp_games[:10]
            ],
            "outOfOrderBattleTimestampBattleIds": [
                str(row.get("battleId") or row.get("battle_id") or "") for row in out_of_order_battle_timestamp_games[:10]
            ],
        },
        "analysis": analysis,
        "replays": {
            "missingSustainReplayBattleIds": [
                str(row.get("battleId") or row.get("battle_id") or "") for row in missing_replay_games[:10]
            ],
            "mismatchedSustainReplayBattleIds": [
                str(row.get("battleId") or row.get("battle_id") or "") for row in mismatched_replay_games[:10]
            ],
            "duplicateSustainBattleIds": duplicate_battle_ids[:10],
            "duplicateSustainReplayIds": duplicate_replay_ids[:10],
        },
        "noRuntimeActions": True,
    }


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def offline_eval_resume_proof_status(
    *,
    root: Path = ROOT,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read existing offline eval result artifacts before allowing stop-loss recovery."""

    env = os.environ if env is None else env
    try:
        from infrastructure.offline_eval_readiness import offline_eval_result_proof

        proof = offline_eval_result_proof(root=root, env=env)
    except Exception as exc:
        proof = {
            "schemaVersion": OFFLINE_EVAL_RESULT_PROOF_SCHEMA_VERSION,
            "ready": False,
            "accepted": False,
            "status": "unavailable",
            "verdict": "unavailable",
            "reasons": [f"{type(exc).__name__}: {exc}"],
            "noRuntimeActions": True,
        }

    blockers: list[str] = []
    if not isinstance(proof, dict):
        proof = {
            "schemaVersion": OFFLINE_EVAL_RESULT_PROOF_SCHEMA_VERSION,
            "ready": False,
            "accepted": False,
            "status": "malformed",
            "verdict": "malformed",
            "reasons": ["offline eval result proof did not return a JSON object"],
            "noRuntimeActions": True,
        }

    if proof.get("schemaVersion") != OFFLINE_EVAL_RESULT_PROOF_SCHEMA_VERSION:
        blockers.append(
            f"offline eval result proof schemaVersion must be {OFFLINE_EVAL_RESULT_PROOF_SCHEMA_VERSION}"
        )
    if proof.get("ready") is not True:
        blockers.append("offline eval result proof ready must be true")
    if proof.get("accepted") is not True:
        blockers.append("offline eval compare proof must accept the candidate")
    if str(proof.get("status") or "").strip().lower() != "accepted":
        blockers.append("offline eval result proof status must be accepted")

    required_battles = _as_int(proof.get("requiredBattles"))
    candidate_battles = _as_int(proof.get("candidateBattles"))
    compare_candidate_battles = _as_int(proof.get("compareCandidateBattles"))
    if required_battles is None or required_battles <= 0:
        blockers.append("offline eval result proof requiredBattles must be a positive integer")
    if required_battles is not None:
        if candidate_battles is None or candidate_battles < required_battles:
            blockers.append(
                f"offline eval candidateBattles {candidate_battles} is below required {required_battles}"
            )
        if compare_candidate_battles is None or compare_candidate_battles < required_battles:
            blockers.append(
                f"offline eval compareCandidateBattles {compare_candidate_battles} is below required {required_battles}"
            )
    if proof.get("noRuntimeActions") is not True:
        blockers.append("offline eval resume proof must be read-only with noRuntimeActions=true")

    return {
        "policy": OFFLINE_EVAL_RESUME_PROOF_POLICY,
        "ready": not blockers,
        "status": "accepted" if not blockers else "blocked",
        "blockers": blockers,
        "requiredProof": [
            "eval_results/offline/candidate.json exists and has enough candidate battles",
            "eval_results/offline/compare-frozen-vs-candidate.json accepts the candidate",
            "candidate and compare candidate battle counts match the configured offline eval bound",
            "the proof was produced by the read-only offline eval result checker",
        ],
        "resultProof": proof,
        "noRuntimeActions": True,
    }


def active_improvement_proof_status(
    proof: dict[str, Any] | None = None,
    *,
    max_age_seconds: int = ACTIVE_IMPROVEMENT_PROOF_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Classify post-packet proof that a skid repair produced real learning."""

    proof = proof if isinstance(proof, dict) else load_json(ACTIVE_IMPROVEMENT_PROOF_FILE, {})
    if not isinstance(proof, dict) or not proof:
        return {
            "policy": ACTIVE_IMPROVEMENT_PROOF_POLICY,
            "ready": False,
            "status": "missing",
            "blockers": [f"missing {display_path(ACTIVE_IMPROVEMENT_PROOF_FILE)}"],
            "path": display_path(ACTIVE_IMPROVEMENT_PROOF_FILE),
            "noRuntimeActions": True,
        }

    blockers: list[str] = []
    status = str(proof.get("status") or "").strip()
    packet = proof.get("packet") if isinstance(proof.get("packet"), dict) else {}
    latest_battle = proof.get("latestBattle") if isinstance(proof.get("latestBattle"), dict) else {}
    proof_window = proof.get("proofWindow") if isinstance(proof.get("proofWindow"), dict) else {}
    evidence_integrity = (
        proof.get("evidenceIntegrity")
        if isinstance(proof.get("evidenceIntegrity"), dict)
        else {}
    )
    failure_class = proof.get("failureClass") if isinstance(proof.get("failureClass"), dict) else {}

    if proof.get("schemaVersion") != ACTIVE_IMPROVEMENT_PROOF_SCHEMA_VERSION:
        blockers.append(
            f"active improvement proof schemaVersion must be {ACTIVE_IMPROVEMENT_PROOF_SCHEMA_VERSION}"
        )
    if proof.get("actionablePostPacketEval") is not True:
        blockers.append("active improvement proof actionablePostPacketEval must be true")
    if status not in {"post-packet-eval-improving", "post-packet-eval-accepted"}:
        blockers.append("active improvement proof status must be post-packet-eval-improving or post-packet-eval-accepted")
    if packet.get("status") != "implemented":
        blockers.append("active improvement proof packet.status must be implemented")
    if proof_window.get("latestBattleAfterPacket") is not True:
        blockers.append("active improvement proof must include a battle after the packet timestamp")
    if proof_window.get("autoresearchCoversLatestBattle") is not True:
        blockers.append("active improvement proof autoresearch must cover the latest post-packet battle")
    if latest_battle.get("performanceImprovementVerified") is not True:
        blockers.append("active improvement proof must show a positive aggregate performance signal")
    if failure_class.get("status") != "reduced":
        blockers.append("active improvement proof must show the packet failure class is reduced")
    if evidence_integrity.get("ok") is not True:
        blockers.append("active improvement proof evidenceIntegrity.ok must be true")
    if proof.get("runtimeMutationTouched") is not False:
        blockers.append("active improvement proof runtimeMutationTouched must be false")
    if proof.get("networkSendAllowed") is not False:
        blockers.append("active improvement proof networkSendAllowed must be false")

    checked_at = proof.get("checkedAtUtc") or proof.get("checkedAt")
    checked_age = age_seconds(checked_at)
    if checked_age is None:
        blockers.append("active improvement proof checkedAtUtc is missing or invalid")
    elif checked_age > max_age_seconds:
        blockers.append(
            f"active improvement proof is stale: ageSeconds={checked_age}, maxAgeSeconds={max_age_seconds}"
        )

    proof_blockers = [str(item) for item in proof.get("blockers") or []]
    if proof_blockers:
        blockers.append("active improvement proof has blocker(s): " + "; ".join(proof_blockers[:5]))

    return {
        "policy": ACTIVE_IMPROVEMENT_PROOF_POLICY,
        "ready": not blockers,
        "status": "accepted" if not blockers else "blocked",
        "blockers": blockers,
        "path": display_path(ACTIVE_IMPROVEMENT_PROOF_FILE),
        "checkedAtUtc": checked_at,
        "ageSeconds": checked_age,
        "maxAgeSeconds": max_age_seconds,
        "requiredProof": [
            "devstream/truth/post-packet-eval.json uses schemaVersion fouler-play-post-packet-eval/v1",
            "the packet is implemented and evidenceIntegrity.ok is true",
            "a post-packet battle exists and fresh autoresearch consumed it",
            "the packet failure class is reduced and latestBattle.performanceImprovementVerified=true",
            "status is post-packet-eval-improving or post-packet-eval-accepted",
            "runtimeMutationTouched=false and networkSendAllowed=false",
        ],
        "packet": {
            "id": packet.get("id"),
            "status": packet.get("status"),
            "findingKey": packet.get("findingKey"),
            "path": packet.get("path"),
        },
        "latestBattle": latest_battle,
        "proofWindow": proof_window,
        "failureClass": failure_class,
        "evidenceIntegrity": evidence_integrity,
        "noRuntimeActions": True,
    }


def lease_active(lease: dict[str, Any], *, now: datetime | None = None) -> bool:
    status = str(lease.get("status") or "").strip().lower()
    if status and status not in {"active", "approved", "current", "open"}:
        return False
    expires_at = parse_timestamp(lease.get("expiresAt") or (lease.get("proofWindow") or {}).get("expiresAt"))
    if expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return expires_at > now


def issue(
    issue_id: str,
    severity: str,
    summary: str,
    evidence: dict[str, Any],
    next_action: str,
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "severity": severity,
        "summary": summary,
        "evidence": evidence,
        "nextHermesAction": next_action,
    }


def elo_sustain_stop_loss_issues(
    sustain_proof: dict[str, Any],
    *,
    existing_issue_ids: set[str],
) -> list[dict[str, Any]]:
    target = sustain_proof.get("target") if isinstance(sustain_proof.get("target"), dict) else {}
    counts = sustain_proof.get("counts") if isinstance(sustain_proof.get("counts"), dict) else {}
    ratings = sustain_proof.get("ratings") if isinstance(sustain_proof.get("ratings"), dict) else {}
    freshness = sustain_proof.get("freshness") if isinstance(sustain_proof.get("freshness"), dict) else {}
    blockers = [str(item) for item in sustain_proof.get("blockers") or []]
    issues: list[dict[str, Any]] = []

    max_drawdown = ratings.get("maxSustainDrawdown")
    max_pre_target_drawdown = ratings.get("maxPreTargetDrawdown")
    current_rating = next(
        (
            value
            for value in (
                _as_float(ratings.get("liveProfileRating")),
                _as_float(ratings.get("currentRating")),
                _as_float(ratings.get("finalRating")),
                _as_float(ratings.get("summaryFinalRating")),
            )
            if value is not None
        ),
        None,
    )
    peak_rating = _as_float(ratings.get("peakRating"))
    pre_target_peak_rating = _as_float(ratings.get("preTargetDrawdownPeakRating"))
    first_target_battle_id = ratings.get("firstTargetBattleId")
    active_sustain_drawdown = (
        max(0.0, peak_rating - current_rating)
        if first_target_battle_id and peak_rating is not None and current_rating is not None
        else None
    )
    active_pre_target_drawdown = (
        max(0.0, pre_target_peak_rating - current_rating)
        if not first_target_battle_id
        and pre_target_peak_rating is not None
        and current_rating is not None
        else None
    )
    drawdown_threshold = target.get("maximumSustainDrawdown")
    pre_target_drawdown_threshold = target.get("maximumPreTargetDrawdown") or drawdown_threshold
    sustain_breach = (
        isinstance(active_sustain_drawdown, (int, float))
        and isinstance(drawdown_threshold, (int, float))
        and active_sustain_drawdown >= float(drawdown_threshold)
    )
    pre_target_breach = (
        isinstance(active_pre_target_drawdown, (int, float))
        and isinstance(pre_target_drawdown_threshold, (int, float))
        and active_pre_target_drawdown >= float(pre_target_drawdown_threshold)
    )
    if (
        "fouler-rating-drawdown" not in existing_issue_ids
        and (sustain_breach or pre_target_breach)
    ):
        issues.append(
            issue(
                "fouler-rating-drawdown",
                "RELIABILITY_BLOCKER",
                "Fouler latest ELO proof contains an ELO drawdown stop-loss breach.",
                {
                    "policy": "fouler-elo-proof-stop-loss/v1",
                    "source": display_path(LATEST_ELO_PROOF_FILE),
                    "maxSustainDrawdown": max_drawdown,
                    "maxPreTargetDrawdown": max_pre_target_drawdown,
                    "activeSustainDrawdown": active_sustain_drawdown,
                    "activePreTargetDrawdown": active_pre_target_drawdown,
                    "currentRating": current_rating,
                    "threshold": drawdown_threshold,
                    "preTargetThreshold": pre_target_drawdown_threshold,
                    "sustainBreach": sustain_breach,
                    "preTargetBreach": pre_target_breach,
                    "ratings": ratings,
                    "counts": counts,
                    "freshness": freshness,
                    "proofBlockers": blockers,
                },
                "pause the ladder batch, analyze the ELO proof drawdown window, and require a targeted fix plus offline evaluation before continuing",
            )
        )

    below_floor_after_first_target = counts.get("belowFloorAfterFirstTarget")
    if (
        "fouler-elo-target-floor-breach" not in existing_issue_ids
        and isinstance(below_floor_after_first_target, int)
        and below_floor_after_first_target > 0
    ):
        issues.append(
            issue(
                "fouler-elo-target-floor-breach",
                "RELIABILITY_BLOCKER",
                "Fouler latest ELO proof dips below 1700 after first reaching the target.",
                {
                    "policy": "fouler-elo-proof-stop-loss/v1",
                    "source": display_path(LATEST_ELO_PROOF_FILE),
                    "belowFloorAfterFirstTarget": below_floor_after_first_target,
                    "ratings": ratings,
                    "counts": counts,
                    "freshness": freshness,
                    "proofBlockers": blockers,
                },
                "pause the ladder batch, analyze the post-target floor breach, and require a targeted fix plus offline evaluation before continuing",
            )
        )

    win_rate = counts.get("winRateAtOrAboveFloor")
    minimum_win_rate = target.get("minimumSustainWinRate")
    if (
        "fouler-low-recent-win-rate" not in existing_issue_ids
        and isinstance(win_rate, (int, float))
        and isinstance(minimum_win_rate, (int, float))
        and win_rate < float(minimum_win_rate)
    ):
        issues.append(
            issue(
                "fouler-low-recent-win-rate",
                "RELIABILITY_BLOCKER",
                "Fouler latest ELO proof contains a sustain-window win-rate stop-loss breach.",
                {
                    "policy": "fouler-elo-proof-stop-loss/v1",
                    "source": display_path(LATEST_ELO_PROOF_FILE),
                    "winRateAtOrAboveFloor": win_rate,
                    "threshold": minimum_win_rate,
                    "ratings": ratings,
                    "counts": counts,
                    "freshness": freshness,
                    "proofBlockers": blockers,
                },
                "pause the ladder batch, analyze the ELO proof loss window, and require a targeted fix plus offline evaluation before continuing",
            )
        )

    return issues


def session_governance(
    issues: list[dict[str, Any]],
    *,
    trend: dict[str, Any],
    drawdown: dict[str, Any],
    ladder_stage: dict[str, Any],
    offline_eval_resume_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stop_loss_ids = [item["id"] for item in issues if item["id"] in SESSION_STOP_LOSS_ISSUE_IDS]
    allow_laddering = not stop_loss_ids
    return {
        "policy": "fouler-session-stop-loss/v1",
        "allowLaddering": allow_laddering,
        "decision": "allow-laddering" if allow_laddering else "pause-laddering",
        "stopLossBreached": not allow_laddering,
        "blockingIssueIds": stop_loss_ids,
        "requiredAction": (
            "continue bounded monitoring under the active lease"
            if allow_laddering
            else "pause ladder starts, analyze the failing window, land one targeted fix, and pass offline/live evaluation before resuming"
        ),
        "resumeCriteria": [
            "fresh health proof with exactly one ladder runner and no stop file",
            "fresh offline evaluation readiness with candidate and compare proof",
            "fresh active improvement proof from an implemented packet, post-packet battle, and autoresearch review",
            "recent 20 decisive battles at or above the configured win-rate threshold",
            "recent rated-window drawdown below the configured threshold",
            "latest ELO proof passes the 1700 sustain contract before any completion claim",
            "bounded proof batch authorized by a current runtime lease",
        ],
        "recentResults": trend,
        "ratingDrawdown": drawdown,
        "ladderStage": ladder_stage,
        "offlineEvalResumeProof": offline_eval_resume_proof or {
            "policy": OFFLINE_EVAL_RESUME_PROOF_POLICY,
            "ready": False,
            "status": "not-checked",
            "noRuntimeActions": True,
        },
    }


def enforce_stop_loss_tripwire(classification: dict[str, Any], *, write: bool) -> dict[str, Any] | None:
    """Turn a stop-loss classification into runner/supervisor tripwires."""

    governance = classification.get("sessionGovernance") if isinstance(classification.get("sessionGovernance"), dict) else {}
    trigger_ids = [
        str(issue_id)
        for issue_id in governance.get("blockingIssueIds") or []
        if str(issue_id) in SESSION_STOP_LOSS_ISSUE_IDS
    ]
    if not trigger_ids and governance.get("stopLossBreached") is not True:
        return None

    proof_window = recovery_proof_window_status()
    if proof_window.get("active") is True:
        return {
            "action": "stop-loss-tripwire-suppressed-for-recovery-proof-window",
            "schemaVersion": "fouler-play-stop-loss-tripwire/v1",
            "dryRun": not write,
            "written": False,
            "triggerIssueIds": trigger_ids,
            "recoveryProofWindow": proof_window,
            "reason": "stop-loss recovery proof window is active",
            "effect": (
                "leave the finite approved proof window running so post-packet evidence can be collected; "
                "normal drain and supervisor stop remain enforced when the marker is absent, invalid, or expired"
            ),
        }

    reason = "stop-loss breached: " + ", ".join(trigger_ids or ["unknown-stop-loss"])
    payload = {
        "action": "enforce-stop-loss-tripwire",
        "schemaVersion": "fouler-play-stop-loss-tripwire/v1",
        "dryRun": not write,
        "written": False,
        "triggerIssueIds": trigger_ids,
        "drainFile": display_path(DRAIN_FILE),
        "supervisorStopFile": display_path(SUPERVISOR_STOP_FILE),
        "reason": reason,
        "effect": (
            "request the live runner to finish active battles without queueing new ones, "
            "and block supervisor restarts until a fresh start gate clears"
        ),
    }
    if write:
        DRAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        line = f"{iso_now()} {reason}\n"
        DRAIN_FILE.write_text(line, encoding="utf-8")
        SUPERVISOR_STOP_FILE.write_text(line, encoding="utf-8")
        payload["written"] = True
    return payload


def reconcile_recovered_stop_loss_tripwire(
    classification: dict[str, Any], *, write: bool
) -> dict[str, Any] | None:
    """Clear only monitor-owned stop markers after active risk has recovered."""

    governance = (
        classification.get("sessionGovernance")
        if isinstance(classification.get("sessionGovernance"), dict)
        else {}
    )
    if governance.get("stopLossBreached") is True or not SUPERVISOR_STOP_FILE.exists():
        return None

    try:
        stop_text = SUPERVISOR_STOP_FILE.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return {
            "action": "stop-loss-tripwire-reconcile-failed",
            "written": False,
            "reason": f"could not read supervisor stop marker: {exc}",
        }
    if "stop-loss breached:" not in stop_text.lower():
        return {
            "action": "retain-non-stop-loss-supervisor-stop",
            "written": False,
            "reason": "supervisor.stop is not owned by the mission stop-loss tripwire",
        }

    payload = {
        "action": "clear-recovered-stop-loss-tripwire",
        "schemaVersion": "fouler-play-stop-loss-tripwire/v1",
        "dryRun": not write,
        "written": False,
        "supervisorStopFile": display_path(SUPERVISOR_STOP_FILE),
        "drainFile": display_path(DRAIN_FILE),
        "reason": "active drawdown and other session stop-loss signals are below threshold",
    }
    if not write:
        return payload

    SUPERVISOR_STOP_FILE.unlink(missing_ok=True)
    drain_cleared = False
    if DRAIN_FILE.exists():
        try:
            drain_text = DRAIN_FILE.read_text(encoding="utf-8-sig")
        except OSError:
            drain_text = ""
        if "stop-loss breached:" in drain_text.lower():
            DRAIN_FILE.unlink(missing_ok=True)
            drain_cleared = True
    payload["written"] = True
    payload["drainCleared"] = drain_cleared
    return payload


def start_gate_status(
    issues: list[dict[str, Any]],
    *,
    governance: dict[str, Any],
    duplicate_runners: bool,
    stop_file_present: bool,
    recovery_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue_ids = [str(item.get("id") or "") for item in issues]
    blocking_ids = [issue_id for issue_id in issue_ids if issue_id in START_GATE_BLOCKING_ISSUE_IDS]
    if duplicate_runners and "fouler-duplicate-ladder-runners" not in blocking_ids:
        blocking_ids.append("fouler-duplicate-ladder-runners")
    if stop_file_present:
        blocking_ids.append(SUPERVISOR_STOP_FILE_ISSUE_ID)
    governance_blockers = [str(item) for item in governance.get("blockingIssueIds") or []]
    for issue_id in governance_blockers:
        if issue_id not in blocking_ids:
            blocking_ids.append(issue_id)
    blocking_ids = list(dict.fromkeys(blocking_ids))
    recovery_validation = recovery_validation if isinstance(recovery_validation, dict) else {}
    recovery_allowed_ids = set()
    if recovery_validation.get("ready") is True:
        recovery_allowed_ids = {
            str(item)
            for item in recovery_validation.get("allowedBlockingIssueIds") or []
        }
    recovery_suppressed_ids = [
        issue_id for issue_id in blocking_ids if issue_id in recovery_allowed_ids
    ]
    blocking_ids = [
        issue_id for issue_id in blocking_ids if issue_id not in recovery_allowed_ids
    ]
    decision = "block-ladder-start"
    if not blocking_ids and recovery_suppressed_ids:
        decision = "allow-stop-loss-recovery-proof-window"
    elif not blocking_ids:
        decision = "allow-next-proof-window"
    return {
        "policy": "fouler-runtime-start-gate/v1",
        "ready": not blocking_ids,
        "decision": decision,
        "blockingIssueIds": blocking_ids,
        "recoveryValidationSuppressedIssueIds": recovery_suppressed_ids,
        "recoveryValidation": recovery_validation,
        "allowedOpenIssueIds": [
            issue_id
            for issue_id in issue_ids
            if issue_id and issue_id not in blocking_ids
        ],
        "requiredAction": (
            "open only the requested finite proof window and re-run this gate before any next batch"
            if not blocking_ids
            else "repair the blocking issue ids before opening another ladder proof window"
        ),
    }


def stop_loss_recovery_validation_window(
    *,
    governance: dict[str, Any],
    offline_eval_resume_proof: dict[str, Any],
    active_improvement_proof: dict[str, Any] | None = None,
    requested_run_count: int | None,
    requested_max_cycles: int | None,
) -> dict[str, Any]:
    """Classify the narrow proof window that breaks stop-loss recovery deadlock."""

    run_count = requested_run_count or DEFAULT_MONITOR_RUN_COUNT
    max_cycles = requested_max_cycles or DEFAULT_MONITOR_MAX_CYCLES
    blockers: list[str] = []
    if governance.get("stopLossBreached") is not True:
        blockers.append("session stop-loss has not been breached; use the normal start gate")
    if offline_eval_resume_proof.get("ready") is not True:
        blockers.append("offline eval resume proof must be accepted before recovery validation")
    if active_improvement_recovery_window_failed(active_improvement_proof):
        blockers.append("completed recovery proof window did not produce an accepted active improvement proof")
    if run_count > STOP_LOSS_RECOVERY_VALIDATION_MAX_RUN_COUNT:
        blockers.append(
            f"requested run count {run_count} exceeds recovery max {STOP_LOSS_RECOVERY_VALIDATION_MAX_RUN_COUNT}"
        )
    if max_cycles > STOP_LOSS_RECOVERY_VALIDATION_MAX_CYCLES:
        blockers.append(
            f"requested max cycles {max_cycles} exceeds recovery max {STOP_LOSS_RECOVERY_VALIDATION_MAX_CYCLES}"
        )
    return {
        "policy": "fouler-stop-loss-recovery-validation-window/v1",
        "ready": not blockers,
        "status": "allowed" if not blockers else "blocked",
        "requestedRunCount": run_count,
        "requestedMaxCycles": max_cycles,
        "maxRunCount": STOP_LOSS_RECOVERY_VALIDATION_MAX_RUN_COUNT,
        "maxCycles": STOP_LOSS_RECOVERY_VALIDATION_MAX_CYCLES,
        "allowedBlockingIssueIds": sorted(STOP_LOSS_RECOVERY_VALIDATION_ALLOWED_ISSUE_IDS),
        "blockers": blockers,
        "requiredProofAfterWindow": [
            "refresh devstream/truth/latest-elo-proof.json",
            "refresh replay_analysis/autoresearch_latest.json",
            "refresh devstream/truth/post-packet-eval.json",
            "re-run fouler_mission_monitor.py before any next proof window",
        ],
        "noRuntimeActions": True,
    }


def active_improvement_recovery_window_failed(proof: dict[str, Any] | None) -> bool:
    if not isinstance(proof, dict) or proof.get("ready") is True:
        return False
    proof_window = proof.get("proofWindow") if isinstance(proof.get("proofWindow"), dict) else {}
    if proof_window.get("latestBattleAfterPacket") is not True:
        return False
    if proof_window.get("autoresearchCoversLatestBattle") is not True:
        return False
    blockers = [str(item) for item in proof.get("blockers") or []]
    return any(
        "post-packet-eval-improving" in blocker
        or "positive performance" in blocker
        or "reduced failure class" in blocker
        for blocker in blockers
    )


def build_repair_queue(
    issues: list[dict[str, Any]],
    *,
    governance: dict[str, Any],
    start_gate: dict[str, Any],
    trend: dict[str, Any],
    rating_truth: dict[str, Any],
    drawdown: dict[str, Any],
    ladder_stage: dict[str, Any],
    sustain_proof: dict[str, Any],
    offline_eval_resume_proof: dict[str, Any],
    active_improvement_proof: dict[str, Any],
) -> dict[str, Any]:
    """Build read-only DEKU packets for stop-loss and 1700 sustain gaps."""

    issue_ids = [str(item.get("id") or "") for item in issues if item.get("id")]
    trigger_issue_ids = [
        issue_id
        for issue_id in issue_ids
        if issue_id in REPAIR_QUEUE_TRIGGER_ISSUE_IDS
    ]
    stop_loss_issue_ids = [
        issue_id
        for issue_id in governance.get("blockingIssueIds") or []
        if str(issue_id) in SESSION_STOP_LOSS_ISSUE_IDS
    ]
    blocking_issue_ids = [str(item) for item in start_gate.get("blockingIssueIds") or []]
    packets: list[dict[str, Any]] = []

    base_authority = {
        "sourceOfTruth": "HERMES",
        "runtimeMutationAllowed": False,
        "networkSendAllowed": False,
        "discordPostAllowed": False,
        "streamKeyRequired": False,
        "teamEditsAllowed": False,
        "protectedFiles": ["run.py", "config.py", ".env", "teams/**"],
        "noRuntimeActions": True,
    }

    if governance.get("stopLossBreached") is True:
        packets.append(
            {
                "schemaVersion": REPAIR_PACKET_SCHEMA_VERSION,
                "id": "fouler-stop-loss-recovery",
                "title": "Recover Fouler from ladder skid before the next proof window",
                "status": "blocked",
                "priority": "P0",
                "issueIds": list(dict.fromkeys(stop_loss_issue_ids + trigger_issue_ids)),
                "blockedBy": blocking_issue_ids,
                "objective": (
                    "Convert the failing ladder window into one constrained code-eval packet, "
                    "prove it offline, then prove a fresh post-packet battle improved before "
                    "any further ladder start."
                ),
                "evidence": {
                    "recentResults": trend,
                    "ratingTruth": rating_truth,
                    "ratingDrawdown": drawdown,
                    "ladderStage": ladder_stage,
                    "eloSustainProof": {
                        "status": sustain_proof.get("status"),
                        "ready": sustain_proof.get("ready"),
                        "blockers": [str(item) for item in sustain_proof.get("blockers") or []],
                        "ratings": sustain_proof.get("ratings"),
                        "counts": sustain_proof.get("counts"),
                    },
                    "offlineEvalResumeProof": offline_eval_resume_proof,
                    "activeImprovementProof": active_improvement_proof,
                    "startGate": start_gate,
                },
                "nextActions": [
                    "freeze-ladder-starts-through-mission-start-gate",
                    "run-autoresearch-on-the-failing-rated-window",
                    "generate-or-select-one-constrained-devstream-work-packet",
                    "implement-one-allowed-code-fix-with-tests",
                    "produce-accepted-offline-eval-resume-proof",
                    "produce-fresh-post-packet-active-improvement-proof",
                    "rerun-start-gate-for-one-bounded-proof-window",
                    "produce-fresh-latest-elo-proof-after-the-bounded-window",
                ],
                "acceptance": {
                    "offlineEvalResumeProofReady": True,
                    "activeImprovementProofReady": True,
                    "recentRatingTruthComplete": True,
                    "ratingDrawdownBelowThreshold": True,
                    "startGateDecision": "allow-next-proof-window",
                    "nextProofWindowMustBeBounded": True,
                },
                "authority": base_authority,
                "noRuntimeActions": True,
            }
        )
    elif sustain_proof.get("ready") is not True and "fouler-elo-sustain-proof-missing-or-failing" in issue_ids:
        packets.append(
            {
                "schemaVersion": REPAIR_PACKET_SCHEMA_VERSION,
                "id": "fouler-1700-sustain-proof",
                "title": "Produce the 1700 sustain proof through bounded proof windows",
                "status": "ready-for-bounded-proof-window" if start_gate.get("ready") else "blocked",
                "priority": "P1",
                "issueIds": ["fouler-elo-sustain-proof-missing-or-failing"],
                "blockedBy": blocking_issue_ids,
                "objective": (
                    "Accumulate the canonical 30-game post-1700 sustain proof without "
                    "authorizing an unattended long grind or treating a rating spike as done."
                ),
                "evidence": {
                    "ladderStage": ladder_stage,
                    "eloSustainProof": sustain_proof,
                    "startGate": start_gate,
                },
                "nextActions": [
                    "open-only-the-next-finite-proof-window-if-start-gate-allows",
                    "write-fresh-devstream-truth-latest-elo-proof-json",
                    "verify-uninterrupted-post-target-floor-and-team-coverage",
                    "rerun-mission-monitor-before-any-next-batch",
                ],
                "acceptance": {
                    "latestEloProofReady": True,
                    "sustainProofComplete": True,
                    "startGateDecision": "allow-next-proof-window",
                    "nextProofWindowMustBeBounded": True,
                },
                "authority": base_authority,
                "noRuntimeActions": True,
            }
        )

    status = "ready"
    if packets:
        status = "blocked" if any(packet["status"] == "blocked" for packet in packets) else "actionable"
    return {
        "schemaVersion": REPAIR_QUEUE_SCHEMA_VERSION,
        "projectId": "fouler-play",
        "status": status,
        "packetCount": len(packets),
        "blockedIssueIds": blocking_issue_ids,
        "triggerIssueIds": list(dict.fromkeys(trigger_issue_ids)),
        "nextPacketId": packets[0]["id"] if packets else None,
        "packets": packets,
        "authority": base_authority,
        "noRuntimeActions": True,
    }


def classify_mission(
    *,
    health: dict[str, Any],
    supervisor: dict[str, Any],
    lease: dict[str, Any],
    battles: list[dict[str, Any]],
    max_health_age_seconds: int,
    loss_streak_threshold: int,
    low_win_rate_threshold: float,
    rating_drawdown_threshold: float,
    rating_drawdown_window: int,
    elo_proof: dict[str, Any] | None = None,
    max_elo_proof_age_seconds: int = 86400,
    requested_run_count: int | None = None,
    requested_max_cycles: int | None = None,
    account_season: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    issues: list[dict[str, Any]] = []
    runtime = health.get("runtimeOwnership") if isinstance(health.get("runtimeOwnership"), dict) else {}
    readiness = health.get("readiness") if isinstance(health.get("readiness"), dict) else {}
    discord_queue = health.get("discordQueue") if isinstance(health.get("discordQueue"), dict) else {}
    supervisor_state = str(supervisor.get("state") or "").strip()
    battle_runner_count = int(runtime.get("battleRunnerProcessCount") or runtime.get("battleRunnerCount") or 0)
    active_battle_count = int(health.get("activeBattleCount") or 0)
    duplicate_runners = bool(runtime.get("duplicateBattleRunners"))
    checked_age = age_seconds(health.get("checkedAt"), now=now)
    season = account_season_status(
        account_season if isinstance(account_season, dict) else load_json(ACCOUNT_SEASON_FILE, {}),
        lease=lease,
        battles=battles,
    )
    season_boundary = parse_timestamp(season.get("createdAtUtc")) if season.get("active") else None
    abandoned_cleanup = abandoned_battle_cleanup_status(
        battles,
        season_started_at=season_boundary,
        season_account=str(season.get("account") or "") or None,
    )

    if checked_age is None or checked_age > max_health_age_seconds:
        issues.append(
            issue(
                "fouler-health-stale",
                "RELIABILITY_BLOCKER",
                "Fouler health truth is stale or missing.",
                {"checkedAt": health.get("checkedAt"), "ageSeconds": checked_age},
                "refresh scripts/devstream_health.py --write and repair the scheduled health monitor",
            )
        )

    if abandoned_cleanup.get("ready") is not True:
        issues.append(
            issue(
                ABANDONED_BATTLE_ISSUE_ID,
                "HARD_BLOCKER",
                "Fouler archived an active battle without a completed battle_stats result row.",
                abandoned_cleanup,
                str(abandoned_cleanup.get("requiredAction") or "repair result capture before opening another ladder proof window"),
            )
        )

    if duplicate_runners:
        issues.append(
            issue(
                "fouler-duplicate-ladder-runners",
                "HARD_BLOCKER",
                "More than one logical ladder runner is active.",
                runtime,
                "drain/adopt exactly one ladder client before any restart",
            )
        )

    runtime_ready = bool(readiness.get("runtimeReady")) or battle_runner_count > 0 or active_battle_count > 0
    runtime_idle = not runtime_ready and battle_runner_count == 0 and active_battle_count == 0
    if runtime_idle:
        issues.append(
            issue(
                "fouler-runtime-idle",
                "RELIABILITY_BLOCKER",
                "Fouler ladder runtime is idle; Discord/OBS alone is not useful work.",
                {
                    "healthStatus": health.get("status"),
                    "healthy": health.get("healthy"),
                    "running": health.get("running"),
                    "runtimeReady": readiness.get("runtimeReady"),
                    "battleRunnerProcessCount": battle_runner_count,
                    "activeBattleCount": active_battle_count,
                    "supervisorState": supervisor_state,
                },
                "renew a bounded runtime lease and start HERMES-FoulerBattleSupervisor if no stop file or duplicates exist",
            )
        )

    account_authority = runtime_account_authority_status(health, lease)
    if not account_authority["ready"]:
        issues.append(
            issue(
                ACCOUNT_AUTHORITY_MISMATCH_ISSUE_ID,
                "HARD_BLOCKER",
                "Fouler health, lease, or runtime account authority names the wrong Showdown account.",
                account_authority,
                "align the active runtime lease and runtime environment to the canonical Fouler account before opening another proof window",
            )
        )
    elif runtime_ready and not account_authority["observable"]:
        issues.append(
            issue(
                ACCOUNT_TELEMETRY_MISSING_ISSUE_ID,
                "QUALITY_GAP",
                "Fouler health does not expose the active Showdown account for the live runtime.",
                account_authority,
                "refresh devstream health from a version that publishes accountAuthority before treating live runtime proof as complete",
            )
        )

    if supervisor_state == "completed-max-cycles":
        issues.append(
            issue(
                "fouler-supervisor-max-cycles-complete",
                "RELIABILITY_BLOCKER",
                "The bounded battle supervisor completed its finite cycle budget and stopped.",
                {
                    "completedLearningCycles": supervisor.get("completedLearningCycles"),
                    "completedAt": supervisor.get("completedAt"),
                    "lastHeartbeatAt": supervisor.get("lastHeartbeatAt"),
                },
                "HERMES should open a new finite proof window or deliberately pause the lane",
            )
        )

    queue_class = discord_queue.get("backlogClassification") if isinstance(discord_queue.get("backlogClassification"), dict) else {}
    placeholder_counts = discord_queue.get("pendingPlaceholderFieldCounts") or {}
    proof_readiness = discord_queue.get("proofReadiness") if isinstance(discord_queue.get("proofReadiness"), dict) else {}
    local_proof_ready = (
        proof_readiness.get("readyForLocalProofHandoff") is True
        or proof_readiness.get("localProofClassified") is True
    )
    transport_backlog_only = (
        queue_class.get("blocking")
        and local_proof_ready
        and not placeholder_counts
        and int(discord_queue.get("deliveryFailures") or 0) <= 0
    )
    if (queue_class.get("blocking") and not transport_backlog_only) or placeholder_counts or int(discord_queue.get("deliveryFailures") or 0) > 0:
        issues.append(
            issue(
                "fouler-discord-reporting-unhealthy",
                "QUALITY_GAP",
                "Fouler Discord reporting has blocking, failed, or placeholder-laden queue evidence.",
                {
                    "status": discord_queue.get("status"),
                    "backlogClassification": queue_class,
                    "pendingPlaceholderFieldCounts": placeholder_counts,
                    "deliveryFailures": discord_queue.get("deliveryFailures"),
                    "failedEventTypes": discord_queue.get("failedEventTypes"),
                },
                "repair report generation before trusting Discord as operator proof",
            )
        )

    trend = recent_result_summary(battles, window=20)
    if trend["streakKind"] == "loss" and int(trend["streak"] or 0) >= loss_streak_threshold:
        issues.append(
            issue(
                "fouler-loss-streak",
                "RELIABILITY_BLOCKER",
                "Fouler hit a loss streak safety valve.",
                trend,
                "pause promotion/self-improvement and open a repair lane with recent replay evidence",
            )
        )
    elif trend["decisive"] >= 10 and trend["winRate"] is not None and trend["winRate"] < low_win_rate_threshold:
        issues.append(
            issue(
                "fouler-low-recent-win-rate",
                "RELIABILITY_BLOCKER",
                "Fouler recent ladder win rate is below the safety threshold.",
                trend,
                "open a repair lane with the recent 20-battle window and block candidate promotion",
            )
        )

    rating_truth = rating_truth_summary(battles, window=min(20, rating_drawdown_window))
    rating_truth_building = (
        season.get("active") is True
        and rating_truth["ratedDecisiveBattles"] < RATING_TRUTH_MIN_RATED_DECISIVE_BATTLES
        and rating_truth["missingRatingBattles"] == 0
        and rating_truth["ratedDecisiveBattles"] == rating_truth["decisiveBattles"]
    )
    if rating_truth_building:
        issues.append(
            issue(
                RATING_TRUTH_BUILDING_ISSUE_ID,
                "QUALITY_GAP",
                "Fouler is building the current account season's initial rated truth window.",
                {
                    **rating_truth,
                    "accountSeason": season,
                    "remainingRatedDecisiveBattles": (
                        RATING_TRUTH_MIN_RATED_DECISIVE_BATTLES
                        - rating_truth["ratedDecisiveBattles"]
                    ),
                },
                "continue bounded proof windows while preserving complete rating capture until the first 20 decisive battles are recorded",
            )
        )
    elif (
        rating_truth["ratedDecisiveBattles"] < RATING_TRUTH_MIN_RATED_DECISIVE_BATTLES
        or rating_truth["missingRatingBattles"] > 0
        or rating_truth["ratingCoverage"] != 1.0
    ):
        issues.append(
            issue(
                "fouler-rating-truth-insufficient",
                "RELIABILITY_BLOCKER",
                "Fouler recent ladder rating truth is not complete enough for drawdown governance.",
                rating_truth,
                "collect at least 20 recent rated decisive battles with complete Showdown rating-line capture before opening another ladder proof window",
            )
        )

    drawdown = rating_drawdown_summary(battles, window=rating_drawdown_window)
    current_drawdown = drawdown.get("currentDrawdown")
    if isinstance(current_drawdown, (int, float)) and current_drawdown >= rating_drawdown_threshold:
        evidence = {
            **drawdown,
            "threshold": rating_drawdown_threshold,
            "ratingWindow": rating_drawdown_window,
            "triggerMetric": "currentDrawdown",
        }
        issues.append(
            issue(
                "fouler-rating-drawdown",
                "RELIABILITY_BLOCKER",
                "Fouler active rated ladder drawdown has exceeded the safety valve.",
                evidence,
                "pause the ladder batch, analyze the drawdown window, and require a targeted fix plus evaluation gate before continuing",
            )
        )

    ladder_stage = ladder_stage_status(
        battles,
        requested_run_count=requested_run_count,
        requested_max_cycles=requested_max_cycles,
    )
    if ladder_stage["batchSizeOk"] is False:
        issues.append(
            issue(
                "fouler-ladder-batch-too-large-for-stage",
                "RELIABILITY_BLOCKER",
                "Requested Fouler ladder proof window is too large for the current rating stage.",
                ladder_stage,
                "reduce the requested supervisor run count and max cycles so the total proof window stays within the stage max, then re-run the monitor before starting laddering",
            )
        )
    floor_regression = ladder_stage.get("floorRegression") if isinstance(ladder_stage.get("floorRegression"), dict) else {}
    if floor_regression.get("regressed") is True:
        issues.append(
            issue(
                "fouler-ladder-floor-regression",
                "RELIABILITY_BLOCKER",
                "Fouler dropped below a previously proven ladder floor.",
                floor_regression,
                "pause the ladder batch, analyze the floor-breach window, and require a targeted fix plus offline evaluation before continuing",
            )
        )

    sustain_proof = elo_sustain_proof_status(
        elo_proof or {},
        lease=lease,
        max_age_seconds=max_elo_proof_age_seconds,
        current_checkout_commit=current_source_commit(),
    )
    if not sustain_proof["ready"]:
        issues.append(
            issue(
                "fouler-elo-sustain-proof-missing-or-failing",
                "RELIABILITY_BLOCKER",
                "Fouler has not proven the canonical 1700 ELO sustain mission.",
                sustain_proof,
                "continue bounded proof batches and do not claim devstream readiness until latest-elo-proof.json passes the sustain contract",
            )
        )
    proof_stop_loss_issues = elo_sustain_stop_loss_issues(
        sustain_proof,
        existing_issue_ids={item["id"] for item in issues},
    )
    issues.extend(proof_stop_loss_issues)

    offline_eval_resume_proof = offline_eval_resume_proof_status()
    active_improvement_proof = active_improvement_proof_status()
    governance = session_governance(
        issues,
        trend=trend,
        drawdown=drawdown,
        ladder_stage=ladder_stage,
        offline_eval_resume_proof=offline_eval_resume_proof,
    )
    if governance["stopLossBreached"] and not offline_eval_resume_proof["ready"]:
        issues.append(
            issue(
                OFFLINE_EVAL_RESUME_ISSUE_ID,
                "RELIABILITY_BLOCKER",
                "Fouler stop-loss recovery lacks accepted offline evaluation proof.",
                {
                    "blockingIssueIds": governance["blockingIssueIds"],
                    "offlineEvalResumeProof": offline_eval_resume_proof,
                },
                "produce an accepted offline candidate/compare proof before opening another ladder proof window after stop-loss",
            )
        )
        governance = session_governance(
            issues,
            trend=trend,
            drawdown=drawdown,
            ladder_stage=ladder_stage,
            offline_eval_resume_proof=offline_eval_resume_proof,
        )
    if (
        governance["stopLossBreached"]
        and offline_eval_resume_proof["ready"]
        and not active_improvement_proof["ready"]
    ):
        issues.append(
            issue(
                ACTIVE_IMPROVEMENT_ISSUE_ID,
                "RELIABILITY_BLOCKER",
                "Fouler stop-loss recovery lacks a fresh active improvement proof.",
                {
                    "blockingIssueIds": governance["blockingIssueIds"],
                    "offlineEvalResumeProof": offline_eval_resume_proof,
                    "activeImprovementProof": active_improvement_proof,
                },
                "produce an implemented work packet with post-packet battle/autoresearch proof before reopening laddering after stop-loss",
            )
        )
        governance = session_governance(
            issues,
            trend=trend,
            drawdown=drawdown,
            ladder_stage=ladder_stage,
            offline_eval_resume_proof=offline_eval_resume_proof,
        )
    if governance["stopLossBreached"]:
        issues.append(
            issue(
                "fouler-session-stop-loss-breached",
                "RELIABILITY_BLOCKER",
                "Fouler session stop-loss governance blocks further ladder starts.",
                governance,
                "do not start another ladder batch until the failing window is repaired and resume criteria are proven",
            )
        )
        governance = session_governance(
            issues,
            trend=trend,
            drawdown=drawdown,
            ladder_stage=ladder_stage,
            offline_eval_resume_proof=offline_eval_resume_proof,
        )

    active = lease_active(lease, now=now)
    stop_file_present = SUPERVISOR_STOP_FILE.exists()
    if stop_file_present:
        issues.append(
            issue(
                SUPERVISOR_STOP_FILE_ISSUE_ID,
                "HARD_BLOCKER",
                "Fouler supervisor stop file is present.",
                {
                    "policy": "fouler-runtime-start-gate/v1",
                    "path": display_path(SUPERVISOR_STOP_FILE),
                    "runtimeLeaseActive": active,
                    "sessionGovernance": governance,
                },
                "remove the supervisor stop file only after confirming the lane should resume through a fresh mission start gate",
            )
        )
    recovery_validation = stop_loss_recovery_validation_window(
        governance=governance,
        offline_eval_resume_proof=offline_eval_resume_proof,
        active_improvement_proof=active_improvement_proof,
        requested_run_count=requested_run_count,
        requested_max_cycles=requested_max_cycles,
    )
    start_gate = start_gate_status(
        issues,
        governance=governance,
        duplicate_runners=duplicate_runners,
        stop_file_present=stop_file_present,
        recovery_validation=recovery_validation,
    )
    repair_queue = build_repair_queue(
        issues,
        governance=governance,
        start_gate=start_gate,
        trend=trend,
        rating_truth=rating_truth,
        drawdown=drawdown,
        ladder_stage=ladder_stage,
        sustain_proof=sustain_proof,
        offline_eval_resume_proof=offline_eval_resume_proof,
        active_improvement_proof=active_improvement_proof,
    )
    return {
        "issues": issues,
        "runtimeIdle": runtime_idle,
        "runtimeReady": runtime_ready,
        "duplicateRunners": duplicate_runners,
        "stopFilePresent": stop_file_present,
        "runtimeLeaseActive": active,
        "recentResults": trend,
        "abandonedBattleCleanup": abandoned_cleanup,
        "ratingTruth": rating_truth,
        "accountSeason": season,
        "ratingDrawdown": drawdown,
        "ladderStage": ladder_stage,
        "accountAuthority": account_authority,
        "eloSustainProof": sustain_proof,
        "offlineEvalResumeProof": offline_eval_resume_proof,
        "activeImprovementProof": active_improvement_proof,
        "sessionGovernance": governance,
        "stopLossRecoveryValidation": recovery_validation,
        "startGate": start_gate,
        "repairQueue": repair_queue,
    }


def write_tickets(issues: list[dict[str, Any]], *, source: str) -> list[str]:
    written: list[str] = []
    TICKET_DIR.mkdir(parents=True, exist_ok=True)
    for item in issues:
        path = TICKET_DIR / f"{item['id']}.json"
        existing = load_json(path, {}) if path.exists() else {}
        existing_status = str(existing.get("status") or "").strip().lower()
        payload = {
            "schemaVersion": "hermes-devstream-ticket/v1",
            "projectId": "fouler-play",
            "ticketId": item["id"],
            "status": "open",
            "openedAt": existing.get("openedAt") if existing_status == "open" else iso_now(),
            "updatedAt": iso_now(),
            "source": source,
            "severity": item["severity"],
            "summary": item["summary"],
            "evidence": item["evidence"],
            "nextHermesAction": item["nextHermesAction"],
            "rollbackConcern": "Do not kill or start ladder clients outside the singleton supervisor wrapper.",
        }
        if existing_status and existing_status != "open":
            payload["reopenedFromStatus"] = existing_status
            payload["previousClearedAt"] = existing.get("clearedAt")
        write_json(path, payload)
        written.append(display_path(path))
    return written


def reconcile_cleared_tickets(
    active_issue_ids: set[str],
    *,
    classification: dict[str, Any],
    source: str,
) -> list[str]:
    if not TICKET_DIR.exists():
        return []
    cleared: list[str] = []
    for path in TICKET_DIR.glob("*.json"):
        ticket = load_json(path, {})
        if not isinstance(ticket, dict):
            continue
        ticket_id = str(ticket.get("ticketId") or path.stem)
        if ticket_id not in KNOWN_ISSUE_IDS or ticket_id in active_issue_ids:
            continue
        status = str(ticket.get("status") or "").strip().lower()
        if status not in {"open", "action-required"}:
            continue
        ticket["status"] = "cleared"
        ticket["clearedAt"] = iso_now()
        ticket["updatedAt"] = ticket["clearedAt"]
        ticket["clearedBy"] = source
        ticket["clearanceEvidence"] = {
            "runtimeReady": classification.get("runtimeReady"),
            "runtimeIdle": classification.get("runtimeIdle"),
            "runtimeLeaseActive": classification.get("runtimeLeaseActive"),
            "duplicateRunners": classification.get("duplicateRunners"),
            "stopFilePresent": classification.get("stopFilePresent"),
            "recentResults": classification.get("recentResults"),
            "ratingDrawdown": classification.get("ratingDrawdown"),
            "eloSustainProof": classification.get("eloSustainProof"),
            "offlineEvalResumeProof": classification.get("offlineEvalResumeProof"),
            "repairQueue": classification.get("repairQueue"),
            "sessionGovernance": classification.get("sessionGovernance"),
        }
        ticket["nextHermesAction"] = "No repair action now; keep monitoring this lane for recurrence."
        write_json(path, ticket)
        cleared.append(display_path(path))
    return cleared


def queue_discord_alert(issues: list[dict[str, Any]], classification: dict[str, Any]) -> dict[str, Any]:
    if not issues:
        return {"queued": False, "reason": "no issues"}
    try:
        from infrastructure.event_queue_lib import queue_event
    except Exception as exc:
        return {"queued": False, "error": f"{type(exc).__name__}: {exc}"}

    leading = issues[0]
    content = (
        f"[ALERT] **Fouler mission monitor: {leading['id']}**\n"
        f"What happened: {leading['summary']}\n"
        "Why it matters: Fouler is supposed to keep producing bounded ladder proof; stale health or idle runtime means HERMES is not closing the loop.\n"
        "Proof: `devstream/truth/mission-monitor.json` and `devstream/tickets/fouler-play/`.\n"
        f"Remaining: {leading['nextHermesAction']}"
    )
    event_id = queue_event(
        "mission_alert",
        "project",
        content,
        dedup_window_sec=900,
        project_id="fouler-play",
        issue_ids=[item["id"] for item in issues],
        runtime_idle=classification.get("runtimeIdle"),
        duplicate_runners=classification.get("duplicateRunners"),
    )
    return {"queued": True, "eventId": event_id, "issueIds": [item["id"] for item in issues]}


def renew_runtime_lease(args: argparse.Namespace, lease: dict[str, Any]) -> dict[str, Any]:
    account = (
        str(lease.get("account") or "")
        or str((lease.get("battleScope") or {}).get("account") or "")
        or os.getenv("FOULER_ACTIVE_ACCOUNT", "")
        or os.getenv("PS_USERNAME", "")
        or DEFAULT_ACCOUNT
    ).strip()
    return run_command(
        [
            runtime_python(),
            "scripts/devstream_runtime_lease.py",
            "--purpose",
            "devstream-supervise",
            "--write",
            "--machine",
            "JIGGLYPUFF",
            "--run-count",
            str(args.run_count),
            "--max-cycles",
            str(args.max_cycles),
            "--max-concurrent-battles",
            str(args.max_concurrent_battles),
            "--account",
            account,
            "--replay-behavior",
            "always",
            "--valid-minutes",
            str(args.lease_minutes),
            "--approved",
            "--runtime-lease",
            str(RUNTIME_LEASE_FILE.relative_to(ROOT)),
        ],
        timeout=60,
    )


def start_supervisor(args: argparse.Namespace) -> dict[str, Any]:
    installer = ROOT / "scripts" / "install_battle_supervisor_task.ps1"
    command = [
        powershell_exe(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer),
        "-Apply",
        "-Start",
        "-RunCount",
        str(args.run_count),
        "-MaxConcurrentBattles",
        str(args.max_concurrent_battles),
        "-MaxCycles",
        str(args.max_cycles),
        "-QueueTimeoutSeconds",
        str(args.queue_timeout_seconds),
        "-SleepSeconds",
        str(args.sleep_seconds),
        "-RuntimeLease",
        str(RUNTIME_LEASE_FILE.relative_to(ROOT)),
        "-LoopBreak",
        CANONICAL_LOOP_BREAK,
    ]
    if args.auto_improve:
        command.append("-AutoImprove")
    return run_command(command, timeout=90)


def maybe_repair_runtime(args: argparse.Namespace, classification: dict[str, Any], lease: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not args.repair_runtime:
        return actions
    if not classification.get("runtimeIdle"):
        return actions
    if classification.get("duplicateRunners"):
        actions.append({"action": "repair-skipped", "reason": "duplicate runners require manual drain/adopt"})
        return actions
    if classification.get("stopFilePresent"):
        actions.append({"action": "repair-skipped", "reason": "supervisor stop file is present"})
        return actions
    start_gate = classification.get("startGate") if isinstance(classification.get("startGate"), dict) else {}
    if start_gate.get("ready") is False:
        actions.append(
            {
                "action": "repair-skipped",
                "reason": "runtime start gate blocks ladder start",
                "startGate": start_gate,
            }
        )
        return actions
    governance = classification.get("sessionGovernance") if isinstance(classification.get("sessionGovernance"), dict) else {}
    recovery_window_allowed = start_gate.get("decision") == "allow-stop-loss-recovery-proof-window"
    if governance.get("allowLaddering") is False and not recovery_window_allowed:
        actions.append(
            {
                "action": "repair-skipped",
                "reason": "session stop-loss governance blocks ladder start",
                "sessionGovernance": governance,
            }
        )
        return actions
    if args.renew_lease or not classification.get("runtimeLeaseActive"):
        renew = renew_runtime_lease(args, lease)
        renew["action"] = "renew-runtime-lease"
        actions.append(renew)
        if not renew.get("ok"):
            return actions
    start = start_supervisor(args)
    start["action"] = "start-battle-supervisor"
    actions.append(start)
    return actions


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if args.refresh_health:
        refresh = refresh_health(skip_http=args.skip_http, write=args.write)
        refresh["action"] = "refresh-health"
        actions.append(refresh)
    task = supervisor_task_status()
    task["action"] = "supervisor-task-status"
    actions.append(task)

    health = load_json(HEALTH_FILE, {})
    supervisor = load_json(SUPERVISOR_STATUS_FILE, {})
    lease = load_json(RUNTIME_LEASE_FILE, {})
    elo_proof = load_json(LATEST_ELO_PROOF_FILE, {})
    account_season = load_json(ACCOUNT_SEASON_FILE, {})
    battles = read_battles()

    def classify_current() -> dict[str, Any]:
        return classify_mission(
            health=health,
            supervisor=supervisor,
            lease=lease,
            battles=battles,
            max_health_age_seconds=args.max_health_age_seconds,
            loss_streak_threshold=args.loss_streak_threshold,
            low_win_rate_threshold=args.low_win_rate_threshold,
            rating_drawdown_threshold=args.rating_drawdown_threshold,
            rating_drawdown_window=args.rating_drawdown_window,
            elo_proof=elo_proof,
            max_elo_proof_age_seconds=args.max_elo_proof_age_seconds,
            requested_run_count=args.run_count,
            requested_max_cycles=args.max_cycles,
            account_season=account_season,
        )

    classification = classify_current()

    tripwire_action = enforce_stop_loss_tripwire(classification, write=args.write)
    if tripwire_action is not None:
        actions.append(tripwire_action)
        if tripwire_action.get("written") is True:
            classification = classify_current()

    recovered_action = reconcile_recovered_stop_loss_tripwire(
        classification, write=args.write
    )
    if recovered_action is not None:
        actions.append(recovered_action)
        if recovered_action.get("written") is True:
            classification = classify_current()

    repair_actions = maybe_repair_runtime(args, classification, lease)
    actions.extend(repair_actions)
    if repair_actions and args.refresh_health_after_repair:
        refresh = refresh_health(skip_http=args.skip_http, write=args.write)
        refresh["action"] = "refresh-health-after-repair"
        actions.append(refresh)
        health = load_json(HEALTH_FILE, {})
        supervisor = load_json(SUPERVISOR_STATUS_FILE, {})
        lease = load_json(RUNTIME_LEASE_FILE, {})
        elo_proof = load_json(LATEST_ELO_PROOF_FILE, {})
        account_season = load_json(ACCOUNT_SEASON_FILE, {})
        classification = classify_mission(
            health=health,
            supervisor=supervisor,
            lease=lease,
            battles=read_battles(),
            max_health_age_seconds=args.max_health_age_seconds,
            loss_streak_threshold=args.loss_streak_threshold,
            low_win_rate_threshold=args.low_win_rate_threshold,
            rating_drawdown_threshold=args.rating_drawdown_threshold,
            rating_drawdown_window=args.rating_drawdown_window,
            elo_proof=elo_proof,
            max_elo_proof_age_seconds=args.max_elo_proof_age_seconds,
            requested_run_count=args.run_count,
            requested_max_cycles=args.max_cycles,
            account_season=account_season,
        )

    active_issue_ids = {item["id"] for item in classification["issues"]}
    tickets = write_tickets(classification["issues"], source="scripts/fouler_mission_monitor.py") if args.write else []
    tickets_cleared = (
        reconcile_cleared_tickets(
            active_issue_ids,
            classification=classification,
            source="scripts/fouler_mission_monitor.py",
        )
        if args.write
        else []
    )
    discord_alert = queue_discord_alert(classification["issues"], classification) if args.queue_alerts else {"queued": False, "reason": "disabled"}
    repair_action_failures = [
        action
        for action in actions
        if action.get("action") in {"renew-runtime-lease", "start-battle-supervisor"}
        and (action.get("ok") is False or (action.get("returnCode") not in (None, 0)))
    ]
    start_gate = dict(classification.get("startGate") or {})
    start_gate["repairActionsOk"] = not repair_action_failures
    start_gate["repairActionFailures"] = repair_action_failures
    source_commit = current_source_commit()
    signal_fields = signal_freshness_status()
    decision_divergence = decision_divergence_status()
    payload = {
        "schemaVersion": "fouler-play-mission-monitor/v1",
        "projectId": "fouler-play",
        "checkedAt": iso_now(),
        "sourceCommit": source_commit,
        "healthy": not classification["issues"],
        "status": "healthy" if not classification["issues"] else "action-required",
        "issues": classification["issues"],
        "startGate": start_gate,
        "repairQueue": classification.get("repairQueue"),
        "ticketsWritten": tickets,
        "ticketsCleared": tickets_cleared,
        "discordAlert": discord_alert,
        "classification": {key: value for key, value in classification.items() if key != "issues"},
        "decisionDivergence": decision_divergence,
        "actions": actions,
        "paths": {
            "health": str(HEALTH_FILE.relative_to(ROOT)),
            "supervisorStatus": str(SUPERVISOR_STATUS_FILE.relative_to(ROOT)),
            "runtimeLease": str(RUNTIME_LEASE_FILE.relative_to(ROOT)),
            "latestEloProof": str(LATEST_ELO_PROOF_FILE.relative_to(ROOT)),
            "tickets": str(TICKET_DIR.relative_to(ROOT)),
        },
        "secretValuesPrinted": False,
    }
    payload.update(signal_fields)
    if args.write:
        write_json(MISSION_MONITOR_FILE, payload)
    return payload


def repair_runtime_succeeded(payload: dict[str, Any]) -> bool:
    """Return true when --repair-runtime opened or adopted an authorized proof window."""

    if payload.get("healthy") is True:
        return True
    start_gate = payload.get("startGate") if isinstance(payload.get("startGate"), dict) else {}
    if not start_gate.get("ready") or start_gate.get("repairActionsOk", True) is False:
        return False
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    start_actions = [
        action
        for action in actions
        if isinstance(action, dict) and action.get("action") == "start-battle-supervisor"
    ]
    if start_actions:
        return any(action.get("ok") is not False and action.get("returnCode") in (None, 0) for action in start_actions)
    classification = payload.get("classification") if isinstance(payload.get("classification"), dict) else {}
    return classification.get("runtimeIdle") is False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fouler mission monitor for HERMES/DEKU")
    parser.add_argument("--write", action="store_true", help="write truth and ticket files")
    parser.add_argument("--refresh-health", action="store_true", default=True)
    parser.add_argument("--no-refresh-health", action="store_false", dest="refresh_health", help="classify existing health truth without refreshing devstream_health.py")
    parser.add_argument("--skip-http", action="store_true", help="skip HTTP checks in devstream_health")
    parser.add_argument("--repair-runtime", action="store_true", help="start a bounded supervisor when runtime is safely idle")
    parser.add_argument("--renew-lease", action="store_true", help="write a fresh finite runtime lease before supervisor start")
    parser.add_argument("--queue-alerts", action="store_true", help="queue mission alerts through Discord event queue")
    parser.add_argument("--start-gate-only", action="store_true", help="return success when the next bounded ladder proof window is safe, even if final 1700 sustain proof is still incomplete")
    parser.add_argument("--refresh-health-after-repair", action="store_true", default=True)
    parser.add_argument("--no-refresh-health-after-repair", action="store_false", dest="refresh_health_after_repair", help="skip health refresh after a repair action")
    parser.add_argument("--run-count", type=int, default=DEFAULT_MONITOR_RUN_COUNT)
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MONITOR_MAX_CYCLES)
    parser.add_argument("--max-concurrent-battles", type=int, default=1)
    parser.add_argument("--queue-timeout-seconds", type=int, default=180)
    parser.add_argument("--sleep-seconds", type=int, default=20)
    parser.add_argument("--lease-minutes", type=int, default=720)
    parser.add_argument("--auto-improve", action="store_true", default=False, help="explicitly allow the supervisor AutoImprove mode; default stays bounded proof-only")
    parser.add_argument("--max-health-age-seconds", type=int, default=300)
    parser.add_argument("--loss-streak-threshold", type=int, default=5)
    parser.add_argument("--low-win-rate-threshold", type=float, default=0.45)
    parser.add_argument("--rating-drawdown-threshold", type=float, default=75.0)
    parser.add_argument("--rating-drawdown-window", type=int, default=60)
    parser.add_argument("--max-elo-proof-age-seconds", type=int, default=86400)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_payload(args)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.start_gate_only:
        start_gate = payload.get("startGate") if isinstance(payload.get("startGate"), dict) else {}
        return 0 if start_gate.get("ready") and start_gate.get("repairActionsOk", True) else 2
    if args.repair_runtime:
        return 0 if repair_runtime_succeeded(payload) else 2
    return 0 if payload.get("healthy") else 2


if __name__ == "__main__":
    raise SystemExit(main())
