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
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TRUTH_DIR = ROOT / "devstream" / "truth"
TICKET_DIR = ROOT / "devstream" / "tickets" / "fouler-play"
MISSION_MONITOR_FILE = TRUTH_DIR / "mission-monitor.json"
HEALTH_FILE = TRUTH_DIR / "health.json"
SUPERVISOR_STATUS_FILE = TRUTH_DIR / "supervisor-status.json"
RUNTIME_LEASE_FILE = TRUTH_DIR / "runtime-lease.json"
ELO_PROOF_FILE = TRUTH_DIR / "latest-elo-proof.json"
DISCORD_REPORTING_FILE = TRUTH_DIR / "discord-reporting.json"
BATTLE_STATS_FILE = ROOT / "battle_stats.json"
SUPERVISOR_STOP_FILE = ROOT / ".pids" / "supervisor.stop"

DEFAULT_ACCOUNT = "LEBOTJAMESXD00N"
LOSS_WORDS = {"loss", "lost", "timeout", "timed out", "disconnect", "disconnected", "inactive", "forfeit"}
WIN_WORDS = {"win", "won"}
KNOWN_ISSUE_IDS = {
    "fouler-health-stale",
    "fouler-duplicate-ladder-runners",
    "fouler-runtime-idle",
    "fouler-supervisor-max-cycles-complete",
    "fouler-discord-reporting-unhealthy",
    "fouler-stale-active-battle-truth",
    "fouler-battle-proof-stale-for-lease",
    "fouler-loss-streak",
    "fouler-low-recent-win-rate",
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


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
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


def powershell_exe() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.exists() else "powershell.exe"


def refresh_health(*, skip_http: bool = False) -> dict[str, Any]:
    command = [runtime_python(), "scripts/devstream_health.py", "--write"]
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


def normalize_result(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in WIN_WORDS:
        return "win"
    if text in LOSS_WORDS or any(word in text for word in ("timeout", "timed out", "disconnect", "inactive")):
        return "loss"
    if text in {"tie", "draw"}:
        return "loss"
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


def _linear_slope(values: list[float]) -> float | None:
    """Least-squares slope of ``values`` vs their index. None if < 2 points."""
    n = len(values)
    if n < 2:
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return None
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    return num / denom


def true_trajectory(
    all_battles: list[dict[str, Any]],
    *,
    account: str,
    window: int = 200,
) -> dict[str, Any]:
    """Honest rolling trajectory across ALL restarts (NOT lease-windowed).

    The lease-windowed proof can look flattering because it drops every battle
    before the last restart. This computes the TRUE picture from the full history:
      * last-``window`` decisive win rate (across restarts),
      * a real slope: first-half vs second-half WR over that window AND a
        least-squares ELO fit, so "improving" means an actual upward trend, not
        merely ``recentWR >= 0.45``.
    Only same-account decisive battles are used.
    """
    decisive = [
        row
        for row in sorted_battles(all_battles)
        if isinstance(row, dict)
        and battle_belongs_to_account(row, account)
        and normalize_result(row.get("result")) in {"win", "loss"}
    ]
    recent = decisive[-window:]
    n = len(recent)
    wins = sum(1 for r in recent if normalize_result(r.get("result")) == "win")
    wr = round(wins / n, 4) if n else None

    half = n // 2
    first, second = recent[:half], recent[half:]

    def _wr(rows: list[dict[str, Any]]) -> float | None:
        if not rows:
            return None
        w = sum(1 for r in rows if normalize_result(r.get("result")) == "win")
        return round(w / len(rows), 4)

    fh_wr, sh_wr = _wr(first), _wr(second)
    wr_delta = round(sh_wr - fh_wr, 4) if (fh_wr is not None and sh_wr is not None) else None

    ratings = [rating_after(r) for r in recent]
    ratings = [v for v in ratings if v is not None]
    elo_slope = _linear_slope(ratings)
    elo_drift = round(elo_slope * len(ratings), 1) if elo_slope is not None else None

    # Honest trend label: require BOTH a positive WR delta between halves AND a
    # non-trivial positive ELO drift across the window. Otherwise it's flat.
    if wr_delta is None or elo_drift is None:
        trend = "insufficient-data"
    elif wr_delta >= 0.03 and elo_drift >= 15:
        trend = "climbing"
    elif wr_delta <= -0.03 or elo_drift <= -15:
        trend = "declining"
    else:
        trend = "flat"

    return {
        "scope": "all-restarts (true history, not lease-windowed)",
        "decisiveWindow": n,
        "windowWinRate": wr,
        "firstHalfWinRate": fh_wr,
        "secondHalfWinRate": sh_wr,
        "winRateDelta": wr_delta,
        "eloSlopePerBattle": round(elo_slope, 4) if elo_slope is not None else None,
        "eloDriftOverWindow": elo_drift,
        "firstRatingInWindow": int(ratings[0]) if ratings else None,
        "lastRatingInWindow": int(ratings[-1]) if ratings else None,
        "totalDecisiveAllTime": len(decisive),
        "allTimeWinRate": round(
            sum(1 for r in decisive if normalize_result(r.get("result")) == "win") / len(decisive), 4
        )
        if decisive
        else None,
        "trueTrend": trend,
    }


def account_from_runtime_truth(lease: dict[str, Any], health: dict[str, Any]) -> str:
    """Return the active Showdown account from durable runtime truth."""

    lease_account = runtime_lease_account(lease)
    if lease_account:
        return lease_account

    for value in (
        os.getenv("FOULER_ACTIVE_ACCOUNT"),
        os.getenv("PS_USERNAME"),
    ):
        account = str(value or "").strip()
        if account:
            return account

    status = ((health.get("endpoints") or {}).get("/status") or {}).get("json")
    if isinstance(status, dict):
        accounts = status.get("accounts_elo")
        if isinstance(accounts, dict) and accounts:
            first = next(iter(accounts.keys()), "")
            if first:
                return str(first)
    return DEFAULT_ACCOUNT


def normalize_account(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def runtime_lease_account(lease: dict[str, Any]) -> str:
    battle_scope = lease.get("battleScope") if isinstance(lease.get("battleScope"), dict) else {}
    for value in (
        lease.get("account"),
        lease.get("psUsername"),
        lease.get("showdownAccount"),
        battle_scope.get("account"),
        battle_scope.get("psUsername"),
    ):
        account = str(value or "").strip()
        if account:
            return account
    return ""


def lease_start_time(lease: dict[str, Any]) -> datetime | None:
    proof_window = lease.get("proofWindow") if isinstance(lease.get("proofWindow"), dict) else {}
    return parse_timestamp(proof_window.get("startsAt") or lease.get("createdAt") or lease.get("startedAt"))


def battle_belongs_to_account(row: dict[str, Any], account: str) -> bool:
    wanted = normalize_account(account)
    if not wanted:
        return True
    opponent = normalize_account(row.get("opponent"))
    stamped = [
        row.get("account"),
        row.get("bot_username"),
        row.get("ps_username"),
        row.get("showdownAccount"),
        row.get("botUsername"),
    ]
    stamped_norm = [normalize_account(value) for value in stamped if normalize_account(value)]
    if stamped_norm:
        return wanted in stamped_norm
    winner = normalize_account(row.get("winner"))
    loser = normalize_account(row.get("loser"))
    if winner == wanted or loser == wanted:
        return True
    result = normalize_result(row.get("result"))
    if result == "win" and winner and winner != wanted:
        return False
    if opponent and winner == opponent and result == "loss":
        return True
    return not winner


def filter_battles_for_lease(
    battles: list[dict[str, Any]],
    *,
    account: str,
    lease: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    starts_at = lease_start_time(lease)
    filtered: list[dict[str, Any]] = []
    dropped_other_account = 0
    dropped_before_lease = 0
    unstamped_kept = 0
    for row in battles:
        if not isinstance(row, dict):
            continue
        if not battle_belongs_to_account(row, account):
            dropped_other_account += 1
            continue
        row_time = battle_time(row)
        if starts_at and row_time and row_time < starts_at:
            dropped_before_lease += 1
            continue
        if not any(row.get(key) for key in ("account", "bot_username", "ps_username", "showdownAccount", "botUsername")):
            unstamped_kept += 1
        filtered.append(row)
    return filtered, {
        "leaseStartsAt": starts_at.isoformat() if starts_at else None,
        "inputRows": len(battles),
        "filteredRows": len(filtered),
        "droppedOtherAccountRows": dropped_other_account,
        "droppedBeforeLeaseRows": dropped_before_lease,
        "unstampedRowsKept": unstamped_kept,
    }


def numeric_value(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def battle_time(row: dict[str, Any]) -> datetime | None:
    return parse_timestamp(row.get("timestamp") or row.get("ended_at") or row.get("created_at"))


def sorted_battles(battles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[int, str]:
        parsed = battle_time(row)
        if parsed is None:
            return (0, "")
        return (1, parsed.isoformat())

    return sorted(battles, key=key)


def rating_after(row: dict[str, Any]) -> float | None:
    for key in ("elo_after", "rating", "post_elo", "rating_after"):
        value = numeric_value(row.get(key))
        if value is not None:
            return value
    return None


def current_ladder_rating(health: dict[str, Any], account: str, battles: list[dict[str, Any]]) -> float | None:
    normalized = account.lower()
    endpoints = health.get("endpoints") if isinstance(health.get("endpoints"), dict) else {}
    for path in ("/status", "/state"):
        status = (endpoints.get(path) or {}).get("json") if isinstance(endpoints.get(path), dict) else None
        if not isinstance(status, dict):
            continue
        accounts = status.get("accounts_elo")
        if isinstance(accounts, dict):
            for name, rating in accounts.items():
                if str(name).lower() == normalized:
                    numeric = numeric_value(rating)
                    if numeric is not None:
                        return numeric
        numeric = numeric_value(status.get("elo"))
        if numeric is not None:
            return numeric

    for row in reversed(sorted_battles(battles)):
        numeric = rating_after(row)
        if numeric is not None:
            return numeric
    return None


def record_with_percent(summary: dict[str, Any]) -> str:
    window = int(summary.get("windowSize") or 0)
    wins = int(summary.get("wins") or 0)
    losses = int(summary.get("losses") or 0)
    decisive = wins + losses
    pct = int(round((wins / decisive) * 100)) if decisive else 0
    return f"last {window}: {wins}-{losses} ({pct}% WR)"


def build_latest_elo_proof(
    *,
    health: dict[str, Any],
    lease: dict[str, Any],
    battles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the HERMES-facing ELO proof from current runtime truth.

    This intentionally replaces older manually mirrored proof snapshots. The
    active account comes from the runtime lease; the latest battle and ratings
    come from live battle_stats/health, so a stale alternate cannot look fresh.
    """

    account = account_from_runtime_truth(lease, health)
    lease_battles, filter_summary = filter_battles_for_lease(battles, account=account, lease=lease)
    ordered = sorted_battles(lease_battles)
    latest = ordered[-1] if ordered else {}
    ratings = [rating_after(row) for row in ordered]
    ratings = [value for value in ratings if value is not None]
    current_rating = current_ladder_rating(health, account, ordered)
    if current_rating is None and ratings:
        current_rating = ratings[-1]
    first_rating = ratings[0] if ratings else current_rating
    latest_rating_delta = numeric_value(latest.get("rating_delta"))

    recent5 = recent_result_summary(ordered, window=5)
    recent20 = recent_result_summary(ordered, window=20)
    all_results = recent_result_summary(ordered, window=len(ordered) or 1)
    latest_at = latest.get("timestamp") or latest.get("ended_at") or latest.get("created_at")
    latest_id = latest.get("battle_id") or latest.get("battleId") or latest.get("battle_tag") or latest.get("id")
    battle_stats_mtime = BATTLE_STATS_FILE.stat().st_mtime if BATTLE_STATS_FILE.exists() else None
    health_checked_at = health.get("checkedAt")
    win_rate = recent20.get("winRate")
    # TRUE rolling trajectory across ALL restarts (ground truth, not lease-windowed).
    trajectory = true_trajectory(battles, account=account, window=200)
    # Honest trend status: derive from the real slope, not from "recentWR >= 0.45".
    # The lease-windowed recent WR is kept as a level indicator only.
    trend_map = {
        "climbing": "improving",
        "declining": "declining",
        "flat": "flat",
        "insufficient-data": "unknown",
    }
    trend_status = trend_map.get(trajectory.get("trueTrend", "insufficient-data"), "unknown")

    latest_battles = []
    for row in ordered[-10:]:
        latest_battles.append(
            {
                "battleId": row.get("battle_id") or row.get("battleId") or row.get("battle_tag") or row.get("id"),
                "timestamp": row.get("timestamp") or row.get("ended_at") or row.get("created_at"),
                "opponent": row.get("opponent"),
                "result": normalize_result(row.get("result")),
                "winner": row.get("winner"),
                "rating": rating_after(row),
                "ratingDelta": numeric_value(row.get("rating_delta")),
                "replayStatus": row.get("replay_status"),
                "replayUrl": row.get("replay_url"),
            }
        )

    return {
        "schemaVersion": "fouler-play-elo-proof/v2",
        "checkedAtUtc": iso_now(),
        "account": {
            "showdownUserId": account,
            "source": "devstream/truth/runtime-lease.json",
            "ratingSource": "devstream/truth/health.json accounts_elo plus battle_stats.json",
        },
        "source": {
            "repoRoot": str(ROOT),
            "battleStatsPath": str(BATTLE_STATS_FILE),
            "battleStatsMtime": datetime.fromtimestamp(battle_stats_mtime, timezone.utc).isoformat() if battle_stats_mtime else None,
            "healthPath": str(HEALTH_FILE),
            "healthCheckedAt": health_checked_at,
            "runtimeLeasePath": str(RUNTIME_LEASE_FILE),
            "runtimeLeaseId": lease.get("leaseId"),
            "leaseStartsAt": filter_summary.get("leaseStartsAt"),
        },
        "summary": {
            "completedGames": len(ordered),
            "wins": all_results["wins"],
            "losses": all_results["losses"],
            "unknownResults": max(0, len(ordered) - int(all_results["decisive"] or 0)),
            "winRate": recent20.get("winRate"),
            "recent5": record_with_percent(recent5),
            "recent20": record_with_percent(recent20),
            "latestBattleAt": latest_at,
            "latestBattleId": latest_id,
            "latestOpponent": latest.get("opponent"),
            "latestResult": normalize_result(latest.get("result")),
            "latestBattleLearningVerified": bool(latest),
            "latestReplayAnalysisExists": False,
            "latestBattleLogCount": 1 if latest else 0,
            "finalRating": int(current_rating) if current_rating is not None else None,
            "peakRating": int(max(ratings)) if ratings else (int(current_rating) if current_rating is not None else None),
            "ratingDelta": int(round((current_rating - first_rating))) if current_rating is not None and first_rating is not None else None,
            "latestRatingDelta": int(latest_rating_delta) if latest_rating_delta is not None else None,
            "performanceTrendStatus": trend_status,
            "performanceImprovementVerified": trend_status == "improving",
            "trueTrajectory": trajectory,
            "activeImprovementVerified": bool(health.get("activeBattleCount") or 0),
            "passesTarget": bool(current_rating is not None and current_rating >= 1700),
        },
        "target": {
            "ratingFloor": 1700,
            "opponentBand": "prefer stronger opponents when opponent rating is known",
        },
        "evidence": {
            "latestBattles": latest_battles,
            "runtimeLeaseAccount": account,
            "healthStatus": health.get("status"),
            "activeBattleCount": health.get("activeBattleCount"),
            "battleFilter": filter_summary,
        },
        "staleGuard": {
            "proofAccountMatchesRuntimeLease": normalize_account(account) == normalize_account(runtime_lease_account(lease) or account),
            "latestBattleWithinRuntimeLease": bool(latest),
            "battleRowsDroppedBeforeLease": filter_summary.get("droppedBeforeLeaseRows"),
            "battleRowsDroppedForOtherAccount": filter_summary.get("droppedOtherAccountRows"),
            "unstampedBattleRowsKept": filter_summary.get("unstampedRowsKept"),
            "generatedBy": "scripts/fouler_mission_monitor.py",
            "rejectIfOlderThanSeconds": 900,
        },
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


def classify_mission(
    *,
    health: dict[str, Any],
    discord_report: dict[str, Any] | None = None,
    supervisor: dict[str, Any],
    lease: dict[str, Any],
    battles: list[dict[str, Any]],
    max_health_age_seconds: int,
    loss_streak_threshold: int,
    low_win_rate_threshold: float,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    issues: list[dict[str, Any]] = []
    runtime = health.get("runtimeOwnership") if isinstance(health.get("runtimeOwnership"), dict) else {}
    readiness = health.get("readiness") if isinstance(health.get("readiness"), dict) else {}
    discord_queue = health.get("discordQueue") if isinstance(health.get("discordQueue"), dict) else {}
    discord_queue_source = "health.discordQueue"
    if isinstance(discord_report, dict):
        report_queue = discord_report.get("queue") if isinstance(discord_report.get("queue"), dict) else {}
        report_queue_health = report_queue.get("health") if isinstance(report_queue.get("health"), dict) else {}
        if report_queue_health.get("available") is True:
            discord_queue = report_queue_health
            discord_queue_source = "devstream/truth/discord-reporting.json queue.health"
    runtime_truth = health.get("runtimeTruthDisposition") if isinstance(health.get("runtimeTruthDisposition"), dict) else {}
    supervisor_state = str(supervisor.get("state") or "").strip()
    battle_runner_count = int(runtime.get("battleRunnerProcessCount") or runtime.get("battleRunnerCount") or 0)
    active_battle_count = int(health.get("activeBattleCount") or 0)
    duplicate_runners = bool(runtime.get("duplicateBattleRunners"))
    checked_age = age_seconds(health.get("checkedAt"), now=now)
    supervisor_completed_max_cycles = supervisor_state == "completed-max-cycles"
    blockers = [str(item) for item in (health.get("blockers") or [])]
    active_battle_truth_stale = (
        active_battle_count > 0
        and battle_runner_count == 0
        and (
            "stale" in " ".join(blockers).lower()
            or str(runtime_truth.get("state") or "").strip().lower() == "blocked"
        )
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

    runtime_ready = bool(readiness.get("runtimeReady")) or battle_runner_count > 0
    runtime_idle = not runtime_ready and battle_runner_count == 0 and active_battle_count == 0
    if active_battle_truth_stale:
        issues.append(
            issue(
                "fouler-stale-active-battle-truth",
                "RELIABILITY_BLOCKER",
                "Fouler has stale active-battle truth but no battle runner is alive.",
                {
                    "activeBattleCount": active_battle_count,
                    "battleRunnerProcessCount": battle_runner_count,
                    "healthBlockers": blockers,
                    "runtimeTruthDisposition": runtime_truth,
                },
                "archive/adopt stale active_battles.json only through a finite runtime lease before starting a new supervisor",
            )
        )
    runtime_draining_after_supervisor_completion = (
        supervisor_completed_max_cycles
        and runtime_ready
        and not runtime_idle
        and not duplicate_runners
    )
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

    if supervisor_completed_max_cycles:
        issues.append(
            issue(
                "fouler-supervisor-max-cycles-complete",
                "RELIABILITY_BLOCKER",
                "The bounded battle supervisor completed its finite cycle budget and stopped.",
                {
                    "completedLearningCycles": supervisor.get("completedLearningCycles"),
                    "completedAt": supervisor.get("completedAt"),
                    "lastHeartbeatAt": supervisor.get("lastHeartbeatAt"),
                    "runtimeReady": runtime_ready,
                    "activeBattleCount": active_battle_count,
                    "battleRunnerProcessCount": battle_runner_count,
                    "runtimeDrainingAfterSupervisorCompletion": runtime_draining_after_supervisor_completion,
                },
                "HERMES should start a new bounded supervisor proof window; the wrapper must adopt any live runner without starting a duplicate",
            )
        )

    queue_class = discord_queue.get("backlogClassification") if isinstance(discord_queue.get("backlogClassification"), dict) else {}
    placeholder_counts = discord_queue.get("pendingPlaceholderFieldCounts") or {}
    if queue_class.get("blocking") or placeholder_counts or int(discord_queue.get("deliveryFailures") or 0) > 0:
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
                    "source": discord_queue_source,
                },
                "repair report generation before trusting Discord as operator proof",
            )
        )

    trend = recent_result_summary(battles, window=20)
    account = account_from_runtime_truth(lease, health)
    lease_battles, battle_filter = filter_battles_for_lease(battles, account=account, lease=lease)
    starts_at = lease_start_time(lease)
    if starts_at and battles and not lease_battles:
        latest = sorted_battles(battles)[-1]
        issues.append(
            issue(
                "fouler-battle-proof-stale-for-lease",
                "RELIABILITY_BLOCKER",
                "Fouler battle statistics predate the current runtime lease.",
                {
                    "runtimeLeaseAccount": account,
                    "leaseStartsAt": starts_at.isoformat(),
                    "latestBattleAt": latest.get("timestamp") or latest.get("ended_at") or latest.get("created_at"),
                    "latestBattleId": latest.get("battle_id") or latest.get("battleId") or latest.get("battle_tag") or latest.get("id"),
                    "battleFilter": battle_filter,
                },
                "start a fresh bounded battle proof window or stop surfacing stale pre-lease battle stats",
            )
        )
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

    active = lease_active(lease, now=now)
    return {
        "issues": issues,
        "runtimeIdle": runtime_idle,
        "runtimeReady": runtime_ready,
        "duplicateRunners": duplicate_runners,
        "activeBattleTruthStale": active_battle_truth_stale,
        "stopFilePresent": SUPERVISOR_STOP_FILE.exists(),
        "runtimeLeaseActive": active,
        "supervisorState": supervisor_state,
        "supervisorCompletedMaxCycles": supervisor_completed_max_cycles,
        "runtimeDrainingAfterSupervisorCompletion": runtime_draining_after_supervisor_completion,
        "discordQueueSource": discord_queue_source,
        "recentResults": trend,
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
    ]
    if args.auto_improve:
        command.append("-AutoImprove")
    return run_command(command, timeout=90)


def maybe_repair_runtime(args: argparse.Namespace, classification: dict[str, Any], lease: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not args.repair_runtime:
        return actions
    needs_supervisor_window = bool(
        classification.get("runtimeIdle")
        or classification.get("supervisorCompletedMaxCycles")
    )
    if not needs_supervisor_window:
        return actions
    if classification.get("duplicateRunners"):
        actions.append({"action": "repair-skipped", "reason": "duplicate runners require manual drain/adopt"})
        return actions
    if classification.get("activeBattleTruthStale"):
        actions.append({"action": "repair-skipped", "reason": "stale active battle truth must be archived/adopted before restart"})
        return actions
    safety_issue_ids = {
        str(item.get("id"))
        for item in (classification.get("issues") or [])
        if str(item.get("id")) in {"fouler-loss-streak", "fouler-low-recent-win-rate"}
    }
    if safety_issue_ids:
        actions.append(
            {
                "action": "repair-skipped",
                "reason": "recent-results safety valve is open",
                "issueIds": sorted(safety_issue_ids),
            }
        )
        return actions
    if classification.get("stopFilePresent"):
        actions.append({"action": "repair-skipped", "reason": "supervisor stop file is present"})
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
        refresh = refresh_health(skip_http=args.skip_http)
        refresh["action"] = "refresh-health"
        actions.append(refresh)
    task = supervisor_task_status()
    task["action"] = "supervisor-task-status"
    actions.append(task)

    health = load_json(HEALTH_FILE, {})
    discord_report = load_json(DISCORD_REPORTING_FILE, {})
    supervisor = load_json(SUPERVISOR_STATUS_FILE, {})
    lease = load_json(RUNTIME_LEASE_FILE, {})
    battles = read_battles()
    elo_proof = build_latest_elo_proof(health=health, lease=lease, battles=battles)
    classification = classify_mission(
        health=health,
        discord_report=discord_report,
        supervisor=supervisor,
        lease=lease,
        battles=battles,
        max_health_age_seconds=args.max_health_age_seconds,
        loss_streak_threshold=args.loss_streak_threshold,
        low_win_rate_threshold=args.low_win_rate_threshold,
    )

    repair_actions = maybe_repair_runtime(args, classification, lease)
    actions.extend(repair_actions)
    if repair_actions and args.refresh_health_after_repair:
        refresh = refresh_health(skip_http=args.skip_http)
        refresh["action"] = "refresh-health-after-repair"
        actions.append(refresh)
        health = load_json(HEALTH_FILE, {})
        discord_report = load_json(DISCORD_REPORTING_FILE, {})
        supervisor = load_json(SUPERVISOR_STATUS_FILE, {})
        lease = load_json(RUNTIME_LEASE_FILE, {})
        battles = read_battles()
        elo_proof = build_latest_elo_proof(health=health, lease=lease, battles=battles)
        classification = classify_mission(
            health=health,
            discord_report=discord_report,
            supervisor=supervisor,
            lease=lease,
            battles=battles,
            max_health_age_seconds=args.max_health_age_seconds,
            loss_streak_threshold=args.loss_streak_threshold,
            low_win_rate_threshold=args.low_win_rate_threshold,
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
    payload = {
        "schemaVersion": "fouler-play-mission-monitor/v1",
        "projectId": "fouler-play",
        "checkedAt": iso_now(),
        "healthy": not classification["issues"],
        "status": "healthy" if not classification["issues"] else "action-required",
        "issues": classification["issues"],
        "latestEloProof": {
            "account": (elo_proof.get("account") or {}).get("showdownUserId"),
            "latestBattleId": (elo_proof.get("summary") or {}).get("latestBattleId"),
            "latestBattleAt": (elo_proof.get("summary") or {}).get("latestBattleAt"),
            "finalRating": (elo_proof.get("summary") or {}).get("finalRating"),
            "recent5": (elo_proof.get("summary") or {}).get("recent5"),
            "recent20": (elo_proof.get("summary") or {}).get("recent20"),
        },
        "ticketsWritten": tickets,
        "ticketsCleared": tickets_cleared,
        "discordAlert": discord_alert,
        "classification": {key: value for key, value in classification.items() if key != "issues"},
        "actions": actions,
        "paths": {
            "health": str(HEALTH_FILE.relative_to(ROOT)),
            "supervisorStatus": str(SUPERVISOR_STATUS_FILE.relative_to(ROOT)),
            "runtimeLease": str(RUNTIME_LEASE_FILE.relative_to(ROOT)),
            "latestEloProof": str(ELO_PROOF_FILE.relative_to(ROOT)),
            "tickets": str(TICKET_DIR.relative_to(ROOT)),
        },
        "secretValuesPrinted": False,
    }
    if args.write:
        write_json(ELO_PROOF_FILE, elo_proof)
        write_json(MISSION_MONITOR_FILE, payload)
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fouler mission monitor for HERMES/DEKU")
    parser.add_argument("--write", action="store_true", help="write truth and ticket files")
    parser.add_argument("--refresh-health", action="store_true", default=True)
    parser.add_argument("--skip-http", action="store_true", help="skip HTTP checks in devstream_health")
    parser.add_argument("--repair-runtime", action="store_true", help="start a bounded supervisor when runtime is safely idle")
    parser.add_argument("--renew-lease", action="store_true", help="write a fresh finite runtime lease before supervisor start")
    parser.add_argument("--queue-alerts", action="store_true", help="queue mission alerts through Discord event queue")
    parser.add_argument("--refresh-health-after-repair", action="store_true", default=True)
    parser.add_argument("--run-count", type=int, default=30)
    parser.add_argument("--max-cycles", type=int, default=12)
    parser.add_argument("--max-concurrent-battles", type=int, default=1)
    parser.add_argument("--queue-timeout-seconds", type=int, default=180)
    parser.add_argument("--sleep-seconds", type=int, default=20)
    parser.add_argument("--lease-minutes", type=int, default=720)
    parser.add_argument("--auto-improve", action="store_true", default=False)
    parser.add_argument("--max-health-age-seconds", type=int, default=300)
    parser.add_argument("--loss-streak-threshold", type=int, default=5)
    parser.add_argument("--low-win-rate-threshold", type=float, default=0.45)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_payload(args)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("healthy") else 2


if __name__ == "__main__":
    raise SystemExit(main())
