#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devstream_runtime_checks import recent_showdown_credential_failure

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.runtime_paths import (  # noqa: E402
    RuntimePathError,
    is_production_runtime,
    paths_overlap,
    resolve_runtime_paths,
)
from streaming import state_store  # noqa: E402
from scripts.devstream_runtime_lease import (  # noqa: E402
    RUNTIME_LEASE_PATH_ENV,
    lease_environment,
    runtime_lease_path,
    validate_runtime_lease,
)
from infrastructure.runtime_lease_client import (  # noqa: E402
    ProtocolError,
    RUNTIME_RESERVATION_PURPOSE,
    broker_request_payload,
    require_exact_reservation_binding,
    request_with_retry,
    response_error_text,
)

DEFAULT_RUN_COUNT = 1
DEFAULT_MAX_CONCURRENT = 3
PILOT_SEARCH_PARALLELISM = 2
RUN_COUNT_CAP_ENV = "FOULER_DEVSTREAM_RUN_COUNT_CAP"
DEFAULT_RUN_COUNT_CAP = 30
AUTO_IMPROVE_MAX_CYCLES_ENV = "FOULER_AUTO_IMPROVE_MAX_CYCLES"
DEFAULT_AUTO_IMPROVE_MAX_CYCLES = 1
DEFAULT_IMPROVE_TIMEOUT_SECONDS = 18000
CHILD_LOG_MAX_BYTES_ENV = "FOULER_DEVSTREAM_CHILD_LOG_MAX_BYTES"
DEFAULT_CHILD_LOG_MAX_BYTES = 64 * 1024 * 1024
JUDGMENT_BATTLE_COUNT = 30
RUNTIME_LEASE_CONSUMPTION_ROOT_OVERRIDE: Path | None = None
RUNTIME_LEASE_RESERVATION_ID_ENV = "FOULER_RUNTIME_LEASE_RESERVATION_ID"
RUNTIME_SUPERVISOR_INSTANCE_ID_ENV = "FOULER_RUNTIME_SUPERVISOR_INSTANCE_ID"
RUNTIME_RESERVATION_PURPOSE_ENV = "FOULER_RUNTIME_RESERVATION_PURPOSE"
RUNTIME_RESERVATION_KIND_ENV = "FOULER_RUNTIME_RESERVATION_KIND"
RUNTIME_RESERVATION_BATTLE_COUNT_ENV = "FOULER_RUNTIME_RESERVATION_BATTLE_COUNT"
RUNTIME_RESERVATION_CYCLE_COUNT_ENV = "FOULER_RUNTIME_RESERVATION_CYCLE_COUNT"
RUNTIME_RESERVATION_CONCURRENCY_ENV = "FOULER_RUNTIME_RESERVATION_MAX_CONCURRENT_BATTLES"
RUNTIME_SUPERVISOR_PID_ENV = "FOULER_RUNTIME_SUPERVISOR_PID"
RUNTIME_SUPERVISOR_CREATION_FILETIME_ENV = (
    "FOULER_RUNTIME_SUPERVISOR_CREATION_FILETIME"
)
RUNTIME_LAUNCH_NONCE_ENV = "FOULER_RUNTIME_LAUNCH_NONCE"
_RUNTIME_PATHS = resolve_runtime_paths(ROOT)
RUNTIME_STATE_ROOT = _RUNTIME_PATHS.state_root
RUNTIME_TRUTH_DIR = RUNTIME_STATE_ROOT / "truth"
RUNTIME_LOG_ROOT = _RUNTIME_PATHS.log_root
PID_DIR = RUNTIME_STATE_ROOT / "pids"
OBS_PID_FILE = PID_DIR / "devstream_obs_http.pid"
BATTLE_PID_FILE = PID_DIR / "devstream_battle_session.pid"
DRAIN_FILE = PID_DIR / "drain.request"
SUPERVISOR_PID_FILE = PID_DIR / "devstream_battle_supervisor.pid"
SUPERVISOR_STOP_FILE = PID_DIR / "supervisor.stop"
IMPROVE_AGENT_LOCK_FILE = PID_DIR / "improve-agent.lock"
IMPROVE_AGENT_RECOVERY_BLOCK_FILE = PID_DIR / "improve-agent-recovery-block.json"
SUPERVISOR_STATUS_FILE = RUNTIME_TRUTH_DIR / "supervisor-status.json"
IMPROVE_RUNTIME_LEASE_PATH_ENV = "FOULER_IMPROVE_RUNTIME_LEASE_PATH"
STALE_BATTLE_BACKUP_DIR = RUNTIME_TRUTH_DIR / "stale-active-battles-backups"
STALE_STREAM_STATUS_BACKUP_DIR = RUNTIME_TRUTH_DIR / "stale-stream-status-backups"
if os.name == "nt":
    _DEFAULT_SECRET_ENV_FILE = (
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "HERMES"
        / "secrets"
        / "fouler.env"
    )
else:
    _DEFAULT_SECRET_ENV_FILE = Path.home() / ".config" / "deku-devstream" / "secrets" / "fouler.env"
SECRET_ENV_FILE = Path(
    os.getenv("FOULER_ENV_FILE", str(_DEFAULT_SECRET_ENV_FILE))
).expanduser().absolute()
ENV_FILES = [SECRET_ENV_FILE, ROOT / ".env", ROOT / ".env.deku"]
if os.name == "nt":
    _DEFAULT_ACCOUNT_SEASON_AUTHORITY_FILE = (
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "HERMES"
        / "authority"
        / "fouler"
        / "account-season.json"
    )
else:
    _DEFAULT_ACCOUNT_SEASON_AUTHORITY_FILE = (
        Path.home()
        / ".config"
        / "deku-devstream"
        / "authority"
        / "fouler"
        / "account-season.json"
    )
ACCOUNT_SEASON_AUTHORITY_FILE = Path(
    os.getenv(
        "FOULER_ACCOUNT_SEASON_PATH",
        str(_DEFAULT_ACCOUNT_SEASON_AUTHORITY_FILE),
    )
).expanduser().absolute()
BOT_LOCK_PID_FILE = PID_DIR / "bot.pid"
STREAM_STATUS_FILE = RUNTIME_STATE_ROOT / "stream_status.json"
BATTLE_STATS_FILE = RUNTIME_STATE_ROOT / "battle_stats.json"
# This writable runtime copy is display/reporting state only. It never authorizes a launch.
ACCOUNT_SEASON_FILE = RUNTIME_TRUTH_DIR / "account-season.json"
BATTLE_LOG_DIR = RUNTIME_LOG_ROOT
REPLAY_ANALYSIS_DIR = RUNTIME_STATE_ROOT / "replay_analysis"
TEAMS_DIR = ROOT / "teams"
STALE_ACTIVE_TRUTH_SECONDS = 1800
STALE_STREAM_TRUTH_SECONDS = 21600
IDLE_RUNNER_STALE_SECONDS = int(os.getenv("FP_IDLE_RUNNER_STALE_SECONDS", "180"))
RESULT_PERSISTENCE_GRACE_SECONDS = int(os.getenv("FP_RESULT_PERSISTENCE_GRACE_SECONDS", "90"))
ACTIVE_STREAM_STATUSES = {"active", "battling", "running", "searching"}
STALE_TRUTH_CLEANUP_PURPOSE = "devstream-stale-truth-cleanup"
STALE_TRUTH_CLEANUP_DRY_RUN_PURPOSE = f"{STALE_TRUTH_CLEANUP_PURPOSE}-dry-run"
FINITE_RUNTIME_LEASE_PRECONDITIONS = [
    "a current proof-window runtime lease validates for the requested HERMES action",
    "the lease names projectId=fouler-play, runtime machine, Showdown account, replay behavior, and expiry",
    "requested --run-count and --max-concurrent-battles are positive finite bounds within the lease",
    "archive/adopt/clear actions run only through devstream_session.py cleanup-stale-truth/start/stop --execute after lease validation",
]
AUTO_IMPROVE_SENTINEL = "FOULER_PLAY_ENABLE_AUTO_IMPROVE"
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
RUNTIME_PROVENANCE_ENV_NAMES = (
    "FOULER_SOURCE_COMMIT",
    "FOULER_SESSION_ID",
    "FOULER_CHANGE_ID",
    "FOULER_DEPLOYMENT_ID",
    "FOULER_RUNTIME_LEASE_ID",
    "FOULER_RUNTIME_AUTHORIZATION_SHA256",
    "FOULER_SOURCE_TREE",
    "FOULER_RUNTIME_MANIFEST_DIGEST",
    "FOULER_DEPLOYMENT_RECEIPT_SHA256",
    "FOULER_DEPLOYMENT_RECEIPT_PATH",
)
IMPROVE_AUTHORITY_ENV_NAMES = {
    "FOULER_SOURCE_COMMIT": "FOULER_IMPROVE_SOURCE_COMMIT",
    "FOULER_SESSION_ID": "FOULER_IMPROVE_SESSION_ID",
    "FOULER_CHANGE_ID": "FOULER_IMPROVE_CHANGE_ID",
    "FOULER_DEPLOYMENT_ID": "FOULER_IMPROVE_DEPLOYMENT_ID",
    "FOULER_RUNTIME_LEASE_ID": "FOULER_IMPROVE_RUNTIME_LEASE_ID",
    "FOULER_RUNTIME_AUTHORIZATION_SHA256": "FOULER_IMPROVE_RUNTIME_AUTHORIZATION_SHA256",
    "FOULER_SOURCE_TREE": "FOULER_IMPROVE_SOURCE_TREE",
    "FOULER_RUNTIME_MANIFEST_DIGEST": "FOULER_IMPROVE_RUNTIME_MANIFEST_DIGEST",
    "FOULER_DEPLOYMENT_RECEIPT_SHA256": "FOULER_IMPROVE_DEPLOYMENT_RECEIPT_SHA256",
    "FOULER_DEPLOYMENT_RECEIPT_PATH": "FOULER_IMPROVE_DEPLOYMENT_RECEIPT_PATH",
    RUNTIME_LEASE_PATH_ENV: IMPROVE_RUNTIME_LEASE_PATH_ENV,
}
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
ACCOUNT_AUTHORITY_FILES = [ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "TASKBOARD.md"]
ACCOUNT_AUTHORITY_PATTERNS = (
    ("current PS_USERNAME", re.compile(r"Current:\s*`?PS_USERNAME=([A-Za-z0-9_.-]+)`?", re.IGNORECASE)),
    ("current SHOWDOWN_USER_ID", re.compile(r"Current:\s*`?SHOWDOWN_USER_ID=([A-Za-z0-9_.-]+)`?", re.IGNORECASE)),
    (
        "current live bot account prose",
        re.compile(r"\blive\s+bot\s+account\b.*?\bcurrently\s+\**[\"']?([A-Za-z0-9_.-]+)[\"']?\**", re.IGNORECASE),
    ),
)


def runtime_state_root(env: dict[str, str] | None = None) -> Path:
    environment = os.environ if env is None else env
    if not is_production_runtime(ROOT, environ=environment) and not any(
        str(environment.get(name) or "").strip()
        for name in (
            "FOULER_RUNTIME_STATE_ROOT",
            "FOULER_RUNTIME_LOG_ROOT",
            "FOULER_RUNTIME_CACHE_ROOT",
            "FOULER_RUNTIME_TEMP_ROOT",
        )
    ):
        return ROOT
    return resolve_runtime_paths(ROOT, environ=environment).state_root


def runtime_log_root(env: dict[str, str] | None = None) -> Path:
    environment = os.environ if env is None else env
    if not is_production_runtime(ROOT, environ=environment) and not any(
        str(environment.get(name) or "").strip()
        for name in (
            "FOULER_RUNTIME_STATE_ROOT",
            "FOULER_RUNTIME_LOG_ROOT",
            "FOULER_RUNTIME_CACHE_ROOT",
            "FOULER_RUNTIME_TEMP_ROOT",
        )
    ):
        return ROOT / "logs"
    return resolve_runtime_paths(ROOT, environ=environment).log_root


