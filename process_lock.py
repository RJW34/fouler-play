"""
Process lock to prevent duplicate bot instances.
Creates a PID file and checks for stale processes before starting.
"""

import os
import sys
import signal
import subprocess
import time
import atexit
import json
import ipaddress
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import psutil

from scripts.devstream_runtime_lease import lease_environment, validate_runtime_lease
from infrastructure.runtime_lease_client import (
    ProtocolError,
    RUNTIME_RESERVATION_PURPOSE,
    broker_request_payload,
    require_exact_reservation_binding,
    request_with_retry,
    response_error_text,
)

LOCK_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_STATE_ROOT = os.path.abspath(
    os.path.expanduser(os.environ.get("FOULER_RUNTIME_STATE_ROOT", LOCK_DIR))
)
PID_FILE = os.path.join(RUNTIME_STATE_ROOT, "pids", "bot.pid")
PID_CREATE_TIME_TOLERANCE_SECONDS = 2.0
# A matching ladder process younger than this is treated as a healthy,
# still-materializing sibling rather than a stale corpse. Without this guard,
# a keepalive launch that overlaps a healthy client would hard-kill it during
# kill_stale_processes(), stranding its in-flight battles server-side and
# forcing the next client into a slow orphan-resume recovery (the ~1-2 min
# restart downtime). Override via FOULER_STALE_GRACE_SEC.
STALE_PROCESS_GRACE_SECONDS = float(os.environ.get("FOULER_STALE_GRACE_SEC", "90") or 90)
BATTLE_RUNNER_MODES = {"search_ladder", "accept_challenge", "challenge_user"}
_OFFLINE_EVAL_AUTHORITY = object()
_EFFECTIVE_BOT_MODE: object | None = None
_EFFECTIVE_WEBSOCKET_URI: object | None = None
_EFFECTIVE_OFFLINE_EVAL_AUTHORITY: object | None = None
RUNTIME_RESERVATION_ID_ENV = "FOULER_RUNTIME_LEASE_RESERVATION_ID"
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
_BROKER_RESERVATION: dict[str, object] | None = None
_BROKER_COMPLETED = False
_BROKER_OUTCOME = "failed"
_LIVE_CREDENTIAL_ENV_NAMES = (
    "PS_PASSWORD",
    "SHOWDOWN_PASSWORD",
    "FOULER_SHOWDOWN_PASSWORD",
)
EXPECTED_LIVE_MAX_CONCURRENT_BATTLES = 3
EXPECTED_LIVE_SEARCH_PARALLELISM = 2


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _offline_eval_mode() -> bool:
    return _truthy_env("FOULER_OFFLINE_EVAL")


def _offline_eval_environment_is_isolated() -> bool:
    return (
        _offline_eval_mode()
        and _truthy_env("FOULER_NO_SECURITY_LOGIN")
        and all(
            not str(os.environ.get(name) or "").strip()
            for name in _LIVE_CREDENTIAL_ENV_NAMES
        )
    )


def _normalize_bot_mode(value: object) -> str:
    raw = getattr(value, "name", value)
    text = str(raw or "").strip().lower()
    if text.startswith("botmodes."):
        text = text.rsplit(".", 1)[-1]
    return text


