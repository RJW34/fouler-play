"""Recursive self-improvement supervisor for the DekuFoulerLab gen9ou ladder.

CYCLE (owner directive 2026-07-25): play a BATCH of 30 laddered battles -> run an
IMPROVE step that rebuilds the loss-derived matchup weights from the recent replay
window (cycle_improve.py) -> play 30 more -> ... UNTIL current ELO reaches the
internal stop target, then idle. Each batch is a FRESH child process, so it loads
the freshly-refreshed weights at the start of every batch (closed learn loop).

HARDENING: the previous supervisor only relaunched on a child *exit* -- a
child that HUNG alive (dead dispatcher / wedged battle / clock-forfeit deadlock)
wedged the whole ladder for 30+ min with the OBS state frozen on a dead battle.
This supervisor runs a PROGRESS WATCHDOG with TWO complementary layers:

  1. LIVENESS layer (init.log mtime): kills the child tree if the child stops
     logging anything for STALL_LIMIT_SEC -- catches a hard freeze (dead thread,
     deadlocked I/O) where the process stops emitting entirely.

  2. REAL-PROGRESS layer (completed-game count from battle_stats.json): kills the
     child tree if NO new battle is recorded as COMPLETED for PROGRESS_LIMIT_SEC.
     This is the layer that matters. LIVENESS != PROGRESS: the 2026-07-25 forfeit
     doom-loop churned buffers/purges/searches (so init.log mtime stayed fresh and
     the liveness layer never fired) while ZERO real games completed for ~4.5h --
     only the 6h MAX_BATCH backstop eventually caught it. Counting *completed*
     games (battle_stats.json record count) is the signal that a game was actually
     PLAYED TO THE END, not merely that the process is busy. PROGRESS_LIMIT_SEC is
     set well above the worst observed healthy inter-completion gap (~15 min) and
     far above any single long MCTS game, so a bot mid-real-game is NEVER killed.

Either layer (plus the MAX_BATCH backstop) relaunches the child, so a hang or a
no-progress churn-loop self-recovers in minutes, not hours.

INTERNAL-ONLY STOP TARGET (HARD RAIL): the ELO stop target is a code constant used
only to decide when to idle. It is NEVER written to any audience surface (Discord /
OBS state files / battle logs / decision traces). The CURRENT elo may surface; the
TARGET must not. This file logs only to logs/ladder_supervisor.log (not mirrored to
OBS) and deliberately never prints the target number or the gap-to-target.

Credentials are never logged: PS_PASSWORD is loaded from .env by the child itself.
"""
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Projects\fouler-play")
PY = ROOT / ".venv" / "Scripts" / "python.exe"
CHILD = ROOT / "ladder_run.py"
IMPROVE = ROOT / "cycle_improve.py"
LOGDIR = ROOT / "logs"
SUP_LOG = LOGDIR / "ladder_supervisor.log"
CHILD_LOG = LOGDIR / "ladder_child.log"
INIT_LOG = LOGDIR / "init.log"          # child's live LIVENESS heartbeat (churns on any activity)
BATTLE_STATS = ROOT / "battle_stats.json"  # ledger of COMPLETED games (real-progress signal)
LOCK = ROOT / ".pids" / "ladder_supervisor.lock"

# --- Cycle / hardening tunables (env-overridable) --------------------------
BATCH_RUN_COUNT = int(os.getenv("FOULER_BATCH_RUN_COUNT", "30"))   # battles per batch
IMPROVE_WINDOW = int(os.getenv("FOULER_IMPROVE_WINDOW", "500"))    # replay window for the rebuild
STALL_LIMIT_SEC = int(os.getenv("FOULER_STALL_LIMIT_SEC", "600"))  # LIVENESS: kill child if init.log frozen this long
# REAL-PROGRESS: kill child if NO new COMPLETED game is recorded this long. 1800s (30 min)
# is 2x the worst observed healthy inter-completion gap (~15 min) and far above any single
# long MCTS game, so a bot mid-real-game is never killed; but a churn/no-progress loop that
# keeps init.log fresh (the forfeit doom-loop class) is caught in ~30 min instead of ~6h.
PROGRESS_LIMIT_SEC = int(os.getenv("FOULER_PROGRESS_LIMIT_SEC", "1800"))
WATCH_POLL_SEC = int(os.getenv("FOULER_WATCH_POLL_SEC", "15"))
MAX_BATCH_SEC = int(os.getenv("FOULER_MAX_BATCH_SEC", str(6 * 3600)))  # paranoia backstop
MIN_PRODUCTIVE_SEC = int(os.getenv("FOULER_MIN_PRODUCTIVE_SEC", "120"))  # skip improve on crash-loops
IMPROVE_TIMEOUT_SEC = int(os.getenv("FOULER_IMPROVE_TIMEOUT_SEC", "300"))
ELO_POLL_ON_TARGET_SEC = int(os.getenv("FOULER_ELO_IDLE_POLL_SEC", "600"))

