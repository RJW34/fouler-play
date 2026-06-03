#!/usr/bin/env python3
"""
selfplay_eval.py -- fouler-NEW-vs-fouler-OLD self-play eval harness.

WHY THIS EXISTS
---------------
The offline_eval.py harness plays fouler against a FROZEN poke-env baseline
(SimpleHeuristicsPlayer / MaxBasePowerPlayer). That baseline is too weak to
discriminate small engine changes: against `maxbp`, new / pen_on / no_ss all
swept ~20/20, so the gate cannot rank variants. The ONLY thing that can rank a
candidate engine change is to play it against the incumbent engine itself.

This harness does exactly that. It runs TWO real fouler engines on a local
pokemon-showdown server:

  * OLD (incumbent) in accept_challenge mode -- the reference engine.
  * NEW (candidate) in challenge_user mode  -- repeatedly challenges OLD.

Both are the SAME run.py / fp engine; they differ only by:
  * a git checkout (NEW = working tree / a branch, OLD = a worktree at an
    incumbent commit), and/or
  * per-arm environment variables (e.g. FOULER_PENALTY_PIPELINE,
    FOULER_FORCE_NO_SETSAMPLE, MCTS_BLEND_MAX_SAMPLES) so the SAME code can be
    A/B'd by behaviour toggle for smoke-testing the gate logic.

Because BOTH players are the fouler engine, this harness needs NO poke-env and
NO .venv-eval -- only the project's own venv and a running showdown server.

Outcomes are read from each engine's own stdout log line:
    "Battle finished: <tag> Winner: <name>"
We count, from the NEW engine's perspective, how many battles NEW won. We then
report win-rate, a Wilson lower confidence bound, and a two-proportion z-test of
NEW's win-rate vs 0.5 (the self-play null: equally strong engines split 50/50).

ACCEPTANCE RULE (the gate):
    NEW beats OLD iff the Wilson LCB of NEW's win-rate > 0.50.
That is: we are statistically confident NEW wins MORE than half of head-to-head
games against the incumbent.

CAPACITY
--------
A full N>=50 self-play eval is HEAVY (~2.5h on the local showdown server). Use
--battles 4 for a smoke run that only proves the gate logic / ranking, and the
large N in a JIGGLY low-load burst window.

Usage:
  # env-arm A/B smoke (same code, behaviour toggled) -- proves ranking logic:
  python infrastructure/selfplay_eval.py \
      --battles 4 --teams gen9/ou/fat-team-1-stall \
      --new-env MCTS_BLEND_MAX_SAMPLES=8 \
      --old-env MCTS_BLEND_MAX_SAMPLES=1 \
      --label smoke

  # checkout A/B (the loop's real use): NEW=working tree, OLD=worktree@<commit>
  python infrastructure/selfplay_eval.py \
      --battles 50 --teams-from teams/fat-teams.list \
      --old-checkout /home/ryan/projects/fouler-play.old \
      --label gate

Writes eval_results/selfplay/<label>.json with the verdict.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "eval_results" / "selfplay"

# Reuse the audited statistics from offline_eval.py (single source of truth).
_oe_spec = importlib.util.spec_from_file_location(
    "offline_eval", PROJECT_ROOT / "infrastructure" / "offline_eval.py"
)
offline_eval = importlib.util.module_from_spec(_oe_spec)
_oe_spec.loader.exec_module(offline_eval)
wilson_lower_bound = offline_eval.wilson_lower_bound
two_proportion_z = offline_eval.two_proportion_z

# "Battle finished: <tag> Winner: <name>"  (name may be empty on tie/forfeit)
_WINNER_RE = re.compile(r"Battle finished:\s*(\S+)\s+Winner:\s*(.*?)\s*$")


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def parse_winners(log_path: Path) -> list[tuple[str, str]]:
    """Return [(battle_tag, normalized_winner), ...] from a fouler engine log.

    An empty/None winner (tie or forfeit) yields ("", "") for the winner field.
    """
    out: list[tuple[str, str]] = []
    if not log_path.exists():
        return out
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _WINNER_RE.search(line)
        if m:
            tag = m.group(1).strip()
            winner = m.group(2).strip()
            if winner.lower() in {"none", "tie", ""}:
                winner = ""
            out.append((tag, _normalize(winner)))
    return out


def tally(
    new_user: str, old_user: str, new_winners: list[tuple[str, str]]
) -> dict:
    """Count NEW wins / OLD wins / ties from the NEW engine's winner log.

    Deduped by battle tag (the same battle can appear once); a battle with no
    decisive winner counts as a tie and is EXCLUDED from the win-rate
    denominator (it gives no signal about which engine is stronger).
    """
    new_n = _normalize(new_user)
    old_n = _normalize(old_user)
    seen: dict[str, str] = {}
    for tag, winner in new_winners:
        seen[tag] = winner  # last write wins; finals are stable
    new_wins = old_wins = ties = unknown = 0
    for winner in seen.values():
        if winner == new_n:
            new_wins += 1
        elif winner == old_n:
            old_wins += 1
        elif winner == "":
            ties += 1
        else:
            unknown += 1
    decisive = new_wins + old_wins
    return {
        "battles_finished": len(seen),
        "decisive": decisive,
        "new_wins": new_wins,
        "old_wins": old_wins,
        "ties": ties,
        "unknown": unknown,
    }


# Minimum decisive head-to-head games before the gate may ACCEPT. Without a
# floor, a tiny sweep can spuriously clear LCB>0.5 (e.g. 4/4 -> LCB 0.51), so a
# 4-game smoke could falsely "accept". The floor makes smoke runs prove RANKING
# only; promotion requires a real burst eval (N>=MIN_DECISIVE).
MIN_DECISIVE = int(os.getenv("SELFPLAY_MIN_DECISIVE", "30"))


def verdict_from_counts(new_wins: int, decisive: int, label: str,
                        min_decisive: int = MIN_DECISIVE) -> dict:
    wr = (new_wins / decisive) if decisive else 0.0
    lcb = wilson_lower_bound(new_wins, decisive)
    # Two-proportion z of NEW's record vs a notional 50/50 split over the same n.
    half = decisive // 2
    z, p = two_proportion_z(new_wins, decisive, half, decisive)
    accept = bool(lcb > 0.50 and decisive >= min_decisive)
    return {
        "label": label,
        "decisive_battles": decisive,
        "min_decisive": min_decisive,
        "new_wins": new_wins,
        "old_wins": decisive - new_wins,
        "new_win_rate": round(wr, 4),
        "new_wilson_lcb": round(lcb, 4),
        "z_vs_50_50": round(z, 3),
        "p_value": round(p, 4),
        "rule": f"ACCEPT iff Wilson LCB(new win-rate) > 0.50 AND decisive >= {min_decisive}",
        "ACCEPT": accept,
    }


def _build_env(arm_env: dict | None, search_time_ms: int, stats_file: Path) -> dict:
    env = os.environ.copy()
    env.setdefault("PS_PASSWORD", "")
    env["FOULER_NO_SECURITY_LOGIN"] = "1"
    # FOULER-EVAL-NO-SINGLETON-2026-06-03: eval arms run run.py from the SAME
    # directory as the live ladder bot. Without this, run.py's singleton lock
    # would refuse to start the arm (live bot holds the lock) AND its stale-proc
    # reaper would KILL the live ladder bot. Skip the singleton lock for eval
    # arms -- they are fully isolated (own users/port/stats file).
    env["FOULER_NO_SINGLETON_LOCK"] = "1"
    env["SEARCH_TIME_MS"] = str(search_time_ms)
    env["MIN_SEARCH_TIME_MS"] = "0"
    env["LOSS_TRIGGERED_DRAIN"] = "0"  # play all battles regardless of losses
    env["MAX_CONCURRENT_BATTLES"] = "1"
    # FOULER-EVAL-TURNCAP-2026-06-03: forward the eval-only hard turn cap to BOTH
    # arms. run.py/fp.run_battle honour FOULER_MAX_TURNS (default 0 = disabled in
    # the live ladder bot); setting it here makes stall/fat mirror matches that
    # would otherwise run hundreds of turns terminate at the cap with a decisive
    # fewer-fainted (remaining-mons) winner, so the self-play gate can actually
    # reach N>=30 decisive in a sane window. Read from the eval process env so the
    # burst runner can set it once; both arms inherit the same cap.
    _cap = os.getenv("FOULER_MAX_TURNS")
    if _cap and _cap.strip() not in ("", "0"):
        env["FOULER_MAX_TURNS"] = _cap.strip()
    # CRITICAL: redirect each eval engine's battle stats to a throwaway file so
    # the eval NEVER pollutes the live ladder battle_stats.json (which feeds
    # autoresearch + elo_watchdog). run.py honours BATTLE_STATS_FILE.
    env["BATTLE_STATS_FILE"] = str(stats_file)
    if arm_env:
        env.update({k: str(v) for k, v in arm_env.items()})
    return env


def _run_arm(
    *,
    cwd: Path,
    log_path: Path,
    user: str,
    bot_mode: str,
    team: str,
    fmt: str,
    ws_uri: str,
    run_count: int,
    search_time_ms: int,
    env: dict,
    challenge_user: str | None = None,
) -> subprocess.Popen:
    cmd = [
        sys.executable, "run.py",
        "--websocket-uri", ws_uri,
        "--ps-username", user,
        "--bot-mode", bot_mode,
        "--pokemon-format", fmt,
        "--team-name", team,
        "--run-count", str(run_count),
        "--search-time-ms", str(search_time_ms),
        "--save-replay", "never",
    ]
    if bot_mode == "challenge_user" and challenge_user:
        cmd += ["--user-to-challenge", challenge_user]
    flog = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        cmd, cwd=str(cwd), env=env, stdout=flog, stderr=subprocess.STDOUT
    )


def run_selfplay(
    *,
    battles: int,
    teams: list[str],
    label: str,
    showdown_port: int,
    search_time_ms: int,
    new_user: str,
    old_user: str,
    new_env: dict | None,
    old_env: dict | None,
    new_checkout: Path,
    old_checkout: Path,
    per_battle_timeout: float,
    fmt: str = "gen9ou",
) -> dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ws_uri = f"ws://localhost:{showdown_port}/showdown/websocket"

    # Distribute N battles across the provided teams (round-robin blocks).
    per_team = max(1, battles // len(teams))
    remainder = battles - per_team * len(teams)
    team_counts = []
    for i, t in enumerate(teams):
        n = per_team + (1 if i < remainder else 0)
        if n > 0:
            team_counts.append((t, n))

    agg = {"battles_finished": 0, "decisive": 0, "new_wins": 0,
           "old_wins": 0, "ties": 0, "unknown": 0}
    per_team_results = []

    for team, n in team_counts:
        safe = team.replace("/", "_")
        old_log = RESULTS_DIR / f"{label}-{safe}-old.log"
        new_log = RESULTS_DIR / f"{label}-{safe}-new.log"
        old_stats = RESULTS_DIR / f"{label}-{safe}-old-stats.json"
        new_stats = RESULTS_DIR / f"{label}-{safe}-new-stats.json"

        old_e = _build_env(old_env, search_time_ms, old_stats)
        new_e = _build_env(new_env, search_time_ms, new_stats)

        print(f"[selfplay:{label}] team={team} n={n}: starting OLD (accept) ...",
              flush=True)
        # OLD accepts; keep it alive past the challenge count so it never strands
        # a pending challenge.
        old_proc = _run_arm(
            cwd=old_checkout, log_path=old_log, user=old_user,
            bot_mode="accept_challenge", team=team, fmt=fmt, ws_uri=ws_uri,
            run_count=n + 5, search_time_ms=search_time_ms, env=old_e,
        )
        time.sleep(12)  # let OLD log in and idle in accept state

        print(f"[selfplay:{label}] team={team} n={n}: starting NEW (challenge) ...",
              flush=True)
        new_proc = _run_arm(
            cwd=new_checkout, log_path=new_log, user=new_user,
            bot_mode="challenge_user", team=team, fmt=fmt, ws_uri=ws_uri,
            run_count=n, search_time_ms=search_time_ms, env=new_e,
            challenge_user=old_user,
        )

        overall_timeout = per_battle_timeout * n + 120
        try:
            new_proc.wait(timeout=overall_timeout)
        except subprocess.TimeoutExpired:
            print(f"[selfplay:{label}] NEW timed out after {overall_timeout:.0f}s",
                  flush=True)
            new_proc.kill()
        # OLD should drain; give it a moment then stop.
        try:
            old_proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            old_proc.terminate()
            try:
                old_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                old_proc.kill()

        counts = tally(new_user, old_user, parse_winners(new_log))
        counts["team"] = team
        counts["requested"] = n
        per_team_results.append(counts)
        for k in agg:
            agg[k] += counts[k]
        print(f"[selfplay:{label}] team={team}: NEW {counts['new_wins']} / "
              f"OLD {counts['old_wins']} / tie {counts['ties']} "
              f"(decisive {counts['decisive']})", flush=True)

    v = verdict_from_counts(agg["new_wins"], agg["decisive"], label)
    v.update({
        "teams": teams,
        "requested_battles": battles,
        "battles_finished": agg["battles_finished"],
        "ties": agg["ties"],
        "unknown": agg["unknown"],
        "new_user": new_user,
        "old_user": old_user,
        "new_env": new_env or {},
        "old_env": old_env or {},
        "new_checkout": str(new_checkout),
        "old_checkout": str(old_checkout),
        "search_time_ms": search_time_ms,
        "per_team": per_team_results,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    (RESULTS_DIR / f"{label}.json").write_text(json.dumps(v, indent=2),
                                               encoding="utf-8")
    print(f"[selfplay:{label}] VERDICT: NEW {v['new_wins']}/{v['decisive_battles']} "
          f"= {v['new_win_rate']:.1%} (LCB {v['new_wilson_lcb']:.1%}) "
          f"ACCEPT={v['ACCEPT']}", flush=True)
    return v


def _parse_kv_list(items: list[str] | None) -> dict:
    out: dict[str, str] = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--*-env expects KEY=VALUE, got {it!r}")
        k, val = it.split("=", 1)
        out[k.strip()] = val.strip()
    return out


def _load_teams(args) -> list[str]:
    if args.teams_from:
        path = PROJECT_ROOT / args.teams_from if not Path(args.teams_from).is_absolute() else Path(args.teams_from)
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
        return [ln for ln in lines if ln and not ln.startswith("#")]
    if args.teams:
        return [t.strip() for t in args.teams.split(",") if t.strip()]
    return ["gen9/ou/fat-team-1-stall"]


def main():
    ap = argparse.ArgumentParser(description="fouler NEW-vs-OLD self-play eval")
    ap.add_argument("--battles", type=int, default=4)
    ap.add_argument("--teams", default=None,
                    help="Comma-separated team names (relative to ./teams).")
    ap.add_argument("--teams-from", default=None,
                    help="File listing team names, one per line (e.g. teams/fat-teams.list).")
    ap.add_argument("--label", default="selfplay")
    ap.add_argument("--showdown-port", type=int,
                    default=int(os.getenv("EVAL_SHOWDOWN_PORT", "8765")))
    ap.add_argument("--search-time-ms", type=int, default=1200)
    ap.add_argument("--new-user", default="foulerNEW")
    ap.add_argument("--old-user", default="foulerOLD")
    ap.add_argument("--new-env", action="append",
                    help="KEY=VALUE env for the NEW arm (repeatable).")
    ap.add_argument("--old-env", action="append",
                    help="KEY=VALUE env for the OLD arm (repeatable).")
    ap.add_argument("--new-checkout", default=str(PROJECT_ROOT),
                    help="Working dir for NEW (default: this checkout).")
    ap.add_argument("--old-checkout", default=str(PROJECT_ROOT),
                    help="Working dir for OLD (default: this checkout; use a "
                         "git worktree at the incumbent commit for a real gate).")
    ap.add_argument("--per-battle-timeout", type=float, default=180.0)
    # NOTE: with FOULER_MAX_TURNS set and the HO eval teams, battles finish in
    # ~1-3min; the 180s/battle default is a safe ceiling, not the expected time.
    args = ap.parse_args()

    teams = _load_teams(args)
    run_selfplay(
        battles=args.battles,
        teams=teams,
        label=args.label,
        showdown_port=args.showdown_port,
        search_time_ms=args.search_time_ms,
        new_user=args.new_user,
        old_user=args.old_user,
        new_env=_parse_kv_list(args.new_env),
        old_env=_parse_kv_list(args.old_env),
        new_checkout=Path(args.new_checkout),
        old_checkout=Path(args.old_checkout),
        per_battle_timeout=args.per_battle_timeout,
    )


if __name__ == "__main__":
    main()
