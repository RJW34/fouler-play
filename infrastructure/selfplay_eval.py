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


# Substrings (lowercased) that, if any of them appear in an arm's stdout/stderr
# log, mean the arm never got into a usable Showdown session. Most commonly the
# local pokemon-showdown server is missing / restarted / a stray process owns
# the port and replies with the wrong protocol. When this happens we get 0
# finished battles and the math layer reports "REJECT, 0/0" -- which is
# indistinguishable from a real loss. The scanner+summary below let the verdict
# JSON say WHY the gate produced nothing.
_ARM_FAILURE_PATTERNS: tuple[str, ...] = (
    "unsupported protocol",                 # websockets: HTTP/1.0 vs 1.1
    "expected http/1.1",                    # same family
    "handshake_exc",                        # websockets handshake failure
    "connectionrefusederror",               # TCP connect refused
    "[errno 111]",                          # connection refused on linux
    "no route to host",                     # network unreachable
    "name or service not known",            # DNS failure
    "websocket connection is closed",       # disconnected mid-handshake
)


def scan_arm_log_for_failure(log_path: Path) -> str | None:
    """Return the most-informative log line that signals the arm never connected.

    Python tracebacks print frames first ("most recent call last") and the
    exception message last, so we prefer the LAST matching line in the log --
    that's almost always the human-readable exception (e.g.
    "ValueError: unsupported protocol; ...") rather than an intermediate
    `raise self.protocol.handshake_exc` frame. Returns None if the log is
    missing, empty, or contains nothing matching _ARM_FAILURE_PATTERNS.
    """
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    best: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        for pat in _ARM_FAILURE_PATTERNS:
            if pat in low:
                best = line
                break
    return best


def gate_failure_summary(per_team_results: list[dict]) -> str | None:
    """One-line "why the gate produced nothing" string, or None.

    Returns a summary iff there is at least one arm-failure across the per-team
    results AND the run as a whole produced zero finished battles. If even one
    team got a real battle in, we did NOT fail to run -- a real result exists
    and the math layer's REJECT is meaningful.
    """
    total_finished = sum(int(t.get("battles_finished", 0)) for t in per_team_results)
    if total_finished > 0:
        return None
    errs: list[str] = []
    for t in per_team_results:
        ae = t.get("arm_errors") or {}
        # Prefer NEW (the candidate) since the candidate failing is the
        # actionable case; fall back to OLD if NEW is silent.
        msg = ae.get("new") or ae.get("old")
        if msg:
            errs.append(f"{t.get('team', '?')}: {msg}")
    if not errs:
        return None
    # Collapse to the first arm error -- they're almost always the same root
    # cause (e.g. the websocket port is wrong for every arm). Keep all teams
    # in the per-team blob; the top-level just needs ONE actionable line.
    return errs[0]


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

# Hard per-battle turn cap. A stall mirror can otherwise run ~1 turn/70s and
# NEVER emit a "Battle finished" line inside per_battle_timeout, so it is killed
# and counted as 0 decisive -- which is exactly why the N>=30 gate could never
# terminate. With a cap, run.py force-decides at this turn via the HP-fraction
# score-on-cap (one side forfeits), Showdown emits a real |win|, and the battle
# counts as DECISIVE. 0 disables (NOT used by the gate). The eval ALWAYS sets a
# positive cap so every battle terminates decisively.
DEFAULT_TURN_CAP = int(os.getenv("SELFPLAY_TURN_CAP", "60"))


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


def _build_env(arm_env: dict | None, search_time_ms: int, stats_file: Path,
               turn_cap: int = DEFAULT_TURN_CAP) -> dict:
    env = os.environ.copy()
    env.setdefault("PS_PASSWORD", "")
    env["FOULER_NO_SECURITY_LOGIN"] = "1"
    env["SEARCH_TIME_MS"] = str(search_time_ms)
    env["MIN_SEARCH_TIME_MS"] = "0"
    # Force-decide stall mirrors at the turn cap so EVERY eval battle is
    # decisive within per_battle_timeout (the keystone of gate viability).
    env["FOULER_BATTLE_TURN_CAP"] = str(int(turn_cap))
    env["LOSS_TRIGGERED_DRAIN"] = "0"  # play all battles regardless of losses
    env["MAX_CONCURRENT_BATTLES"] = "1"
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
) -> tuple[subprocess.Popen, object]:
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
    # Mirror the env turn cap onto the CLI so it is explicit in the arm log
    # (run.py reads FOULER_BATTLE_TURN_CAP as the arg default, but a stale
    # incumbent worktree may predate that default -- the explicit flag wins).
    _cap = env.get("FOULER_BATTLE_TURN_CAP")
    if _cap and _cap != "0":
        cmd += ["--battle-turn-cap", _cap]
    if bot_mode == "challenge_user" and challenge_user:
        cmd += ["--user-to-challenge", challenge_user]
    flog = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=env, stdout=flog, stderr=subprocess.STDOUT
    )
    # Return the log handle too so the caller can ALWAYS close it (an
    # un-closed handle per arm leaks an fd across a 50-battle gate).
    return proc, flog


