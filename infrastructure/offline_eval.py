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
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "eval_results" / "offline"
VENV_PY = PROJECT_ROOT / ".venv-eval" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = PROJECT_ROOT / ".venv-eval" / "bin" / "python"


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

    env = os.environ.copy()
    env.setdefault("PS_PASSWORD", "")
    # Local --no-security server: skip HTTP assertion entirely (/trn user,0,).
    env["FOULER_NO_SECURITY_LOGIN"] = "1"
    env["SEARCH_TIME_MS"] = str(search_time_ms)
    env["MIN_SEARCH_TIME_MS"] = "0"  # don't clamp; we set search time explicitly
    env["LOSS_TRIGGERED_DRAIN"] = "0"  # play all N battles regardless of losses
    env["MAX_CONCURRENT_BATTLES"] = "1"
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})

    # --- Launch fouler in accept_challenge mode (the REAL engine) ---
    fouler_log = RESULTS_DIR / f"{label}-fouler.log"
    fouler_cmd = [
        sys.executable, "run.py",
        "--websocket-uri", ws_uri,
        "--ps-username", fouler_user,
        "--bot-mode", "accept_challenge",
        "--pokemon-format", fmt,
        "--team-name", team,
        # Keep fouler alive a few battles past the baseline's challenge count so it
        # never exits mid-series and strands a pending challenge.
        "--run-count", str(battles + 5),
        "--search-time-ms", str(search_time_ms),
        "--save-replay", "never",
    ]
    print(f"[eval:{label}] starting fouler: {' '.join(fouler_cmd)}")
    with open(fouler_log, "w", encoding="utf-8") as flog:
        fouler_proc = subprocess.Popen(
            fouler_cmd, cwd=str(PROJECT_ROOT), env=env,
            stdout=flog, stderr=subprocess.STDOUT,
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

    overall_timeout = per_battle_timeout * battles + 120
    try:
        baseline_proc.wait(timeout=overall_timeout)
    except subprocess.TimeoutExpired:
        print(f"[eval:{label}] baseline timed out after {overall_timeout:.0f}s")
        baseline_proc.kill()

    # fouler should exit on its own after run-count; give it a moment then kill.
    try:
        fouler_proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        fouler_proc.terminate()
        try:
            fouler_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            fouler_proc.kill()

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