def env_flag_enabled(env: dict[str, str], name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in TRUTHY_ENV_VALUES


def supervisor_auto_improve_enabled(args: argparse.Namespace, env: dict[str, str] | None = None) -> tuple[bool, str]:
    env = env if env is not None else load_env_files()
    requested_by: list[str] = []
    if getattr(args, "enable_auto_improve", False):
        requested_by.append("--enable-auto-improve")
    if env_flag_enabled(env, AUTO_IMPROVE_SENTINEL):
        requested_by.append(f"{AUTO_IMPROVE_SENTINEL}=1")
    if getattr(args, "skip_improve", False):
        requested_by.append("--skip-improve")
    request_text = ", ".join(requested_by) if requested_by else "not requested"
    return (
        False,
        "immutable runtime improvement is disabled and delegated to the external "
        f"DEKU control plane ({request_text})",
    )


def positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def improve_eval_battle_count(env: dict[str, str]) -> int:
    requested = positive_int(env.get("IMPROVE_AGENT_EVAL_BATTLES"), 60)
    return requested if requested >= 60 and requested % 12 == 0 else 60


def minimum_improve_timeout_seconds(env: dict[str, str]) -> int:
    battles = improve_eval_battle_count(env)
    try:
        per_battle = float(env.get("IMPROVE_AGENT_EVAL_PER_BATTLE_TIMEOUT", "240"))
    except (TypeError, ValueError):
        per_battle = 240.0
    per_battle = per_battle if per_battle > 0 else 240.0
    # Match the child gate's full matrix timeout and leave room for tests,
    # candidate generation, worktree setup, and cleanup.
    return int(battles * per_battle + 2400)


def effective_run_count(run_count: object, env: dict[str, str] | None = None) -> int:
    env = env if env is not None else os.environ
    requested = positive_int(run_count, DEFAULT_RUN_COUNT)
    cap = positive_int(env.get(RUN_COUNT_CAP_ENV), DEFAULT_RUN_COUNT_CAP)
    return max(1, min(requested, cap))


def supervisor_cycle_limit(args: argparse.Namespace, env: dict[str, str] | None = None) -> tuple[int, str]:
    requested = positive_int(getattr(args, "max_cycles", 0), 0)
    if requested > 0:
        return requested, "--max-cycles"
    enabled, reason = supervisor_auto_improve_enabled(args, env)
    if not enabled:
        return 0, "unbounded supervisor without auto-improve"
    env = env if env is not None else load_env_files()
    limit = positive_int(env.get(AUTO_IMPROVE_MAX_CYCLES_ENV), DEFAULT_AUTO_IMPROVE_MAX_CYCLES)
    return limit, f"auto-improve lease via {reason}"


def runtime_lease_guard(
    *,
    purpose: str,
    args: argparse.Namespace,
    env: dict[str, str],
    run_count: int,
    max_cycles: int | None = None,
    require_max_cycles: bool = False,
) -> dict[str, Any]:
    requires_deployment_identity = purpose in {
        "devstream-start",
        "devstream-start-continuous",
        "devstream-supervise",
    }
    return validate_runtime_lease(
        purpose=purpose,
        lease_path=getattr(args, "runtime_lease", None),
        requested_run_count=run_count,
        requested_max_cycles=max_cycles,
        requested_max_concurrent_battles=getattr(args, "max_concurrent_battles", None),
        requested_account=env_value(env, "PS_USERNAME", "SHOWDOWN_USER_ID") or None,
        requested_replay_behavior=env_value(env, "SAVE_REPLAY", default="always"),
        require_run_count=True,
        require_max_cycles=require_max_cycles,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        require_deployment_receipt=requires_deployment_identity,
        verify_deployment_checkout=requires_deployment_identity,
    )


def cleanup_runtime_lease_guard(*, purpose: str, args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    return validate_runtime_lease(
        purpose=purpose,
        lease_path=getattr(args, "runtime_lease", None),
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        requested_account=env_value(env, "PS_USERNAME", "SHOWDOWN_USER_ID") or None,
        requested_replay_behavior=env_value(env, "SAVE_REPLAY", default="always"),
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
    )


def runtime_lease_blocked_message(guard: dict[str, Any]) -> str:
    blockers = guard.get("blockers") if isinstance(guard.get("blockers"), list) else []
    if blockers:
        return "runtime lease/proof window required: " + "; ".join(str(item) for item in blockers)
    return "runtime lease/proof window required"


def child_log_max_bytes(env: dict[str, str] | None = None) -> int:
    env = env if env is not None else os.environ
    return positive_int(env.get(CHILD_LOG_MAX_BYTES_ENV), DEFAULT_CHILD_LOG_MAX_BYTES)


def runtime_python() -> str:
    """Prefer the repo-local virtualenv expected by the devstream contract."""
    if os.name == "nt":
        candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def prepare_runtime_env(env: dict[str, str]) -> dict[str, str]:
    if os.name == "nt":
        bin_dir = ROOT / ".venv" / "Scripts"
    else:
        bin_dir = ROOT / ".venv" / "bin"
    if bin_dir.exists():
        env = dict(env)
        env.setdefault("VIRTUAL_ENV", str(ROOT / ".venv"))
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


def current_source_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip().lower() if result.returncode == 0 else ""


def apply_runtime_process_identity(
    env: dict[str, str],
    lease_guard: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Bind every child to one verified source/deployment/lease/session identity."""
    updated = dict(env)
    blockers: list[str] = []
    source_commit = current_source_commit()
    if not GIT_COMMIT_RE.fullmatch(source_commit):
        blockers.append("current Git source commit is unavailable or malformed")
    try:
        approved = lease_environment(lease_guard)
    except (OSError, ValueError) as exc:
        approved = {}
        blockers.append(f"runtime lease process identity is unavailable: {exc}")
    for name, approved_value in approved.items():
        configured = str(updated.get(name) or "").strip()
        if configured and configured != approved_value:
            blockers.append(f"{name} does not match the validated runtime lease")
    if approved.get("FOULER_SOURCE_COMMIT") != source_commit:
        blockers.append("runtime lease sourceCommit does not match the current checkout HEAD")
    if not blockers:
        updated.update(approved)
    public = {
        "schemaVersion": "fouler-runtime-process-identity/v1",
        "ok": not blockers,
        "sourceCommit": source_commit or None,
        "deploymentId": approved.get("FOULER_DEPLOYMENT_ID") or None,
        "runtimeLeaseId": approved.get("FOULER_RUNTIME_LEASE_ID") or None,
        "runtimeAuthorizationSha256": approved.get("FOULER_RUNTIME_AUTHORIZATION_SHA256") or None,
        "sessionId": approved.get("FOULER_SESSION_ID") or None,
        "changeId": approved.get("FOULER_CHANGE_ID") or None,
        "sourceTree": approved.get("FOULER_SOURCE_TREE") or None,
        "runtimeManifestDigest": approved.get("FOULER_RUNTIME_MANIFEST_DIGEST") or None,
        "deploymentReceiptSha256": approved.get("FOULER_DEPLOYMENT_RECEIPT_SHA256") or None,
        "deploymentReceiptPath": approved.get("FOULER_DEPLOYMENT_RECEIPT_PATH") or None,
        "runtimeLeasePath": approved.get(RUNTIME_LEASE_PATH_ENV) or None,
        "blockers": blockers,
        "envNamesApplied": list(RUNTIME_PROVENANCE_ENV_NAMES) if not blockers else [],
    }
    return updated, public


def resolved_runtime_lease_path(lease_guard: dict[str, Any]) -> str:
    """Return the exact absolute lease path approved by validation."""
    approved = lease_environment(lease_guard)
    return approved[RUNTIME_LEASE_PATH_ENV]


def improve_authority_environment(lease_guard: dict[str, Any]) -> dict[str, str]:
    """Namespace improve authority without replacing the live battle identity."""
    approved = lease_environment(lease_guard)
    return {
        improve_name: str(approved.get(runtime_name) or "")
        for runtime_name, improve_name in IMPROVE_AUTHORITY_ENV_NAMES.items()
    }


def _broker_lease_identity(lease_guard: dict[str, Any]) -> tuple[str, str, list[str]]:
    summary = lease_guard.get("lease") if isinstance(lease_guard.get("lease"), dict) else {}
    lease_id = str(summary.get("id") or "").strip()
    authorization_digest = str(summary.get("authorizationSha256") or "").strip().lower()
    blockers: list[str] = []
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}", lease_id):
        blockers.append("validated runtime lease id is missing or malformed")
    if not re.fullmatch(r"[0-9a-f]{64}", authorization_digest):
        blockers.append("validated runtime authorization digest is missing or malformed")
    return lease_id, authorization_digest, blockers


def runtime_lease_consumption_root(env: dict[str, str] | None = None) -> Path:
    """Return the administrator/service-only broker store root."""

    del env
    if RUNTIME_LEASE_CONSUMPTION_ROOT_OVERRIDE is not None:
        return RUNTIME_LEASE_CONSUMPTION_ROOT_OVERRIDE.expanduser().resolve()
    if os.name == "nt":
        return Path(r"C:\ProgramData\HERMES-LeaseBroker\fouler")
    return (Path.home() / ".local" / "state" / "hermes-lease-broker" / "fouler").resolve()


def initialize_runtime_lease_consumption(
    lease_guard: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    del env
    lease_id, authorization_digest, blockers = _broker_lease_identity(lease_guard)
    return {
        "ok": not blockers,
        "blockers": blockers,
        "authority": "windows-named-pipe-lease-broker",
        "stateRoot": str(runtime_lease_consumption_root()),
        "runtimeLeaseId": lease_id or None,
        "runtimeAuthorizationSha256": authorization_digest or None,
        "exhausted": False,
        "note": "capacity is checked atomically when the supervisor reserves a cycle",
    }


def reserve_runtime_lease_consumption(
    lease_guard: dict[str, Any],
    *,
    run_count: int,
    cycle_index: int,
    supervisor_instance_id: str,
    max_concurrent_battles: int = DEFAULT_MAX_CONCURRENT,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    del env
    lease_id, authorization_digest, blockers = _broker_lease_identity(lease_guard)
    result: dict[str, Any] = {
        "ok": False,
        "reserved": False,
        "authority": "windows-named-pipe-lease-broker",
        "blockers": blockers,
    }
    requested = positive_int(run_count, 0)
    concurrency = positive_int(max_concurrent_battles, 0)
    if requested <= 0:
        blockers.append("runtime broker reservation run count must be positive")
    if concurrency <= 0 or concurrency > 3:
        blockers.append("runtime broker reservation concurrency must be between one and three")
    if not supervisor_instance_id:
        blockers.append("runtime broker reservation supervisor identity is missing")
    if blockers:
        return result
    request = broker_request_payload(
        "reserve-runtime",
        authorization_digest=authorization_digest,
        lease_id=lease_id,
        request_id=f"reserve-runtime-{cycle_index}-{uuid.uuid4().hex}",
        purpose=RUNTIME_RESERVATION_PURPOSE,
        kind="runtime",
        battleCount=requested,
        cycleCount=1,
        maxConcurrentBattles=concurrency,
        supervisorInstanceId=supervisor_instance_id,
    )
    try:
        response = request_with_retry(request)
    except (OSError, PermissionError, ValueError) as exc:
        result["blockers"] = [f"lease broker request failed closed: {exc}"]
        return result
    result["brokerRequestId"] = response.get("requestId")
    result["brokerAction"] = response.get("action")
    if not response.get("ok"):
        error = response.get("error") if isinstance(response.get("error"), dict) else {}
        error_code = str(error.get("code") or "")
        if error_code in {"run_bound_exhausted", "cycle_bound_exhausted"}:
            result.update({"ok": True, "exhausted": True, "blockers": []})
            return result
        result["blockers"] = [response_error_text(response)]
        return result
    broker_reservation = response.get("result") if isinstance(response.get("result"), dict) else {}
    reservation_id = str(broker_reservation.get("reservationId") or "")
    if not re.fullmatch(r"res-[0-9a-f]{32}", reservation_id):
        result["blockers"] = ["lease broker returned a malformed reservation id"]
        return result
    expected_static = {
        "kind": "runtime",
        "purpose": RUNTIME_RESERVATION_PURPOSE,
        "battleCount": requested,
        "cycleCount": 1,
        "maxConcurrentBattles": concurrency,
        "supervisorInstanceId": supervisor_instance_id,
    }
    mismatches = [
        name
        for name, expected in expected_static.items()
        if broker_reservation.get(name) != expected
    ]
    supervisor_pid = broker_reservation.get("supervisorProcessId")
    supervisor_creation = broker_reservation.get("supervisorProcessCreationFiletime")
    launch_nonce = str(broker_reservation.get("launchNonce") or "").lower()
    if supervisor_pid != os.getpid():
        mismatches.append("supervisorProcessId")
    if type(supervisor_creation) is not int or supervisor_creation <= 0:
        mismatches.append("supervisorProcessCreationFiletime")
    if not re.fullmatch(r"[0-9a-f]{64}", launch_nonce):
        mismatches.append("launchNonce")
    if mismatches:
        result["blockers"] = [
            "lease broker returned a mismatched reservation binding: "
            + ", ".join(sorted(set(mismatches)))
        ]
        return result
    binding = {
        "reservationId": reservation_id,
        **expected_static,
        "supervisorProcessId": supervisor_pid,
        "supervisorProcessCreationFiletime": supervisor_creation,
        "launchNonce": launch_nonce,
    }
    try:
        require_exact_reservation_binding(broker_reservation, binding)
    except (ProtocolError, ValueError) as exc:
        result["blockers"] = [f"lease broker reservation binding failed closed: {exc}"]
        return result
    result.update(
        {
            "ok": True,
            "reserved": True,
            "exhausted": False,
            "blockers": [],
            "reservation": {
                **broker_reservation,
                "runtimeLeaseId": lease_id,
                "authorizationDigest": authorization_digest,
                "cycleIndex": cycle_index,
                "runCount": requested,
            },
        }
    )
    return result


def validate_runtime_lease_reservation(
    lease_guard: dict[str, Any],
    *,
    reservation_id: str,
    run_count: int,
    max_concurrent_battles: int,
    supervisor_instance_id: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    _lease_id, _authorization_digest, blockers = _broker_lease_identity(lease_guard)
    if not re.fullmatch(r"res-[0-9a-f]{32}", str(reservation_id or "")):
        blockers.append("broker reservation id is missing or malformed")
    if positive_int(run_count, 0) <= 0:
        blockers.append("broker reservation run count is invalid")
    if not supervisor_instance_id:
        blockers.append("broker reservation supervisor identity is missing")
    binding = runtime_reservation_binding_from_env(env or os.environ)
    if binding.get("reservationId") != reservation_id:
        blockers.append("broker reservation environment id does not match")
    if binding.get("purpose") != RUNTIME_RESERVATION_PURPOSE:
        blockers.append("broker reservation purpose is invalid")
    if binding.get("kind") != "runtime":
        blockers.append("broker reservation kind is invalid")
    if binding.get("battleCount") != run_count:
        blockers.append("broker reservation battle count does not match")
    if binding.get("cycleCount") != 1:
        blockers.append("broker reservation cycle count must equal one")
    if binding.get("maxConcurrentBattles") != max_concurrent_battles:
        blockers.append("broker reservation concurrency does not match")
    if binding.get("supervisorInstanceId") != supervisor_instance_id:
        blockers.append("broker reservation supervisor instance does not match")
    if type(binding.get("supervisorProcessId")) is not int:
        blockers.append("broker reservation supervisor PID is invalid")
    if type(binding.get("supervisorProcessCreationFiletime")) is not int:
        blockers.append("broker reservation supervisor creation identity is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("launchNonce") or "")):
        blockers.append("broker reservation launch nonce is invalid")
    return {
        "ok": not blockers,
        "valid": not blockers,
        "blockers": blockers,
        "authority": "windows-named-pipe-lease-broker",
        "reservation": public_runtime_reservation(binding),
    }


def _positive_env_integer(env: dict[str, str], name: str) -> int | None:
    try:
        parsed = int(str(env.get(name) or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def runtime_reservation_binding_from_env(env: dict[str, str]) -> dict[str, Any]:
    return {
        "reservationId": str(env.get(RUNTIME_LEASE_RESERVATION_ID_ENV) or "").strip(),
        "kind": str(env.get(RUNTIME_RESERVATION_KIND_ENV) or "").strip(),
        "purpose": str(env.get(RUNTIME_RESERVATION_PURPOSE_ENV) or "").strip(),
        "battleCount": _positive_env_integer(env, RUNTIME_RESERVATION_BATTLE_COUNT_ENV),
        "cycleCount": _positive_env_integer(env, RUNTIME_RESERVATION_CYCLE_COUNT_ENV),
        "maxConcurrentBattles": _positive_env_integer(
            env, RUNTIME_RESERVATION_CONCURRENCY_ENV
        ),
        "supervisorProcessId": _positive_env_integer(env, RUNTIME_SUPERVISOR_PID_ENV),
        "supervisorProcessCreationFiletime": _positive_env_integer(
            env, RUNTIME_SUPERVISOR_CREATION_FILETIME_ENV
        ),
        "supervisorInstanceId": str(
            env.get(RUNTIME_SUPERVISOR_INSTANCE_ID_ENV) or ""
        ).strip(),
        "launchNonce": str(env.get(RUNTIME_LAUNCH_NONCE_ENV) or "").strip().lower(),
    }


def runtime_reservation_environment(reservation: dict[str, Any]) -> dict[str, str]:
    return {
        RUNTIME_LEASE_RESERVATION_ID_ENV: str(reservation["reservationId"]),
        RUNTIME_RESERVATION_KIND_ENV: str(reservation["kind"]),
        RUNTIME_RESERVATION_PURPOSE_ENV: str(reservation["purpose"]),
        RUNTIME_RESERVATION_BATTLE_COUNT_ENV: str(reservation["battleCount"]),
        RUNTIME_RESERVATION_CYCLE_COUNT_ENV: str(reservation["cycleCount"]),
        RUNTIME_RESERVATION_CONCURRENCY_ENV: str(
            reservation["maxConcurrentBattles"]
        ),
        RUNTIME_SUPERVISOR_PID_ENV: str(reservation["supervisorProcessId"]),
        RUNTIME_SUPERVISOR_CREATION_FILETIME_ENV: str(
            reservation["supervisorProcessCreationFiletime"]
        ),
        RUNTIME_SUPERVISOR_INSTANCE_ID_ENV: str(reservation["supervisorInstanceId"]),
        RUNTIME_LAUNCH_NONCE_ENV: str(reservation["launchNonce"]),
    }


def public_runtime_reservation(reservation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in reservation.items()
        if key not in {"launchNonce", "authorizationDigest"}
    } | {"launchNoncePresent": bool(reservation.get("launchNonce"))}


def runtime_reservation_status(
    lease_guard: dict[str, Any], reservation: dict[str, Any]
) -> dict[str, Any]:
    lease_id, authorization_digest, blockers = _broker_lease_identity(lease_guard)
    if blockers:
        return {"ok": False, "blockers": blockers}
    request = broker_request_payload(
        "status",
        authorization_digest=authorization_digest,
        lease_id=lease_id,
        lookupType="reservation",
        lookupId=str(reservation.get("reservationId") or ""),
    )
    try:
        response = request_with_retry(request)
    except (OSError, PermissionError, ValueError) as exc:
        return {"ok": False, "blockers": [f"lease broker status failed closed: {exc}"]}
    if not response.get("ok"):
        return {"ok": False, "blockers": [response_error_text(response)]}
    status = response.get("result") if isinstance(response.get("result"), dict) else {}
    if not status.get("found"):
        return {"ok": False, "blockers": ["lease broker reservation status is missing"]}
    compared_fields = (
        "reservationId",
        "kind",
        "purpose",
        "battleCount",
        "cycleCount",
        "maxConcurrentBattles",
        "supervisorProcessId",
        "supervisorProcessCreationFiletime",
        "supervisorInstanceId",
    )
    mismatches = [
        name for name in compared_fields if status.get(name) != reservation.get(name)
    ]
    if mismatches:
        return {
            "ok": False,
            "blockers": [
                "lease broker status binding mismatch: " + ", ".join(mismatches)
            ],
        }
    return {"ok": True, "blockers": [], "status": status}


def complete_runtime_lease_consumption(
    lease_guard: dict[str, Any],
    *,
    reservation: dict[str, Any],
    outcome: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    del env
    lease_id, authorization_digest, blockers = _broker_lease_identity(lease_guard)
    result: dict[str, Any] = {
        "ok": False,
        "completed": False,
        "blockers": blockers,
        "authority": "windows-named-pipe-lease-broker",
    }
    if outcome not in {"completed", "failed", "aborted"}:
        result["blockers"].append("runtime reservation outcome is invalid")
    if blockers:
        return result
    binding = {
        name: reservation.get(name)
        for name in (
            "reservationId",
            "kind",
            "purpose",
            "battleCount",
            "cycleCount",
            "maxConcurrentBattles",
            "supervisorProcessId",
            "supervisorProcessCreationFiletime",
            "supervisorInstanceId",
            "launchNonce",
        )
    }
    request = broker_request_payload(
        "complete",
        authorization_digest=authorization_digest,
        lease_id=lease_id,
        **binding,
        outcome=outcome,
    )
    try:
        response = request_with_retry(request)
    except (OSError, PermissionError, ValueError) as exc:
        result["blockers"] = [f"lease broker completion failed closed: {exc}"]
        return result
    if not response.get("ok"):
        result["blockers"] = [response_error_text(response)]
        return result
    broker_result = response.get("result") if isinstance(response.get("result"), dict) else {}
    try:
        require_exact_reservation_binding(broker_result, binding)
    except (ProtocolError, ValueError) as exc:
        result["blockers"] = [f"lease broker completion binding failed closed: {exc}"]
        return result
    result.update(
        {
            "ok": True,
            "completed": broker_result.get("state") == "completed",
            "state": broker_result.get("state"),
            "reservationId": broker_result.get("reservationId"),
            "blockers": [],
            "reservation": public_runtime_reservation(reservation),
            "outcome": broker_result.get("outcome"),
            "completionActor": broker_result.get("completionActor"),
            "capacityReturned": False,
        }
    )
    return result


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_json(command: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=20, check=False)
    except Exception as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip() or f"command failed: {command}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON from {command}: {exc}"


def python_module_available(python: str, module_name: str) -> dict[str, Any]:
    command = [
        python,
        "-c",
        "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 3)",
        module_name,
    ]
    try:
        result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=8, check=False)
    except Exception as exc:
        return {
            "name": f"runtime_dependency_{module_name}",
            "ok": False,
            "python": python,
            "module": module_name,
            "error": str(exc),
        }
    requirements = (
        "infrastructure\\requirements-eval.txt"
        if ".venv-eval" in Path(python).parts
        else "requirements.txt"
    )
    return {
        "name": f"runtime_dependency_{module_name}",
        "ok": result.returncode == 0,
        "python": python,
        "module": module_name,
        "returnCode": result.returncode,
        "installHint": f"{python} -m pip install -r {requirements}",
        **({"stderr": result.stderr.strip()} if result.stderr.strip() else {}),
    }


def strip_env_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def unquote_env_value(value: str) -> str:
    value = strip_env_inline_comment(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _default_secret_env_path(
    environment: dict[str, str],
    *,
    platform_name: str,
) -> Path:
    if platform_name == "nt":
        return (
            Path(environment.get("PROGRAMDATA") or r"C:\ProgramData")
            / "HERMES"
            / "secrets"
            / "fouler.env"
        )
    return Path.home() / ".config" / "deku-devstream" / "secrets" / "fouler.env"


def env_file_candidates(
    environment: dict[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> list[Path]:
    environment = dict(os.environ if environment is None else environment)
    platform = os.name if platform_name is None else platform_name
    if platform == "nt" and is_production_runtime(
        ROOT,
        environ=environment,
        platform_name=platform,
    ):
        configured = str(environment.get("FOULER_ENV_FILE") or "").strip()
        path = Path(configured) if configured else _default_secret_env_path(
            environment,
            platform_name=platform,
        )
        return [path.expanduser()]
    return [Path(path).expanduser() for path in ENV_FILES]


def load_env_files(
    *,
    environ: dict[str, str] | None = None,
    platform_name: str | None = None,
) -> dict[str, str]:
    env = dict(os.environ if environ is None else environ)
    platform = os.name if platform_name is None else platform_name
    production_windows = platform == "nt" and is_production_runtime(
        ROOT,
        environ=env,
        platform_name=platform,
    )
    if production_windows and not production_env_file_status(
        env,
        platform_name=platform,
    )["ok"]:
        return env
    for path in env_file_candidates(env, platform_name=platform):
        if production_windows and not path.is_absolute():
            continue
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = unquote_env_value(value.strip())
            if key and key not in env:
                env[key] = value
    return env


def env_value(env: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return default


def runtime_lease_account(args: argparse.Namespace, env: dict[str, str]) -> str:
    path = runtime_lease_path(getattr(args, "runtime_lease", None), env)
    command = str(getattr(args, "command", "") or "")
    execute = bool(getattr(args, "execute", False))
    if command == "cleanup-stale-truth":
        purpose = STALE_TRUTH_CLEANUP_PURPOSE if execute else STALE_TRUTH_CLEANUP_DRY_RUN_PURPOSE
    elif command == "supervise":
        purpose = "devstream-supervise"
    elif command == "start" and getattr(args, "continuous", False):
        purpose = "devstream-start-continuous" if execute else "devstream-start-continuous-dry-run"
    else:
        purpose = "devstream-start" if execute else "devstream-start-dry-run"
    validation = validate_runtime_lease(purpose=purpose, lease_path=path)
    if not validation.get("ok"):
        return ""
    summary = validation.get("lease") if isinstance(validation.get("lease"), dict) else {}
    return str(summary.get("account") or "").strip()


def load_launch_environment(args: argparse.Namespace) -> dict[str, str]:
    inherited = dict(os.environ)
    lease_path = str(getattr(args, "runtime_lease", None) or "").strip()
    if lease_path:
        inherited[RUNTIME_LEASE_PATH_ENV] = lease_path
    try:
        loaded = load_env_files(environ=inherited)
    except TypeError:
        # Compatibility for narrow unit-test loaders that predate the explicit
        # inherited-environment parameter.
        loaded = load_env_files()
        if lease_path:
            loaded[RUNTIME_LEASE_PATH_ENV] = lease_path
    return prepare_runtime_env(loaded)


def apply_runtime_lease_account(env: dict[str, str], args: argparse.Namespace) -> dict[str, str]:
    account = runtime_lease_account(args, env)
    if not account:
        return env
    env = dict(env)
    env["FOULER_RUNTIME_LEASE_ACCOUNT"] = account
    return env


def normalize_account_name(value: object) -> str:
    return str(value or "").strip().strip("\"'`").lower()


def normalize_showdown_account(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _default_account_season_authority_path(
    environment: dict[str, str],
    *,
    platform_name: str,
) -> Path:
    if platform_name == "nt":
        return (
            Path(environment.get("PROGRAMDATA") or r"C:\ProgramData")
            / "HERMES"
            / "authority"
            / "fouler"
            / "account-season.json"
        )
    return (
        Path.home()
        / ".config"
        / "deku-devstream"
        / "authority"
        / "fouler"
        / "account-season.json"
    )


def account_season_authority_path(
    environment: dict[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> Path:
    environment = dict(os.environ if environment is None else environment)
    platform = os.name if platform_name is None else platform_name
    configured = str(environment.get("FOULER_ACCOUNT_SEASON_PATH") or "").strip()
    path = Path(configured) if configured else _default_account_season_authority_path(
        environment,
        platform_name=platform,
    )
    return path.expanduser()


def _reparse_components(path: Path) -> list[str]:
    try:
        absolute = path.absolute()
    except OSError:
        absolute = path
    current = Path(absolute.anchor) if absolute.anchor else Path()
    components: list[str] = []
    for part in absolute.parts[1:] if absolute.anchor else absolute.parts:
        current = current / part
        if not os.path.lexists(current):
            continue
        try:
            metadata = os.lstat(current)
        except OSError:
            continue
        attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or 0x400)
        if stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag):
            components.append(str(current))
    return components


def _read_only_regular_file(path: Path, *, platform_name: str) -> tuple[bool, bool]:
    try:
        metadata = path.lstat()
    except OSError:
        return False, False
    regular = stat.S_ISREG(metadata.st_mode)
    mode_read_only = (stat.S_IMODE(metadata.st_mode) & 0o222) == 0
    if platform_name == "nt":
        attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
        readonly_flag = int(getattr(stat, "FILE_ATTRIBUTE_READONLY", 1) or 1)
        mode_read_only = mode_read_only and bool(attributes & readonly_flag)
    return regular, mode_read_only


def _strict_json_object(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > 64 * 1024:
        raise ValueError("authority file exceeds 64 KiB")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    if type(payload) is not dict:
        raise ValueError("authority payload must be a JSON object")
    return payload


def account_season_authority_check(
    lease_guard: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    path: Path | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    environment = dict(os.environ if env is None else env)
    platform = os.name if platform_name is None else platform_name
    authority_path = path or account_season_authority_path(
        environment,
        platform_name=platform,
    )
    blockers: list[str] = []
    summary = lease_guard.get("lease") if isinstance(lease_guard.get("lease"), dict) else {}
    lease_account = str(summary.get("account") or "").strip()
    if not lease_guard.get("ok") or not lease_account:
        blockers.append("a validated runtime lease account is required")

    if not authority_path.is_absolute():
        blockers.append("FOULER_ACCOUNT_SEASON_PATH must be absolute")
        resolved_path = authority_path
    else:
        try:
            resolved_path = authority_path.resolve(strict=False)
        except OSError:
            resolved_path = authority_path.absolute()

    runtime_roots: dict[str, Path] = {}
    try:
        runtime_paths = resolve_runtime_paths(
            ROOT,
            environ=environment,
            platform_name=platform,
            require_existing=False,
        )
        runtime_roots = {
            "runtime state root": runtime_paths.state_root,
            "runtime log root": runtime_paths.log_root,
            "runtime cache root": runtime_paths.cache_root,
            "runtime temp root": runtime_paths.temp_root,
        }
    except RuntimePathError as exc:
        blockers.append(f"runtime path policy is invalid: {exc}")

    if authority_path.is_absolute():
        for label, root in {"immutable release": ROOT, **runtime_roots}.items():
            try:
                if paths_overlap(authority_path, root):
                    blockers.append(f"account-season authority overlaps {label}")
            except RuntimePathError as exc:
                blockers.append(f"account-season authority path is invalid: {exc}")
                break

    reparse_components = _reparse_components(authority_path) if authority_path.is_absolute() else []
    if reparse_components:
        blockers.append("account-season authority path contains a symlink or reparse point")

    exists = authority_path.exists()
    regular, read_only = _read_only_regular_file(authority_path, platform_name=platform)
    if not exists:
        blockers.append("protected account-season authority file is missing")
    elif not regular:
        blockers.append("protected account-season authority path is not a regular file")
    elif not read_only:
        blockers.append("protected account-season authority file is writable")

    payload: dict[str, Any] = {}
    if exists and regular and not reparse_components:
        try:
            payload = _strict_json_object(authority_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(f"protected account-season authority is malformed: {exc}")

    schema_version = payload.get("schemaVersion")
    account = payload.get("account")
    season_id = payload.get("seasonId")
    if payload:
        if type(schema_version) is not str or schema_version != "fouler-play-account-season/v1":
            blockers.append(
                "protected account-season schemaVersion must equal fouler-play-account-season/v1"
            )
        if type(account) is not str or not normalize_showdown_account(account):
            blockers.append("protected account-season account must be a non-empty string")
        if type(season_id) is not str or not season_id.strip():
            blockers.append("protected account-season seasonId must be a non-empty string")

    normalized_lease = normalize_showdown_account(lease_account)
    normalized_authority = normalize_showdown_account(account)
    if normalized_lease and normalized_authority and normalized_lease != normalized_authority:
        blockers.append("protected account-season account does not match the runtime lease account")

    return {
        "schemaVersion": "fouler-account-season-authority-check/v1",
        "ok": not blockers,
        "blockers": blockers,
        "path": str(resolved_path),
        "source": (
            "FOULER_ACCOUNT_SEASON_PATH"
            if str(environment.get("FOULER_ACCOUNT_SEASON_PATH") or "").strip()
            else "platform-default"
        ),
        "account": account if type(account) is str else None,
        "seasonId": season_id if type(season_id) is str else None,
        "leaseAccount": lease_account or None,
        "readOnly": read_only,
        "regularFile": regular,
        "reparseComponents": reparse_components,
        "runtimeMirrorAuthoritative": False,
        "secretValuesPrinted": False,
    }


def documented_showdown_accounts() -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, str]] = set()
    for path in ACCOUNT_AUTHORITY_FILES:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for kind, pattern in ACCOUNT_AUTHORITY_PATTERNS:
                for match in pattern.finditer(line):
                    account = str(match.group(1) or "").strip().strip("\"'`")
                    if not account or account.upper().startswith("YOUR_"):
                        continue
                    key = (str(path), line_number, kind, account.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    accounts.append({
                        "account": account,
                        "source": str(path),
                        "line": line_number,
                        "kind": kind,
                    })
    return accounts


def showdown_account_authority_check(env: dict[str, str]) -> dict[str, Any]:
    runtime_account = env_value(env, "PS_USERNAME", "SHOWDOWN_USER_ID")
    documented_accounts = documented_showdown_accounts()
    distinct: dict[str, str] = {}
    if runtime_account:
        distinct[normalize_account_name(runtime_account)] = runtime_account
    for item in documented_accounts:
        account = str(item.get("account") or "").strip()
        normalized = normalize_account_name(account)
        if normalized:
            distinct.setdefault(normalized, account)
    return {
        "name": "showdown_account_authority",
        "ok": len(distinct) <= 1,
        "runtimeAccount": runtime_account or None,
        "documentedAccounts": documented_accounts,
        "distinctAccounts": sorted(distinct.values(), key=str.lower),
        "note": (
            "PS_USERNAME/SHOWDOWN_USER_ID, current account docs, and runtime lease account must agree before execute; "
            "historical mission prose is not treated as live account authority."
        ),
    }


def battle_supervisor_contract() -> dict[str, Any]:
    wrapper = ROOT / "scripts" / "start_battle_supervisor_task.ps1"
    installer = ROOT / "scripts" / "install_battle_supervisor_task.ps1"
    runtime = ROOT / "scripts" / "fouler_jigglypuff_runtime.ps1"
    session = ROOT / "scripts" / "devstream_session.py"
    required = [
        {
            "name": "start_battle_supervisor_task.ps1",
            "path": str(wrapper),
            "ok": wrapper.exists(),
        },
        {
            "name": "install_battle_supervisor_task.ps1",
            "path": str(installer),
            "ok": installer.exists(),
        },
        {
            "name": "fouler_jigglypuff_runtime.ps1",
            "path": str(runtime),
            "ok": runtime.exists(),
        },
        {
            "name": "devstream_session.py",
            "path": str(session),
            "ok": session.exists(),
        },
    ]
    try:
        wrapper_text = wrapper.read_text(encoding="utf-8", errors="replace") if wrapper.exists() else ""
        runtime_text = runtime.read_text(encoding="utf-8", errors="replace") if runtime.exists() else ""
        installer_text = installer.read_text(encoding="utf-8", errors="replace") if installer.exists() else ""
    except OSError as exc:
        return {
            "ok": False,
            "requirements": required,
            "error": str(exc),
        }
    checks = [
        {
            "name": "supervise_subcommand",
            "ok": '"supervise"' in wrapper_text and '"supervise"' in runtime_text,
        },
        {
            "name": "bounded_cycles",
            "ok": "--max-cycles" in wrapper_text and "--max-cycles" in runtime_text and "-MaxCycles" in installer_text,
        },
        {
            "name": "runtime_lease_forwarded",
            "ok": "--runtime-lease" in wrapper_text and "Test-RuntimeLease" in runtime_text,
        },
        {
            "name": "auto_improve_explicit_opt_in",
            "ok": "--enable-auto-improve" in wrapper_text and "--enable-auto-improve" in runtime_text,
        },
        {
            "name": "supervisor_status_path",
            "ok": str(SUPERVISOR_STATUS_FILE.name) == "supervisor-status.json",
            "path": str(SUPERVISOR_STATUS_FILE),
        },
    ]
    return {
        "ok": all(item.get("ok") for item in required + checks),
        "requirements": required,
        "checks": checks,
        "statusPath": str(SUPERVISOR_STATUS_FILE),
        "note": "No-start readiness verifies the HERMES battle supervisor contract without starting it.",
    }


def shell_command_for_session(run_count: int, max_concurrent: int, env: dict[str, str] | None = None) -> list[str]:
    env = env or load_env_files()
    username = env_value(env, "PS_USERNAME", "SHOWDOWN_USER_ID")
    battle_command = [
        runtime_python(),
        "run.py",
        "--websocket-uri",
        env_value(env, "PS_WEBSOCKET_URI", default="wss://sim3.psim.us/showdown/websocket"),
        "--ps-username",
        username,
        "--bot-mode",
        env_value(env, "PS_BOT_MODE", default="search_ladder"),
        "--pokemon-format",
        env_value(env, "PS_FORMAT", "POKEMON_FORMAT", default="gen9ou"),
        "--run-count",
        str(run_count),
        "--max-concurrent-battles",
        str(max_concurrent),
        "--search-parallelism",
        str(PILOT_SEARCH_PARALLELISM),
        "--save-replay",
        env_value(env, "SAVE_REPLAY", default="always"),
        "--log-to-file",
    ]
    avatar = env_value(env, "PS_AVATAR")
    if avatar:
        battle_command.extend(["--ps-avatar", avatar])
    team_names = env_value(env, "TEAM_NAMES")
    team_list = env_value(env, "TEAM_LIST")
    team_name = env_value(env, "TEAM_NAME")
    if team_names:
        battle_command.extend(["--team-names", team_names])
    elif team_list:
        battle_command.extend(["--team-list", team_list])
    elif team_name:
        battle_command.extend(["--team-name", team_name])
    spectator = env_value(env, "SPECTATOR_USERNAME")
    if spectator:
        battle_command.extend(["--spectator-username", spectator])
    return [runtime_python(), "scripts/run_bounded_battle_session.py", "--", *battle_command]


def supervisor_command(
    run_count: int,
    max_concurrent: int,
    queue_timeout_seconds: int,
    sleep_seconds: int,
    *,
    enable_auto_improve: bool = False,
    max_cycles: int = 0,
    runtime_lease: str | None = None,
) -> list[str]:
    command = [
        runtime_python(),
        "scripts/devstream_session.py",
        "supervise",
        "--run-count",
        str(run_count),
        "--max-concurrent-battles",
        str(max_concurrent),
        "--queue-timeout-seconds",
        str(queue_timeout_seconds),
        "--sleep-seconds",
        str(sleep_seconds),
    ]
    if max_cycles > 0:
        command.extend(["--max-cycles", str(max_cycles)])
    if runtime_lease:
        command.extend(["--runtime-lease", runtime_lease])
    if enable_auto_improve:
        command.append("--enable-auto-improve")
    else:
        command.append("--skip-improve")
    return command


def showdown_password_required(env: dict[str, str]) -> bool:
    mode = env_value(env, "PS_BOT_MODE", default="search_ladder")
    return mode == "search_ladder"


def obs_server_command() -> list[str]:
    return [runtime_python(), "streaming/serve_obs_page.py"]


def obs_http_ready(timeout_seconds: float = 1.5) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8777/health", timeout=timeout_seconds) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def adopted_process_ready(pid_file: Path, command: list[str]) -> bool:
    expected_tokens = _pid_file_expected_tokens(pid_file, {"command": command})
    if "serve_obs_page.py" in expected_tokens:
        return obs_http_ready()
    return True


def is_obs_http_command(command: list[str]) -> bool:
    return "serve_obs_page.py" in _command_expected_tokens(command)


def battle_supervisor_task_command(args: argparse.Namespace) -> list[str]:
    installer = ROOT / "scripts" / "install_battle_supervisor_task.ps1"
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(powershell):
        powershell = "powershell.exe"
    command = [
        powershell,
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
        "-QueueTimeoutSeconds",
        str(args.queue_timeout_seconds),
        "-SleepSeconds",
        str(args.supervisor_sleep_seconds),
    ]
    if positive_int(getattr(args, "max_cycles", 0), 0) > 0:
        command.extend(["-MaxCycles", str(args.max_cycles)])
    runtime_lease = str(getattr(args, "runtime_lease", "") or "").strip()
    if runtime_lease:
        command.extend(["-RuntimeLease", runtime_lease])
    if getattr(args, "enable_auto_improve", False):
        command.append("-AutoImprove")
    return command


def start_supervisor_runtime(args: argparse.Namespace, command: list[str], env: dict[str, str]) -> dict[str, Any]:
    installer = ROOT / "scripts" / "install_battle_supervisor_task.ps1"
    if os.name != "nt" or not installer.exists():
        return start_process(command, SUPERVISOR_PID_FILE, detached_child_env(env))
    task_command = battle_supervisor_task_command(args)
    started = time.time()
    result = subprocess.run(
        task_command,
        cwd=str(ROOT),
        env=prepare_runtime_env(env),
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    payload: dict[str, Any] = {
        "command": task_command,
        "code": result.returncode,
        "ok": result.returncode == 0,
        "stdoutTail": tail_text(result.stdout),
        "stderrTail": tail_text(result.stderr),
        "durationSeconds": round(time.time() - started, 3),
    }
    try:
        parsed = json.loads(result.stdout)
        if isinstance(parsed, dict):
            payload["taskStatus"] = parsed
    except json.JSONDecodeError:
        pass
    return payload


def supervisor_child_python() -> str:
    return runtime_python()


def read_active_battles() -> int:
    path = active_battles_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    battles = data.get("battles")
    return len(battles) if isinstance(battles, list) else 0


def active_battles_path() -> Path:
    return runtime_state_root() / "active_battles.json"


def active_battles_age_seconds() -> float | None:
    path = active_battles_path()
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def file_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_battle_stats(path: Path | None = None) -> list[dict[str, Any]]:
    stats_path = path or BATTLE_STATS_FILE
    payload = read_json_object(stats_path)
    battles = payload.get("battles")
    if not isinstance(battles, list):
        return []
    return [row for row in battles if isinstance(row, dict)]


def battle_stats_max_entries() -> int:
    raw = os.getenv("BATTLE_STATS_MAX_ENTRIES", "5000").strip()
    try:
        return max(100, int(raw))
    except ValueError:
        return 5000


def battle_identity_set(battles: list[dict[str, Any]]) -> set[str]:
    identities: set[str] = set()
    for row in battles:
        for key in ("battle_id", "battleId", "replay_id", "battle_tag", "id"):
            value = str(row.get(key) or "").strip()
            if value:
                identities.add(value)
    return identities


def battle_identity_index(battles: list[dict[str, Any]]) -> dict[str, int]:
    identities: dict[str, int] = {}
    for index, row in enumerate(battles):
        for key in ("battle_id", "battleId", "replay_id", "battle_tag", "id"):
            value = str(row.get(key) or "").strip()
            if value:
                identities.setdefault(value, index)
    return identities


def parse_active_battle_id(row: dict[str, Any]) -> str:
    for key in ("id", "battle_id", "battleId", "battle_tag", "replay_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def newest_battle_stats_time(battles: list[dict[str, Any]]) -> datetime | None:
    newest: datetime | None = None
    for row in battles:
        raw = row.get("timestamp") or row.get("updated") or row.get("endedAt")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if newest is None or parsed > newest:
            newest = parsed
    return newest


def abandoned_battle_stats_row(
    battle: dict[str, Any],
    *,
    backup_path: Path,
    backup_mtime: datetime,
    source_updated: Any,
    clear_reason: str,
) -> dict[str, Any] | None:
    battle_id = parse_active_battle_id(battle)
    if not battle_id:
        return None
    return {
        "battle_id": battle_id,
        "timestamp": iso_now(),
        "team_file": str(battle.get("team_file") or battle.get("team") or "unknown"),
        "result": "loss",
        "replay_id": battle_id,
        "rating": None,
        "opponent": str(battle.get("opponent") or ""),
        "battle_url": str(battle.get("url") or ""),
        "operational_loss": True,
        "provenance_status": "recovered-unattributed",
        "outcome_detail": "abandoned-active-battle-without-result",
        "source": "stale-active-battle-cleanup",
        "source_backup_path": str(backup_path),
        "source_backup_mtime_utc": backup_mtime.isoformat(),
        "source_active_battles_updated": source_updated,
        "abandoned_started_at": battle.get("started"),
        "abandoned_status": battle.get("status"),
        "cleanup_reason": clear_reason,
    }


def normalize_pokemon_name(name: str) -> str:
    cleaned = re.sub(r"\([^)]*\)", "", str(name or ""))
    cleaned = cleaned.split(",", 1)[0]
    return re.sub(r"[^a-z0-9]", "", cleaned.lower())


def parse_team_file_pokemon(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return names
    for line in lines:
        stripped = line.strip()
        if "@" not in stripped:
            continue
        header = stripped.split("@", 1)[0].strip()
        normalized = normalize_pokemon_name(header)
        if normalized:
            names.add(normalized)
    return names


def match_team_file_from_pokemon(
    pokemon_names: set[str],
    *,
    teams_dir: Path | None = None,
) -> str | None:
    teams_dir = teams_dir or TEAMS_DIR
    target = {name for name in pokemon_names if name}
    if not target or not teams_dir.is_dir():
        return None
    best_path: Path | None = None
    best_score = 0.0
    skipped_suffixes = {".md", ".txt", ".list", ".py", ".pyc"}
    try:
        candidates = [path for path in teams_dir.rglob("*") if path.is_file()]
    except OSError:
        return None
    for path in candidates:
        if path.name.startswith(".") or path.suffix.lower() in skipped_suffixes:
            continue
        team_names = parse_team_file_pokemon(path)
        if not team_names:
            continue
        score = len(target & team_names) / max(1, len(target))
        if score > best_score:
            best_score = score
            best_path = path
    if best_path is None or best_score < 0.45:
        return None
    try:
        return best_path.relative_to(teams_dir).as_posix()
    except ValueError:
        return str(best_path)


def infer_team_file_from_replay(
    battle_id: str,
    account: str,
    *,
    replay_dir: Path | None = None,
    teams_dir: Path | None = None,
) -> str | None:
    replay_dir = replay_dir or REPLAY_ANALYSIS_DIR
    replay_id = battle_id.replace("battle-", "", 1)
    replay_path = replay_dir / f"{replay_id}.json"
    payload = read_json_object(replay_path)
    raw_log = payload.get("log") or payload.get("logs") or payload.get("battle_log")
    if isinstance(raw_log, str):
        lines = raw_log.splitlines()
    elif isinstance(raw_log, list):
        lines = [str(line) for line in raw_log]
    else:
        return None

    account_lower = str(account or "").strip().lower()
    bot_side = ""
    for line in lines:
        if not line.startswith("|player|"):
            continue
        parts = line.split("|")
        if len(parts) >= 4 and parts[3].strip().lower() == account_lower:
            bot_side = parts[2].strip()
            break
    if not bot_side:
        return None

    pokemon_names: set[str] = set()
    prefix = f"|poke|{bot_side}|"
    for line in lines:
        if not line.startswith(prefix):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        normalized = normalize_pokemon_name(parts[3])
        if normalized:
            pokemon_names.add(normalized)
    return match_team_file_from_pokemon(pokemon_names, teams_dir=teams_dir)


def showdown_account_from_runtime() -> str:
    try:
        env = prepare_runtime_env(load_env_files())
        validation = validate_runtime_lease(
            purpose="devstream-supervise",
            lease_path=runtime_lease_path(None, env),
        )
        if validation.get("ok"):
            lease = validation.get("lease") if isinstance(validation.get("lease"), dict) else {}
            return str(lease.get("account") or "").strip()
    except Exception:
        return ""
    return ""


def opponent_from_battle_log_path(path: Path, battle_id: str, winner: str, account: str) -> str:
    if winner and winner.strip().lower() != account.strip().lower():
        return winner.strip()
    prefix = f"{battle_id}_"
    stem = path.stem
    if stem.startswith(prefix):
        return stem[len(prefix) :].strip()
    return ""


def battle_log_proves_account(text: str, account: str) -> bool:
    expected = normalize_account_name(account)
    if not expected:
        return False
    candidates: list[str] = []
    patterns = (
        r"\|player\|p[12]\|([^|\r\n]+)\|",
        r'"side"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"',
        r"'side'\s*:\s*\{\s*'name'\s*:\s*'([^']+)'",
        r"Battle finished:\s+battle-[A-Za-z0-9-]+\s+Winner:\s*([^\r\n]+)",
    )
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return expected in {normalize_account_name(candidate) for candidate in candidates}


def active_season_id_for_account(account: str, path: Path | None = None) -> str:
    payload = read_json_object(path or ACCOUNT_SEASON_FILE)
    if normalize_account_name(payload.get("account")) != normalize_account_name(account):
        return ""
    return str(payload.get("seasonId") or "").strip()


def completed_battle_stats_row_from_log(
    path: Path,
    *,
    account: str,
    replay_dir: Path | None = None,
    teams_dir: Path | None = None,
    season_id: str = "",
) -> dict[str, Any] | None:
    if not account:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        log_mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None
    finished = re.search(
        r"Battle finished:\s+(battle-[A-Za-z0-9-]+)\s+Winner:\s*([^\r\n]+)",
        text,
    )
    if not finished:
        return None
    if not battle_log_proves_account(text, account):
        return None
    battle_id = finished.group(1).strip()
    winner = finished.group(2).strip()
    account_lower = account.strip().lower()
    result = "win" if winner.lower() == account_lower else "loss"
    rating_before = None
    rating_after = None
    rating_delta = None
    rating_match = re.search(
        rf"Captured authoritative rating transition for\s+{re.escape(battle_id)}:\s+(\d+)\s*->\s*(\d+)\s*\(([+-]?\d+)\)",
        text,
    )
    if rating_match:
        rating_before = int(rating_match.group(1))
        rating_after = int(rating_match.group(2))
        rating_delta = int(rating_match.group(3))
    replay_match = re.search(r"Replay saved:\s+(https://replay\.pokemonshowdown\.com/[A-Za-z0-9-]+)", text)
    replay_url = replay_match.group(1).strip() if replay_match else ""
    team_file = infer_team_file_from_replay(
        battle_id,
        account,
        replay_dir=replay_dir,
        teams_dir=teams_dir,
    ) or "unknown"
    row: dict[str, Any] = {
        "battle_id": battle_id,
        "timestamp": log_mtime.isoformat(),
        "team_file": team_file,
        "result": result,
        "replay_id": battle_id,
        "rating": rating_after,
        "battle_tag": battle_id,
        "winner": winner,
        "opponent": opponent_from_battle_log_path(path, battle_id, winner, account),
        "recovered_result": True,
        "provenance_status": "recovered-unattributed",
        "source": "completed-battle-log-recovery",
        "source_log_path": str(path),
        "replay_url": replay_url,
        "public_replay_id": battle_id.replace("battle-", "", 1),
        "replay_status": "public" if replay_url else "unknown",
        "account": account,
    }
    if season_id:
        row["season_id"] = season_id
    if rating_match:
        row.update(
            {
                "elo_before": rating_before,
                "elo_after": rating_after,
                "rating_delta": rating_delta,
                "rating_source": "showdown_raw_log_recovery",
            }
        )
    return row


def missing_battle_stats_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().lower() in {"unknown", "---"}
    return False


def enrich_existing_battle_stats_row(
    existing: dict[str, Any],
    recovered: dict[str, Any],
    *,
    account: str = "",
) -> bool:
    changed = False
    fill_if_missing = [
        "rating",
        "battle_tag",
        "winner",
        "opponent",
        "replay_url",
        "public_replay_id",
        "replay_status",
        "elo_before",
        "elo_after",
        "rating_delta",
        "rating_source",
        "account",
        "season_id",
    ]
    for key in fill_if_missing:
        if missing_battle_stats_value(existing.get(key)) and not missing_battle_stats_value(recovered.get(key)):
            existing[key] = recovered[key]
            changed = True
    existing_opponent = normalize_account_name(existing.get("opponent"))
    recovered_opponent = normalize_account_name(recovered.get("opponent"))
    normalized_account = normalize_account_name(account)
    if (
        normalized_account
        and existing_opponent == normalized_account
        and recovered_opponent
        and recovered_opponent != normalized_account
    ):
        existing["opponent"] = recovered["opponent"]
        changed = True
    if missing_battle_stats_value(existing.get("team_file")) and not missing_battle_stats_value(recovered.get("team_file")):
        existing["team_file"] = recovered["team_file"]
        changed = True
    if missing_battle_stats_value(existing.get("replay_id")) and not missing_battle_stats_value(recovered.get("replay_id")):
        existing["replay_id"] = recovered["replay_id"]
        changed = True
    if changed:
        existing["result_enriched_from_log"] = True
        existing["source_log_path"] = recovered.get("source_log_path")
        existing["rating_source"] = recovered.get("rating_source") or existing.get("rating_source")
    return changed


def recover_completed_battle_results_from_logs(
    *,
    execute: bool,
    max_logs: int = 20,
    log_dir: Path | None = None,
    battle_stats_file: Path | None = None,
    account: str | None = None,
    replay_dir: Path | None = None,
    teams_dir: Path | None = None,
    season_id: str | None = None,
) -> dict[str, Any]:
    log_dir = log_dir or BATTLE_LOG_DIR
    battle_stats_file = battle_stats_file or BATTLE_STATS_FILE
    account = (account or showdown_account_from_runtime()).strip()
    season_id = active_season_id_for_account(account) if season_id is None else season_id.strip()
    payload: dict[str, Any] = {
        "policy": "fouler-completed-battle-log-result-recovery/v1",
        "execute": execute,
        "logDir": str(log_dir),
        "battleStatsPath": str(battle_stats_file),
        "account": account,
        "seasonId": season_id or None,
        "checkedLogs": 0,
        "rowsPlanned": 0,
        "rowsAdded": 0,
        "rowsUpdated": 0,
        "battleIds": [],
        "updatedBattleIds": [],
        "skippedExistingBattleIds": [],
        "recovered": False,
    }
    if not account:
        payload["reason"] = "cannot recover completed battle logs without a Showdown account"
        return payload
    if not log_dir.is_dir():
        payload["reason"] = "no battle log directory to inspect"
        return payload

    battles = read_battle_stats(battle_stats_file)
    known_battle_ids = battle_identity_set(battles)
    known_battle_indexes = battle_identity_index(battles)
    latest_stats_time = newest_battle_stats_time(battles)
    if latest_stats_time is not None:
        payload["latestBattleStatsAtUtc"] = latest_stats_time.isoformat()

    try:
        logs = sorted(
            log_dir.glob("battle-*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:max_logs]
    except OSError as exc:
        payload["reason"] = "failed to scan battle logs"
        payload["error"] = str(exc)
        return payload

    rows_to_add: list[dict[str, Any]] = []
    rows_updated = 0
    for path in logs:
        payload["checkedLogs"] += 1
        try:
            log_mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        row = completed_battle_stats_row_from_log(
            path,
            account=account,
            replay_dir=replay_dir,
            teams_dir=teams_dir,
            season_id=season_id,
        )
        if row is None:
            continue
        battle_id = str(row.get("battle_id") or "").strip()
        if not battle_id:
            continue
        if battle_id in known_battle_ids:
            existing_index = known_battle_indexes.get(battle_id)
            if existing_index is not None and enrich_existing_battle_stats_row(
                battles[existing_index], row, account=account
            ):
                rows_updated += 1
                payload["updatedBattleIds"].append(battle_id)
            else:
                payload["skippedExistingBattleIds"].append(battle_id)
            continue
        if latest_stats_time is not None and log_mtime <= latest_stats_time:
            continue
        known_battle_ids.add(battle_id)
        known_battle_indexes[battle_id] = len(battles) + len(rows_to_add)
        payload["battleIds"].append(battle_id)
        rows_to_add.append(row)

    payload["rowsPlanned"] = len(rows_to_add)
    payload["rowsUpdated"] = rows_updated
    if not rows_to_add and not rows_updated:
        payload["reason"] = "no completed battle logs missing from battle_stats.json"
        return payload
    if not execute:
        payload["reason"] = "dry run; completed battle log recovery planned only"
        return payload

    battles.extend(rows_to_add)
    max_entries = battle_stats_max_entries()
    if len(battles) > max_entries:
        del battles[:-max_entries]
        payload["trimmedToMaxEntries"] = max_entries
    try:
        _atomic_write_text(
            battle_stats_file,
            json.dumps({"battles": battles}, indent=2, ensure_ascii=False) + "\n",
        )
    except PermissionError:
        battle_stats_file.chmod(0o666)
        _atomic_write_text(
            battle_stats_file,
            json.dumps({"battles": battles}, indent=2, ensure_ascii=False) + "\n",
        )
        payload["permissionRepair"] = "chmod 666 before rewrite"
    payload["rowsAdded"] = len(rows_to_add)
    payload["recovered"] = True
    payload["reason"] = "completed battle logs recovered as authoritative result rows"
    return payload


def recover_abandoned_battle_results_from_backups(
    *,
    execute: bool,
    max_backups: int = 20,
    clear_reason: str = "stale active battle truth had no live battle runner",
    backup_dir: Path | None = None,
    battle_stats_file: Path | None = None,
) -> dict[str, Any]:
    backup_dir = backup_dir or STALE_BATTLE_BACKUP_DIR
    battle_stats_file = battle_stats_file or BATTLE_STATS_FILE
    payload: dict[str, Any] = {
        "policy": "fouler-abandoned-active-battle-result-recovery/v1",
        "execute": execute,
        "backupDir": str(backup_dir),
        "battleStatsPath": str(battle_stats_file),
        "checkedBackups": 0,
        "rowsPlanned": 0,
        "rowsAdded": 0,
        "battleIds": [],
        "skippedExistingBattleIds": [],
        "recovered": False,
    }
    if any_battle_runner_alive():
        payload["reason"] = "battle runner is alive; preserving result history until runtime settles"
        return payload
    if not backup_dir.is_dir():
        payload["reason"] = "no stale active battle backups to inspect"
        return payload

    battles = read_battle_stats(battle_stats_file)
    known_battle_ids = battle_identity_set(battles)
    latest_stats_time = newest_battle_stats_time(battles)
    if latest_stats_time is not None:
        payload["latestBattleStatsAtUtc"] = latest_stats_time.isoformat()

    try:
        backups = sorted(
            backup_dir.glob("active_battles-*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:max_backups]
    except OSError as exc:
        payload["reason"] = "failed to scan stale active battle backups"
        payload["error"] = str(exc)
        return payload

    rows_to_add: list[dict[str, Any]] = []
    for path in backups:
        payload["checkedBackups"] += 1
        try:
            backup_mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if latest_stats_time is not None and backup_mtime <= latest_stats_time:
            continue
        backup_payload = read_json_object(path)
        entries = backup_payload.get("battles")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            battle_id = parse_active_battle_id(entry)
            if not battle_id:
                continue
            if battle_id in known_battle_ids:
                payload["skippedExistingBattleIds"].append(battle_id)
                continue
            row = abandoned_battle_stats_row(
                entry,
                backup_path=path,
                backup_mtime=backup_mtime,
                source_updated=backup_payload.get("updated"),
                clear_reason=clear_reason,
            )
            if row is None:
                continue
            known_battle_ids.add(battle_id)
            payload["battleIds"].append(battle_id)
            rows_to_add.append(row)

    payload["rowsPlanned"] = len(rows_to_add)
    if not rows_to_add:
        payload["reason"] = "no abandoned active battle results missing from battle_stats.json"
        return payload
    if not execute:
        payload["reason"] = "dry run; abandoned active battle result recovery planned only"
        return payload

    battles.extend(rows_to_add)
    max_entries = battle_stats_max_entries()
    if len(battles) > max_entries:
        del battles[:-max_entries]
        payload["trimmedToMaxEntries"] = max_entries
    try:
        _atomic_write_text(
            battle_stats_file,
            json.dumps({"battles": battles}, indent=2, ensure_ascii=False) + "\n",
        )
    except PermissionError:
        battle_stats_file.chmod(0o666)
        _atomic_write_text(
            battle_stats_file,
            json.dumps({"battles": battles}, indent=2, ensure_ascii=False) + "\n",
        )
        payload["permissionRepair"] = "chmod 666 before rewrite"
    payload["rowsAdded"] = len(rows_to_add)
    payload["recovered"] = True
    payload["reason"] = "abandoned active battles recovered as operational loss rows"
    return payload


def finite_runtime_lease_preconditions() -> list[str]:
    return list(FINITE_RUNTIME_LEASE_PRECONDITIONS)


def archived_active_battle_truth(payload: dict[str, Any], active_count: int) -> bool:
    if active_count != 0:
        return False
    return bool(payload.get("clearedBy") or payload.get("clearReason") or payload.get("runtime_blocked"))


def stream_status_claims_runtime(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "").strip().lower()
    return status in ACTIVE_STREAM_STATUSES or bool(payload.get("streaming")) or bool(payload.get("stream_pid"))


def public_truth_artifact_disposition(
    *,
    active_payload: dict[str, Any],
    stream_payload: dict[str, Any],
    active_count: int,
    active_stale: bool,
    stream_stale: bool,
    runner_alive: bool,
) -> dict[str, Any]:
    status = str(stream_payload.get("status") or "").strip()
    stream_claims_runtime = stream_status_claims_runtime(stream_payload)
    if archived_active_battle_truth(active_payload, active_count):
        active_state = {
            "state": "archived",
            "classification": "archived-active-battle-truth",
            "proofUse": "not-live-runtime-proof",
            "reason": active_payload.get("clearReason") or active_payload.get("blocker_summary"),
            "clearedBy": active_payload.get("clearedBy"),
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    elif runner_alive and active_count > 0:
        active_state = {
            "state": "adopted",
            "classification": "adopted-active-battle-truth",
            "proofUse": "live-runtime-proof",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    elif active_stale:
        active_state = {
            "state": "blocked",
            "classification": "blocked-stale-active-battle-truth",
            "proofUse": "not-live-runtime-proof",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    else:
        active_state = {
            "state": "idle",
            "classification": "empty-active-battle-truth",
            "proofUse": "not-live-runtime-proof",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }

    if runner_alive and stream_claims_runtime:
        stream_state = {
            "state": "adopted",
            "classification": "adopted-stream-status",
            "proofUse": "live-runtime-proof",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    elif stream_payload.get("runtime_blocked"):
        stream_state = {
            "state": "blocked",
            "classification": "runtime-blocked-stream-status",
            "proofUse": "not-live-runtime-proof",
            "reason": stream_payload.get("blocker_summary") or status or "runtime blocked",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    elif stream_claims_runtime and stream_stale:
        stream_state = {
            "state": "blocked",
            "classification": "blocked-stale-stream-status",
            "proofUse": "not-live-runtime-proof",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    elif stream_claims_runtime:
        stream_state = {
            "state": "candidate-active",
            "classification": "active-stream-status",
            "proofUse": "requires-live-runner-adoption",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    else:
        stream_state = {
            "state": "idle-stale" if stream_stale else "idle",
            "classification": "stream-status",
            "proofUse": "not-live-runtime-proof",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }

    states = [active_state["state"], stream_state["state"]]
    if "blocked" in states:
        overall = "blocked"
    elif "adopted" in states:
        overall = "adopted"
    elif "archived" in states:
        overall = "archived"
    else:
        overall = "idle"
    return {
        "state": overall,
        "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        "artifacts": {
            "activeBattles": active_state,
            "streamStatus": stream_state,
        },
    }


def public_runtime_truth_check(stale_after_seconds: int = STALE_ACTIVE_TRUTH_SECONDS) -> dict[str, Any]:
    active_count = read_active_battles()
    active_path = active_battles_path()
    active_payload = read_json_object(active_path)
    active_age = active_battles_age_seconds()
    stream_payload = read_json_object(STREAM_STATUS_FILE)
    stream_age = file_age_seconds(STREAM_STATUS_FILE)
    status = str(stream_payload.get("status") or "").strip()
    status_normalized = status.lower()
    runner_alive = any_battle_runner_alive()
    stale_truth = bool(active_age is not None and active_age >= stale_after_seconds)
    stream_stale = bool(stream_age is not None and stream_age >= STALE_STREAM_TRUTH_SECONDS)
    disposition = public_truth_artifact_disposition(
        active_payload=active_payload,
        stream_payload=stream_payload,
        active_count=active_count,
        active_stale=stale_truth,
        stream_stale=stream_stale,
        runner_alive=runner_alive,
    )
    active_status_without_runner = (
        bool(status_normalized in ACTIVE_STREAM_STATUSES or stream_payload.get("streaming") or stream_payload.get("stream_pid"))
        and not runner_alive
        and stream_stale
    )
    stale_active_truth_without_runner = (
        stale_truth
        and not runner_alive
        and disposition["artifacts"]["activeBattles"].get("state") != "archived"
    )
    blockers: list[str] = []
    if stale_active_truth_without_runner:
        blockers.append(
            "active_battles.json is stale and no expected Fouler battle runner owns it; "
            "archive/adopt/clear requires a finite proof-window runtime lease"
        )
    if active_status_without_runner:
        blockers.append(
            f"stream_status.json reports stale {status or 'active runtime'} without an expected Fouler battle runner; "
            "archive/adopt/clear requires a finite proof-window runtime lease"
        )
    return {
        "name": "public_runtime_truth",
        "ok": not blockers,
        "activeBattles": {
            "path": str(active_path),
            "exists": active_path.exists(),
            "count": active_count,
            "ageSeconds": round(active_age, 3) if active_age is not None else None,
            "staleAfterSeconds": stale_after_seconds,
            "stale": stale_truth,
            "disposition": disposition["artifacts"]["activeBattles"],
        },
        "streamStatus": {
            "path": str(STREAM_STATUS_FILE),
            "exists": STREAM_STATUS_FILE.exists(),
            "status": status or None,
            "streaming": stream_payload.get("streaming"),
            "streamPid": stream_payload.get("stream_pid"),
            "ageSeconds": round(stream_age, 3) if stream_age is not None else None,
            "staleAfterSeconds": STALE_STREAM_TRUTH_SECONDS,
            "stale": stream_stale,
            "disposition": disposition["artifacts"]["streamStatus"],
        },
        "disposition": disposition,
        "battleRunnerAlive": runner_alive,
        "blockers": blockers,
        "note": "Doctor fails closed when public runtime truth claims activity without a live expected Fouler runner and finite lease.",
    }


def read_pid(path: Path) -> int | None:
    payload = read_pid_payload(path)
    if isinstance(payload, dict):
        try:
            return int(payload.get("pid") or 0) or None
        except (TypeError, ValueError):
            return None
    try:
        return int(str(payload or "").strip())
    except (TypeError, ValueError):
        return None


def read_pid_payload(path: Path) -> dict[str, Any] | str | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        if raw.startswith("{"):
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        return raw
    except json.JSONDecodeError:
        return None


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def write_pid_value(path: Path, pid: int, command: list[str], **extra: Any) -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "pid": pid,
            "command": command,
            "startedAt": iso_now(),
            **extra,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    _atomic_write_text(path, payload)


def _parse_started_at(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _command_expected_tokens(command: object) -> list[str]:
    if isinstance(command, str):
        command_parts = command.split()
    elif isinstance(command, list):
        command_parts = [str(item) for item in command]
    else:
        command_parts = []
    tokens = [
        Path(part).name.lower()
        for part in command_parts
        if str(part).lower().endswith((".py", ".bat", ".ps1", ".sh"))
    ]
    for index, part in enumerate(command_parts):
        if Path(part).name.lower() != "devstream_session.py":
            continue
        if index + 1 < len(command_parts):
            subcommand = command_parts[index + 1].strip().lower()
            if subcommand and not subcommand.startswith("-"):
                tokens.append(subcommand)
    return tokens


def _normalize_command_argument(value: object) -> str:
    text = str(value or "").strip().strip('"')
    if not text:
        return ""
    looks_like_path = (
        text.lower().endswith((".py", ".ps1", ".bat", ".cmd", ".exe", ".sh"))
        or "\\" in text
        or "/" in text
    )
    if looks_like_path and not text.startswith("-"):
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        try:
            text = str(candidate.resolve())
        except OSError:
            text = os.path.abspath(str(candidate))
    return os.path.normcase(text)


def _command_identity_matches(expected: object, observed: object) -> bool:
    """Match the complete recorded argv, allowing only an added interpreter prefix."""
    if not isinstance(expected, list) or not expected:
        return False
    if not isinstance(observed, list) or not observed:
        return False
    expected_parts = [_normalize_command_argument(item) for item in expected]
    observed_parts = [_normalize_command_argument(item) for item in observed]
    if observed_parts == expected_parts:
        return True
    return len(observed_parts) == len(expected_parts) + 1 and observed_parts[1:] == expected_parts


def _process_cwd_matches_root(snapshot: dict[str, Any]) -> bool:
    cwd = str(snapshot.get("cwd") or "").strip()
    return bool(cwd) and os.path.normcase(os.path.abspath(cwd)) == os.path.normcase(os.path.abspath(ROOT))


def _pid_file_expected_tokens(path: Path, payload: dict[str, Any] | str | None) -> list[str]:
    if isinstance(payload, dict):
        tokens = _command_expected_tokens(payload.get("command") or [])
        if tokens:
            return tokens
    if path == BOT_LOCK_PID_FILE or path == BATTLE_PID_FILE:
        return ["run.py"]
    if path == OBS_PID_FILE:
        return ["serve_obs_page.py"]
    return []


def _process_snapshot(pid: int) -> dict[str, Any] | None:
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        status = proc.status() if hasattr(proc, "status") else ""
        if status == getattr(psutil, "STATUS_ZOMBIE", "zombie"):
            return None
        return {
            "running": bool(proc.is_running()),
            "cmdline": [str(item) for item in proc.cmdline()],
            "cwd": proc.cwd(),
            "createTime": float(proc.create_time()),
            "parentPid": int(proc.ppid()),
        }
    except Exception:
        return None


def _find_existing_process(command: list[str]) -> int | None:
    if not command:
        return None
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["pid", "cmdline", "cwd", "status"]):
            try:
                if int(proc.info.get("pid") or 0) == os.getpid():
                    continue
                if proc.info.get("status") == getattr(psutil, "STATUS_ZOMBIE", "zombie"):
                    continue
                snapshot = {
                    "cwd": proc.info.get("cwd"),
                    "cmdline": [str(item) for item in (proc.info.get("cmdline") or [])],
                }
                if not _process_cwd_matches_root(snapshot):
                    continue
                if _command_identity_matches(command, snapshot["cmdline"]):
                    return int(proc.info["pid"])
            except Exception:
                continue
    except Exception:
        return None
    return None


def _pid_matches_expected_process(path: Path, pid: int, payload: dict[str, Any] | str | None) -> bool:
    snapshot = _process_snapshot(pid)
    if not snapshot or not snapshot.get("running"):
        return False
    if not isinstance(payload, dict):
        return False
    expected_command = payload.get("command")
    if not _command_identity_matches(expected_command, snapshot.get("cmdline")):
        return False
    if not _process_cwd_matches_root(snapshot):
        return False
    started_at = _parse_started_at(payload.get("startedAt") or payload.get("started_at"))
    create_time = snapshot.get("createTime")
    if started_at is not None and create_time is not None and float(create_time) < started_at - 2:
        return False
    recorded_create_time = payload.get("createTime")
    if recorded_create_time not in (None, "") and create_time is not None:
        try:
            if abs(float(recorded_create_time) - float(create_time)) > 2:
                return False
        except (TypeError, ValueError):
            return False
    return True


def pid_alive(path: Path) -> tuple[bool, int | None]:
    payload = read_pid_payload(path)
    pid = read_pid(path)
    if pid is None or pid <= 0:
        return False, pid
    try:
        os.kill(pid, 0)
        return _pid_matches_expected_process(path, pid, payload), pid
    except PermissionError:
        return _pid_matches_expected_process(path, pid, payload), pid
    except OSError:
        # Windows can raise for signal 0 even when psutil can still inspect a
        # valid process. The structured PID record still has to match exactly.
        return _pid_matches_expected_process(path, pid, payload), pid


def pid_file_status(path: Path) -> dict[str, Any]:
    payload = read_pid_payload(path)
    pid = read_pid(path)
    alive, observed_pid = pid_alive(path)
    item: dict[str, Any] = {
        "pidFile": str(path),
        "exists": path.exists(),
        "pid": observed_pid if observed_pid is not None else pid,
        "alive": alive,
        "stale": False,
    }
    if isinstance(payload, dict):
        item["command"] = payload.get("command")
        item["startedAt"] = payload.get("startedAt") or payload.get("started_at")
    if not path.exists():
        item["reason"] = "missing"
        return item
    if pid is None:
        item["stale"] = True
        item["reason"] = "pid file exists but does not contain a valid positive PID"
    elif not alive:
        item["stale"] = True
        item["reason"] = "pid file exists but PID is not a live expected Fouler process"
    else:
        item["reason"] = "live expected Fouler process"
    return item


def runtime_pid_file_check() -> dict[str, Any]:
    details = [pid_file_status(path) for path in [BOT_LOCK_PID_FILE, BATTLE_PID_FILE, SUPERVISOR_PID_FILE]]
    stale = [item for item in details if item.get("stale")]
    return {
        "name": "runtime_pid_files",
        "ok": not stale,
        "details": details,
        "staleCount": len(stale),
        "note": "Stale PID files are blockers until cleared, replaced, or adopted by a no-start readiness flow.",
    }


def clear_dead_battle_pid_files(*, reason: str) -> list[dict[str, Any]]:
    """Remove only battle PID files whose recorded process is already dead."""
    actions: list[dict[str, Any]] = []
    for pid_file in battle_pid_files():
        status = pid_file_status(pid_file)
        if not status.get("exists") or not status.get("stale") or status.get("alive"):
            continue
        action = {
            "pidFile": str(pid_file),
            "pid": status.get("pid"),
            "reason": reason,
            "statusReason": status.get("reason"),
            "removed": False,
        }
        try:
            pid_file.unlink()
            action["removed"] = True
        except OSError as exc:
            action["error"] = str(exc)
        actions.append(action)
    return actions


def write_pid(path: Path, proc: subprocess.Popen[Any], command: list[str]) -> None:
    write_pid_value(path, proc.pid, command)


def rotate_child_log_before_append(log_path: Path, env: dict[str, str]) -> dict[str, Any] | None:
    max_bytes = child_log_max_bytes(env)
    try:
        previous_bytes = log_path.stat().st_size
    except FileNotFoundError:
        return None
    if previous_bytes < max_bytes:
        return None
    rotated_path = log_path.with_name(f"{log_path.name}.old")
    os.replace(log_path, rotated_path)
    return {
        "path": str(log_path),
        "rotatedTo": str(rotated_path),
        "previousBytes": previous_bytes,
        "maxBytes": max_bytes,
    }


def production_env_file_status(
    env: dict[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> dict[str, Any]:
    environment = dict(os.environ if env is None else env)
    platform = os.name if platform_name is None else platform_name
    required = platform == "nt" and is_production_runtime(
        ROOT,
        environ=environment,
        platform_name=platform,
    )
    candidates = env_file_candidates(environment, platform_name=platform)
    if not required:
        return {
            "ok": True,
            "required": False,
            "blockers": [],
            "paths": [str(path) for path in candidates],
        }

    blockers: list[str] = []
    path = candidates[0]
    if len(candidates) != 1:
        blockers.append("Windows production must select exactly one FOULER_ENV_FILE")
    if not path.is_absolute():
        blockers.append("FOULER_ENV_FILE must be absolute for Windows production")

    runtime_roots: dict[str, Path] = {}
    try:
        runtime_paths = resolve_runtime_paths(
            ROOT,
            environ=environment,
            platform_name=platform,
            require_existing=False,
        )
        runtime_roots = {
            "runtime state root": runtime_paths.state_root,
            "runtime log root": runtime_paths.log_root,
            "runtime cache root": runtime_paths.cache_root,
            "runtime temp root": runtime_paths.temp_root,
        }
    except RuntimePathError as exc:
        blockers.append(f"runtime path policy is invalid: {exc}")

    if path.is_absolute():
        for label, root in {"immutable release": ROOT, **runtime_roots}.items():
            try:
                if paths_overlap(path, root):
                    blockers.append(f"FOULER_ENV_FILE overlaps {label}")
            except RuntimePathError as exc:
                blockers.append(f"FOULER_ENV_FILE is invalid: {exc}")
                break

    reparse_components = _reparse_components(path) if path.is_absolute() else []
    if reparse_components:
        blockers.append("FOULER_ENV_FILE path contains a symlink or reparse point")
    exists = path.exists()
    regular, read_only = _read_only_regular_file(path, platform_name=platform)
    if not exists:
        blockers.append("protected FOULER_ENV_FILE is missing")
    elif not regular:
        blockers.append("protected FOULER_ENV_FILE is not a regular file")
    elif not read_only:
        blockers.append("protected FOULER_ENV_FILE is writable")
    return {
        "ok": not blockers,
        "required": True,
        "blockers": blockers,
        "paths": [str(path)],
        "path": str(path.absolute()) if path.is_absolute() else str(path),
        "regularFile": regular,
        "readOnly": read_only,
        "reparseComponents": reparse_components,
    }


def secure_env_files(
    *,
    execute: bool,
    env: dict[str, str] | None = None,
    platform_name: str | None = None,
) -> list[dict[str, Any]]:
    environment = dict(os.environ if env is None else env)
    platform = os.name if platform_name is None else platform_name
    policy = production_env_file_status(environment, platform_name=platform)
    if policy["required"]:
        return [
            {
                "path": policy.get("path"),
                "changed": False,
                "ok": policy["ok"],
                "readOnly": policy.get("readOnly", False),
                "regularFile": policy.get("regularFile", False),
                "reparseComponents": policy.get("reparseComponents", []),
                "blockers": policy.get("blockers", []),
                "policy": "protected-windows-production-env/v1",
            }
        ]

    items: list[dict[str, Any]] = []
    for path in env_file_candidates(environment, platform_name=platform):
        if not path.exists():
            continue
        mode = path.stat().st_mode & 0o777
        item: dict[str, Any] = {"path": str(path), "mode": oct(mode), "changed": False}
        if execute and platform != "nt" and mode != 0o600:
            path.chmod(0o600)
            item["changed"] = True
            mode = path.stat().st_mode & 0o777
            item["mode"] = oct(mode)
        item["ok"] = platform == "nt" or mode == 0o600
        items.append(item)
    return items


def secure_env_file_report(*, execute: bool, env: dict[str, str]) -> list[dict[str, Any]]:
    try:
        return secure_env_files(execute=execute, env=env)
    except TypeError:
        # Preserve narrow test/embedding shims that implement the historical
        # execute-only call contract.
        return secure_env_files(execute=execute)


def start_process(command: list[str], pid_file: Path, env: dict[str, str]) -> dict[str, Any]:
    if pid_file == BATTLE_PID_FILE:
        existing_battle_runner = existing_battle_runner_start_result(command)
        if existing_battle_runner is not None:
            return existing_battle_runner
    alive, pid = pid_alive(pid_file)
    if alive:
        return {"pidFile": str(pid_file), "alreadyRunning": True, "pid": pid, "command": command}
    existing_pid = _find_existing_process(command)
    stale_existing: dict[str, Any] | None = None
    if existing_pid is not None and adopted_process_ready(pid_file, command):
        write_pid_value(
            pid_file,
            existing_pid,
            command,
            adoptedExistingProcess=True,
            previousPid=pid,
        )
        return {
            "pidFile": str(pid_file),
            "alreadyRunning": True,
            "pid": existing_pid,
            "command": command,
            "adoptedExistingProcess": True,
            "previousPid": pid,
        }
    elif existing_pid is not None:
        stale_existing = {
            "pid": existing_pid,
            "reason": "matching process existed but readiness probe failed",
        }
        if is_obs_http_command(command):
            stale_existing["termination"] = terminate_process_pid(
                existing_pid,
                force=True,
                reason="OBS HTTP process matched command but /health was unavailable before restart",
            )
        try:
            pid_file.unlink()
        except OSError:
            pass
    if existing_pid is None and is_obs_http_command(command) and obs_http_ready():
        removed_stale_pid_file = False
        try:
            pid_file.unlink()
            removed_stale_pid_file = True
        except OSError:
            pass
        return {
            "pidFile": str(pid_file),
            "alreadyRunning": True,
            "pid": None,
            "command": command,
            "adoptedHealthyEndpoint": True,
            "externalLifecycleOwner": True,
            "previousPid": pid,
            "removedStalePidFile": removed_stale_pid_file,
        }
    PID_DIR.mkdir(parents=True, exist_ok=True)
    log_path = runtime_log_root(env) / f"{pid_file.stem}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_rotation = rotate_child_log_before_append(log_path, env)
    handle = log_path.open("a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | breakaway
        )
    proc = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    write_pid(pid_file, proc, command)
    payload = {"pidFile": str(pid_file), "pid": proc.pid, "log": str(log_path), "command": command}
    if log_rotation is not None:
        payload["logRotation"] = log_rotation
    if stale_existing is not None:
        payload["staleExistingProcess"] = stale_existing
    return payload


def process_age_seconds(pid: int) -> float | None:
    try:
        import psutil  # type: ignore

        return max(0.0, time.time() - psutil.Process(pid).create_time())
    except Exception:
        return None


def process_parent_pid(pid: int) -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.Process(pid).ppid())
    except Exception:
        return None


def battle_pid_files() -> list[Path]:
    return [BOT_LOCK_PID_FILE, BATTLE_PID_FILE]


def live_battle_runner_owners() -> list[dict[str, Any]]:
    runners: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for pid_file in battle_pid_files():
        alive, pid = pid_alive(pid_file)
        if not alive or pid is None:
            continue
        key = (str(pid_file), int(pid))
        if key in seen:
            continue
        seen.add(key)
        runner = {
            "pidFile": str(pid_file),
            "pid": int(pid),
        }
        parent_pid = process_parent_pid(int(pid))
        if parent_pid is not None:
            runner["parentPid"] = parent_pid
        runners.append(runner)
    return runners


def _distinct_battle_runner_pids(runners: list[dict[str, Any]]) -> list[int]:
    return sorted({int(runner["pid"]) for runner in runners})


def _logical_battle_runner_groups(runners: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_pid = {int(runner["pid"]): runner for runner in runners}
    children_by_parent: dict[int, list[int]] = {}
    for pid, runner in by_pid.items():
        parent_pid = runner.get("parentPid")
        if parent_pid is None:
            continue
        try:
            parent_int = int(parent_pid)
        except (TypeError, ValueError):
            continue
        if parent_int in by_pid:
            children_by_parent.setdefault(parent_int, []).append(pid)

    roots = sorted(
        pid
        for pid, runner in by_pid.items()
        if int(runner.get("parentPid") or 0) not in by_pid
    )
    groups: list[dict[str, Any]] = []
    assigned: set[int] = set()
    for root_pid in roots:
        stack = [root_pid]
        members: list[int] = []
        while stack:
            pid = stack.pop()
            if pid in assigned:
                continue
            assigned.add(pid)
            members.append(pid)
            stack.extend(children_by_parent.get(pid, []))
        groups.append({
            "rootPid": root_pid,
            "memberPids": sorted(members),
            "pidFiles": [
                str(by_pid[pid].get("pidFile"))
                for pid in sorted(members)
                if by_pid.get(pid)
            ],
        })

    for pid in sorted(set(by_pid) - assigned):
        groups.append({
            "rootPid": pid,
            "memberPids": [pid],
            "pidFiles": [str(by_pid[pid].get("pidFile"))],
        })
    return groups


def _battle_runtime_ownership_conflict_payload(
    command: list[str],
    runners: list[dict[str, Any]],
    runner_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    distinct_pids = [int(group["rootPid"]) for group in runner_groups]
    return {
        "alreadyRunning": True,
        "blocked": True,
        "skipped": True,
        "runtimeOwnershipConflict": True,
        "duplicateBattleRunners": True,
        "battleRunnerCount": len(distinct_pids),
        "battleRunnerProcessCount": len(_distinct_battle_runner_pids(runners)),
        "distinctPids": distinct_pids,
        "logicalBattleRunners": runner_groups,
        "knownRunners": runners,
        "adoptedPidFile": None,
        "command": command,
        "requiredHermesAction": "drain/adopt exactly one live battle runner before starting another cycle",
        "reason": "multiple live battle runner owner PIDs found; refusing to spawn or adopt one over another",
    }


def supervisor_alive() -> tuple[bool, int | None]:
    return pid_alive(SUPERVISOR_PID_FILE)


def existing_battle_runner_start_result(command: list[str]) -> dict[str, Any] | None:
    runners = live_battle_runner_owners()
    if not runners:
        return None
    runner_groups = _logical_battle_runner_groups(runners)
    if len(runner_groups) > 1:
        return _battle_runtime_ownership_conflict_payload(command, runners, runner_groups)

    canonical_runner = next(
        (runner for runner in runners if runner["pidFile"] == str(BATTLE_PID_FILE)),
        runners[0],
    )
    adopted = None
    if not any(runner["pidFile"] == str(BATTLE_PID_FILE) for runner in runners):
        write_pid_value(
            BATTLE_PID_FILE,
            int(canonical_runner["pid"]),
            command,
            adoptedExistingProcess=True,
            adoptedFrom=canonical_runner["pidFile"],
        )
        adopted = {
            "pidFile": str(BATTLE_PID_FILE),
            "pid": canonical_runner["pid"],
            "adoptedFrom": canonical_runner["pidFile"],
        }
    return {
        "alreadyRunning": True,
        "pid": canonical_runner["pid"],
        "pidFile": canonical_runner["pidFile"],
        "knownRunners": runners,
        "battleRunnerCount": len(runner_groups),
        "battleRunnerProcessCount": len(_distinct_battle_runner_pids(runners)),
        "logicalBattleRunners": runner_groups,
        "adoptedPidFile": adopted,
        "command": command,
        "reason": "existing battle runner is alive; not spawning duplicate runner",
    }


def any_battle_runner_alive() -> bool:
    return any(pid_alive(path)[0] for path in battle_pid_files())


def supervisor_runtime_state() -> dict[str, Any]:
    active_count = read_active_battles()
    battle_runner_alive = any_battle_runner_alive()
    return {
        "activeBattleCount": active_count,
        "battleRunnerAlive": battle_runner_alive,
        "inFlight": active_count > 0 or battle_runner_alive,
    }


def idle_battle_runner_recovery_candidate(stale_after_seconds: int = IDLE_RUNNER_STALE_SECONDS) -> dict[str, Any]:
    active_battles = read_active_battles()
    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()
    for pid_file in battle_pid_files():
        alive, pid = pid_alive(pid_file)
        item: dict[str, Any] = {
            "pidFile": str(pid_file),
            "pid": pid,
            "alive": alive,
        }
        if pid and pid not in seen:
            seen.add(pid)
            age = process_age_seconds(pid)
            if age is None and pid_file.exists():
                age = max(0.0, time.time() - pid_file.stat().st_mtime)
            item["ageSeconds"] = round(age, 3) if age is not None else None
            item["stale"] = bool(alive and age is not None and age >= stale_after_seconds)
        else:
            item["stale"] = False
        candidates.append(item)
    stale_alive = [item for item in candidates if item.get("alive") and item.get("stale")]
    live_young = [item for item in candidates if item.get("alive") and not item.get("stale")]
    should_recover = bool(active_battles <= 0 and stale_alive and not live_young)
    return {
        "activeBattleCount": active_battles,
        "staleAfterSeconds": stale_after_seconds,
        "candidates": candidates,
        "staleAliveCount": len(stale_alive),
        "liveYoungCount": len(live_young),
        "shouldRecover": should_recover,
        "reason": (
            "idle stale runner is safe to drain/recover"
            if should_recover
            else "active battle or non-stale runner blocks recovery"
        ),
    }


def learning_cycle_completed_after_cycle(
    *,
    battle_was_in_flight: bool,
    pre_cycle_runtime: dict[str, Any],
    cycle: dict[str, Any],
) -> bool:
    recovered_idle_runner = bool(
        isinstance(cycle.get("staleBattleRuntimeRecovery"), dict)
        and cycle["staleBattleRuntimeRecovery"].get("recovered")
    )
    return bool(
        battle_was_in_flight
        and cycle.get("proofRefreshed")
        and (not pre_cycle_runtime.get("inFlight") or recovered_idle_runner)
    )


def clear_stale_active_battles(
    *,
    execute: bool,
    stale_after_seconds: int,
    force: bool = False,
    clear_reason: str = "stale active battle truth had no live battle runner",
) -> dict[str, Any]:
    active_count = read_active_battles()
    age = active_battles_age_seconds()
    runner_alive = any_battle_runner_alive()
    path = active_battles_path()
    truth_exists = path.exists()
    stale_truth = bool(age is not None and age >= stale_after_seconds)
    payload: dict[str, Any] = {
        "activeBattleCount": active_count,
        "ageSeconds": round(age, 3) if age is not None else None,
        "activeBattleTruthExists": truth_exists,
        "stale": stale_truth,
        "battleRunnerAlive": runner_alive,
        "execute": execute,
        "staleAfterSeconds": stale_after_seconds,
        "force": force,
        "cleared": False,
    }
    if runner_alive and not force:
        payload["reason"] = "battle runner is alive; preserving active battle truth"
        return payload
    if active_count <= 0 and not (force and truth_exists) and not (truth_exists and stale_truth):
        payload["reason"] = "no stale active battle truth to clear"
        return payload
    if not force and not stale_truth:
        payload["reason"] = "active battle truth is not stale enough to clear"
        return payload
    if not execute:
        payload["reason"] = "dry run; stale active battle truth cleanup planned only"
        return payload

    STALE_BATTLE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = STALE_BATTLE_BACKUP_DIR / f"active_battles-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    if path.exists():
        shutil.copy2(path, backup)
        payload["backupPath"] = str(backup)
    replacement = {
        "battles": [],
        "count": 0,
        "max_slots": DEFAULT_MAX_CONCURRENT,
        "updated": iso_now(),
        "clearedBy": "HERMES devstream_session stop" if force else "HERMES devstream_session start",
        "clearReason": clear_reason,
        "previousBattleCount": active_count,
        "previousTruthWasEmpty": active_count <= 0,
    }
    try:
        _atomic_write_text(path, json.dumps(replacement, indent=2, sort_keys=True) + "\n")
    except PermissionError:
        path.chmod(0o666)
        _atomic_write_text(path, json.dumps(replacement, indent=2, sort_keys=True) + "\n")
        payload["permissionRepair"] = "chmod 666 before rewrite"
    payload["cleared"] = True
    if force:
        payload["reason"] = "active battle truth cleared after forced stop"
    elif active_count <= 0:
        payload["reason"] = "stale empty active battle truth refreshed before bounded session start"
    else:
        payload["reason"] = "stale active battle truth cleared before bounded session start"
    return payload


def archive_stale_stream_status(
    *,
    execute: bool,
    stale_after_seconds: int = STALE_STREAM_TRUTH_SECONDS,
    force: bool = False,
    clear_reason: str = "stale stream status had no live battle runner",
) -> dict[str, Any]:
    path = STREAM_STATUS_FILE
    stream_payload = read_json_object(path)
    age = file_age_seconds(path)
    runner_alive = any_battle_runner_alive()
    truth_exists = path.exists()
    stale_truth = bool(age is not None and age >= stale_after_seconds)
    claims_runtime = stream_status_claims_runtime(stream_payload)
    status = str(stream_payload.get("status") or "").strip()
    payload: dict[str, Any] = {
        "path": str(path),
        "exists": truth_exists,
        "status": status or None,
        "streaming": stream_payload.get("streaming"),
        "streamPid": stream_payload.get("stream_pid"),
        "claimsRuntime": claims_runtime,
        "ageSeconds": round(age, 3) if age is not None else None,
        "staleAfterSeconds": stale_after_seconds,
        "stale": stale_truth,
        "battleRunnerAlive": runner_alive,
        "execute": execute,
        "force": force,
        "archived": False,
        "blocked": False,
    }
    if runner_alive and not force:
        payload["reason"] = "battle runner is alive; preserving stream status truth"
        return payload
    if not truth_exists:
        payload["reason"] = "stream status truth does not exist"
        return payload
    if not force and not claims_runtime:
        payload["reason"] = "stream status does not claim active runtime"
        return payload
    if not force and not stale_truth:
        payload["reason"] = "stream status truth is not stale enough to archive"
        return payload
    if not execute:
        payload["reason"] = "dry run; stale stream status cleanup planned only"
        payload["plannedAction"] = "archive stale stream_status.json and publish runtime_blocked status"
        return payload

    STALE_STREAM_STATUS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = STALE_STREAM_STATUS_BACKUP_DIR / f"stream_status-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    shutil.copy2(path, backup)
    now = iso_now()
    replacement = dict(state_store.DEFAULT_STATUS)
    replacement.update(
        {
            "status": "Runtime blocked",
            "battle_info": clear_reason,
            "streaming": False,
            "stream_pid": None,
            "updated": now,
            "runtime_blocked": True,
            "blocker_code": "stale_stream_status_archived",
            "blocker_summary": clear_reason,
            "archivedBy": "HERMES devstream_session cleanup-stale-truth",
            "archivedAt": now,
            "archivePath": str(backup),
            "previousStatus": status or None,
            "previousStreaming": stream_payload.get("streaming"),
            "previousStreamPid": stream_payload.get("stream_pid"),
        }
    )
    try:
        _atomic_write_text(path, json.dumps(replacement, indent=2, sort_keys=True) + "\n")
    except PermissionError:
        path.chmod(0o666)
        _atomic_write_text(path, json.dumps(replacement, indent=2, sort_keys=True) + "\n")
        payload["permissionRepair"] = "chmod 666 before rewrite"
    payload["backupPath"] = str(backup)
    payload["archived"] = True
    payload["blocked"] = True
    payload["reason"] = "stale stream status archived and replaced with runtime blocked truth"
    return payload


def recover_stale_battle_runtime(*, execute: bool, stale_after_seconds: int) -> dict[str, Any]:
    """Replace only idle, stale battle runners through the HERMES start path.

    A live battle is never interrupted. This only clears singleton runners that
    have produced no active battle proof beyond the queue timeout, which is the
    same condition reported as a runtime blocker by devstream_health.py.
    """
    active_battles = read_active_battles()
    payload: dict[str, Any] = {
        "activeBattleCount": active_battles,
        "execute": execute,
        "staleAfterSeconds": stale_after_seconds,
        "candidates": [],
        "actions": [],
        "recovered": False,
    }
    if active_battles > 0:
        payload["reason"] = "active battles are present; not replacing runner"
        return payload

    seen: set[int] = set()
    for pid_file in battle_pid_files():
        alive, pid = pid_alive(pid_file)
        item: dict[str, Any] = {
            "pidFile": str(pid_file),
            "pid": pid,
            "alive": alive,
        }
        if pid and pid not in seen:
            seen.add(pid)
            age = process_age_seconds(pid)
            if age is None and pid_file.exists():
                age = max(0.0, time.time() - pid_file.stat().st_mtime)
            item["ageSeconds"] = round(age, 3) if age is not None else None
            item["stale"] = bool(alive and age is not None and age >= stale_after_seconds)
        else:
            item["stale"] = False
        payload["candidates"].append(item)

    stale_alive = [item for item in payload["candidates"] if item.get("alive") and item.get("stale")]
    live_young = [item for item in payload["candidates"] if item.get("alive") and not item.get("stale")]
    if live_young:
        payload["reason"] = "battle runner is alive but not stale enough to replace"
        return payload
    if not stale_alive:
        payload["reason"] = "no stale live battle runner found"
        return payload
    if not execute:
        payload["reason"] = "dry run; stale runner replacement planned only"
        return payload

    PID_DIR.mkdir(parents=True, exist_ok=True)
    DRAIN_FILE.write_text(iso_now() + "\n", encoding="utf-8")
    for pid_file in battle_pid_files():
        action = terminate_pid_file(pid_file, force=True)
        if read_pid(pid_file) is not None:
            alive, _ = pid_alive(pid_file)
            if not alive:
                try:
                    pid_file.unlink()
                    action["pidFileRemoved"] = True
                except OSError as exc:
                    action["pidFileRemoveError"] = str(exc)
        payload["actions"].append(action)
    failed_actions = [
        action
        for action in payload["actions"]
        if action.get("wasRunning") and not action.get("stopped")
    ]
    if failed_actions:
        payload["reason"] = "stale battle process tree survived or could not be verified"
        payload["terminationFailures"] = failed_actions
        return payload
    payload["recovered"] = True
    payload["reason"] = "stale idle battle runner replaced before bounded session start"
    return payload


def obs_http_env(env: dict[str, str]) -> dict[str, str]:
    obs_env = dict(env)
    # The devstream start command exits after spawning child processes; the
    # OBS HTTP surface is intentionally stopped later via its PID file.
    obs_env["FP_PARENT_PID"] = "0"
    return obs_env


def detached_child_env(env: dict[str, str]) -> dict[str, str]:
    child_env = dict(env)
    # The bounded devstream launcher is a one-shot supervisor that records PID
    # files and exits. Runtime children must survive that exit so OBS can keep
    # showing active battles during unattended rehearsal/live-build audits.
    child_env["FP_PARENT_PID"] = "0"
    return child_env


def _owned_process_tree_snapshots(
    pid: int,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    root_snapshot = _process_snapshot(pid)
    if not root_snapshot or not _pid_matches_expected_process(Path("."), pid, payload):
        return [], "root PID no longer matches the complete recorded process identity"
    snapshots = [{"pid": pid, **root_snapshot}]
    try:
        import psutil  # type: ignore

        root_process = psutil.Process(pid)
        for child in root_process.children(recursive=True):
            try:
                snapshots.append(
                    {
                        "pid": int(child.pid),
                        "running": bool(child.is_running()),
                        "cmdline": [str(item) for item in child.cmdline()],
                        "cwd": child.cwd(),
                        "createTime": float(child.create_time()),
                        "parentPid": int(child.ppid()),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
    except Exception as exc:
        return snapshots, f"owned process descendants could not be enumerated: {type(exc).__name__}: {exc}"
    return snapshots, None


def _same_process_still_alive(snapshot: dict[str, Any]) -> bool:
    current = _process_snapshot(int(snapshot.get("pid") or 0))
    if not current or not current.get("running"):
        return False
    try:
        return abs(float(current.get("createTime")) - float(snapshot.get("createTime"))) <= 2
    except (TypeError, ValueError):
        return True


def _expected_ladder_commands(payload: dict[str, Any]) -> list[list[str]]:
    command = payload.get("command") if isinstance(payload.get("command"), list) else []
    if not command:
        return []
    commands: list[list[str]] = []
    names = [Path(str(part)).name.lower() for part in command]
    if "run.py" in names:
        run_index = names.index("run.py")
        start_index = max(0, run_index - 1)
        commands.append([str(item) for item in command[start_index:]])
    if "run_bounded_battle_session.py" in names and "--" in command:
        separator = command.index("--")
        nested = [str(item) for item in command[separator + 1 :]]
        if nested:
            commands.append(nested)
    return commands


def _rescan_exact_ladder_processes(expected_commands: list[list[str]]) -> tuple[list[dict[str, Any]], str | None]:
    if not expected_commands:
        return [], None
    matches: list[dict[str, Any]] = []
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["pid", "cmdline", "cwd", "status", "create_time"]):
            try:
                if proc.info.get("status") == getattr(psutil, "STATUS_ZOMBIE", "zombie"):
                    continue
                snapshot = {
                    "pid": int(proc.info.get("pid") or 0),
                    "cmdline": [str(item) for item in (proc.info.get("cmdline") or [])],
                    "cwd": proc.info.get("cwd"),
                    "createTime": proc.info.get("create_time"),
                }
                if not _process_cwd_matches_root(snapshot):
                    continue
                if any(
                    _command_identity_matches(expected, snapshot["cmdline"])
                    for expected in expected_commands
                ):
                    matches.append(snapshot)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, TypeError, ValueError):
                continue
    except Exception as exc:
        return matches, f"post-termination ladder rescan failed: {type(exc).__name__}: {exc}"
    return matches, None


def _terminate_owned_process_tree(
    path: Path,
    pid: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "pidFile": str(path),
        "pid": pid,
        "wasRunning": True,
        "stopped": False,
        "treeVerified": False,
    }
    snapshots, enumeration_error = _owned_process_tree_snapshots(pid, payload)
    item["ownedTreePids"] = [int(snapshot["pid"]) for snapshot in snapshots]
    if enumeration_error:
        item["enumerationError"] = enumeration_error
    try:
        if os.name == "nt":
            killed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            item["sent"] = "taskkill /PID /T /F"
            item["taskkillReturnCode"] = killed.returncode
            item["taskkillStdout"] = tail_text(killed.stdout, 1000)
            item["taskkillStderr"] = tail_text(killed.stderr, 1000)
        else:
            try:
                import psutil  # type: ignore

                root_process = psutil.Process(pid)
                descendants = root_process.children(recursive=True)
                for process in reversed(descendants):
                    try:
                        process.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                root_process.terminate()
                _, alive = psutil.wait_procs([*descendants, root_process], timeout=10)
                for process in alive:
                    try:
                        process.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                item["sent"] = "psutil terminate/kill tree"
            except Exception as exc:
                item["terminateError"] = f"{type(exc).__name__}: {exc}"
    except (OSError, subprocess.SubprocessError) as exc:
        item["terminateError"] = f"{type(exc).__name__}: {exc}"

    deadline = time.time() + 10
    captured_survivors = [snapshot for snapshot in snapshots if _same_process_still_alive(snapshot)]
    while captured_survivors and time.time() < deadline:
        time.sleep(0.2)
        captured_survivors = [snapshot for snapshot in snapshots if _same_process_still_alive(snapshot)]
    ladder_survivors, rescan_error = _rescan_exact_ladder_processes(_expected_ladder_commands(payload))
    survivors_by_pid = {
        int(snapshot["pid"]): snapshot
        for snapshot in [*captured_survivors, *ladder_survivors]
    }
    if rescan_error:
        item["rescanError"] = rescan_error
    item["survivors"] = list(survivors_by_pid.values())
    item["treeVerified"] = not enumeration_error and not rescan_error
    item["stopped"] = bool(item["treeVerified"] and not survivors_by_pid)
    if survivors_by_pid:
        item["error"] = "owned process tree or exact ladder descendant survived termination"
    elif not item["treeVerified"]:
        item["error"] = "owned process tree termination could not be fully verified"
    return item


def terminate_pid_file(path: Path, *, force: bool = False) -> dict[str, Any]:
    payload = read_pid_payload(path)
    pid = read_pid(path)
    alive = bool(pid and _pid_matches_expected_process(path, pid, payload))
    item: dict[str, Any] = {"pidFile": str(path), "pid": pid, "wasRunning": alive}
    if not alive or pid is None:
        if pid is not None:
            item["skipped"] = True
            item["reason"] = "PID does not match the complete recorded command identity and release root"
        return item
    assert isinstance(payload, dict)
    item = _terminate_owned_process_tree(path, pid, payload)
    item["forceRequested"] = force
    return item


def terminate_process_pid(pid: int, *, force: bool = False, reason: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {"pid": pid, "wasRunning": False}
    if reason:
        item["reason"] = reason
    if pid <= 0:
        return item
    try:
        os.kill(pid, 0)
        item["wasRunning"] = True
    except OSError:
        return item
    try:
        os.kill(pid, signal.SIGTERM)
        item["sent"] = "SIGTERM"
    except OSError as exc:
        item["error"] = str(exc)
        return item
    for _ in range(15):
        try:
            os.kill(pid, 0)
        except OSError:
            item["stopped"] = True
            return item
        time.sleep(0.2)
    if force:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            item["forced"] = True
        except OSError as exc:
            item["forceError"] = str(exc)
    return item


def terminate_battle_runners(*, force: bool = False) -> dict[str, Any]:
    return {
        path.name: terminate_pid_file(path, force=force)
        for path in battle_pid_files()
    }


def tail_text(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _output_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def terminate_supervisor_process_tree(process: subprocess.Popen) -> dict[str, Any]:
    """Terminate a timed-out child and all descendants before the supervisor proceeds."""
    detail: dict[str, Any] = {"pid": process.pid, "platform": os.name, "stopped": False}
    if process.poll() is not None:
        detail["stopped"] = True
        detail["returnCode"] = process.returncode
        return detail
    snapshots: list[dict[str, Any]] = []
    enumeration_error: str | None = None
    root_snapshot = _process_snapshot(process.pid)
    if root_snapshot:
        snapshots.append({"pid": process.pid, **root_snapshot})
    try:
        import psutil  # type: ignore

        for child in psutil.Process(process.pid).children(recursive=True):
            try:
                child_snapshot = _process_snapshot(int(child.pid))
                if child_snapshot:
                    snapshots.append({"pid": int(child.pid), **child_snapshot})
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
    except Exception as exc:
        enumeration_error = f"{type(exc).__name__}: {exc}"
        detail["enumerationError"] = enumeration_error
    detail["ownedTreePids"] = [int(snapshot["pid"]) for snapshot in snapshots]
    try:
        if os.name == "nt":
            killed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            detail["taskkillReturnCode"] = killed.returncode
            detail["taskkillStdout"] = tail_text(killed.stdout, 1000)
            detail["taskkillStderr"] = tail_text(killed.stderr, 1000)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            detail["signal"] = "SIGTERM"
    except (OSError, subprocess.SubprocessError) as exc:
        detail["terminateError"] = f"{type(exc).__name__}: {exc}"
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            detail["forced"] = True
            process.wait(timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            detail["forceError"] = f"{type(exc).__name__}: {exc}"
    detail["returnCode"] = process.poll()
    survivors = [snapshot for snapshot in snapshots if _same_process_still_alive(snapshot)]
    detail["survivors"] = survivors
    detail["stopped"] = bool(
        process.poll() is not None
        and not survivors
        and enumeration_error is None
    )
    return detail


def run_supervisor_command(
    command: list[str],
    *,
    timeout: int,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.time()
    child_env = prepare_runtime_env(load_env_files())
    if env_overrides:
        child_env.update({str(key): str(value) for key, value in env_overrides.items()})
    process: subprocess.Popen | None = None
    try:
        popen_options: dict[str, Any] = {}
        if os.name == "nt":
            popen_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_options["start_new_session"] = True
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **popen_options,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        result = {
            "command": command,
            "returnCode": process.returncode,
            "stdoutTail": tail_text(stdout or ""),
            "stderrTail": tail_text(stderr or ""),
            "durationSeconds": round(time.time() - started, 3),
        }
        try:
            parsed = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            result["json"] = parsed
        return result
    except subprocess.TimeoutExpired as exc:
        cleanup = terminate_supervisor_process_tree(process) if process is not None else {"stopped": False}
        remainder_stdout = ""
        remainder_stderr = ""
        if process is not None:
            try:
                remainder_stdout, remainder_stderr = process.communicate(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        return {
            "command": command,
            "returnCode": None,
            "timedOut": True,
            "processTreeCleanup": cleanup,
            "stdoutTail": tail_text(_output_text(exc.stdout) + _output_text(remainder_stdout)),
            "stderrTail": tail_text(_output_text(exc.stderr) + _output_text(remainder_stderr)),
            "durationSeconds": round(time.time() - started, 3),
        }
    except Exception as exc:
        cleanup = None
        if process is not None and process.poll() is None:
            cleanup = terminate_supervisor_process_tree(process)
        return {
            "command": command,
            "returnCode": None,
            "error": f"{type(exc).__name__}: {exc}",
            "processTreeCleanup": cleanup,
            "durationSeconds": round(time.time() - started, 3),
        }


def improvement_checkout_guard() -> dict[str, Any]:
    blockers: list[str] = []
    dirty_engine: list[str] = []
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", "fp"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        dirty_engine = [line for line in status.stdout.splitlines() if line.strip()]
        if status.returncode:
            blockers.append("engine checkout status command failed")
        elif dirty_engine:
            blockers.append("engine checkout has uncommitted or untracked changes")
    except (OSError, subprocess.SubprocessError) as exc:
        blockers.append(f"engine checkout status failed: {type(exc).__name__}: {exc}")
    if IMPROVE_AGENT_LOCK_FILE.exists():
        blockers.append("improve-agent lock exists")
    if IMPROVE_AGENT_RECOVERY_BLOCK_FILE.exists():
        blockers.append("improve-agent recovery block exists")
    patch_artifact = ROOT / ".agent_diff.patch"
    if patch_artifact.exists():
        blockers.append("stale improve-agent patch artifact exists")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "dirtyEngineEntries": dirty_engine,
        "lockPath": str(IMPROVE_AGENT_LOCK_FILE),
        "lockExists": IMPROVE_AGENT_LOCK_FILE.exists(),
        "recoveryBlockPath": str(IMPROVE_AGENT_RECOVERY_BLOCK_FILE),
        "recoveryBlockExists": IMPROVE_AGENT_RECOVERY_BLOCK_FILE.exists(),
        "patchArtifactExists": patch_artifact.exists(),
    }


def termination_failures(payload: object) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if payload.get("wasRunning") and not payload.get("stopped"):
            failures.append(payload)
        for value in payload.values():
            if isinstance(value, dict):
                failures.extend(termination_failures(value))
    return failures


def write_supervisor_status(payload: dict[str, Any]) -> None:
    SUPERVISOR_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUPERVISOR_STATUS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def runtime_launch_preflight(
    args: argparse.Namespace,
    *,
    env: dict[str, str] | None = None,
    lease_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate credential and finite launch blockers before broker reserve."""

    env = dict(load_launch_environment(args) if env is None else env)
    blockers: list[str] = []
    lease_bound = bool(lease_guard and lease_guard.get("ok"))
    policy_env = dict(env)
    if lease_bound:
        policy_env.setdefault(
            RUNTIME_LEASE_PATH_ENV,
            str(getattr(args, "runtime_lease", None) or "validated-runtime-lease"),
        )
    production = is_production_runtime(ROOT, environ=policy_env)
    env_file_policy = production_env_file_status(policy_env)
    if env_file_policy["required"] and not env_file_policy["ok"]:
        blockers.extend(str(item) for item in env_file_policy["blockers"])

    username_present = bool(env_value(env, "PS_USERNAME", "SHOWDOWN_USER_ID"))
    password_required = showdown_password_required(env)
    password_present = bool(env_value(env, "PS_PASSWORD"))
    if not username_present:
        blockers.append("PS_USERNAME or SHOWDOWN_USER_ID is required")
    if password_required and not password_present:
        blockers.append("PS_PASSWORD is required for registered Showdown ladder sessions")
    credential_failure = recent_showdown_credential_failure(ROOT)
    if credential_failure.get("found"):
        blockers.append(
            str(
                credential_failure.get("summary")
                or "recent Showdown credential failure is unresolved"
            )
        )
    concurrency = positive_int(getattr(args, "max_concurrent_battles", 0), 0)
    configured_parallelism = positive_int(
        env_value(env, "SEARCH_PARALLELISM", default=str(PILOT_SEARCH_PARALLELISM)),
        0,
    )
    if production or lease_bound:
        if concurrency != DEFAULT_MAX_CONCURRENT:
            blockers.append("production pilot max concurrent battles must equal three")
        if configured_parallelism != PILOT_SEARCH_PARALLELISM:
            blockers.append("production pilot search parallelism must equal two")
    elif concurrency < 1 or concurrency > 3:
        blockers.append("max concurrent battles must be between one and three")

    account_season_authority: dict[str, Any] = {
        "schemaVersion": "fouler-account-season-authority-check/v1",
        "ok": True,
        "required": False,
        "blockers": [],
        "runtimeMirrorAuthoritative": False,
    }
    if production or lease_bound:
        account_season_authority = account_season_authority_check(
            lease_guard or {},
            env=policy_env,
        )
        account_season_authority["required"] = True
        if not account_season_authority["ok"]:
            blockers.extend(
                str(item) for item in account_season_authority.get("blockers") or []
            )
    return {
        "ok": not blockers,
        "blockers": blockers,
        "production": production,
        "leaseBound": lease_bound,
        "searchParallelism": configured_parallelism,
        "environmentFilePolicy": env_file_policy,
        "accountSeasonAuthority": account_season_authority,
        "usernamePresent": username_present,
        "passwordRequired": password_required,
        "passwordPresent": password_present,
        "recentCredentialFailure": bool(credential_failure.get("found")),
        "credentialFailureCode": credential_failure.get("code"),
        "secretValuesPrinted": False,
    }


def run_supervisor_cycle(
    args: argparse.Namespace,
    cycle_index: int,
    *,
    start_next: bool = True,
    authority_ok: bool = True,
    lease_guard: dict[str, Any] | None = None,
    supervisor_instance_id: str | None = None,
    reservation_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_count = effective_run_count(getattr(args, "run_count", DEFAULT_RUN_COUNT))
    active_count = read_active_battles()
    battle_runner_alive = any_battle_runner_alive()
    payload: dict[str, Any] = {
        "schemaVersion": "fouler-play-battle-supervisor-cycle/v1",
        "checkedAt": iso_now(),
        "cycleIndex": cycle_index,
        "activeBattleCount": active_count,
        "battleRunnerAlive": battle_runner_alive,
        "actions": [],
        "requestedRunCount": getattr(args, "run_count", DEFAULT_RUN_COUNT),
        "effectiveRunCount": effective_count,
        "startNextBattleSession": start_next,
        "parentRuntimeAuthorityValid": authority_ok,
    }
    if not authority_ok:
        payload["state"] = "blocked-runtime-lease"
        payload["autoImprove"] = {
            "enabled": False,
            "reason": "parent runtime lease is invalid",
            "sentinel": AUTO_IMPROVE_SENTINEL,
        }
        payload["startSkipped"] = {
            "reason": "parent runtime lease is invalid; no proof, improvement, or runtime child may start",
        }
        payload["nextAction"] = "renew and revalidate the parent runtime lease before any bounded child action"
        return payload
    queue_timeout_seconds = positive_int(getattr(args, "queue_timeout_seconds", 180), 180)
    if active_count > 0 and not battle_runner_alive:
        stale_clear = clear_stale_active_battles(
            execute=True,
            stale_after_seconds=max(queue_timeout_seconds, 60),
            clear_reason="stale active battle truth blocked three-slot supervisor restart",
        )
        payload["staleActiveBattleClear"] = stale_clear
        if stale_clear.get("cleared"):
            active_count = read_active_battles()
            battle_runner_alive = any_battle_runner_alive()
            payload["activeBattleCountAfterClear"] = active_count
            payload["battleRunnerAliveAfterClear"] = battle_runner_alive

    idle_recovery = idle_battle_runner_recovery_candidate(max(IDLE_RUNNER_STALE_SECONDS, queue_timeout_seconds))
    payload["idleRunnerRecovery"] = idle_recovery
    if active_count > 0 or (battle_runner_alive and not idle_recovery.get("shouldRecover")):
        payload["state"] = "battle-cycle-in-flight"
        payload["nextAction"] = "wait for active battle runner/drain before proof refresh"
        return payload
    if battle_runner_alive and idle_recovery.get("shouldRecover"):
        active_truth_age = active_battles_age_seconds()
        if active_truth_age is not None and active_truth_age < RESULT_PERSISTENCE_GRACE_SECONDS:
            payload["resultPersistenceGrace"] = {
                "activeBattleTruthAgeSeconds": round(active_truth_age, 3),
                "minimumGraceSeconds": RESULT_PERSISTENCE_GRACE_SECONDS,
                "reason": "recently cleared active battle truth may still be waiting for run.py to persist battle_stats",
            }
            payload["state"] = "result-persistence-grace"
            payload["nextAction"] = "wait for run.py to persist completed battle_stats before stale idle runner recovery"
            return payload
        payload["completedBattleLogResultRecoveryBeforeRuntimeRecovery"] = (
            recover_completed_battle_results_from_logs(execute=True)
        )
        recovery = recover_stale_battle_runtime(
            execute=True,
            stale_after_seconds=int(idle_recovery.get("staleAfterSeconds") or IDLE_RUNNER_STALE_SECONDS),
        )
        payload["staleBattleRuntimeRecovery"] = recovery
        payload["completedBattleLogResultRecovery"] = recover_completed_battle_results_from_logs(execute=True)
        active_count = read_active_battles()
        battle_runner_alive = any_battle_runner_alive()
        payload["activeBattleCountAfterRecovery"] = active_count
        payload["battleRunnerAliveAfterRecovery"] = battle_runner_alive
        if active_count > 0 or battle_runner_alive:
            payload["state"] = "battle-cycle-in-flight"
            payload["nextAction"] = "stale idle runner recovery attempted; wait for drain before proof refresh"
            return payload

    if reservation_state:
        reservation_status = runtime_reservation_status(
            lease_guard or {}, reservation_state
        )
        payload["leaseConsumptionStatus"] = reservation_status
        if not reservation_status.get("ok"):
            payload["state"] = "blocked-lease-consumption-reconciliation"
            payload["nextAction"] = (
                "an outstanding reservation could not be authoritatively queried; "
                "no second reservation will be created"
            )
            return payload
        status_payload = (
            reservation_status.get("status")
            if isinstance(reservation_status.get("status"), dict)
            else {}
        )
        if status_payload.get("state") == "completed":
            payload["leaseConsumptionTerminal"] = status_payload
            reservation_state.clear()
        elif status_payload.get("state") in {"reserved", "claimed"}:
            outcome = "aborted" if status_payload.get("state") == "reserved" else "failed"
            reconciliation = complete_runtime_lease_consumption(
                lease_guard or {}, reservation=reservation_state, outcome=outcome
            )
            payload["leaseConsumptionTerminal"] = reconciliation
            if reconciliation.get("ok") and reconciliation.get("completed"):
                reservation_state.clear()
                payload["state"] = "reconciled-orphaned-runtime"
                payload["nextAction"] = (
                    "orphaned runtime was terminally reconciled without returning capacity; "
                    "review before the next bounded launch"
                )
            else:
                payload["state"] = "blocked-lease-consumption-reconciliation"
                payload["nextAction"] = (
                    "orphaned runtime could not be terminally reconciled"
                )
            return payload
        else:
            payload["state"] = "blocked-lease-consumption-reconciliation"
            payload["nextAction"] = "lease broker returned an unsupported reservation state"
            return payload

    payload["state"] = "idle-restoring-runtime"
    payload["completedBattleLogResultRecovery"] = recover_completed_battle_results_from_logs(
        execute=True
    )
    payload["proofRefreshed"] = True
    py = supervisor_child_python()
    live_start_blockers: list[str] = []
    activation_managed = False
    activation_ready = False
    runtime_authority_env: dict[str, str] = {}
    runtime_lease_text = ""
    deployment_receipt_text = ""
    if isinstance(lease_guard, dict) and lease_guard.get("ok"):
        try:
            runtime_authority_env = lease_environment(lease_guard)
            runtime_lease_text = runtime_authority_env[RUNTIME_LEASE_PATH_ENV]
        except (OSError, ValueError) as exc:
            live_start_blockers.append(f"validated runtime lease environment is unavailable: {exc}")
        lease_summary_payload = (
            lease_guard.get("lease") if isinstance(lease_guard.get("lease"), dict) else {}
        )
        deployment_receipt_text = str(
            lease_summary_payload.get("deploymentReceiptPath") or ""
        ).strip()
    if runtime_lease_text and deployment_receipt_text and not live_start_blockers:
        activation_managed = True
        activation_result = run_supervisor_command(
            [
                py,
                "scripts/fouler_deployment_state.py",
                "--ensure-activation",
                "--root",
                str(ROOT),
                "--deployment-receipt",
                deployment_receipt_text,
                "--runtime-lease",
                runtime_lease_text,
                "--battle-stats",
                str(BATTLE_STATS_FILE),
            ],
            timeout=getattr(args, "proof_timeout_seconds", 300),
            env_overrides=runtime_authority_env,
        )
        payload["deploymentActivation"] = activation_result
        payload["actions"].append(activation_result)
        activation_json = (
            activation_result.get("json")
            if isinstance(activation_result.get("json"), dict)
            else {}
        )
        activation_ready = bool(
            activation_result.get("returnCode") == 0
            and activation_json.get("status") == "active"
        )
        if activation_result.get("returnCode") != 0:
            live_start_blockers.append("deployment activation proof failed")
        elif activation_json.get("status") not in {"active", "waiting-for-first-battle"}:
            live_start_blockers.append("deployment activation returned an unsupported state")
        if activation_ready:
            judgment_result = run_supervisor_command(
                [py, "infrastructure/elo_watchdog.py"],
                timeout=getattr(args, "proof_timeout_seconds", 300),
                env_overrides=runtime_authority_env,
            )
            payload["deploymentJudgment"] = judgment_result
            payload["actions"].append(judgment_result)
            if judgment_result.get("returnCode") not in {0, None}:
                live_start_blockers.append("deployment judgment blocked the next live batch")
    else:
        live_start_blockers.append(
            "live child is not managed by the validated deployment activation/judgment identity"
        )
    payload["deploymentLifecycleManaged"] = activation_managed
    payload["actions"].append(
        run_supervisor_command(
            [py, "pipeline.py", "autoresearch", "-n", str(getattr(args, "autoresearch_count", 30)), "--no-discord"],
            timeout=getattr(args, "proof_timeout_seconds", 300),
            env_overrides=runtime_authority_env or None,
        )
    )
    payload["actions"].append(
        run_supervisor_command(
            [py, "scripts/devstream_cycle_report.py", "--write"],
            timeout=getattr(args, "proof_timeout_seconds", 300),
            env_overrides=runtime_authority_env or None,
        )
    )

    improve_env = load_env_files()
    improve_requested = bool(
        getattr(args, "enable_auto_improve", False)
        or env_flag_enabled(improve_env, AUTO_IMPROVE_SENTINEL)
    )
    _improve_enabled, improve_reason = supervisor_auto_improve_enabled(args, improve_env)
    payload["autoImprove"] = {
        "enabled": False,
        "requested": improve_requested,
        "reason": improve_reason,
        "delegatedTo": "DEKU external control plane",
        "runtimeMayLaunchImprovementAgent": False,
        "sentinel": AUTO_IMPROVE_SENTINEL,
    }

    checkout_guard = improvement_checkout_guard()
    payload["improvementCheckoutGuard"] = checkout_guard
    if not checkout_guard["ready"]:
        live_start_blockers.extend(checkout_guard["blockers"])
    launch_preflight = runtime_launch_preflight(args)
    payload["runtimeLaunchPreflight"] = launch_preflight
    if start_next and not launch_preflight["ok"]:
        live_start_blockers.extend(launch_preflight["blockers"])
    start_command = [
        py,
        "scripts/devstream_session.py",
        "start",
        "--run-count",
        str(effective_count),
        "--max-concurrent-battles",
        str(args.max_concurrent_battles),
        "--queue-timeout-seconds",
        str(args.queue_timeout_seconds),
        "--execute",
    ]
    runtime_lease = runtime_lease_text
    if runtime_lease:
        start_command.extend(["--runtime-lease", runtime_lease])
    if not start_next:
        payload["startSkipped"] = {
            "reason": "bounded learning-cycle limit reached; proof refreshed without launching another batch",
        }
        payload["battleRunnerAliveAfter"] = any_battle_runner_alive()
        payload["activeBattleCountAfter"] = read_active_battles()
        payload["nextAction"] = "bounded learning cycle complete; supervisor may stop"
        return payload
    if not launch_preflight["ok"]:
        payload["state"] = "blocked-runtime-launch-preflight"
        payload["startSkipped"] = {
            "reason": "credential or finite launch preflight failed before reservation",
            "blockers": list(dict.fromkeys(launch_preflight["blockers"])),
        }
        payload["battleRunnerAliveAfter"] = any_battle_runner_alive()
        payload["activeBattleCountAfter"] = read_active_battles()
        payload["nextAction"] = "resolve launch preflight before consuming lease capacity"
        return payload
    if live_start_blockers:
        payload["state"] = "blocked-improvement-checkout"
        payload["startSkipped"] = {
            "reason": "live battle start is blocked until improvement ownership and checkout recovery are clean",
            "blockers": list(dict.fromkeys(live_start_blockers)),
        }
        payload["battleRunnerAliveAfter"] = any_battle_runner_alive()
        payload["activeBattleCountAfter"] = read_active_battles()
        payload["nextAction"] = "inspect improve-agent cleanup/recovery proof before starting another battle"
        return payload
    reservation = reserve_runtime_lease_consumption(
        lease_guard or {},
        run_count=effective_count,
        cycle_index=cycle_index,
        supervisor_instance_id=(supervisor_instance_id or f"supervisor-process-{os.getpid()}"),
        max_concurrent_battles=args.max_concurrent_battles,
        env=runtime_authority_env or None,
    )
    payload["leaseConsumptionReservation"] = {
        **reservation,
        **(
            {"reservation": public_runtime_reservation(reservation["reservation"])}
            if isinstance(reservation.get("reservation"), dict)
            else {}
        ),
    }
    if reservation.get("ok") and reservation.get("exhausted"):
        payload["state"] = "completed-lease-consumption"
        payload["startSkipped"] = {
            "reason": "the append-only lease broker reports that this signed lease is exhausted"
        }
        payload["nextAction"] = "issue a new DEKU-signed lease only after reviewing the completed proof window"
        return payload
    if not reservation.get("ok") or not reservation.get("reserved"):
        payload["state"] = "blocked-lease-consumption"
        payload["startSkipped"] = {
            "reason": "runtime lease cumulative authority could not be reserved",
            "blockers": reservation.get("blockers") or ["lease reservation was not created"],
        }
        payload["nextAction"] = "manually reconcile the external runtime lease consumption ledger"
        return payload
    reservation_payload = (
        reservation.get("reservation")
        if isinstance(reservation.get("reservation"), dict)
        else {}
    )
    if reservation_state is not None:
        reservation_state.clear()
        reservation_state.update(reservation_payload)
    start_env = dict(runtime_authority_env)
    start_env.update(runtime_reservation_environment(reservation_payload))
    start_result = run_supervisor_command(
        start_command,
        timeout=getattr(args, "start_timeout_seconds", 60),
        env_overrides=start_env,
    )
    payload["actions"].append(start_result)
    payload["battleStart"] = start_result
    payload["battleRunnerAliveAfter"] = any_battle_runner_alive()
    payload["activeBattleCountAfter"] = read_active_battles()
    if start_result.get("returnCode") != 0 or start_result.get("timedOut") or start_result.get("error"):
        if payload["battleRunnerAliveAfter"] or payload["activeBattleCountAfter"] > 0:
            payload["state"] = "battle-cycle-in-flight"
            payload["nextAction"] = (
                "launcher reported failure but the bound runtime appeared; preserve the "
                "reservation and monitor its broker terminal state"
            )
            return payload
        reconciliation = complete_runtime_lease_consumption(
            lease_guard or {},
            reservation=reservation_payload,
            outcome="aborted",
            env=runtime_authority_env or None,
        )
        payload["leaseConsumptionTerminal"] = reconciliation
        if reconciliation.get("ok") and reconciliation.get("completed"):
            if reservation_state is not None:
                reservation_state.clear()
            payload["state"] = "blocked-battle-launch"
            payload["nextAction"] = (
                "launch failed before a healthy battle runtime appeared; capacity remains "
                "consumed and the reservation was durably aborted"
            )
        else:
            payload["state"] = "blocked-lease-consumption-reconciliation"
            payload["nextAction"] = (
                "battle launch failed and terminal reconciliation did not complete"
            )
        return payload
    payload["nextAction"] = "monitor bounded battle cycle, then refresh proof and restart if idle"
    return payload


def cmd_supervise(args: argparse.Namespace) -> int:
    cycle_index = 0
    completed_learning_cycles = 0
    battle_was_in_flight = False
    current_reservation: dict[str, Any] = {}
    supervisor_instance_id = f"supervisor-{os.getpid()}-{uuid.uuid4().hex}"
    env = load_launch_environment(args)
    effective_max_cycles, max_cycles_reason = supervisor_cycle_limit(args, env)
    effective_count = effective_run_count(getattr(args, "run_count", DEFAULT_RUN_COUNT), env)
    lease_guard = runtime_lease_guard(
        purpose="devstream-supervise",
        args=args,
        env=env,
        run_count=effective_count,
        max_cycles=effective_max_cycles,
        require_max_cycles=True,
    )
    payload: dict[str, Any] = {
        "schemaVersion": "fouler-play-battle-supervisor/v1",
        "startedAt": iso_now(),
        "pid": os.getpid(),
        "state": "starting",
        "cycles": [],
        "bounds": {
            "runCount": args.run_count,
            "maxConcurrentBattles": args.max_concurrent_battles,
            "queueTimeoutSeconds": args.queue_timeout_seconds,
            "sleepSeconds": args.sleep_seconds,
            "maxCycles": args.max_cycles,
            "effectiveMaxCycles": effective_max_cycles,
            "effectiveRunCount": effective_count,
            "maxCyclesSemantics": "completed bounded learning cycles, not supervisor polling heartbeats",
        },
        "completedLearningCycles": completed_learning_cycles,
        "supervisorInstanceId": supervisor_instance_id,
        "cycleLease": {
            "reason": max_cycles_reason,
            "autoImproveSentinel": AUTO_IMPROVE_SENTINEL,
            "autoImproveMaxCyclesEnv": AUTO_IMPROVE_MAX_CYCLES_ENV,
        },
        "runtimeLease": lease_guard,
        "stopFile": str(SUPERVISOR_STOP_FILE),
        "secretValuesPrinted": False,
    }
    if not lease_guard.get("ok"):
        payload["state"] = "blocked-runtime-lease"
        payload["error"] = runtime_lease_blocked_message(lease_guard)
        write_supervisor_status(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    try:
        args.runtime_lease = resolved_runtime_lease_path(lease_guard)
    except (OSError, ValueError) as exc:
        payload["state"] = "blocked-runtime-lease"
        payload["error"] = f"validated runtime lease path is unavailable: {exc}"
        write_supervisor_status(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    payload["resolvedRuntimeLeasePath"] = args.runtime_lease
    env, runtime_identity = apply_runtime_process_identity(env, lease_guard)
    payload["runtimeIdentity"] = runtime_identity
    if not runtime_identity.get("ok"):
        payload["state"] = "blocked-runtime-identity"
        payload["error"] = "; ".join(str(item) for item in runtime_identity.get("blockers") or [])
        write_supervisor_status(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    launch_preflight = runtime_launch_preflight(args, env=env, lease_guard=lease_guard)
    payload["runtimeLaunchPreflight"] = launch_preflight
    if not launch_preflight["ok"]:
        payload["state"] = "blocked-runtime-launch-preflight"
        payload["error"] = "; ".join(str(item) for item in launch_preflight["blockers"])
        write_supervisor_status(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    for name in (*RUNTIME_PROVENANCE_ENV_NAMES, RUNTIME_LEASE_PATH_ENV):
        if name in env:
            os.environ[name] = env[name]
    lease_consumption = initialize_runtime_lease_consumption(lease_guard, env=env)
    payload["leaseConsumption"] = lease_consumption
    if not lease_consumption.get("ok"):
        payload["state"] = "blocked-lease-consumption"
        payload["error"] = "; ".join(str(item) for item in lease_consumption.get("blockers") or [])
        payload["nextAction"] = "manually reconcile the external runtime lease consumption ledger"
        write_supervisor_status(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    if any_battle_runner_alive() or read_active_battles() > 0:
        payload["state"] = "blocked-unreserved-runtime"
        payload["error"] = (
            "live battle state exists without an in-flight reservation owned by this supervisor"
        )
        payload["nextAction"] = "manually reconcile the live runner and external lease consumption ledger"
        write_supervisor_status(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    if lease_consumption.get("exhausted"):
        payload["state"] = "completed-lease-consumption"
        payload["completedAt"] = iso_now()
        write_supervisor_status(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    payload["deadBattlePidCleanup"] = clear_dead_battle_pid_files(
        reason="supervisor start confirmed recorded battle runner PID is not alive"
    )
    if SUPERVISOR_STOP_FILE.exists():
        SUPERVISOR_STOP_FILE.unlink()
    write_pid_value(
        SUPERVISOR_PID_FILE,
        os.getpid(),
        supervisor_command(
            args.run_count,
            args.max_concurrent_battles,
            args.queue_timeout_seconds,
            args.sleep_seconds,
            enable_auto_improve=getattr(args, "enable_auto_improve", False),
            max_cycles=positive_int(getattr(args, "max_cycles", 0), 0),
            runtime_lease=getattr(args, "runtime_lease", None),
        ),
    )
    payload["state"] = "supervisor-loop-starting"
    payload["lastHeartbeatAt"] = iso_now()
    write_supervisor_status(payload)
    try:
        while True:
            if SUPERVISOR_STOP_FILE.exists():
                payload["state"] = "stopping"
                payload["stopReason"] = "stop file present"
                write_supervisor_status(payload)
                return 0
            cycle_index += 1
            pre_cycle_runtime = supervisor_runtime_state()
            current_lease_guard = runtime_lease_guard(
                purpose="devstream-supervise",
                args=args,
                env=env,
                run_count=effective_count,
                max_cycles=effective_max_cycles,
                require_max_cycles=True,
            )
            current_summary = (
                current_lease_guard.get("lease")
                if isinstance(current_lease_guard.get("lease"), dict)
                else {}
            )
            if current_summary.get("id") != runtime_identity.get("runtimeLeaseId"):
                current_lease_guard.setdefault("blockers", []).append(
                    "runtime lease identity changed during the supervisor session"
                )
                current_lease_guard["ok"] = False
            payload["runtimeLease"] = current_lease_guard
            current_account_season = account_season_authority_check(
                current_lease_guard,
                env=env,
            )
            payload["accountSeasonAuthority"] = current_account_season
            if not current_account_season["ok"]:
                payload["state"] = "blocked-account-season-authority"
                payload["error"] = "; ".join(
                    str(item) for item in current_account_season["blockers"]
                )
                write_supervisor_status(payload)
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 2
            lease_ok = bool(current_lease_guard.get("ok"))
            stale_after_seconds = max(
                IDLE_RUNNER_STALE_SECONDS,
                positive_int(getattr(args, "queue_timeout_seconds", 180), 180),
            )
            pre_cycle_idle_recovery = idle_battle_runner_recovery_candidate(stale_after_seconds)
            pre_cycle_completes_learning = bool(
                battle_was_in_flight
                and (
                    not pre_cycle_runtime["inFlight"]
                    or pre_cycle_idle_recovery.get("shouldRecover")
                )
            )
            completing_final_learning_cycle = bool(
                effective_max_cycles
                and pre_cycle_completes_learning
                and completed_learning_cycles + 1 >= effective_max_cycles
            )
            payload["state"] = "running-cycle"
            payload["lastHeartbeatAt"] = iso_now()
            payload["currentCycleIndex"] = cycle_index
            payload["currentCycleStartedAt"] = payload["lastHeartbeatAt"]
            payload["runtimeLease"] = current_lease_guard
            payload["preCycleRuntime"] = pre_cycle_runtime
            payload["preCycleIdleRunnerRecovery"] = pre_cycle_idle_recovery
            payload["nextAction"] = (
                "running proof/improve/start cycle; status will refresh when bounded action returns"
            )
            write_supervisor_status(payload)
            cycle = run_supervisor_cycle(
                args,
                cycle_index,
                start_next=(
                    lease_ok
                    and not completing_final_learning_cycle
                    and not pre_cycle_completes_learning
                ),
                authority_ok=lease_ok,
                lease_guard=current_lease_guard,
                supervisor_instance_id=supervisor_instance_id,
                reservation_state=current_reservation,
            )
            cycle["runtimeLease"] = current_lease_guard
            completed_this_cycle = learning_cycle_completed_after_cycle(
                battle_was_in_flight=battle_was_in_flight,
                pre_cycle_runtime=pre_cycle_runtime,
                cycle=cycle,
            )
            if completed_this_cycle:
                terminal = (
                    cycle.get("leaseConsumptionTerminal")
                    if isinstance(cycle.get("leaseConsumptionTerminal"), dict)
                    else {}
                )
                terminal_status = (
                    terminal.get("status")
                    if isinstance(terminal.get("status"), dict)
                    else terminal
                )
                if terminal_status.get("state") != "completed":
                    cycle["leaseConsumptionCompletion"] = {
                        "ok": False,
                        "blockers": [
                            "completed battle runtime lacks a broker-confirmed terminal reservation"
                        ],
                    }
                    payload["state"] = "blocked-lease-consumption-reconciliation"
                    payload["lastCycle"] = cycle
                    payload["error"] = cycle["leaseConsumptionCompletion"]["blockers"][0]
                    write_supervisor_status(payload)
                    print(json.dumps(payload, indent=2, sort_keys=True))
                    return 2
                terminal_outcome = str(terminal_status.get("outcome") or "")
                cycle["leaseConsumptionCompletion"] = {
                    "ok": True,
                    "completed": True,
                    "authority": "windows-named-pipe-lease-broker",
                    "reservationId": terminal_status.get("reservationId"),
                    "outcome": terminal_outcome,
                    "capacityReturned": False,
                }
                completed_this_cycle = terminal_outcome == "completed"
                if completed_this_cycle:
                    completed_learning_cycles += 1
                else:
                    cycle["learningCycleFailure"] = (
                        "battle runtime ended without a completed broker outcome"
                    )
            cycle["learningCycleCompleted"] = completed_this_cycle
            cycle["completedLearningCycles"] = completed_learning_cycles
            cycle["preCycleRuntime"] = pre_cycle_runtime
            post_cycle_in_flight = bool(
                cycle.get("state") == "battle-cycle-in-flight"
                or cycle.get("battleRunnerAliveAfter")
                or positive_int(cycle.get("activeBattleCountAfter"), 0) > 0
                or pre_cycle_runtime["inFlight"]
            )
            if completed_this_cycle:
                battle_was_in_flight = bool(
                    cycle.get("battleRunnerAliveAfter")
                    or positive_int(cycle.get("activeBattleCountAfter"), 0) > 0
                )
            elif post_cycle_in_flight:
                battle_was_in_flight = True
            payload["state"] = cycle.get("state", "unknown")
            payload["lastHeartbeatAt"] = iso_now()
            payload["lastCycle"] = cycle
            payload["cycles"] = (payload.get("cycles") or [])[-9:] + [cycle]
            payload["completedLearningCycles"] = completed_learning_cycles
            if not lease_ok:
                payload["state"] = "blocked-runtime-lease"
                payload["error"] = runtime_lease_blocked_message(current_lease_guard)
                payload["nextAction"] = (
                    "wait for in-flight runner to drain; no new battle runner will start "
                    "until HERMES renews the runtime lease"
                )
                cycle["leaseBlockedStartNext"] = True
                cycle["nextAction"] = payload["nextAction"]
            write_supervisor_status(payload)
            if cycle.get("state") == "blocked-lease-consumption-reconciliation":
                payload["blockedAt"] = iso_now()
                write_supervisor_status(payload)
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 2
            if not lease_ok and not post_cycle_in_flight:
                payload["blockedAt"] = iso_now()
                write_supervisor_status(payload)
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 2
            if effective_max_cycles and completed_learning_cycles >= effective_max_cycles:
                payload["state"] = "completed-max-cycles"
                payload["completedAt"] = iso_now()
                write_supervisor_status(payload)
                return 0
            time.sleep(args.sleep_seconds)
    except KeyboardInterrupt:
        payload["state"] = "interrupted"
        payload["interruptedAt"] = iso_now()
        write_supervisor_status(payload)
        return 130
    finally:
        pid = None
        try:
            raw = SUPERVISOR_PID_FILE.read_text(encoding="utf-8").strip()
            parsed = json.loads(raw) if raw.startswith("{") else {"pid": int(raw)}
            pid = int(parsed.get("pid"))
        except Exception:
            pass
        if pid == os.getpid():
            try:
                SUPERVISOR_PID_FILE.unlink()
            except OSError:
                pass


def wait_for_drain(max_wait_seconds: int) -> dict[str, Any]:
    started = time.time()
    while time.time() - started < max_wait_seconds:
        count = read_active_battles()
        if count <= 0:
            return {"drained": True, "activeBattleCount": 0, "waitedSeconds": round(time.time() - started, 3)}
        time.sleep(5)
    return {
        "drained": False,
        "activeBattleCount": read_active_battles(),
        "waitedSeconds": round(time.time() - started, 3),
    }


def build_doctor() -> dict[str, Any]:
    env = prepare_runtime_env(load_env_files())
    runtime_py = runtime_python()
    checks = [
        python_module_available(runtime_py, "psutil"),
        showdown_account_authority_check(env),
        runtime_pid_file_check(),
        public_runtime_truth_check(),
    ]
    health, error = run_json([runtime_py, "scripts/devstream_health.py"])
    if error:
        checks.append({"name": "health_probe", "ok": False, "error": error})
    else:
        readiness = health.get("readiness") if isinstance(health, dict) and isinstance(health.get("readiness"), dict) else {}
        proof_handoff_ready = bool(readiness.get("proofHandoffReady"))
        useful_work_ready = bool(readiness.get("usefulWorkProofReady"))
        accepted_mode = None
        if health and not health.get("healthy"):
            if proof_handoff_ready:
                accepted_mode = "proof-handoff"
            elif useful_work_ready:
                accepted_mode = "useful-work-proof"
        health_check = {
            "name": "health_probe",
            "ok": bool(health and (health.get("healthy") or proof_handoff_ready or useful_work_ready)),
            "details": health,
        }
        if accepted_mode:
            health_check.update({
                "acceptedMode": accepted_mode,
                "runtimeRestoration": "runtime is not live-ready; start only through HERMES after readiness gate allows project starts",
            })
        checks.append(health_check)
    schema = ROOT / "devstream" / "truth" / "elo-proof.schema.json"
    example = ROOT / "devstream" / "truth" / "elo-proof.example.json"
    login_check = ROOT / "scripts" / "showdown_login_check.py"
    checks.append({"name": "elo_proof_schema", "ok": schema.exists(), "path": str(schema)})
    checks.append({"name": "elo_proof_example", "ok": example.exists(), "path": str(example)})
    checks.append({"name": "showdown_login_check_tool", "ok": login_check.exists(), "path": str(login_check)})
    env_present = bool(env.get("PS_USERNAME") or env.get("SHOWDOWN_USER_ID"))
    checks.append({"name": "showdown_identity_env", "ok": env_present, "note": "PS_USERNAME or SHOWDOWN_USER_ID must be available at runtime"})
    password_present = bool(env.get("PS_PASSWORD"))
    password_required = showdown_password_required(env)
    checks.append({
        "name": "showdown_password_env",
        "ok": password_present or not password_required,
        "required": password_required,
        "note": "PS_PASSWORD is required for registered Showdown ladder sessions; the value is never emitted.",
    })
    credential_failure = recent_showdown_credential_failure(ROOT)
    checks.append({
        "name": "showdown_recent_credential_failure",
        "ok": not credential_failure.get("found"),
        "details": credential_failure,
    })
    env_modes = secure_env_files(execute=False)
    checks.append({"name": "env_file_permissions", "ok": all(item.get("ok") for item in env_modes), "details": env_modes})
    checks.append({
        "name": "run_command_materializes",
        "ok": env_present,
        "command": shell_command_for_session(DEFAULT_RUN_COUNT, DEFAULT_MAX_CONCURRENT, env) if env_present else [],
    })
    active_battles = read_active_battles()
    checks.append({"name": "active_battle_drain", "ok": active_battles == 0, "activeBattleCount": active_battles})
    supervisor_running, supervisor_pid = supervisor_alive()
    supervisor_contract = battle_supervisor_contract()
    checks.append({
        "name": "battle_supervisor",
        "ok": bool(supervisor_contract.get("ok")),
        "pid": supervisor_pid,
        "running": supervisor_running,
        "statusPath": str(SUPERVISOR_STATUS_FILE),
        "contract": supervisor_contract,
        "note": (
            "No-start doctor verifies the HERMES persistent battle supervisor contract; "
            "execute-time readiness still requires a live supervisor or bounded start lease."
        ),
    })
    return {
        "schemaVersion": "fouler-play-devstream-doctor/v1",
        "checkedAt": iso_now(),
        "ready": all(check.get("ok") for check in checks),
        "checks": checks,
        "note": "Read-only doctor; it does not queue battles or start services."
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    payload = build_doctor()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if args.require_ready and not payload["ready"] else 0


def cmd_cleanup_stale_truth(args: argparse.Namespace) -> int:
    env = load_launch_environment(args)
    purpose = STALE_TRUTH_CLEANUP_PURPOSE if args.execute else STALE_TRUTH_CLEANUP_DRY_RUN_PURPOSE
    lease_guard = cleanup_runtime_lease_guard(purpose=purpose, args=args, env=env)
    payload: dict[str, Any] = {
        "schemaVersion": "fouler-play-stale-truth-cleanup/v1",
        "checkedAt": iso_now(),
        "dryRun": not args.execute,
        "purpose": purpose,
        "runtimeLease": lease_guard,
        "bounds": {
            "activeBattlesStaleAfterSeconds": args.stale_after_seconds,
            "streamStatusStaleAfterSeconds": args.stream_stale_after_seconds,
            "cleanupOperationCount": 1,
        },
        "noRuntimeActions": True,
        "note": "No-start cleanup path; it does not start Showdown, eval, bots, Discord, Twitch, services, or scheduled tasks.",
    }
    if not lease_guard.get("ok"):
        payload["status"] = "blocked-runtime-lease"
        payload["error"] = runtime_lease_blocked_message(lease_guard)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    payload["publicRuntimeTruthBefore"] = public_runtime_truth_check(stale_after_seconds=args.stale_after_seconds)
    payload["activeBattleCleanup"] = clear_stale_active_battles(
        execute=args.execute,
        stale_after_seconds=args.stale_after_seconds,
    )
    payload["abandonedBattleResultRecovery"] = recover_abandoned_battle_results_from_backups(
        execute=args.execute,
    )
    payload["completedBattleLogResultRecovery"] = recover_completed_battle_results_from_logs(
        execute=args.execute,
    )
    payload["streamStatusCleanup"] = archive_stale_stream_status(
        execute=args.execute,
        stale_after_seconds=args.stream_stale_after_seconds,
    )
    if args.execute:
        payload["publicRuntimeTruthAfter"] = public_runtime_truth_check(stale_after_seconds=args.stale_after_seconds)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    env = load_launch_environment(args)
    effective_count = effective_run_count(args.run_count, env)
    continuous = bool(getattr(args, "continuous", False))
    start_purpose = "devstream-start-continuous" if continuous else "devstream-start"
    if not args.execute:
        start_purpose = f"{start_purpose}-dry-run"
    lease_guard = runtime_lease_guard(
        purpose=start_purpose,
        args=args,
        env=env,
        run_count=effective_count,
        max_cycles=positive_int(getattr(args, "max_cycles", 0), 0) if continuous else None,
        require_max_cycles=continuous,
    )
    runtime_identity: dict[str, Any] = {
        "schemaVersion": "fouler-runtime-process-identity/v1",
        "ok": False,
        "blockers": ["runtime lease has not validated"],
    }
    if lease_guard.get("ok"):
        try:
            args.runtime_lease = resolved_runtime_lease_path(lease_guard)
        except (OSError, ValueError) as exc:
            lease_guard.setdefault("blockers", []).append(
                f"validated runtime lease path is unavailable: {exc}"
            )
            lease_guard["ok"] = False
        if lease_guard.get("ok"):
            env, runtime_identity = apply_runtime_process_identity(env, lease_guard)
    commands = {
        "obsHttp": obs_server_command(),
        "battleSession": shell_command_for_session(effective_count, args.max_concurrent_battles, env),
        "battleSupervisor": supervisor_command(
            effective_count,
            args.max_concurrent_battles,
            args.queue_timeout_seconds,
            args.supervisor_sleep_seconds,
            enable_auto_improve=getattr(args, "enable_auto_improve", False),
            max_cycles=positive_int(getattr(args, "max_cycles", 0), 0),
            runtime_lease=getattr(args, "runtime_lease", None),
        ),
    }
    payload = {
        "schemaVersion": "fouler-play-devstream-start-plan/v1",
        "checkedAt": iso_now(),
        "dryRun": not args.execute,
        "commands": commands,
        "bounds": {
            "runCount": effective_count,
            "requestedRunCount": args.run_count,
            "runCountCap": positive_int(env.get(RUN_COUNT_CAP_ENV), DEFAULT_RUN_COUNT_CAP),
            "maxConcurrentBattles": args.max_concurrent_battles,
            "maxRuntimeMinutes": args.max_runtime_minutes,
            "queueTimeoutSeconds": args.queue_timeout_seconds,
            "turnTimeoutSeconds": args.turn_timeout_seconds,
            "maxCycles": positive_int(getattr(args, "max_cycles", 0), 0),
        },
        "envFilePermissions": secure_env_file_report(execute=args.execute, env=env),
        "runtimeLease": lease_guard,
        "runtimeIdentity": runtime_identity,
        "resolvedRuntimeLeasePath": getattr(args, "runtime_lease", None),
    }
    if not lease_guard.get("ok"):
        payload["status"] = "blocked-runtime-lease"
        payload["error"] = runtime_lease_blocked_message(lease_guard)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    if not runtime_identity.get("ok"):
        payload["status"] = "blocked-runtime-identity"
        payload["error"] = "; ".join(str(item) for item in runtime_identity.get("blockers") or [])
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    checkout_guard = improvement_checkout_guard()
    payload["improvementCheckoutGuard"] = checkout_guard
    if not checkout_guard["ready"]:
        payload["status"] = "blocked-improvement-checkout"
        payload["error"] = (
            "battle start blocked until improvement ownership and engine checkout recovery are clean: "
            + "; ".join(checkout_guard["blockers"])
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    launch_preflight = runtime_launch_preflight(args, env=env, lease_guard=lease_guard)
    payload["runtimeLaunchPreflight"] = launch_preflight
    if args.execute and not launch_preflight["ok"]:
        payload["status"] = "blocked-runtime-launch-preflight"
        payload["error"] = "; ".join(str(item) for item in launch_preflight["blockers"])
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    if args.execute and not continuous:
        reservation_guard = validate_runtime_lease_reservation(
            lease_guard,
            reservation_id=str(env.get(RUNTIME_LEASE_RESERVATION_ID_ENV) or "").strip(),
            run_count=effective_count,
            max_concurrent_battles=args.max_concurrent_battles,
            supervisor_instance_id=str(env.get(RUNTIME_SUPERVISOR_INSTANCE_ID_ENV) or "").strip(),
            env=env,
        )
        payload["leaseConsumptionReservation"] = reservation_guard
        if not reservation_guard.get("ok") or not reservation_guard.get("valid"):
            payload["status"] = "blocked-lease-consumption"
            payload["error"] = "; ".join(
                str(item) for item in reservation_guard.get("blockers") or []
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2
    if not args.execute:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.continuous:
        env["FP_PARENT_PID"] = "0"
        env["LOSS_TRIGGERED_DRAIN"] = "0"
        env["BATTLE_STATS_MAX_ENTRIES"] = env_value(env, "BATTLE_STATS_MAX_ENTRIES", default="5000")
        try:
            SUPERVISOR_STOP_FILE.unlink()
        except OSError:
            pass
        payload["started"] = {
            "obsHttp": start_process(commands["obsHttp"], OBS_PID_FILE, obs_http_env(env)),
            "battleSupervisor": start_supervisor_runtime(args, commands["battleSupervisor"], env),
            "battleSession": {
                "skipped": True,
                "reason": "persistent supervisor owns bounded battle session starts",
            },
        }
        time.sleep(2)
        health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
        payload["postHealth"] = health if health is not None else {"error": error}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    env["LOSS_TRIGGERED_DRAIN"] = "0"
    env["BATTLE_STATS_MAX_ENTRIES"] = env_value(env, "BATTLE_STATS_MAX_ENTRIES", default="5000")
    env["FP_PARENT_PID"] = "0"
    payload["staleActiveBattleCleanup"] = clear_stale_active_battles(
        execute=True,
        stale_after_seconds=args.queue_timeout_seconds,
    )
    payload["staleBattleRuntimeRecovery"] = recover_stale_battle_runtime(
        execute=args.replace_stale_runner,
        stale_after_seconds=args.queue_timeout_seconds,
    )
    existing_battle_runner = existing_battle_runner_start_result(commands["battleSession"])
    payload["started"] = {
        "obsHttp": start_process(commands["obsHttp"], OBS_PID_FILE, obs_http_env(env)),
        "battleSession": existing_battle_runner
        or start_process(commands["battleSession"], BATTLE_PID_FILE, detached_child_env(env)),
    }
    time.sleep(2)
    health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
    payload["postHealth"] = health if health is not None else {"error": error}
    credential_failure = (
        (health or {})
        .get("credentials", {})
        .get("recentShowdownFailure", {})
        if isinstance(health, dict)
        else {}
    )
    if isinstance(credential_failure, dict) and credential_failure.get("found"):
        payload["blockedTruth"] = state_store.write_runtime_blocked_status(
            code=str(credential_failure.get("code") or "showdown_credential_blocked"),
            summary=str(
                credential_failure.get("summary")
                or "Showdown login failed; credential was rejected."
            ),
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    active_battles = read_active_battles()
    payload = {
        "schemaVersion": "fouler-play-devstream-stop-plan/v1",
        "checkedAt": iso_now(),
        "dryRun": not args.execute,
        "activeBattleCount": active_battles,
        "recommendedAction": "wait-for-drain" if active_battles and not args.force else "safe-to-stop",
        "force": args.force,
        "note": "Drain-first stop. Without --force, active battles are allowed to finish before processes are terminated."
    }
    if not args.execute:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    PID_DIR.mkdir(parents=True, exist_ok=True)
    DRAIN_FILE.write_text(iso_now() + "\n", encoding="utf-8")
    SUPERVISOR_STOP_FILE.write_text(iso_now() + "\n", encoding="utf-8")
    payload["drain"] = wait_for_drain(args.max_wait_seconds)
    if payload["drain"].get("drained") or args.force:
        payload["terminated"] = {
            "battleSupervisor": terminate_pid_file(SUPERVISOR_PID_FILE, force=args.force),
            "battleSession": terminate_battle_runners(force=args.force),
            "obsHttp": terminate_pid_file(OBS_PID_FILE, force=args.force),
        }
        process_tree_failures = termination_failures(payload["terminated"])
        payload["processTreeTerminationFailures"] = process_tree_failures
        if process_tree_failures:
            payload["error"] = "one or more owned process trees survived or could not be verified"
        if args.force and not process_tree_failures:
            payload["forcedActiveBattleCleanup"] = clear_stale_active_battles(
                execute=True,
                stale_after_seconds=0,
                force=True,
                clear_reason="forced devstream stop terminated the battle runner; stale active battle truth must not stay public",
            )
    else:
        payload["error"] = "active battles did not drain before timeout"
    health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
    payload["postHealth"] = health if health is not None else {"error": error}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if "error" not in payload else 1


def cmd_drain(args: argparse.Namespace) -> int:
    active_battles = read_active_battles()
    runner_alive = any_battle_runner_alive()
    runner_owners = live_battle_runner_owners()
    runner_groups = _logical_battle_runner_groups(runner_owners)
    distinct_runner_pids = [int(group["rootPid"]) for group in runner_groups]
    payload = {
        "schemaVersion": "fouler-play-devstream-drain-plan/v1",
        "checkedAt": iso_now(),
        "dryRun": not args.execute,
        "activeBattleCount": active_battles,
        "battleRunnerAlive": runner_alive,
        "runtimeOwnership": {
            "knownRunners": runner_owners,
            "distinctPids": distinct_runner_pids,
            "logicalBattleRunners": runner_groups,
            "battleRunnerProcessCount": len(_distinct_battle_runner_pids(runner_owners)),
            "duplicateBattleRunners": len(runner_groups) > 1,
            "requiredHermesAction": (
                "drain/adopt exactly one live battle runner before starting another cycle"
                if len(runner_groups) > 1
                else None
            ),
        },
        "drainFile": str(DRAIN_FILE),
        "reason": args.reason,
        "note": "Drain-only control: finish current battle, queue no new battle from the old process, and let the supervisor restart from current code.",
    }
    if args.execute:
        PID_DIR.mkdir(parents=True, exist_ok=True)
        DRAIN_FILE.write_text(f"{iso_now()} {args.reason}\n", encoding="utf-8")
        payload["written"] = True
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="fouler-play devstream bounded session planner")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--require-ready", action="store_true")
    cleanup = sub.add_parser("cleanup-stale-truth")
    cleanup.add_argument(
        "--runtime-lease",
        help=f"Path to proof-window runtime lease JSON. Default: {RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json.",
    )
    cleanup.add_argument("--stale-after-seconds", type=int, default=STALE_ACTIVE_TRUTH_SECONDS)
    cleanup.add_argument("--stream-stale-after-seconds", type=int, default=STALE_STREAM_TRUTH_SECONDS)
    cleanup.add_argument("--execute", action="store_true")
    start = sub.add_parser("start")
    start.add_argument("--run-count", type=int, default=DEFAULT_RUN_COUNT)
    start.add_argument("--max-concurrent-battles", type=int, default=DEFAULT_MAX_CONCURRENT)
    start.add_argument("--max-runtime-minutes", type=int, default=180)
    start.add_argument("--queue-timeout-seconds", type=int, default=180)
    start.add_argument("--turn-timeout-seconds", type=int, default=90)
    start.add_argument("--continuous", action="store_true")
    start.add_argument("--supervisor-sleep-seconds", type=int, default=15)
    start.add_argument("--max-cycles", type=int, default=0)
    start.add_argument(
        "--runtime-lease",
        help=f"Path to proof-window runtime lease JSON. Default: {RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json.",
    )
    start.add_argument("--replace-stale-runner", action=argparse.BooleanOptionalAction, default=True)
    start.add_argument(
        "--enable-auto-improve",
        action="store_true",
        help="Compatibility flag only; immutable runtime improvement is delegated to DEKU.",
    )
    start.add_argument("--execute", action="store_true")
    supervise = sub.add_parser("supervise")
    supervise.add_argument("--run-count", type=int, default=DEFAULT_RUN_COUNT)
    supervise.add_argument("--max-concurrent-battles", type=int, default=DEFAULT_MAX_CONCURRENT)
    supervise.add_argument("--queue-timeout-seconds", type=int, default=180)
    supervise.add_argument("--sleep-seconds", type=int, default=15)
    supervise.add_argument("--max-cycles", type=int, default=0)
    supervise.add_argument(
        "--runtime-lease",
        help=f"Path to proof-window runtime lease JSON. Default: {RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json.",
    )
    supervise.add_argument("--autoresearch-count", type=int, default=30)
    supervise.add_argument("--proof-timeout-seconds", type=int, default=300)
    supervise.add_argument("--start-timeout-seconds", type=int, default=60)
    supervise.add_argument(
        "--improve-timeout-seconds",
        type=int,
        default=DEFAULT_IMPROVE_TIMEOUT_SECONDS,
        help="Compatibility option retained for old launchers; runtime does not launch improvement.",
    )
    supervise.add_argument(
        "--enable-auto-improve",
        action="store_true",
        help="Compatibility flag only; immutable runtime improvement is delegated to DEKU.",
    )
    supervise.add_argument("--skip-improve", action="store_true",
                           help="Compatibility flag; runtime improvement is always disabled.")
    stop = sub.add_parser("stop")
    stop.add_argument("--force", action="store_true")
    stop.add_argument("--max-wait-seconds", type=int, default=1800)
    stop.add_argument("--execute", action="store_true")
    drain = sub.add_parser("drain")
    drain.add_argument("--execute", action="store_true")
    drain.add_argument("--reason", default="HERMES requested graceful code-refresh drain")
    args = parser.parse_args()
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "cleanup-stale-truth":
        return cmd_cleanup_stale_truth(args)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "supervise":
        return cmd_supervise(args)
    if args.command == "stop":
        return cmd_stop(args)
    if args.command == "drain":
        return cmd_drain(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
