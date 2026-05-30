"""
Process lock to prevent duplicate bot instances.
Creates a PID file and checks for stale processes before starting.
"""

import os
import sys
import signal
import atexit
import psutil

LOCK_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(LOCK_DIR, ".bot.pid")


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
    cmdline = " ".join(proc.info.get("cmdline") or []).lower()
    if "run.py" not in cmdline or "search_ladder" not in cmdline:
        return False
    cwd = proc.info.get("cwd", "")
    if not cwd:
        return False
    return os.path.abspath(cwd) == our_dir


def is_bot_process(pid: int) -> bool:
    """Check if a PID is actually a fouler-play bot process."""
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
        cwd = proc.cwd()
        cwd_matches = bool(cwd) and os.path.abspath(cwd) == os.path.abspath(LOCK_DIR)
        return cwd_matches and "run.py" in cmdline and ("showdown" in cmdline or "search_ladder" in cmdline)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def kill_stale_processes():
    """Find and kill any stale bot processes from THIS directory only."""
    our_dir = os.path.abspath(LOCK_DIR)
    protected_pids = _protected_process_ids()
    killed = 0
    for proc in psutil.process_iter(["pid", "cmdline", "cwd"]):
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


def acquire_lock(username: str = "unknown") -> bool:
    """
    Acquire the process lock. Returns True if lock acquired.
    Kills stale processes if the PID file points to a dead/wrong process.
    """
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            
            if is_bot_process(old_pid):
                print(f"[LOCK] Bot already running (PID {old_pid}). Aborting.", file=sys.stderr)
                return False
            else:
                print(f"[LOCK] Stale PID file (PID {old_pid} not a bot). Cleaning up.", file=sys.stderr)
                os.remove(PID_FILE)
        except (ValueError, OSError):
            os.remove(PID_FILE)
    
    # Kill any stale bot processes before starting
    killed = kill_stale_processes()
    if killed:
        print(f"[LOCK] Killed {killed} stale bot process(es).", file=sys.stderr)
    
    # Write our PID
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    
    # Register cleanup
    atexit.register(release_lock)
    signal.signal(signal.SIGTERM, lambda *_: (release_lock(), sys.exit(0)))
    
    print(f"[LOCK] Acquired lock (PID {os.getpid()}, user={username})", file=sys.stderr)
    return True


def release_lock():
    """Release the process lock."""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(PID_FILE)
                print(f"[LOCK] Released lock (PID {os.getpid()})", file=sys.stderr)
    except (ValueError, OSError):
        pass