def _is_loopback_websocket_uri(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = urlsplit(text)
        if parsed.scheme.lower() not in {"ws", "wss"} or not parsed.hostname:
            return False
        hostname = parsed.hostname.strip().rstrip(".").lower()
    except (TypeError, ValueError):
        return False
    if hostname == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _pid_file_path() -> str:
    raw = str(os.environ.get("FOULER_PROCESS_LOCK_FILE") or "").strip()
    if raw and _is_proven_offline_eval(
        _EFFECTIVE_BOT_MODE,
        _EFFECTIVE_WEBSOCKET_URI,
        _EFFECTIVE_OFFLINE_EVAL_AUTHORITY,
    ):
        return os.path.abspath(raw)
    return PID_FILE



def _normalize_cmdline(cmdline) -> list[str]:
    return [str(part) for part in (cmdline or [])]


def _command_text(cmdline) -> str:
    return " ".join(_normalize_cmdline(cmdline)).lower()


def _cwd_matches_repo(cwd: str | None, repo_dir: str) -> bool:
    return bool(cwd) and os.path.abspath(str(cwd)) == os.path.abspath(repo_dir)


def _is_battle_runner_command(cmdline) -> bool:
    command = _command_text(cmdline)
    return "run.py" in command and (
        "showdown" in command
        or "search_ladder" in command
        or "accept_challenge" in command
        or "challenge_user" in command
    )


def _is_run_py_command(cmdline) -> bool:
    return any(
        os.path.basename(part).lower() == "run.py"
        for part in _normalize_cmdline(cmdline)
    )


def _is_devstream_supervisor_command(cmdline) -> bool:
    command = _command_text(cmdline)
    return "devstream_session.py" in command and "supervise" in command


def _is_lock_owner_command(cmdline) -> bool:
    return _is_battle_runner_command(cmdline) or _is_devstream_supervisor_command(cmdline)


def _arg_value(cmdline, name: str) -> str | None:
    parts = _normalize_cmdline(cmdline)
    prefix = f"{name}="
    for index, part in enumerate(parts):
        text = str(part)
        if text == name and index + 1 < len(parts):
            return str(parts[index + 1])
        if text.startswith(prefix):
            return text[len(prefix):]
    return None


def _arg_positive_int(cmdline, name: str) -> int | None:
    value = _arg_value(cmdline, name)
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _effective_bot_mode(value: object | None) -> str:
    if value is None:
        value = _arg_value(sys.argv, "--bot-mode") or os.environ.get("PS_BOT_MODE")
    return _normalize_bot_mode(value)


def _effective_websocket_uri(value: object | None) -> str:
    if value is None:
        value = _arg_value(sys.argv, "--websocket-uri") or os.environ.get(
            "PS_WEBSOCKET_URI"
        )
    return str(value or "").strip()


def _is_proven_offline_eval(
    bot_mode: object | None,
    websocket_uri: object | None,
    offline_eval_authority: object | None = None,
) -> bool:
    return (
        offline_eval_authority is _OFFLINE_EVAL_AUTHORITY
        and _offline_eval_environment_is_isolated()
        and _effective_bot_mode(bot_mode) in BATTLE_RUNNER_MODES
        and _is_loopback_websocket_uri(_effective_websocket_uri(websocket_uri))
    )


def _current_runtime_reservation_guard(
    lease_guard: dict[str, object],
    *,
    run_count: int | None,
    max_concurrent_battles: int | None,
) -> dict[str, object]:
    del lease_guard
    reservation_id = str(os.environ.get(RUNTIME_RESERVATION_ID_ENV) or "").strip()
    supervisor_instance_id = str(
        os.environ.get(RUNTIME_SUPERVISOR_INSTANCE_ID_ENV) or ""
    ).strip()
    purpose = str(os.environ.get(RUNTIME_RESERVATION_PURPOSE_ENV) or "").strip()
    kind = str(os.environ.get(RUNTIME_RESERVATION_KIND_ENV) or "").strip()

    def env_integer(name: str) -> int | None:
        try:
            parsed = int(str(os.environ.get(name) or "").strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    battle_count = env_integer(RUNTIME_RESERVATION_BATTLE_COUNT_ENV)
    cycle_count = env_integer(RUNTIME_RESERVATION_CYCLE_COUNT_ENV)
    concurrency = env_integer(RUNTIME_RESERVATION_CONCURRENCY_ENV)
    supervisor_pid = env_integer(RUNTIME_SUPERVISOR_PID_ENV)
    supervisor_creation = env_integer(RUNTIME_SUPERVISOR_CREATION_FILETIME_ENV)
    launch_nonce = str(os.environ.get(RUNTIME_LAUNCH_NONCE_ENV) or "").strip().lower()
    blockers: list[str] = []
    if not re.fullmatch(r"res-[0-9a-f]{32}", reservation_id):
        blockers.append("broker reservation id is missing or malformed")
    if not supervisor_instance_id:
        blockers.append("supervisor instance identity is missing")
    if not isinstance(run_count, int) or run_count <= 0:
        blockers.append("reserved battle run count is missing or invalid")
    if purpose != RUNTIME_RESERVATION_PURPOSE:
        blockers.append("broker reservation purpose is missing or invalid")
    if kind != "runtime":
        blockers.append("broker reservation kind is missing or invalid")
    if battle_count != run_count:
        blockers.append("broker reservation battle count does not match the runner")
    if cycle_count != 1:
        blockers.append("broker reservation cycle count must equal one")
    if concurrency != max_concurrent_battles:
        blockers.append("broker reservation concurrency does not match the runner")
    if supervisor_pid is None or supervisor_creation is None:
        blockers.append("authorized supervisor process identity is missing or invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", launch_nonce):
        blockers.append("broker-issued launch nonce is missing or malformed")
    binding = {
        "reservationId": reservation_id,
        "kind": kind,
        "purpose": purpose,
        "battleCount": battle_count,
        "cycleCount": cycle_count,
        "maxConcurrentBattles": concurrency,
        "supervisorProcessId": supervisor_pid,
        "supervisorProcessCreationFiletime": supervisor_creation,
        "supervisorInstanceId": supervisor_instance_id,
        "launchNonce": launch_nonce,
    }
    return {
        "ok": not blockers,
        "valid": not blockers,
        "blockers": blockers,
        "reservation": binding,
        "authority": "windows-named-pipe-lease-broker",
    }


def _claim_runtime_broker_reservation(lease_guard: dict[str, object]) -> bool:
    global _BROKER_RESERVATION, _BROKER_COMPLETED, _BROKER_OUTCOME
    if _BROKER_RESERVATION is not None and not _BROKER_COMPLETED:
        print(
            "[LOCK] A prior broker reservation is still claimed; refusing a second claim.",
            file=sys.stderr,
        )
        return False
    summary = lease_guard.get("lease") if isinstance(lease_guard.get("lease"), dict) else {}
    reservation_guard = (
        lease_guard.get("leaseConsumptionReservation")
        if isinstance(lease_guard.get("leaseConsumptionReservation"), dict)
        else {}
    )
    binding = (
        reservation_guard.get("reservation")
        if isinstance(reservation_guard.get("reservation"), dict)
        else {}
    )
    payload = broker_request_payload(
        "claim",
        authorization_digest=str(summary.get("authorizationSha256") or ""),
        lease_id=str(summary.get("id") or ""),
        **binding,
    )
    try:
        response = request_with_retry(payload)
    except (OSError, PermissionError, ValueError) as exc:
        print(f"[LOCK] Lease broker claim failed closed: {exc}", file=sys.stderr)
        return False
    if not response.get("ok"):
        print(
            f"[LOCK] Lease broker claim rejected: {response_error_text(response)}",
            file=sys.stderr,
        )
        return False
    _BROKER_RESERVATION = {
        "authorizationDigest": str(summary.get("authorizationSha256") or ""),
        "leaseId": str(summary.get("id") or ""),
        "binding": dict(binding),
    }
    _BROKER_COMPLETED = False
    _BROKER_OUTCOME = "failed"
    try:
        require_exact_reservation_binding(
            response.get("result") if isinstance(response.get("result"), dict) else {},
            binding,
        )
    except (ProtocolError, ValueError) as exc:
        print(f"[LOCK] Lease broker claim binding failed closed: {exc}", file=sys.stderr)
        _complete_runtime_broker_reservation("aborted")
        return False
    return True


def set_runtime_reservation_outcome(outcome: str) -> None:
    global _BROKER_OUTCOME
    if outcome not in {"completed", "failed", "aborted"}:
        raise ValueError("runtime reservation outcome is invalid")
    _BROKER_OUTCOME = outcome


def _complete_runtime_broker_reservation(outcome: str | None = None) -> bool:
    global _BROKER_RESERVATION, _BROKER_COMPLETED, _BROKER_OUTCOME
    if _BROKER_RESERVATION is None or _BROKER_COMPLETED:
        return True
    effective_outcome = outcome or _BROKER_OUTCOME
    payload = broker_request_payload(
        "complete",
        authorization_digest=str(_BROKER_RESERVATION["authorizationDigest"]),
        lease_id=str(_BROKER_RESERVATION["leaseId"]),
        **dict(_BROKER_RESERVATION["binding"]),
        outcome=effective_outcome,
    )
    try:
        response = request_with_retry(payload)
    except (OSError, PermissionError, ValueError) as exc:
        print(f"[LOCK] Lease broker completion failed closed: {exc}", file=sys.stderr)
        return False
    if not response.get("ok"):
        print(
            f"[LOCK] Lease broker completion rejected: {response_error_text(response)}",
            file=sys.stderr,
        )
        return False
    try:
        require_exact_reservation_binding(
            response.get("result") if isinstance(response.get("result"), dict) else {},
            dict(_BROKER_RESERVATION["binding"]),
        )
    except (ProtocolError, ValueError) as exc:
        print(
            f"[LOCK] Lease broker completion binding failed closed: {exc}",
            file=sys.stderr,
        )
        return False
    _BROKER_COMPLETED = True
    _BROKER_RESERVATION = None
    _BROKER_OUTCOME = "failed"
    return True


def _current_runtime_lease_guard(
    *,
    bot_mode: object | None = None,
    websocket_uri: object | None = None,
    username: str | None = None,
    run_count: int | None = None,
    max_concurrent_battles: int | None = None,
    search_parallelism: int | None = None,
    replay_behavior: object | None = None,
    offline_eval_authority: object | None = None,
) -> dict[str, object] | None:
    effective_context_supplied = bot_mode is not None or websocket_uri is not None
    if not effective_context_supplied and not _is_run_py_command(sys.argv):
        return None
    effective_mode = _effective_bot_mode(bot_mode)
    effective_uri = _effective_websocket_uri(websocket_uri)
    if _is_proven_offline_eval(
        effective_mode,
        effective_uri,
        offline_eval_authority,
    ):
        return None
    effective_concurrency = (
        max_concurrent_battles
        if max_concurrent_battles is not None
        else _arg_positive_int(sys.argv, "--max-concurrent-battles")
    )
    effective_search_parallelism = (
        search_parallelism
        if search_parallelism is not None
        else _arg_positive_int(sys.argv, "--search-parallelism")
    )
    if effective_search_parallelism is None:
        try:
            configured_parallelism = int(
                str(os.environ.get("SEARCH_PARALLELISM") or "").strip()
            )
        except (TypeError, ValueError):
            configured_parallelism = 0
        effective_search_parallelism = configured_parallelism or None

    guard = validate_runtime_lease(
        purpose="run-py-battle-runner",
        lease_path=os.environ.get("FOULER_RUNTIME_LEASE_PATH"),
        requested_run_count=(
            run_count
            if run_count is not None
            else _arg_positive_int(sys.argv, "--run-count")
        ),
        requested_max_concurrent_battles=effective_concurrency,
        requested_account=(
            username
            or _arg_value(sys.argv, "--ps-username")
            or os.environ.get("PS_USERNAME")
        ),
        requested_replay_behavior=replay_behavior,
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        require_deployment_receipt=True,
        verify_deployment_checkout=True,
    )
    if effective_concurrency != EXPECTED_LIVE_MAX_CONCURRENT_BATTLES:
        guard.setdefault("blockers", []).append(
            "live pilot max-concurrent-battles must equal the owner-locked value 3"
        )
        guard["ok"] = False
    if effective_search_parallelism != EXPECTED_LIVE_SEARCH_PARALLELISM:
        guard.setdefault("blockers", []).append(
            "live pilot search-parallelism must equal the owner-locked value 2"
        )
        guard["ok"] = False
    if not guard.get("ok"):
        return guard
    try:
        expected = lease_environment(guard)
    except (OSError, ValueError) as exc:
        guard.setdefault("blockers", []).append(f"runtime lease environment is unavailable: {exc}")
        guard["ok"] = False
        return guard
    mismatches = [
        name
        for name, expected_value in expected.items()
        if str(os.environ.get(name) or "").strip() != expected_value
    ]
    if mismatches:
        guard.setdefault("blockers", []).append(
            "battle process identity does not match the validated runtime lease: "
            + ", ".join(sorted(mismatches))
        )
        guard["ok"] = False
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=LOCK_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        source_commit = head.stdout.strip().lower() if head.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        source_commit = ""
    if source_commit != expected.get("FOULER_SOURCE_COMMIT"):
        guard.setdefault("blockers", []).append(
            "current checkout HEAD does not match the validated runtime lease sourceCommit"
        )
        guard["ok"] = False
    if guard.get("ok"):
        reservation = _current_runtime_reservation_guard(
            guard,
            run_count=(
                run_count
                if run_count is not None
                else _arg_positive_int(sys.argv, "--run-count")
            ),
            max_concurrent_battles=effective_concurrency,
        )
        guard["leaseConsumptionReservation"] = reservation
        if not reservation.get("ok") or not reservation.get("valid"):
            reservation_blockers = (
                reservation.get("blockers")
                if isinstance(reservation.get("blockers"), list)
                else []
            )
            guard.setdefault("blockers", []).extend(
                f"runtime reservation: {item}"
                for item in (
                    reservation_blockers
                    or ["live battle child has no matching supervisor reservation"]
                )
            )
            guard["ok"] = False
    return guard


def _protected_process_ids() -> set[int]:
    """Return PIDs that belong to this launch chain and must not be reaped."""
    protected = {os.getpid()}
    try:
        current = psutil.Process(os.getpid())
        protected.update(parent.pid for parent in current.parents())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return protected


def _is_stale_bot_process(proc, our_dir: str, protected_pids: set[int]) -> bool:
    """Check whether a process is a stale fouler bot from this repo."""
    if proc.pid in protected_pids:
        return False
    cmdline = proc.info.get("cmdline") or []
    if not _is_battle_runner_command(cmdline):
        return False
    cwd = proc.info.get("cwd", "")
    if not _cwd_matches_repo(cwd, our_dir):
        return False
    # Recency guard: never reap a matching client that started within the grace
    # window. A just-launched (or healthy, recently-restarted) ladder client is
    # NOT stale; killing it is what caused the back-to-back restart self-kills
    # (each new launch reaped its predecessor). Genuinely stale corpses are older
    # than the grace window. If the start time is unknowable we preserve the
    # legacy behaviour (treat as stale-eligible) so the singleton-cleanup
    # contract is unchanged; real processes always expose create_time via psutil.
    if STALE_PROCESS_GRACE_SECONDS > 0:
        create_time = _proc_create_time(proc)
        if create_time is not None:
            try:
                age = time.time() - float(create_time)
            except (TypeError, ValueError):
                age = None
            if age is not None and age < STALE_PROCESS_GRACE_SECONDS:
                return False
    return True


def _proc_create_time(proc):
    """Best-effort process start epoch; None if it cannot be determined."""
    try:
        ct = None
        info = getattr(proc, "info", None)
        if isinstance(info, dict):
            ct = info.get("create_time")
        if ct is None:
            getter = getattr(proc, "create_time", None)
            if callable(getter):
                ct = getter()
        return ct
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, AttributeError):
        return None


def _pid_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "pid": os.getpid(),
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "command": _normalize_cmdline(sys.argv),
        "lockFile": _pid_file_path(),
    }
    try:
        payload["createTime"] = psutil.Process(os.getpid()).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        pass
    return payload


