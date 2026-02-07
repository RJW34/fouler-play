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


def is_bot_process(pid: int) -> bool:
    """Check if a PID is actually a fouler-play bot process."""
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
        return "run.py" in cmdline and ("showdown" in cmdline or "search_ladder" in cmdline)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def kill_stale_processes():
    """Find and kill any stale bot processes from THIS directory only."""
    our_dir = os.path.abspath(LOCK_DIR)
    killed = 0
    for proc in psutil.process_iter(["pid", "cmdline", "cwd"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or []).lower()
            if "run.py" in cmdline and "search_ladder" in cmdline:
                cwd = proc.info.get("cwd", "")
                # Only kill processes running from OUR exact directory
                # Never kill processes from other fouler-play installs (e.g. BAKUGO's)
                if cwd and os.path.abspath(cwd) == our_dir:
                    if proc.pid != os.getpid():
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
