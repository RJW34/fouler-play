"""
Process lock to prevent duplicate bot instances.
Creates a PID file and checks for stale processes before starting.

Hardened 2026-06-04: acquisition is now ATOMIC. The previous check-then-write
had a TOCTOU race: two near-simultaneous launches (e.g. a per-logon scheduled
task firing twice, or a supervisor relaunch overlapping the old runner) could
BOTH observe "no live bot" and BOTH proceed -> two real ladder bots on one
account -> ELO thrash. We now claim the lock with O_CREAT|O_EXCL (atomic create)
and write our PID into that exclusive fd immediately, so exactly one racer wins.
Stale lock files (after a crash) are recovered via the same identity check.
"""

import os
import sys
import signal
import atexit
import time
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
    """Check if a PID is actually a fouler-play bot process.

    CONSERVATIVE on AccessDenied (root-cause fix 2026-06-05): a process that EXISTS
    but we cannot inspect is assumed to be a live bot, so the lock is NEVER reclaimed
    out from under a live ladder bot we merely lack permission to read. The previous
    version returned False on AccessDenied -> a SYSTEM-python dup that could not read
    the .venv holder's cwd treated the live holder as 'stale', reclaimed the lock, and
    BOTH ran -> the recurring ELO-thrash duplicate. NoSuchProcess (genuinely gone) is
    still the only path that returns False."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return True  # exists but uninspectable -> assume live bot; never dup
    try:
        cmdline = " ".join(proc.cmdline()).lower()
        cwd = proc.cwd()
        cwd_matches = bool(cwd) and os.path.abspath(cwd) == os.path.abspath(LOCK_DIR)
        return cwd_matches and "run.py" in cmdline and ("showdown" in cmdline or "search_ladder" in cmdline)
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return True  # exists but uninspectable -> assume live bot; never dup


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


def _claim_pid_file() -> bool:
    """Atomically create the PID file and write our PID into it.

    Returns True if WE created it (won the race), False if it already existed.
    Writing happens through the exclusively-created fd so a racing loser can
    never see an empty winner file for long."""
    try:
        fd = os.open(PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)
    return True


def _holder_pid() -> int | None:
    try:
        with open(PID_FILE) as f:
            raw = f.read().strip()
        return int(raw) if raw else None
    except (ValueError, OSError):
        return None


def acquire_lock(username: str = "unknown") -> bool:
    """
    Atomically acquire the process lock. Returns True if lock acquired.

    Exactly one racer can win the O_EXCL create. A live bot holder wins forever
    (loser aborts). A stale/crashed holder is reaped and the lock reclaimed.
    On ANY ambiguity (a holder mid-acquire that has not yet identified itself as
    a live bot) we abort conservatively -- never risk a second ladder bot.
    """
    for attempt in range(3):
        if _claim_pid_file():
            # We won the atomic create. Clean up any orphaned bot from a prior
            # crash (protects our own launch chain), then arm release handlers.
            killed = kill_stale_processes()
            if killed:
                print(f"[LOCK] Killed {killed} stale bot process(es).", file=sys.stderr)
            atexit.register(release_lock)
            signal.signal(signal.SIGTERM, lambda *_: (release_lock(), sys.exit(0)))
            print(f"[LOCK] Acquired lock (PID {os.getpid()}, user={username})", file=sys.stderr)
            return True

        # The file already exists. Identify the holder, with a brief retry so a
        # winner that is mid-write gets a chance to publish its PID.
        holder = None
        for _ in range(10):
            holder = _holder_pid()
            if holder is not None:
                break
            time.sleep(0.05)

        if holder is not None and is_bot_process(holder):
            print(f"[LOCK] Bot already running (PID {holder}). Aborting.", file=sys.stderr)
            return False

        if holder is not None and holder != os.getpid():
            # Holder exists but is NOT a live bot from our dir -> stale/crashed.
            # Reclaim: remove and retry the atomic create.
            print(f"[LOCK] Stale PID file (PID {holder} not a live bot). Reclaiming.", file=sys.stderr)
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
            continue

        # holder unknown after retries: someone is mid-acquire. Abort, do not dup.
        print("[LOCK] Lock contended and holder unresolved; aborting to avoid a duplicate bot.",
              file=sys.stderr)
        return False

    print("[LOCK] Could not acquire lock after retries; aborting.", file=sys.stderr)
    return False


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
