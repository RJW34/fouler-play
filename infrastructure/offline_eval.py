#!/usr/bin/env python3
"""
offline_eval.py -- REAL offline battle eval harness for fouler-play.

This is the acceptance gate the SOTA audit calls for. It plays the ACTUAL fouler
decision engine (via run.py accept_challenge) against a FROZEN poke-env baseline
(SimpleHeuristicsPlayer by default, or MaxBasePowerPlayer) on a LOCAL
pokemon-showdown server, over N battles, and reports win-rate with a Wilson lower
confidence bound and a two-proportion z-test vs a reference win-rate.

A candidate change is ACCEPTED only if its win-rate beats the frozen baseline by a
statistically significant margin (Wilson LCB of the candidate-vs-baseline win-rate
> 0.50, i.e. we are confident fouler beats the dumb baseline more than half the
time, AND a two-proportion z-test vs a stored reference run is not a regression).

Why this design: bridging fouler's bespoke Battle parser into poke-env's Player is
large and fragile. Instead we run fouler END-TO-END exactly as it plays on ladder
(run.py -> websocket -> showdown), so the eval exercises the true decision path.
The opponent is a deterministic poke-env baseline on the same local server.

Topology:
  - Local showdown server (node pokemon-showdown start --no-security PORT)
  - fouler:   run.py --bot-mode accept_challenge  (global python, real engine)
  - baseline: poke-env player in .venv-eval, challenges fouler N times

Usage (orchestrated):
  python infrastructure/offline_eval.py --battles 40 --team gen9/ou/fat-team-1-stall \
      --baseline simple --label candidate

  # A/B the set-sampling fix:
  #   run once with FOULER_FORCE_NO_SETSAMPLE=1 (degraded) -> --label frozen
  #   run once without it (candidate)                       -> --label candidate
  # then: python infrastructure/offline_eval.py --compare frozen candidate

The harness writes results to eval_results/offline/<label>.json.
"""

import argparse
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "eval_results" / "offline"
OFFLINE_RUNNER_SCRIPT = PROJECT_ROOT / "infrastructure" / "offline_eval_runner.py"
VENV_PY = PROJECT_ROOT / ".venv-eval" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = PROJECT_ROOT / ".venv-eval" / "bin" / "python"
FOULER_RUNTIME_IMPORTS = ("aiohttp", "requests", "dotenv", "dateutil", "psutil", "poke_engine")
PROCESS_OWNER_SCHEMA = "fouler-play-offline-eval-process-owner/v1"


def _split_python_command(raw: str) -> list[str]:
    return shlex.split(raw, posix=os.name != "nt")