def _stop_proc(proc, *, grace: float = 15.0) -> None:
    """Terminate then KILL a child engine and REAP it (no zombies, no orphans).

    A bare ``.kill()`` without a following ``.wait()`` leaves a zombie, and a
    raised exception mid-team used to orphan both run.py engines -- which keep
    the showdown websocket session open and can wedge the next team. This makes
    teardown unconditional and idempotent.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        # Best-effort: never let teardown raise and mask the real error.
        try:
            proc.kill()
        except Exception:
            pass


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
    turn_cap: int = DEFAULT_TURN_CAP,
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

        old_e = _build_env(old_env, search_time_ms, old_stats, turn_cap=turn_cap)
        new_e = _build_env(new_env, search_time_ms, new_stats, turn_cap=turn_cap)

        print(f"[selfplay:{label}] team={team} n={n}: starting OLD (accept) ...",
              flush=True)
        # OLD accepts; keep it alive past the challenge count so it never strands
        # a pending challenge.
        old_proc, old_flog = _run_arm(
            cwd=old_checkout, log_path=old_log, user=old_user,
            bot_mode="accept_challenge", team=team, fmt=fmt, ws_uri=ws_uri,
            run_count=n + 5, search_time_ms=search_time_ms, env=old_e,
        )
        new_proc = None
        new_flog = None
        try:
            time.sleep(12)  # let OLD log in and idle in accept state

            print(f"[selfplay:{label}] team={team} n={n}: starting NEW (challenge) ...",
                  flush=True)
            new_proc, new_flog = _run_arm(
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
            # OLD should drain on its own; give it a brief window.
            try:
                old_proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        finally:
            # Unconditionally reap BOTH engines (no zombies / no orphaned
            # run.py holding the showdown port) and close BOTH log handles
            # (no fd leak), even if tally/anything above raised.
            _stop_proc(new_proc)
            _stop_proc(old_proc)
            for _fh in (new_flog, old_flog):
                try:
                    if _fh is not None:
                        _fh.close()
                except Exception:
                    pass

        counts = tally(new_user, old_user, parse_winners(new_log))
        counts["team"] = team
        counts["requested"] = n
        counts["arm_errors"] = {
            "new": scan_arm_log_for_failure(new_log),
            "old": scan_arm_log_for_failure(old_log),
        }
        per_team_results.append(counts)
        for k in agg:
            agg[k] += counts[k]
        print(f"[selfplay:{label}] team={team}: NEW {counts['new_wins']} / "
              f"OLD {counts['old_wins']} / tie {counts['ties']} "
              f"(decisive {counts['decisive']})", flush=True)

    failure_reason = gate_failure_summary(per_team_results)
    v = verdict_from_counts(agg["new_wins"], agg["decisive"], label)
    v.update({
        "gate_failed_to_run": failure_reason is not None,
        "failure_reason": failure_reason,
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
        "turn_cap": turn_cap,
        "per_team": per_team_results,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    (RESULTS_DIR / f"{label}.json").write_text(json.dumps(v, indent=2),
                                               encoding="utf-8")
    if v.get("gate_failed_to_run"):
        # Loud, single-line marker so the agent stdout capture surfaces this.
        print(f"[selfplay:{label}] GATE_FAILED_TO_RUN: {v['failure_reason']}",
              flush=True)
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
    # Fast NON-STALL default so battles end naturally fast; the turn cap is the
    # backstop for any that still drag. fat-team-1-stall is the slowest mirror
    # and is intentionally NOT the eval default.
    fast = PROJECT_ROOT / "teams" / "eval-fast-teams.list"
    if fast.exists():
        lines = [ln.strip() for ln in fast.read_text(encoding="utf-8").splitlines()]
        picked = [ln for ln in lines if ln and not ln.startswith("#")]
        if picked:
            return picked
    return ["gen9/ou/fat-team-2-pivot"]


def main():
    ap = argparse.ArgumentParser(description="fouler NEW-vs-OLD self-play eval")
    ap.add_argument("--battles", type=int, default=4)
    ap.add_argument("--teams", default=None,
                    help="Comma-separated team names (relative to ./teams).")
    ap.add_argument("--teams-from", default=None,
                    help="File listing team names, one per line (e.g. teams/fat-teams.list).")
    ap.add_argument("--label", default="selfplay")
    # Default eval port is 18765, NOT 8765. Port 8765 is permanently held on
    # this devstream box by the PokeCompletionist OBS overlay HTTP server,
    # which makes the gate's WS handshake fail with "HTTP/1.0 404 not found"
    # from a totally unrelated service (the operator-facing reason was
    # captured at eval_results/selfplay/gate-20260603-020259-*.log). With the
    # default off 8765 the probe's reason becomes the truthful
    # "tcp connect refused on 127.0.0.1:18765" -- i.e. "showdown not running"
    # -- and the documented "Start pokemon-showdown --no-security 18765"
    # command can actually succeed because the port is free.
    ap.add_argument("--showdown-port", type=int,
                    default=int(os.getenv("EVAL_SHOWDOWN_PORT", "18765")))
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
    ap.add_argument("--turn-cap", type=int, default=DEFAULT_TURN_CAP,
                    help="Force-decide any battle at this turn via score-on-cap "
                         "(HP-fraction-sum). Makes stall mirrors terminate "
                         "DECISIVELY. 0 disables (do not use for the gate).")
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
        turn_cap=args.turn_cap,
    )


if __name__ == "__main__":
    main()
