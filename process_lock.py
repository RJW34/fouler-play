"""
Process lock to prevent duplicate bot instances.
Creates a PID file and checks for stale processes before starting.
"""

import os
import sys
import signal
import time
import atexit
import json
from datetime import datetime, timezone
import psutil

try:
    from scripts.devstream_runtime_lease import validate_runtime_lease
except Exception:  # pragma: no cover - import failure must fail closed in the guard.
    validate_runtime_lease = None  # type: ignore[assignment]

LOCK_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(LOCK_DIR, ".bot.pid")
PID_CREATE_TIME_TOLERANCE_SECONDS = 2.0
# A matching ladder process younger than this is treated as a healthy,
# still-materializing sibling rather than a stale corpse. Without this guard,
# a keepalive launch that overlaps a healthy client would hard-kill it during
# kill_stale_processes(), stranding its in-flight battles server-side and
# forcing the next client into a slow orphan-resume recovery (the ~1-2 min
# restart downtime). Override via FOULER_STALE_GRACE_SEC.
STALE_PROCESS_GRACE_SECONDS = float(os.environ.get("FOULER_STALE_GRACE_SEC", "90") or 90)


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _offline_eval_mode() -> bool:
    return _truthy_env("FOULER_OFFLINE_EVAL")


def _pid_file_path() -> str:
    raw = str(os.environ.get("FOULER_PROCESS_LOCK_FILE") or "").strip()
    if raw:
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


def _current_runtime_lease_guard() -> dict[str, object] | None:
    if not _is_battle_runner_command(sys.argv):
        return None
    if _offline_eval_mode():
        return None
    if validate_runtime_lease is None:
        return {
            "ok": False,
            "blockers": ["runtime lease helper could not be imported"],
        }
    return validate_runtime_lease(
        purpose="run-py-battle-runner",
        requested_run_count=_arg_positive_int(sys.argv, "--run-count"),
        requested_max_concurrent_battles=_arg_positive_int(sys.argv, "--max-concurrent-battles"),
        requested_account=_arg_value(sys.argv, "--ps-username") or os.environ.get("PS_USERNAME"),
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
    )


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
    if _offline_eval_mode():
        return 0
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


def acquire_lock(username: str = "unknown") -> bool:
    """
    Acquire the process lock. Returns True if lock acquired.
    Kills stale processes if the PID file points to a dead/wrong process.
    """
    lease_guard = _current_runtime_lease_guard()
    if lease_guard is not None and not lease_guard.get("ok"):
        print("[LOCK] Runtime lease/proof window is required for live battle runners.", file=sys.stderr)
        blockers = lease_guard.get("blockers") if isinstance(lease_guard.get("blockers"), list) else []
        for blocker in blockers:
            print(f"[LOCK] BLOCKER: {blocker}", file=sys.stderr)
        return False

    while True:
        try:
            _claim_pid_file_atomically()
            break
        except FileExistsError:
            if not _remove_stale_pid_file():
                return False
        except OSError as exc:
            print(f"[LOCK] Unable to acquire PID file atomically ({exc}).", file=sys.stderr)
            return False
    
    # Kill any stale bot processes before starting
    killed = kill_stale_processes()
    if killed:
        print(f"[LOCK] Killed {killed} stale bot process(es).", file=sys.stderr)
    
    # Register cleanup
    atexit.register(release_lock)
    signal.signal(signal.SIGTERM, lambda *_: (release_lock(), sys.exit(0)))
    
    print(f"[LOCK] Acquired lock (PID {os.getpid()}, user={username})", file=sys.stderr)
    return True


def release_lock():
    """Release the process lock."""
    try:
        path = _pid_file_path()
        if os.path.exists(path):
            pid = _pid_from_payload(_read_pid_payload())
            if pid == os.getpid():
                os.remove(path)
                print(f"[LOCK] Released lock (PID {os.getpid()})", file=sys.stderr)
    except (ValueError, json.JSONDecodeError, OSError):
        pass