def _runtime_python_candidates() -> list[list[str]]:
    explicit = os.getenv("FOULER_RUNTIME_PYTHON")
    if explicit:
        return [_split_python_command(explicit)]

    candidates: list[list[str]] = [[sys.executable]]
    local_venvs = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / ".venv" / "bin" / "python",
    ]
    for python_path in local_venvs:
        if python_path.exists():
            candidates.append([str(python_path)])

    py_launcher = shutil.which("py")
    if py_launcher:
        candidates.append([py_launcher, "-3"])
    for command in ("python", "python3"):
        executable = shutil.which(command)
        if executable:
            candidates.append([executable])

    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _probe_fouler_python(command: list[str]) -> tuple[bool, dict[str, object]]:
    probe_code = (
        "import json, sys; "
        + "; ".join(f"import {module}" for module in FOULER_RUNTIME_IMPORTS)
        + "; print(json.dumps({'executable': sys.executable}))"
    )
    try:
        probe = subprocess.run(
            [*command, "-c", probe_code],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:
        return False, {"command": subprocess.list2cmdline(command), "error": str(exc)}

    detail: dict[str, object] = {
        "command": subprocess.list2cmdline(command),
        "returncode": probe.returncode,
        "stdout": (probe.stdout or "").strip()[-500:],
        "stderr": (probe.stderr or "").strip()[-500:],
    }
    if probe.returncode != 0:
        return False, detail
    try:
        detail.update(json.loads((probe.stdout or "").strip().splitlines()[-1]))
    except Exception:
        pass
    return True, detail


def resolve_fouler_python() -> list[str]:
    failures = []
    for candidate in _runtime_python_candidates():
        ok, detail = _probe_fouler_python(candidate)
        if ok:
            return candidate
        failures.append(detail)
        if os.getenv("FOULER_RUNTIME_PYTHON"):
            break
    raise RuntimeError(
        "No Fouler runtime Python can import required modules "
        f"{FOULER_RUNTIME_IMPORTS}. Set FOULER_RUNTIME_PYTHON or install "
        f"requirements.txt into a runtime Python. Failures: {failures}"
    )


def build_eval_env(
    *,
    label: str,
    showdown_port: int,
    search_time_ms: int,
    extra_env: dict | None,
) -> dict[str, str]:
    """Build the child-process environment for an offline eval run."""
    env = os.environ.copy()
    env.setdefault("PS_PASSWORD", "")
    # Local --no-security server: skip HTTP assertion entirely (/trn user,0,).
    env["FOULER_NO_SECURITY_LOGIN"] = "1"
    env["SEARCH_TIME_MS"] = str(search_time_ms)
    env["MIN_SEARCH_TIME_MS"] = "0"  # don't clamp; we set search time explicitly
    env["LOSS_TRIGGERED_DRAIN"] = "0"  # play all N battles regardless of losses
    env["MAX_CONCURRENT_BATTLES"] = "1"
    env["FOULER_OFFLINE_EVAL"] = "1"
    env["FOULER_OFFLINE_EVAL_LABEL"] = label
    env["FOULER_OFFLINE_BATTLE_STATS_FILE"] = str(RESULTS_DIR / f"{label}-battle_stats.json")
    # Offline eval has no Discord transport and uses --save-replay never, so
    # battle-result queue events only create unpostable replay backlog.
    env["FOULER_BATTLE_RESULT_QUEUE"] = "0"
    env["FOULER_OFFLINE_EVAL_QUEUE_EVENTS"] = "0"
    env["DISCORD_BATTLES_WEBHOOK_URL"] = ""
    env["DISCORD_WEBHOOK_URL"] = ""
    env["DISCORD_FEEDBACK_WEBHOOK_URL"] = ""
    env["EVENT_QUEUE_FILE"] = str(RESULTS_DIR / f"{label}-events_queue.json")
    env["EVENT_QUEUE_BACKLOG_ARCHIVE_DIR"] = str(RESULTS_DIR / "discord-events")
    env["REPLAY_UPLOAD_RESOLVE_ATTEMPTS"] = "1"
    env["REPLAY_UPLOAD_RESOLVE_DELAY_SEC"] = "0"
    env["STREAM_EVENT_URL"] = f"http://127.0.0.1:{showdown_port}/offline-eval-disabled"
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})
    return env