# INTERNAL stop target. NEVER emit this value to any log/state/audience surface.
_TARGET_ELO = int(os.getenv("FOULER_ELO_TARGET", "1700"))
LADDER_ACCOUNT = "thepeakmons"  # public account name (not a secret)

ARGS = [
    str(PY), "-u", str(CHILD),
    "--websocket-uri", "wss://sim3.psim.us/showdown/websocket",
    "--ps-username", LADDER_ACCOUNT,
    "--bot-mode", "search_ladder",
    "--pokemon-format", "gen9ou",
    "--run-count", str(BATCH_RUN_COUNT),  # ONE BATCH: child exits cleanly after the per-worker quota
    "--max-concurrent-battles", "3",
    "--decision-policy", "eval",          # pure MCTS engine, no LLM / API key
    "--save-replay", "always",            # every game -> public replay + local replay JSON (improve input)
    "--team-names", "gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-balance,gen9/ou/fat-team-3-dondozo",  # per-BATTLE cycling via TeamListIterator (owner: all 3 provided teams, properly cycled)
    "--log-to-file",
]

# 3-team round-robin (owner 2026-07-31): rotate the provided fat/stall teams per batch so
# opponents cannot book one list, and the improve window sees all three archetypes.
TEAMS = [
    "gen9/ou/fat-team-2-balance",
    "gen9/ou/fat-team-1-stall",
    "gen9/ou/fat-team-3-dondozo",
]


def log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    line = f"{ts}  {msg}\n"
    try:
        LOGDIR.mkdir(exist_ok=True)
        with open(SUP_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="", flush=True)


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False


def acquire_singleton() -> bool:
    LOCK.parent.mkdir(exist_ok=True)
    if LOCK.exists():
        try:
            old = int(LOCK.read_text().strip() or "0")
        except Exception:
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            log(f"another supervisor is alive (pid {old}); exiting")
            return False
    LOCK.write_text(str(os.getpid()))
    return True


def kill_orphan_children() -> None:
    """Kill any stray ladder_run.py children from a prior crashed supervisor so we
    never end up with two bots searching the ladder as the same account."""
    try:
        import psutil
    except Exception:
        return
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.pid == me:
                continue
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "ladder_run.py" in cmd:
                log(f"killing orphan child pid {proc.pid}")
                proc.kill()
        except Exception:
            continue


def kill_proc_tree(proc: subprocess.Popen, reason: str) -> None:
    """Kill the Popen child AND its descendants (the .venv launcher re-execs a real
    interpreter child, so terminating only the Popen handle leaves the bot alive)."""
    log(f"killing child tree pid {proc.pid} ({reason})")
    try:
        import psutil
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return
    try:
        parent = psutil.Process(proc.pid)
        procs = parent.children(recursive=True) + [parent]
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=8)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass


def build_env() -> dict:
    env = dict(os.environ)
    env["LOSS_TRIGGERED_DRAIN"] = "0"        # never stop after a loss (keep laddering the batch)
    env["RESUME_ACTIVE_BATTLES"] = "0"       # start FRESH each batch (resume path deadlocks); also
                                             # clears active_battles.json so OBS never shows a dead battle.
    env["DISCORD_BATTLES_WEBHOOK_URL"] = ""  # no audience posting
    env["FOULER_BATTLE_RESULT_QUEUE"] = "0"  # do NOT emit battle events to the DEKU/Discord relay
    env["MAX_MCTS_BATTLES"] = "3"            # 3 samples/move (owner 2026-07-31): game-aware budget splits the ~6s; watch forfeits.
    env["FOULER_WORKER_LOG_LEVEL"] = "INFO"  # per-battle logs: DEBUG spam cost disk I/O mid-decision (owner 2026-07-31)
    # PS_PASSWORD deliberately NOT set here; the child loads it from .env.
    return env


def _init_log_mtime() -> float:
    try:
        return INIT_LOG.stat().st_mtime
    except OSError:
        return 0.0


def _completed_battle_count() -> int | None:
    """Number of COMPLETED battles recorded in battle_stats.json -- the real-progress
    signal (a record is appended only after a game is played to the end). Returns None
    if the file can't be read/parsed. The writer (run.py) uses a plain write_text (not a
    temp+rename), so a poll can occasionally catch a partial file; None lets the caller
    treat that as 'no reading this tick' (do not advance, do not penalize) rather than as
    zero progress -- a transient miss can never by itself trip the watchdog."""
    try:
        data = json.loads(BATTLE_STATS.read_text(encoding="utf-8"))
    except Exception:
        return None
    b = data.get("battles")
    return len(b) if isinstance(b, list) else None