def _read_pid_payload() -> dict[str, object] | int | None:
    with open(_pid_file_path(), encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return None
    if raw.startswith("{"):
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    return int(raw)


def _pid_from_payload(payload: dict[str, object] | int | None) -> int | None:
    if isinstance(payload, dict):
        value = payload.get("pid")
    else:
        value = payload
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _payload_create_time(payload: dict[str, object] | int | None) -> float | None:
    if not isinstance(payload, dict):
        return None
    try:
        return float(payload.get("createTime"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _payload_has_create_time(payload: dict[str, object] | int | None) -> bool:
    return _payload_create_time(payload) is not None


def _process_matches_payload_create_time(proc, payload: dict[str, object] | int | None) -> bool:
    expected_create_time = _payload_create_time(payload)
    if expected_create_time is None:
        return True
    try:
        actual_create_time = float(proc.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False
    return abs(actual_create_time - expected_create_time) <= PID_CREATE_TIME_TOLERANCE_SECONDS


def _is_lock_owner_process(proc, payload: dict[str, object] | int | None = None) -> bool:
    cmdline = proc.cmdline()
    cwd = proc.cwd()
    if not _cwd_matches_repo(cwd, LOCK_DIR):
        return False
    if not _is_lock_owner_command(cmdline):
        return False
    return _process_matches_payload_create_time(proc, payload)


def is_bot_process(pid: int) -> bool:
    """Check if a PID is actually a fouler-play runner process."""
    try:
        return _is_lock_owner_process(psutil.Process(pid))
    except psutil.AccessDenied:
        return True
    except (psutil.NoSuchProcess, OSError):
        return False


def kill_stale_processes():
    """Find and kill any stale bot processes from THIS directory only."""
    our_dir = os.path.abspath(LOCK_DIR)
    protected_pids = _protected_process_ids()
    killed = 0
    for proc in psutil.process_iter(["pid", "cmdline", "cwd", "create_time"]):
        try:
            # Only kill processes running from OUR exact directory. Never kill
            # processes from other fouler-play installs, and never kill this
            # launch chain. Windows venvs can expose a launcher parent plus the
            # actual interpreter child, so protecting ancestors prevents the
            # singleton cleanup from terminating its own startup.
            if _is_stale_bot_process(proc, our_dir, protected_pids):
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def _claim_pid_file_atomically():
    """Create the PID file only if no other process already owns it."""
    path = _pid_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        content = json.dumps(_pid_payload(), sort_keys=True) + "\n"
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)


def _remove_stale_pid_file() -> bool:
    """Remove the PID file only when it is inspectable and clearly stale."""
    try:
        payload = _read_pid_payload()
        old_pid = _pid_from_payload(payload)
    except FileNotFoundError:
        return True
    except (ValueError, json.JSONDecodeError):
        old_pid = None
    except OSError as exc:
        print(
            f"[LOCK] Existing PID file cannot be inspected ({exc}). Treating lock as held.",
            file=sys.stderr,
        )
        return False

    if old_pid is not None:
        try:
            if _is_lock_owner_process(psutil.Process(old_pid), payload):
                print(f"[LOCK] Bot already running (PID {old_pid}). Aborting.", file=sys.stderr)
                return False
        except psutil.AccessDenied:
            print(f"[LOCK] Existing PID {old_pid} cannot be inspected. Treating lock as held.", file=sys.stderr)
            return False
        except (psutil.NoSuchProcess, OSError):
            pass

    if old_pid is not None and not _payload_has_create_time(payload) and is_bot_process(old_pid):
        print(f"[LOCK] Bot already running (PID {old_pid}). Aborting.", file=sys.stderr)
        return False

    if old_pid is None:
        print("[LOCK] Corrupt PID file. Cleaning up.", file=sys.stderr)
    else:
        print(f"[LOCK] Stale PID file (PID {old_pid} not a bot). Cleaning up.", file=sys.stderr)

    try:
        os.remove(_pid_file_path())
    except FileNotFoundError:
        return True
    except OSError as exc:
        print(
            f"[LOCK] Unable to remove stale PID file ({exc}). Treating lock as held.",
            file=sys.stderr,
        )
        return False
    return True


def acquire_lock(
    username: str = "unknown",
    *,
    bot_mode: object | None = None,
    websocket_uri: object | None = None,
    run_count: int | None = None,
    max_concurrent_battles: int | None = None,
    search_parallelism: int | None = None,
    replay_behavior: object | None = None,
    offline_eval_authority: object | None = None,
) -> bool:
    """
    Acquire the process lock. Returns True if lock acquired.
    Kills stale processes if the PID file points to a dead/wrong process.
    """
    global _EFFECTIVE_BOT_MODE, _EFFECTIVE_WEBSOCKET_URI
    global _EFFECTIVE_OFFLINE_EVAL_AUTHORITY
    _EFFECTIVE_BOT_MODE = bot_mode
    _EFFECTIVE_WEBSOCKET_URI = websocket_uri
    _EFFECTIVE_OFFLINE_EVAL_AUTHORITY = offline_eval_authority
    lease_guard = _current_runtime_lease_guard(
        bot_mode=bot_mode,
        websocket_uri=websocket_uri,
        username=username,
        run_count=run_count,
        max_concurrent_battles=max_concurrent_battles,
        search_parallelism=search_parallelism,
        replay_behavior=replay_behavior,
        offline_eval_authority=offline_eval_authority,
    )
    if lease_guard is not None and not lease_guard.get("ok"):
        print("[LOCK] Runtime lease/proof window is required for live battle runners.", file=sys.stderr)
        blockers = lease_guard.get("blockers") if isinstance(lease_guard.get("blockers"), list) else []
        for blocker in blockers:
            print(f"[LOCK] BLOCKER: {blocker}", file=sys.stderr)
        return False
    if lease_guard is not None and not _claim_runtime_broker_reservation(lease_guard):
        return False

    while True:
        try:
            _claim_pid_file_atomically()
            break
        except FileExistsError:
            if not _remove_stale_pid_file():
                _complete_runtime_broker_reservation("aborted")
                return False
        except OSError as exc:
            print(f"[LOCK] Unable to acquire PID file atomically ({exc}).", file=sys.stderr)
            _complete_runtime_broker_reservation("aborted")
            return False
    
    # Register cleanup before any post-claim process inspection. Every failure
    # after this point must release the PID file and complete the broker claim.
    try:
        atexit.register(release_lock)
        signal.signal(signal.SIGTERM, lambda *_: (release_lock("aborted"), sys.exit(0)))
    except Exception as exc:
        print(f"[LOCK] Unable to register cleanup handlers ({exc}).", file=sys.stderr)
        release_lock("aborted")
        return False

    try:
        killed = (
            0
            if _is_proven_offline_eval(
                bot_mode,
                websocket_uri,
                offline_eval_authority,
            )
            else kill_stale_processes()
        )
    except Exception as exc:
        print(f"[LOCK] Stale process cleanup failed closed ({exc}).", file=sys.stderr)
        release_lock("aborted")
        return False
    if killed:
        print(f"[LOCK] Killed {killed} stale bot process(es).", file=sys.stderr)
    
    print(f"[LOCK] Acquired lock (PID {os.getpid()}, user={username})", file=sys.stderr)
    return True


def release_lock(outcome: str | None = None):
    """Release the process lock."""
    global _EFFECTIVE_BOT_MODE, _EFFECTIVE_WEBSOCKET_URI
    global _EFFECTIVE_OFFLINE_EVAL_AUTHORITY
    try:
        path = _pid_file_path()
        if os.path.exists(path):
            pid = _pid_from_payload(_read_pid_payload())
            if pid == os.getpid():
                os.remove(path)
                print(f"[LOCK] Released lock (PID {os.getpid()})", file=sys.stderr)
    except (ValueError, json.JSONDecodeError, OSError):
        pass
    finally:
        _complete_runtime_broker_reservation(outcome)
        _EFFECTIVE_BOT_MODE = None
        _EFFECTIVE_WEBSOCKET_URI = None
        _EFFECTIVE_OFFLINE_EVAL_AUTHORITY = None