def build_fouler_command(
    *,
    fouler_python: list[str],
    ws_uri: str,
    fouler_user: str,
    fmt: str,
    team: str,
    battles: int,
    search_time_ms: int,
) -> list[str]:
    return [
        *fouler_python,
        str(OFFLINE_RUNNER_SCRIPT),
        "run.py",
        "--websocket-uri",
        ws_uri,
        "--ps-username",
        fouler_user,
        "--bot-mode",
        "accept_challenge",
        "--pokemon-format",
        fmt,
        "--team-name",
        team,
        # Keep fouler alive a few battles past the baseline's challenge count so it
        # never exits mid-series and strands a pending challenge.
        "--run-count",
        str(battles + 5),
        "--search-time-ms",
        str(search_time_ms),
        "--save-replay",
        "never",
    ]


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git_text(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def current_git_metadata() -> dict[str, object]:
    status = _git_text(["status", "--short"])
    return {
        "head": _git_text(["rev-parse", "HEAD"]),
        "shortHead": _git_text(["rev-parse", "--short", "HEAD"]),
        "commitTime": _git_text(["show", "-s", "--format=%cI", "HEAD"]),
        "branch": _git_text(["branch", "--show-current"]),
        "dirty": bool(status),
        "statusShort": status.splitlines()[:40],
    }


def _process_snapshot(proc: subprocess.Popen | None, command: list[str] | None = None) -> dict[str, object] | None:
    if proc is None:
        return None
    return {
        "pid": proc.pid,
        "returncode": proc.poll(),
        "running": proc.poll() is None,
        "command": subprocess.list2cmdline(command or getattr(proc, "args", []) or []),
    }


def _build_process_owner_payload(
    *,
    label: str,
    stage: str,
    command: list[str],
    fouler_proc: subprocess.Popen | None = None,
    fouler_cmd: list[str] | None = None,
    baseline_proc: subprocess.Popen | None = None,
    baseline_cmd: list[str] | None = None,
    extra: dict | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": PROCESS_OWNER_SCHEMA,
        "updatedAt": _utc_iso(),
        "label": label,
        "stage": stage,
        "projectRoot": str(PROJECT_ROOT),
        "git": current_git_metadata(),
        "processes": {
            "offlineEval": {
                "pid": os.getpid(),
                "parentPid": os.getppid(),
                "executable": sys.executable,
                "command": subprocess.list2cmdline(command),
            },
            "fouler": _process_snapshot(fouler_proc, fouler_cmd),
            "baseline": _process_snapshot(baseline_proc, baseline_cmd),
        },
        "secretValuesPrinted": False,
    }
    if extra:
        payload.update(extra)
    return payload


def write_process_owner_status(**kwargs) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    label = str(kwargs.get("label") or "eval")
    path = RESULTS_DIR / f"{label}-process-owner.json"
    payload = _build_process_owner_payload(**kwargs)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _terminate_process_tree(proc: subprocess.Popen, *, reason: str, timeout: float = 15.0) -> dict[str, object]:
    """Terminate a process tree owned by this eval harness."""
    detail: dict[str, object] = {
        "pid": proc.pid,
        "reason": reason,
        "returncodeBefore": proc.poll(),
        "method": "none",
    }
    if proc.poll() is not None:
        detail["method"] = "already-exited"
        detail["returncodeAfter"] = proc.returncode
        return detail

    try:
        import psutil  # type: ignore

        parent = psutil.Process(proc.pid)
        process_tree = parent.children(recursive=True) + [parent]
        detail["processTreePids"] = [p.pid for p in process_tree]
        for child in process_tree:
            try:
                child.terminate()
            except Exception:
                pass
        gone, alive = psutil.wait_procs(process_tree, timeout=timeout)
        for child in alive:
            try:
                child.kill()
            except Exception:
                pass
        if alive:
            psutil.wait_procs(alive, timeout=5)
        detail["method"] = "psutil-process-tree"
        detail["terminatedPids"] = [p.pid for p in gone]
        detail["killedPids"] = [p.pid for p in alive]
        proc.poll()
        detail["returncodeAfter"] = proc.returncode
        return detail
    except Exception as exc:
        detail["psutilError"] = str(exc)

    if os.name == "nt":
        taskkill = subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        detail["method"] = "taskkill"
        detail["taskkillReturncode"] = taskkill.returncode
        detail["taskkillStdout"] = (taskkill.stdout or "")[-1000:]
        detail["taskkillStderr"] = (taskkill.stderr or "")[-1000:]
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        detail["returncodeAfter"] = proc.poll()
        return detail

    proc.terminate()
    try:
        proc.wait(timeout=timeout)
        detail["method"] = "terminate"
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        detail["method"] = "kill"
    detail["returncodeAfter"] = proc.poll()
    return detail


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score lower confidence bound for a binomial proportion."""
    if n == 0:
        return 0.0
    phat = wins / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return (center - margin) / denom


def two_proportion_z(w1: int, n1: int, w2: int, n2: int) -> tuple[float, float]:
    """
    Two-proportion z-test. Returns (z, p_value_two_sided) for H0: p1 == p2.
    p1 = candidate win-rate, p2 = reference win-rate.
    """
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    p1 = w1 / n1
    p2 = w2 / n2
    p_pool = (w1 + w2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    # two-sided p from standard normal
    p = math.erfc(abs(z) / math.sqrt(2))
    return z, p


def _free_port_guess(default: int) -> int:
    return int(os.getenv("EVAL_SHOWDOWN_PORT", str(default)))


def run_eval(
    *,
    battles: int,
    team: str,
    baseline: str,
    label: str,
    showdown_port: int,
    search_time_ms: int,
    fouler_user: str,
    baseline_user: str,
    extra_env: dict | None,
    per_battle_timeout: float,
) -> dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ws_uri = f"ws://localhost:{showdown_port}/showdown/websocket"
    fmt = "gen9ou"

    env = build_eval_env(
        label=label,
        showdown_port=showdown_port,
        search_time_ms=search_time_ms,
        extra_env=extra_env,
    )
    fouler_python = resolve_fouler_python()
    owner_command = [sys.executable, *sys.argv]
    write_process_owner_status(
        label=label,
        stage="starting",
        command=owner_command,
        extra={
            "configuration": {
                "battles": battles,
                "team": team,
                "baseline": baseline,
                "showdownPort": showdown_port,
                "searchTimeMs": search_time_ms,
                "eventQueueEnabled": env.get("FOULER_BATTLE_RESULT_QUEUE") != "0",
            }
        },
    )

    # --- Launch fouler in accept_challenge mode (the REAL engine) ---
    fouler_log = RESULTS_DIR / f"{label}-fouler.log"
    fouler_cmd = build_fouler_command(
        fouler_python=fouler_python,
        ws_uri=ws_uri,
        fouler_user=fouler_user,
        fmt=fmt,
        team=team,
        battles=battles,
        search_time_ms=search_time_ms,
    )
    print(f"[eval:{label}] starting fouler: {' '.join(fouler_cmd)}")
    with open(fouler_log, "w", encoding="utf-8") as flog:
        fouler_proc = subprocess.Popen(
            fouler_cmd, cwd=str(PROJECT_ROOT), env=env,
            stdout=flog, stderr=subprocess.STDOUT,
        )
    write_process_owner_status(
        label=label,
        stage="fouler-started",
        command=owner_command,
        fouler_proc=fouler_proc,
        fouler_cmd=fouler_cmd,
    )

    # Give fouler time to log in and join the lobby before the baseline challenges.
    time.sleep(12)

    # --- Launch the baseline challenger in the eval venv ---
    baseline_log = RESULTS_DIR / f"{label}-baseline.log"
    result_file = RESULTS_DIR / f"{label}-baseline-result.json"
    if result_file.exists():
        result_file.unlink()
    baseline_cmd = [
        str(VENV_PY), str(Path(__file__).resolve().parent / "_offline_baseline.py"),
        "--server-port", str(showdown_port),
        "--baseline", baseline,
        "--username", baseline_user,
        "--opponent", fouler_user,
        "--battles", str(battles),
        "--format", fmt,
        "--team-file", str(PROJECT_ROOT / "teams" / Path(*team.split("/"))),
        "--result-file", str(result_file),
        "--per-battle-timeout", str(per_battle_timeout),
    ]
    print(f"[eval:{label}] starting baseline ({baseline}) challenger")
    with open(baseline_log, "w", encoding="utf-8") as blog:
        baseline_proc = subprocess.Popen(
            baseline_cmd, cwd=str(PROJECT_ROOT),
            stdout=blog, stderr=subprocess.STDOUT,
        )
    write_process_owner_status(
        label=label,
        stage="baseline-started",
        command=owner_command,
        fouler_proc=fouler_proc,
        fouler_cmd=fouler_cmd,
        baseline_proc=baseline_proc,
        baseline_cmd=baseline_cmd,
    )

    overall_timeout = per_battle_timeout * battles + 120
    cleanup: list[dict[str, object]] = []
    try:
        baseline_proc.wait(timeout=overall_timeout)
    except subprocess.TimeoutExpired:
        print(f"[eval:{label}] baseline timed out after {overall_timeout:.0f}s")
        cleanup.append(_terminate_process_tree(baseline_proc, reason="baseline-timeout"))
    write_process_owner_status(
        label=label,
        stage="baseline-finished",
        command=owner_command,
        fouler_proc=fouler_proc,
        fouler_cmd=fouler_cmd,
        baseline_proc=baseline_proc,
        baseline_cmd=baseline_cmd,
        extra={"cleanup": cleanup},
    )

    # fouler should exit on its own after run-count; give it a moment then kill.
    try:
        fouler_proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        cleanup.append(_terminate_process_tree(fouler_proc, reason="fouler-did-not-exit-after-eval"))
    write_process_owner_status(
        label=label,
        stage="process-cleanup-complete",
        command=owner_command,
        fouler_proc=fouler_proc,
        fouler_cmd=fouler_cmd,
        baseline_proc=baseline_proc,
        baseline_cmd=baseline_cmd,
        extra={"cleanup": cleanup},
    )

    # --- Read the baseline-reported result (baseline's perspective) ---
    if not result_file.exists():
        raise RuntimeError(
            f"baseline produced no result file ({result_file}); see {baseline_log}"
        )
    bres = json.loads(result_file.read_text(encoding="utf-8"))
    baseline_wins = int(bres.get("wins", 0))
    n = int(bres.get("battles", 0))
    fouler_wins = n - baseline_wins - int(bres.get("ties", 0))

    fouler_wr = fouler_wins / n if n else 0.0
    lcb = wilson_lower_bound(fouler_wins, n)
    out = {
        "label": label,
        "team": team,
        "baseline": baseline,
        "battles": n,
        "fouler_wins": fouler_wins,
        "baseline_wins": baseline_wins,
        "ties": int(bres.get("ties", 0)),
        "fouler_win_rate": round(fouler_wr, 4),
        "fouler_wilson_lcb": round(lcb, 4),
        "search_time_ms": search_time_ms,
        "extra_env": extra_env or {},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (RESULTS_DIR / f"{label}.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    write_process_owner_status(
        label=label,
        stage="result-written",
        command=owner_command,
        fouler_proc=fouler_proc,
        fouler_cmd=fouler_cmd,
        baseline_proc=baseline_proc,
        baseline_cmd=baseline_cmd,
        extra={"cleanup": cleanup, "result": out},
    )
    print(f"[eval:{label}] fouler {fouler_wins}/{n} = {fouler_wr:.1%} "
          f"(Wilson LCB {lcb:.1%})")
    return out


def compare(label_frozen: str, label_candidate: str) -> dict:
    f = json.loads((RESULTS_DIR / f"{label_frozen}.json").read_text(encoding="utf-8"))
    c = json.loads((RESULTS_DIR / f"{label_candidate}.json").read_text(encoding="utf-8"))
    z, p = two_proportion_z(
        c["fouler_wins"], c["battles"], f["fouler_wins"], f["battles"]
    )
    improved = c["fouler_win_rate"] > f["fouler_win_rate"]
    significant = p < 0.05 and improved
    verdict = {
        "frozen": {k: f[k] for k in ("label", "fouler_wins", "battles", "fouler_win_rate")},
        "candidate": {k: c[k] for k in ("label", "fouler_wins", "battles", "fouler_win_rate")},
        "delta_win_rate": round(c["fouler_win_rate"] - f["fouler_win_rate"], 4),
        "z": round(z, 3),
        "p_value": round(p, 4),
        "candidate_beats_baseline_lcb_gt_50": c["fouler_wilson_lcb"] > 0.5,
        "statistically_significant_improvement": significant,
        "ACCEPT": bool(significant or (improved and c["fouler_wilson_lcb"] > 0.5)),
    }
    (RESULTS_DIR / f"compare-{label_frozen}-vs-{label_candidate}.json").write_text(
        json.dumps(verdict, indent=2), encoding="utf-8"
    )
    print(json.dumps(verdict, indent=2))
    return verdict


def main():
    ap = argparse.ArgumentParser(description="fouler-play offline battle eval harness")
    ap.add_argument("--battles", type=int, default=40)
    ap.add_argument("--team", default="gen9/ou/fat-team-1-stall")
    ap.add_argument("--baseline", choices=["simple", "maxbp", "random"], default="simple")
    ap.add_argument("--label", default="candidate")
    ap.add_argument("--showdown-port", type=int, default=_free_port_guess(8765))
    ap.add_argument("--search-time-ms", type=int, default=1200)
    ap.add_argument("--fouler-user", default="foulerEvalBot")
    ap.add_argument("--baseline-user", default="evalBaseline")
    ap.add_argument("--per-battle-timeout", type=float, default=180.0)
    ap.add_argument(
        "--no-setsample", action="store_true",
        help="Degrade opponent set-sampling (frozen-baseline A/B arm). Sets "
             "FOULER_FORCE_NO_SETSAMPLE=1 which makes _sample_pokemon skip move "
             "completion, reproducing the pre-fix inert-opponent behavior.",
    )
    ap.add_argument("--compare", nargs=2, metavar=("FROZEN", "CANDIDATE"))
    args = ap.parse_args()

    if args.compare:
        compare(args.compare[0], args.compare[1])
        return

    extra_env = {}
    if args.no_setsample:
        extra_env["FOULER_FORCE_NO_SETSAMPLE"] = "1"

    run_eval(
        battles=args.battles,
        team=args.team,
        baseline=args.baseline,
        label=args.label,
        showdown_port=args.showdown_port,
        search_time_ms=args.search_time_ms,
        fouler_user=args.fouler_user,
        baseline_user=args.baseline_user,
        extra_env=extra_env,
        per_battle_timeout=args.per_battle_timeout,
    )


if __name__ == "__main__":
    main()