class _ProgressWatch:
    """Tracks the wall-clock time since the last NEW completed game. Extracted so the
    stall decision is unit-testable with a synthetic clock (see test_progress_watchdog.py)."""

    def __init__(self, start_wall: float, baseline_count: int | None):
        self.last_count = baseline_count
        self.last_progress_wall = start_wall

    def update(self, now: float, count: int | None) -> None:
        # First successful reading establishes the baseline and resets the clock; any
        # later increase in completed-game count is real progress and resets the clock.
        if count is not None and (self.last_count is None or count > self.last_count):
            self.last_count = count
            self.last_progress_wall = now

    def stalled_for(self, now: float) -> float:
        return now - self.last_progress_wall


def run_batch(env: dict) -> tuple[int | None, float, str]:
    """Run one batch child under the progress watchdog. Returns (rc, duration, why)."""
    start = time.time()
    baseline_mtime = _init_log_mtime()
    last_progress_wall = start  # wall time we last SAW init.log advance (LIVENESS layer)
    progress = _ProgressWatch(start, _completed_battle_count())  # REAL-PROGRESS layer
    log(f"batch watchdog armed (liveness={STALL_LIMIT_SEC}s, real-progress={PROGRESS_LIMIT_SEC}s, "
        f"completed_games_baseline={progress.last_count})")
    try:
        with open(CHILD_LOG, "a", encoding="utf-8") as clog:
            clog.write(f"\n===== batch child start {datetime.datetime.now(datetime.timezone.utc).isoformat()} =====\n")
            clog.flush()
            proc = subprocess.Popen(ARGS, cwd=str(ROOT), env=env, stdout=clog, stderr=subprocess.STDOUT)
    except Exception as e:
        log(f"batch launch error: {e}")
        return None, time.time() - start, "launch_error"

    last_seen_mtime = baseline_mtime
    why = "exited"
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc, time.time() - start, why
        time.sleep(WATCH_POLL_SEC)
        now = time.time()

        # LIVENESS layer: init.log must keep advancing (catches a hard freeze).
        m = _init_log_mtime()
        if m > last_seen_mtime:
            last_seen_mtime = m
            last_progress_wall = now
        stalled = now - last_progress_wall
        if stalled > STALL_LIMIT_SEC:
            kill_proc_tree(proc, f"init.log stalled {stalled:.0f}s > {STALL_LIMIT_SEC}s")
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            return proc.poll(), now - start, "watchdog_stall"

        # REAL-PROGRESS layer: a game must be COMPLETED (battle_stats.json record appended)
        # within PROGRESS_LIMIT_SEC. Catches the forfeit/churn doom-loop that keeps init.log
        # fresh while zero games finish. Conservative threshold => never kills a real game.
        progress.update(now, _completed_battle_count())
        no_progress = progress.stalled_for(now)
        if no_progress > PROGRESS_LIMIT_SEC:
            # Double-check with one fresh read before pulling the trigger, so a lone racy
            # read can't cause a spurious recover right as a game finishes.
            progress.update(now, _completed_battle_count())
            no_progress = progress.stalled_for(now)
            if no_progress > PROGRESS_LIMIT_SEC:
                kill_proc_tree(
                    proc,
                    f"no completed game in {no_progress:.0f}s > {PROGRESS_LIMIT_SEC}s "
                    f"(real-progress watchdog; completed_games={progress.last_count})",
                )
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass
                return proc.poll(), now - start, "watchdog_no_progress"

        if now - start > MAX_BATCH_SEC:
            kill_proc_tree(proc, f"batch exceeded MAX_BATCH_SEC ({MAX_BATCH_SEC}s)")
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            return proc.poll(), now - start, "watchdog_maxbatch"


def run_improve(env: dict) -> None:
    """Run the IMPROVE step (rebuild loss-derived matchup weights) as an isolated
    subprocess with a timeout, so a slow/hung rebuild can never wedge the cycle."""
    try:
        proc = subprocess.run(
            [str(PY), "-u", str(IMPROVE), "--json", "--window", str(IMPROVE_WINDOW)],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=IMPROVE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        log(f"IMPROVE timed out after {IMPROVE_TIMEOUT_SEC}s (weights left unchanged)")
        return
    except Exception as e:
        log(f"IMPROVE launch error: {e} (weights left unchanged)")
        return
    # Summarize the improve result into the supervisor log (proof of real work).
    summary = None
    out = (proc.stdout or "").strip()
    try:
        brace = out.find("{")
        if brace >= 0:
            summary = json.loads(out[brace:])
    except Exception:
        summary = None
    if summary and summary.get("ok"):
        log(
            "IMPROVE ok: window=%s artifacts=%s losses=%s | weights %s -> %s | "
            "problem %s->%s bad %s->%s | live-flagged problem %s->%s bad %s->%s"
            % (
                summary.get("window_used"), summary.get("artifacts_built"), summary.get("losses_in_window"),
                summary.get("prev_updated_at"), summary.get("new_updated_at"),
                summary.get("prev_problem_pokemon"), summary.get("new_problem_pokemon"),
                summary.get("prev_bad_matchups"), summary.get("new_bad_matchups"),
                (summary.get("prev_flagged") or {}).get("flagged_problem"),
                (summary.get("new_flagged") or {}).get("flagged_problem"),
                (summary.get("prev_flagged") or {}).get("flagged_bad"),
                (summary.get("new_flagged") or {}).get("flagged_bad"),
            )
        )
    else:
        err = (summary or {}).get("error") if summary else (proc.stderr or "")[-300:]
        log(f"IMPROVE did not update weights: rc={proc.returncode} detail={err}")


def _fetch_current_elo() -> int | None:
    """Current gen9ou ELO from the canonical ladder JSON API. Internal check only."""
    try:
        import requests
        uid = LADDER_ACCOUNT.lower().replace(" ", "")
        r = requests.get(f"https://pokemonshowdown.com/users/{uid}.json", timeout=10)
        # The users API can answer 404 while still shipping valid ratings JSON in the
        # body (observed for thepeakmons 2026-07-31) -- parse the body regardless.
        try:
            rating = (r.json().get("ratings") or {}).get("gen9ou") or {}
            elo = rating.get("elo")
            if elo is not None:
                return int(round(float(elo)))
        except Exception:
            pass
        # Fallback: last recorded elo_after in our own ledger.
        import json as _json
        _rows = _json.loads(open(os.path.join(ROOT, "battle_stats.json"), encoding="utf-8-sig").read())
        _rows = _rows if isinstance(_rows, list) else (_rows.get("battles") or [])
        for _row in reversed(_rows):
            _v = _row.get("elo_after")
            if isinstance(_v, (int, float)):
                return int(round(_v))
        return None
    except Exception:
        return None


def target_reached(elo: int | None) -> bool:
    return elo is not None and elo >= _TARGET_ELO


def main() -> int:
    if not acquire_singleton():
        return 1
    kill_orphan_children()
    env = build_env()
    backoff = 5
    batch_no = 0
    try:  # persist numbering across supervisor restarts (owner 2026-07-31)
        with open(os.path.join(ROOT, 'logs', 'batch_counter.txt')) as _fh:
            batch_no = int(_fh.read().strip())
    except Exception:
        pass
    log(f"CYCLE SUPERVISOR START pid={os.getpid()} -> play {BATCH_RUN_COUNT} -> improve -> repeat "
        f"(stall_limit={STALL_LIMIT_SEC}s, improve_window={IMPROVE_WINDOW})")

    while True:
        # 1) Internal stop check (target value NEVER logged).
        elo = _fetch_current_elo()
        if target_reached(elo):
            log(f"internal ELO stop-condition met (current_elo={elo}); idling ladder cycle.")
            while True:
                time.sleep(ELO_POLL_ON_TARGET_SEC)
                elo = _fetch_current_elo()
                if not target_reached(elo):
                    log(f"current_elo={elo} fell below internal stop target; resuming cycle.")
                    break

        # 2) Play one batch under the progress watchdog.
        batch_no += 1
        try:
            with open(os.path.join(ROOT, 'logs', 'batch_counter.txt'), 'w') as _fh:
                _fh.write(str(batch_no))
        except Exception:
            pass
        log(f"BATCH #{batch_no} start (current_elo={elo})")
        rc, dur, why = run_batch(env)
        log(f"BATCH #{batch_no} end rc={rc} after {dur:.0f}s ({why})")

        # 3) IMPROVE from the refreshed replay window (skip on crash-loops to avoid spin).
        if dur >= MIN_PRODUCTIVE_SEC:
            run_improve(env)
        else:
            log(f"skipping IMPROVE (batch only {dur:.0f}s < {MIN_PRODUCTIVE_SEC}s; likely a fast crash)")

        # 4) Backoff only for unhealthy fast exits; healthy batches loop immediately.
        backoff = 5 if dur > MIN_PRODUCTIVE_SEC else min(backoff * 2, 60)
        time.sleep(backoff)


if __name__ == "__main__":
    sys.exit(main())
