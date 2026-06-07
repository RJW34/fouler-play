#!/usr/bin/env python3
"""
improve_loop.py -- the CLOSED, MEASURED self-improvement loop for fouler-play.

ONE iteration does, end to end:
  1. MINE recent loss replays  -> run autoresearch to (re)generate the top issue.
  2. PROPOSE one concrete fix    -> improve_agent applies a single targeted diff
                                    on a working branch (NOT master).
  3. GATE                        -> fouler-NEW (with the diff) vs fouler-OLD
                                    (incumbent HEAD) self-play on local showdown;
                                    ACCEPT iff NEW's Wilson LCB over OLD > 0.50.
  4. DECIDE                      -> accept (commit on the branch) or revert
                                    (restore the file). The decision is driven by
                                    the MEASURED self-play verdict, never pytest
                                    and never the weak maxbp baseline.
  5. RECORD                      -> append a durable, append-only ledger entry
                                    (eval_results/improve_ledger.jsonl) capturing
                                    the issue, target file, verdict numbers, and
                                    accept/revert outcome -> a clear audit trail.

HERMES can run this autonomously. It is safe by construction:
  * Works only on a dedicated branch (refuses to run on master unless
    --allow-master is passed). NEVER pushes (push is a separate human/HERMES step).
  * The self-play gate redirects each engine's battle stats to throwaway files,
    so it NEVER touches the live ladder battle_stats.json.
  * If the gate can't run (no local showdown server), the iteration is recorded
    as SKIPPED and no change is promoted.

Capacity note: the self-play gate is HEAVY (~2.5h at N=50). Schedule the full
loop only in a JIGGLY low-load burst window. Use --smoke-battles N for a small
ranking check (the loop's accept/revert wiring works identically at any N).

Usage:
  # one autonomous iteration, full gate:
  python infrastructure/improve_loop.py --iterations 1

  # dry-run the mining + proposal without applying or gating:
  python infrastructure/improve_loop.py --dry-run

  # smoke the loop's decision wiring with a tiny self-play eval:
  python infrastructure/improve_loop.py --iterations 1 --smoke-battles 4
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.runtime_lease import RuntimeLeaseBusy, acquire_runtime_lease

LEDGER_PATH = PROJECT_ROOT / "eval_results" / "improve_ledger.jsonl"
AUTORESEARCH_JSON = PROJECT_ROOT / "replay_analysis" / "autoresearch_latest.json"
BATTLE_STATS_PATH = PROJECT_ROOT / "battle_stats.json"

# An iteration older than this is "STALE" on the status surface. The cron
# cadence today is roughly hourly (entries seen ~74 min apart); 120 min picks
# the smallest threshold that absorbs one missed cron tick without crying
# wolf, while still escalating when the loop has actually gone silent.
STALE_THRESHOLD_MIN = 120

# A separate, orthogonal freshness signal: when the most recent battle in
# battle_stats.json is older than this, the UPSTREAM evidence stream
# (JIGGLY bot -> ubunztu) has dried up regardless of whether the cron is
# iterating. Without this, the headline reads "learn-loop idle" (a
# loop-side framing) when the truth is "the bot stopped feeding battles".
# Surfacing both lets operators route the fix correctly: STALE => check
# the cron; STREAM-STALE => check the bot/sync.  60 min absorbs a normal
# laddering cadence (battles arrive faster than that during play) while
# escalating when the feed has clearly gone silent.
BATTLE_STREAM_STALE_THRESHOLD_MIN = 60
AUTO_IMPROVE_SENTINEL = "FOULER_PLAY_ENABLE_AUTO_IMPROVE"
MAX_ITERATIONS_WITHOUT_RECURSIVE_READY = 1
OFFLINE_NO_LIVE_EXCLUSIONS = ("public ladder", "live Discord transport")


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def auto_improve_enabled(cli_enabled: bool = False) -> bool:
    return bool(cli_enabled or _env_flag_enabled(AUTO_IMPROVE_SENTINEL))


def _utcnow() -> datetime:
    """Indirection so tests can pin "now" without freezing the system clock."""
    return datetime.now(timezone.utc)


def _battle_stream_age_minutes() -> int | None:
    """Wall-clock minutes since the newest battle in battle_stats.json.

    Returns None when the file is missing, unreadable, has no battles, or
    has no parseable timestamps -- those are all "no data" rather than
    "fresh" so the caller can decide how to surface the absence. The
    loop_status() headline only adds a STREAM-STALE prefix when this
    returns an int >= BATTLE_STREAM_STALE_THRESHOLD_MIN; None never
    triggers the prefix so a freshly-cloned repo or a permission glitch
    doesn't cry stale on a state it can't actually observe.
    """
    if not BATTLE_STATS_PATH.exists():
        return None
    try:
        data = json.loads(BATTLE_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    battles = data.get("battles") if isinstance(data, dict) else None
    if not isinstance(battles, list) or not battles:
        return None
    newest_ts = ""
    for b in battles:
        if not isinstance(b, dict):
            continue
        ts = b.get("timestamp")
        if isinstance(ts, str) and ts > newest_ts:
            newest_ts = ts
    if not newest_ts:
        return None
    try:
        ts = datetime.fromisoformat(newest_ts)
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, int((_utcnow() - ts).total_seconds() // 60))


def _run(cmd: list[str], *, timeout: int, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=env,
    )


def _git(args: list[str]) -> str:
    return _run(["git", *args], timeout=60).stdout.strip()


def current_branch() -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"])


def append_ledger(entry: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[LOOP] ledger += {entry.get('outcome')} :: {entry.get('issue','')[:60]}")


def mine_and_research(num_battles: int, dry_run: bool) -> dict:
    """Run autoresearch over recent battles to (re)generate the top issue."""
    # KEYSTONE: pull fresh loss EVIDENCE (replay JSON + decision traces) from
    # the live JIGGLY runtime first. autoresearch only accepts replay/trace-
    # backed losses; without this the window has battle_stats rows but ZERO
    # local evidence -> top_issue=null -> the loop starves. Best-effort: a
    # sync failure (JIGGLY unreachable) must NOT crash the iteration -- it just
    # means we mine whatever evidence is already local.
    if not os.getenv("IMPROVE_LOOP_SKIP_EVIDENCE_SYNC"):
        try:
            sync = _run(
                [sys.executable, "infrastructure/sync_loss_evidence.py",
                 "--window", str(num_battles)],
                timeout=int(os.getenv("IMPROVE_LOOP_EVIDENCE_SYNC_TIMEOUT", "300")),
            )
            tail = (sync.stdout or "").splitlines()[-1:] or [""]
            print(f"[LOOP] evidence-sync: {tail[0]}")
        except Exception as e:
            print(f"[LOOP] evidence-sync skipped ({e!r}); mining local evidence only.")
    print(f"[LOOP] Mining: autoresearch over last {num_battles} battles ...")
    proc = _run(
        [sys.executable, "-m", "replay_analysis.autoresearch",
         "-n", str(num_battles), "--no-discord"],
        timeout=600,
    )
    if proc.returncode != 0:
        print(f"[LOOP] autoresearch exited {proc.returncode}: "
              f"{(proc.stderr or proc.stdout)[-400:]}")
    report = {}
    if AUTORESEARCH_JSON.exists():
        try:
            report = json.loads(AUTORESEARCH_JSON.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[LOOP] could not read autoresearch report: {e}")
    top = (report.get("top_issue") or {}).get("title", "") if report else ""
    print(f"[LOOP] Top issue: {top or '(none)'}")
    return report


def propose_and_gate(dry_run: bool, smoke_battles: int | None) -> dict:
    """Invoke improve_agent (apply diff -> tests -> self-play gate -> accept/revert).

    improve_agent already encapsulates: pick target, build prompt, call model,
    apply diff, syntax/test pre-filter, then offline_eval_gate() (now self-play),
    and commits ONLY if the gate accepts. We run it as a subprocess so a crash
    can't take the loop down, and so we capture its decision from the ledger that
    improve_agent's deploy log + our own post-inspection produce.
    """
    env = os.environ.copy()
    if smoke_battles is not None:
        env["IMPROVE_AGENT_SELFPLAY_BATTLES"] = str(smoke_battles)
    head_before = _git(["rev-parse", "HEAD"])
    # 2s margin: the gate file's mtime can round slightly BELOW the wall-clock
    # instant we captured here (fs timestamp granularity), so floor the "since"
    # a hair earlier to avoid discarding a genuinely-fresh verdict.
    gate_started_at = time.time() - 2.0
    args = [sys.executable, "infrastructure/improve_agent.py"]
    if dry_run:
        args.append("--dry-run")
    print(f"[LOOP] Proposing + gating via improve_agent "
          f"(smoke_battles={smoke_battles}, dry_run={dry_run}) ...")
    # improve_agent runs the heavy self-play gate; give it a wide timeout.
    proc = _run(args, timeout=int(os.getenv("IMPROVE_LOOP_AGENT_TIMEOUT", "20000")), env=env)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    print(out[-2500:])
    head_after = _git(["rev-parse", "HEAD"])
    committed = head_after != head_before
    accepted, gate_skipped, verdict_blob = _parse_verdict_line(out)
    if not verdict_blob:
        # No verdict printed at all => gate never ran (agent crashed early, or
        # short-circuited before reaching it). Fall back to head movement.
        accepted = committed
    # The skipped-field value lives in the verdict detail JSON; pull it now so
    # one_iteration can record WHY the gate couldn't run and loop_status can
    # render a distinct headline. Only meaningful when gate_skipped=True.
    gate_skip_reason = _parse_gate_skip_reason(out) if gate_skipped else None
    return {
        "committed": committed,
        "accepted": accepted,
        "gate_skipped": gate_skipped,
        "gate_skip_reason": gate_skip_reason,
        "head_before": head_before,
        "head_after": head_after,
        "verdict_line": verdict_blob,
        "agent_returncode": proc.returncode,
        "agent_output_tail": out[-1000:],
        "gate_started_at": gate_started_at,
    }


def _parse_verdict_line(out: str) -> tuple[bool, bool, str]:
    """Extract (accepted, gate_skipped, verdict_blob) from improve_agent stdout.

    improve_agent prints exactly one line:
        [AGENT] Eval gate verdict: ACCEPT=<bool> :: <json detail>

    A fail-closed skip (no showdown server, worktree-add failed, harness missing)
    surfaces as ``"skipped"`` / ``"fail_closed"`` keys in the detail blob — this
    is NOT a measured rejection of the candidate. Distinguishing the two is what
    lets the ledger and Discord surface tell operators whether the loop is
    blocked on a runtime prereq vs actually rejecting patches.
    """
    accepted = False
    gate_skipped = False
    verdict_blob = ""
    for line in out.splitlines():
        if "Eval gate verdict:" not in line:
            continue
        verdict_blob = line.strip()
        accepted = "ACCEPT=True" in line
        if '"skipped":' in line or '"fail_closed":' in line:
            gate_skipped = True
    return accepted, gate_skipped, verdict_blob


def _parse_gate_skip_reason(out: str) -> str | None:
    """Extract the gate's skip-reason from improve_agent's stdout verdict line.

    When the self-play gate cannot RUN (no showdown server, both arms died at
    handshake, worktree-add failed, harness missing, gate_failed_to_run from
    selfplay_eval) improve_agent prints a verdict line whose detail JSON carries
    a ``"skipped"`` field. ``gate_failed_to_run`` (selfplay_eval_gate path,
    commit 876f6e0) routes through as ``"gate_failed_to_run: <reason>"``;
    other prereq misses route as ``"no showdown server on :8765"`` etc.

    Loop-status reads this so the Discord/overlay headline can distinguish
    "the GATE couldn't run (showdown/harness/handshake)" from autoresearch
    skip-reasons (``evidence_starved`` / ``no_pattern_matched`` etc., which
    mean the LOOP found nothing to fix, not that the gate failed). Operators
    chase different blockers for each: gate-failure points at the showdown
    server / harness / arm-spawn; autoresearch-starvation points at the
    JIGGLY evidence-sync or the pattern library.

    Mirrors ``_parse_verdict_line`` — takes the LAST verdict line if multiple
    exist, returns None for missing/malformed/non-skip detail. Pure function;
    no IO; safe to test in isolation.
    """
    last_detail: str | None = None
    for line in out.splitlines():
        if "Eval gate verdict:" not in line:
            continue
        idx = line.find(" :: ")
        if idx < 0:
            last_detail = None
            continue
        last_detail = line[idx + len(" :: "):].strip()
    if not last_detail:
        return None
    try:
        parsed = json.loads(last_detail)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("skipped")
    if isinstance(value, str) and value:
        return value
    return None


def latest_selfplay_verdict(since: float | None = None) -> dict | None:
    """Most recent self-play gate result.

    When ``since`` (a POSIX mtime) is given, only a gate-*.json written AFTER
    that instant is returned -- this pins the verdict to the gate THIS iteration
    actually ran, so a stale file from a previous loop run or a concurrent JIGGLY
    burst can never be misattributed to the current candidate. Returns None if no
    qualifying file exists (the caller then records no verdict rather than a lie).
    """
    d = PROJECT_ROOT / "eval_results" / "selfplay"
    if not d.exists():
        return None
    gates = sorted(d.glob("gate-*.json"), key=lambda p: p.stat().st_mtime)
    if since is not None:
        gates = [g for g in gates if g.stat().st_mtime >= since]
    if not gates:
        return None
    try:
        return json.loads(gates[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def _ladder_snapshot() -> dict | None:
    """Real recorded ELO trajectory toward 1700 -- the OUTER truth signal,
    recorded alongside each iteration so the ledger shows whether accepted
    fixes actually move the live ladder, not just win self-play.

    Surfaces the FULL trajectory dict (subset of fields) so the Discord/overlay
    surface can render "1170/1700 (24% of the way), peak 1295, -3.3 ELO/game"
    without re-reading battle_stats. ``target`` and ``remaining_to_target`` are
    included so the headline can name the mission target explicitly instead of
    hardcoding "1700" in the formatter -- the mission is the source of truth
    for the number, ladder_trajectory.trajectory() already knows it.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ladder_trajectory", PROJECT_ROOT / "infrastructure" / "ladder_trajectory.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        t = mod.trajectory()
        return {k: t.get(k) for k in (
            "current_elo", "peak_elo", "recent_slope_per_game",
            "games_to_target_at_rate", "progress_fraction_1000_to_target",
            "rated_games", "target", "remaining_to_target",
            "at_or_above_target")}
    except Exception:
        return None


def classify_skip(report):
    """Classify WHY an iteration produced no top_issue, so the ledger surfaces
    starvation instead of hiding it behind a bare ``skipped_no_issue``.

    Pure function of the autoresearch report (no IO) so it is unit-testable and
    so the same logic feeds both the ledger and any Discord/overlay surface.

    Returns a dict with:
      skip_reason : one of
        "evidence_starved"    -- losses exist in the window but NONE has a local
                                 replay JSON or request-legal decision trace, so
                                 autoresearch cannot ground a fixable issue. This
                                 is THE failure mode the JIGGLY->ubunztu evidence
                                 sync exists to cure; if it persists, the sync is
                                 not landing evidence (check sync_loss_evidence).
        "no_losses_in_window" -- the mined window is clean (no losses): nothing to
                                 learn from this batch; not a fault.
        "no_pattern_matched"  -- evidence IS present (replay/trace-backed losses)
                                 but no known issue pattern fired. The analyzer's
                                 pattern library is the limiter here, not data.
        "no_report"           -- autoresearch produced no report at all (it
                                 crashed or wrote nothing); called out distinctly
                                 so operators look at the autoresearch run rather
                                 than the sync.
      losses / with_replay / with_trace : the counts the reason was derived from.
    """
    if not report:
        return {"skip_reason": "no_report", "losses": 0,
                "with_replay": 0, "with_trace": 0}
    losses = report.get("losses", 0) or 0
    ei = report.get("evidence_integrity") or {}
    with_replay = ei.get("losses_with_replay_json", 0) or 0
    with_trace = ei.get("losses_with_request_legal_options", 0) or 0
    if not losses:
        reason = "no_losses_in_window"
    elif not (with_replay or with_trace):
        reason = "evidence_starved"
    else:
        reason = "no_pattern_matched"
    return {"skip_reason": reason, "losses": losses,
            "with_replay": with_replay, "with_trace": with_trace}


def one_iteration(*, num_battles: int, dry_run: bool, smoke_battles: int | None) -> dict:
    report = mine_and_research(num_battles, dry_run)
    top_issue = (report.get("top_issue") or {}).get("title", "") if report else ""

    if dry_run:
        # Even in dry_run we want the SAME starvation diagnostics the live path
        # emits when there's no top_issue: a bare {"iteration_mode": "dry_run",
        # "issue": "", "outcome": "dry_run"} row hides whether the loop is
        # idle because (a) no losses landed, (b) losses landed without replay/
        # trace evidence (the JIGGLY sync starvation signal), (c) evidence
        # exists but no known pattern fired, or (d) the report itself was
        # empty. Operators read this file to answer "is the gate the
        # blocker, or is the mine empty?" -- dry_run rows must answer that
        # too, or the ledger lies by omission.
        entry = {"iteration_mode": "dry_run", "issue": top_issue, "outcome": "dry_run"}
        if not top_issue:
            diag = classify_skip(report)
            entry.update({
                "skip_reason": diag["skip_reason"],
                "losses_in_window": diag["losses"],
                "losses_with_replay": diag["with_replay"],
                "losses_with_request_legal": diag["with_trace"],
            })
        append_ledger(entry)
        return entry

    if not top_issue:
        diag = classify_skip(report)
        entry = {
            "issue": "", "outcome": "skipped_no_issue",
            "skip_reason": diag["skip_reason"],
            "losses_in_window": diag["losses"],
            "losses_with_replay": diag["with_replay"],
            "losses_with_request_legal": diag["with_trace"],
            "ladder": _ladder_snapshot(),
        }
        append_ledger(entry)
        return entry

    result = propose_and_gate(dry_run=False, smoke_battles=smoke_battles)
    outcome = _classify_outcome(result)
    # On a gate-skipped iteration latest_selfplay_verdict() returns the PREVIOUS
    # iteration's gate-*.json (no new file was written this run). Including it
    # would falsely attribute stale numbers to this iteration, so suppress.
    verdict = (None if result.get("gate_skipped")
               else latest_selfplay_verdict(since=result.get("gate_started_at")))
    entry = {
        "issue": top_issue,
        "outcome": outcome,
        "head_before": result["head_before"][:12],
        "head_after": result["head_after"][:12],
        "verdict_line": result["verdict_line"],
        "selfplay_verdict": {
            k: verdict.get(k) for k in (
                "label", "new_wins", "old_wins", "decisive_battles",
                "new_win_rate", "new_wilson_lcb", "ACCEPT")
        } if verdict else None,
        "decision_source": "selfplay_lcb_gt_0.50",
        "agent_returncode": result.get("agent_returncode"),
        "ladder": _ladder_snapshot(),
        "smoke_battles": smoke_battles,
    }
    # When the gate could NOT run, surface the reason on the ledger entry so
    # loop_status (and any operator reading the JSONL directly) can tell a
    # gate-blocked iteration from an autoresearch-skipped one. The same reason
    # already lives inside verdict_line, but threading it out as a top-level
    # field keeps the headline cheap to compute and keeps the ledger schema
    # parallel to the existing autoresearch ``skip_reason`` field.
    if outcome in {"gate_skipped", "ship_on_skip_unmeasured"}:
        entry["gate_skip_reason"] = result.get("gate_skip_reason")
    if outcome == "agent_failed":
        entry["agent_error_tail"] = result.get("agent_output_tail")
    append_ledger(entry)
    return entry


def _classify_outcome(result: dict) -> str:
    """Map a propose_and_gate() result to a ledger outcome category.

    Distinct outcomes so the ledger can answer "is the loop blocked on a
    runtime prereq, or is the gate genuinely rejecting patches?":

      gate_skipped             gate could not run (fail-closed) AND no change
                               promoted -- safe; loop waiting on showdown.
      ship_on_skip_unmeasured  gate could not run BUT change was promoted
                               (IMPROVE_AGENT_GATE_FAIL_CLOSED=0). Dangerous:
                               the loop just merged a patch without proof.
      accepted_merged          gate ran, accepted, commit landed -- the happy path.
      accepted_but_commit_failed  gate accepted but the commit step did not
                                  move HEAD (e.g. nothing actually changed).
      reverted                 gate ran and REJECTED the candidate.
    """
    if result.get("gate_skipped"):
        return "ship_on_skip_unmeasured" if result["committed"] else "gate_skipped"
    if (
        result.get("agent_returncode") not in (0, None)
        and not result.get("verdict_line")
        and not result.get("committed")
    ):
        return "agent_failed"
    if result["accepted"]:
        return "accepted_merged" if result["committed"] else "accepted_but_commit_failed"
    return "reverted"


def loop_status() -> dict:
    """Honest one-glance state of the learn-loop for the Discord/overlay surface.

    The stream MUST NOT imply autonomous climbing before a measured gate has
    ever closed. This reads the DURABLE ledger + the live ladder and returns a
    truthful summary so the overlay can say e.g.
        "learn-loop fed, awaiting first measured gate; ladder 1170 declining"
    instead of fiction. Pure read; never runs the gate.

    Keys:
      ledger_entries        total recorded iterations
      real_iterations       count of non-dry-run iterations on the ledger
      dry_run_iterations    count of dry-run iterations on the ledger (separate
                            from real_iterations so the headline can be honest
                            about "we ran but only in dry-run mode")
      last_outcome          outcome of the most recent NON-dry_run iteration
      last_skip_reason      its skip_reason if it was skipped_no_issue
      last_gate_skip_reason its gate_skip_reason if outcome was gate_skipped /
                            ship_on_skip_unmeasured (the verdict detail's
                            ``"skipped"`` field — e.g. ``"gate_failed_to_run:
                            both arms died at ws handshake"``). Surfaces WHY
                            the gate could not run so operators chase the
                            showdown/harness/arm-spawn instead of the
                            autoresearch/sync side.
      measured_gate_ever    True iff a real accept/revert (gate ran) is on record
      last_iteration_at     ISO8601 UTC timestamp of the most recent ledger
                            entry (of ANY type), or None if the ledger is empty
                            or the entry has no timestamp. Distinguishes "ran
                            recently and is idle" from "stopped running".
      minutes_since_last_iteration
                            int minutes between last_iteration_at and now,
                            None if last_iteration_at is None.
      stale                 True iff minutes_since_last_iteration >=
                            STALE_THRESHOLD_MIN. The headline prefixes
                            "STALE(>Nm) " when this is set so the Discord
                            overlay can't read "idle" as "alive but quiet".
      ladder                FULL snapshot from ladder_trajectory.trajectory()
                            (subset): current_elo, peak_elo,
                            recent_slope_per_game, games_to_target_at_rate,
                            progress_fraction_1000_to_target, rated_games,
                            target, remaining_to_target, at_or_above_target.
                            Surfaces the OUTER mission signal (climb to 1700
                            ELO) so the overlay can render progress without
                            re-reading battle_stats. Re-shaping to a
                            {current_elo, slope} subset dropped the very
                            fields the overlay needs to answer "how far from
                            1700?"; preserve them all here.
      headline              the human one-liner for the overlay/Discord. The
                            ladder phrase enriches trend with progress-to-
                            target ("23% to 1700"), peak when the current ELO
                            is below it ("peak 1295"), and signed slope
                            magnitude ("-3.3/game") so operators read the
                            regression at a glance instead of just "declining".
    """
    entries = []
    if LEDGER_PATH.exists():
        for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    real = [e for e in entries
            if e.get("outcome") not in {"dry_run", None}
            and e.get("iteration_mode") != "dry_run"]
    dry_runs = [e for e in entries
                if e.get("outcome") == "dry_run"
                or e.get("iteration_mode") == "dry_run"]
    measured = [e for e in real
                if e.get("outcome") in {"accepted_merged", "reverted",
                                        "accepted_but_commit_failed"}]
    last = real[-1] if real else None
    ladder = _ladder_snapshot() or {}
    cur = ladder.get("current_elo")
    slope = ladder.get("recent_slope_per_game")
    peak = ladder.get("peak_elo")
    target = ladder.get("target")
    progress = ladder.get("progress_fraction_1000_to_target")
    if cur is None:
        ladder_phrase = "ladder unknown"
    else:
        trend = ("climbing" if (slope or 0) > 0.2
                 else "declining" if (slope or 0) < -0.2 else "flat")
        # Surface the full OUTER truth signal: slope MAGNITUDE (so operators see
        # "-3.3 ELO/game" not just "declining"), peak ELO (so "current 1170 peak
        # 1295" makes the regression visible), and progress toward the mission
        # target (so "23% of the way to 1700" is read at a glance). The mission
        # is "climb to and sustain 1700 ELO" -- the headline is the one-glance
        # surface for that; "ladder 1170 declining" loses every comparison.
        parts = []
        if target and cur >= target:
            parts.append(f"AT/ABOVE {target:.0f}")
        elif target is not None and progress is not None:
            parts.append(f"{progress * 100:.0f}% to {target:.0f}")
        if peak is not None and peak > cur:
            parts.append(f"peak {peak:.0f}")
        if slope is not None and abs(slope) >= 0.2:
            parts.append(f"{slope:+.1f}/game")
        suffix = f" ({', '.join(parts)})" if parts else ""
        ladder_phrase = f"ladder {cur:.0f} {trend}{suffix}"

    # When ONLY dry-runs are on the ledger (the keystone "loop ran but never
    # gated" state), the latest dry_run may carry a skip_reason from
    # classify_skip() -- that's the truthful WHY operators need to distinguish
    # "JIGGLY evidence-sync broke" (evidence_starved) from "mine is just empty"
    # (no_losses_in_window) from "pattern library missed" (no_pattern_matched).
    # Without surfacing it here, the overlay reports "N dry-runs" with no cue,
    # and the loud STARVED keyword only fires after a non-dry-run iteration --
    # which never happens while we're stuck in dry_run mode.
    last_dry_run = dry_runs[-1] if dry_runs else None
    last_dry_run_skip_reason = (last_dry_run or {}).get("skip_reason")

    if not real:
        if dry_runs:
            if last_dry_run_skip_reason == "evidence_starved":
                headline = (f"learn-loop STARVED ({len(dry_runs)} dry-runs, "
                            f"losses present, no local replay/trace); "
                            f"{ladder_phrase}")
            elif last_dry_run_skip_reason in {"no_losses_in_window",
                                              "no_pattern_matched", "no_report"}:
                headline = (f"learn-loop idle ({len(dry_runs)} dry-runs, "
                            f"{last_dry_run_skip_reason}; no measured gate yet); "
                            f"{ladder_phrase}")
            else:
                headline = (f"learn-loop idle ({len(dry_runs)} dry-runs, no "
                            f"measured gate yet); {ladder_phrase}")
        else:
            headline = f"learn-loop idle (no iterations yet); {ladder_phrase}"
    elif not measured:
        last_outcome = (last or {}).get("outcome")
        gate_skip_reason_val = (last or {}).get("gate_skip_reason")
        if (last_outcome in {"gate_skipped", "ship_on_skip_unmeasured"}
                and gate_skip_reason_val):
            # The GATE could not run (showdown handshake died, harness missing,
            # gate_failed_to_run from selfplay_eval, etc.). Distinct from the
            # autoresearch skip-reasons below: operators chase the showdown/
            # harness/arm-spawn for a gate-failure, the JIGGLY evidence-sync
            # or pattern library for an autoresearch starvation. Conflating
            # them sends operators to the wrong blocker.
            headline = (f"learn-loop gate-blocked ({gate_skip_reason_val}); "
                        f"{ladder_phrase}")
        elif last_outcome == "agent_failed":
            rc = (last or {}).get("agent_returncode")
            headline = (f"learn-loop agent-failed"
                        f"{f' (rc={rc})' if rc is not None else ''}; "
                        f"{ladder_phrase}")
        else:
            reason = (last or {}).get("skip_reason")
            if reason == "evidence_starved":
                headline = (f"learn-loop STARVED (losses present, no local "
                            f"replay/trace); {ladder_phrase}")
            elif reason in {"no_losses_in_window", "no_pattern_matched", "no_report"}:
                headline = (f"learn-loop fed, awaiting first measured gate "
                            f"({reason}); {ladder_phrase}")
            else:
                headline = (f"learn-loop fed, awaiting first measured gate; "
                            f"{ladder_phrase}")
    else:
        n_acc = sum(1 for e in measured if e.get("outcome") == "accepted_merged")
        n_rev = sum(1 for e in measured if e.get("outcome") == "reverted")
        headline = (f"learn-loop measured: {n_acc} accepted / {n_rev} reverted; "
                    f"{ladder_phrase}")

    # Freshness: when ONLY dry-runs exist (or only skipped iterations), the
    # "idle" headline reads identically whether the loop iterated 5 minutes
    # ago or has been silent for 24 hours. Surface the wall-clock age of the
    # most recent entry and prefix STALE on the headline when it exceeds
    # STALE_THRESHOLD_MIN so operators can tell "alive and idle" from
    # "stopped iterating".
    latest_entry = entries[-1] if entries else None
    last_iteration_at = (latest_entry or {}).get("timestamp")
    minutes_since_last_iteration = None
    if last_iteration_at:
        try:
            ts = datetime.fromisoformat(last_iteration_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = _utcnow() - ts
            minutes_since_last_iteration = max(0, int(delta.total_seconds() // 60))
        except Exception:
            minutes_since_last_iteration = None
    stale = (minutes_since_last_iteration is not None
             and minutes_since_last_iteration >= STALE_THRESHOLD_MIN)
    if stale:
        headline = f"STALE(>{minutes_since_last_iteration}m) {headline}"

    # Upstream evidence-stream freshness: orthogonal to iteration freshness.
    # The loop can iterate hourly (stale=False) and still starve when the
    # JIGGLY bot stops producing battles (battle_stats.json has no new rows
    # for hours/days). Without surfacing this, the headline reads "learn-loop
    # idle, no_losses_in_window" -- a loop-side framing of an upstream
    # outage -- and operators chase the wrong blocker. Routes the fix:
    # STALE => check the cron; STREAM-STALE => check the bot/sync.
    battle_stream_age_minutes = _battle_stream_age_minutes()
    battle_stream_stale = (battle_stream_age_minutes is not None
                           and battle_stream_age_minutes
                           >= BATTLE_STREAM_STALE_THRESHOLD_MIN)
    if battle_stream_stale:
        headline = (f"STREAM-STALE(>{battle_stream_age_minutes}m) "
                    f"{headline}")

    return {
        "ledger_entries": len(entries),
        "real_iterations": len(real),
        "dry_run_iterations": len(dry_runs),
        "measured_gate_ever": bool(measured),
        "last_outcome": (last or {}).get("outcome"),
        "last_skip_reason": (last or {}).get("skip_reason"),
        "last_gate_skip_reason": (last or {}).get("gate_skip_reason"),
        "last_agent_returncode": (last or {}).get("agent_returncode"),
        "last_dry_run_skip_reason": last_dry_run_skip_reason,
        "last_iteration_at": last_iteration_at,
        "minutes_since_last_iteration": minutes_since_last_iteration,
        "stale": stale,
        "battle_stream_age_minutes": battle_stream_age_minutes,
        "battle_stream_stale": battle_stream_stale,
        # Preserve the FULL ladder snapshot (peak, target, games_to_target,
        # progress_fraction, rated_games, at_or_above_target) so the Discord/
        # overlay surface can render the OUTER mission signal without
        # re-reading battle_stats. Re-shaping to {current_elo, slope} dropped
        # the very fields the overlay needs to answer "how far from 1700?".
        "ladder": dict(ladder),
        "headline": headline,
    }


def offline_no_live_readiness(status: dict | None = None, *, cli_enabled: bool = False) -> dict:
    status = status or loop_status()
    enabled = auto_improve_enabled(cli_enabled)
    blockers = []
    if not enabled:
        blockers.append(
            f"auto-improvement disabled; set {AUTO_IMPROVE_SENTINEL}=1 or pass --enable-auto-improve"
        )
    if status.get("battle_stream_stale"):
        age = status.get("battle_stream_age_minutes")
        blockers.append(f"battle evidence stream stale ({age}m since newest battle)")

    return {
        "schemaVersion": "fouler-improve-loop-no-live-readiness/v1",
        "readyForOfflineIteration": enabled and not blockers,
        "readyForRecursiveAutoImprove": enabled and not blockers and bool(status.get("measured_gate_ever")),
        "autoImproveEnabled": enabled,
        "sentinel": AUTO_IMPROVE_SENTINEL,
        "exclusions": list(OFFLINE_NO_LIVE_EXCLUSIONS),
        "blockers": blockers,
        "measuredGateEver": bool(status.get("measured_gate_ever")),
        "battleStreamStale": bool(status.get("battle_stream_stale")),
        "battleStreamAgeMinutes": status.get("battle_stream_age_minutes"),
        "headline": status.get("headline"),
        "maxIterationsWithoutRecursiveReadiness": MAX_ITERATIONS_WITHOUT_RECURSIVE_READY,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="fouler closed self-improvement loop")
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--num-battles", type=int, default=30,
                    help="How many recent battles autoresearch mines per iteration.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Mine + show the top issue only; do not apply or gate.")
    ap.add_argument("--smoke-battles", type=int, default=None,
                    help="Override self-play gate battle count (small => fast smoke).")
    ap.add_argument("--enable-auto-improve", action="store_true",
                    help=f"Allow mutating loop iterations. Alternative: {AUTO_IMPROVE_SENTINEL}=1.")
    ap.add_argument("--allow-master", action="store_true",
                    help="Permit running on master/main (default refuses).")
    ap.add_argument("--status", action="store_true",
                    help="Print the honest learn-loop status (for Discord/overlay) and exit.")
    ap.add_argument("--readiness", action="store_true",
                    help="Print no-live auto-improvement readiness and exit.")
    args = ap.parse_args()

    if args.status:
        st = loop_status()
        print(json.dumps(st, indent=2))
        print(f"[LOOP] {st['headline']}")
        return 0

    if args.readiness:
        st = loop_status()
        readiness = offline_no_live_readiness(st, cli_enabled=args.enable_auto_improve)
        print(json.dumps(readiness, indent=2))
        print(f"[LOOP] readiness readyForRecursiveAutoImprove={readiness['readyForRecursiveAutoImprove']}")
        return 0

    if args.iterations < 1:
        print(f"[LOOP] BLOCKED: --iterations must be >= 1 (got {args.iterations}).")
        return 2

    if (
        not args.dry_run
        and args.iterations > MAX_ITERATIONS_WITHOUT_RECURSIVE_READY
    ):
        readiness = offline_no_live_readiness(cli_enabled=args.enable_auto_improve)
        if not readiness.get("readyForRecursiveAutoImprove"):
            print(
                f"[LOOP] BLOCKED: recursive auto-improvement requires "
                f"readyForRecursiveAutoImprove=true before running "
                f"{args.iterations} mutating iterations. "
                f"Readiness: {json.dumps(readiness, sort_keys=True)}"
            )
            return 2

    if not args.dry_run and not auto_improve_enabled(args.enable_auto_improve):
        print(
            f"[LOOP] BLOCKED: auto-improvement is disabled. Set {AUTO_IMPROVE_SENTINEL}=1 "
            f"or pass --enable-auto-improve to allow mutating iterations."
        )
        return 2

    br = current_branch()
    if br in {"master", "main"} and not args.allow_master and not args.dry_run:
        print(f"[LOOP] REFUSING to run on '{br}'. Create a working branch first "
              f"(or pass --allow-master). The loop never pushes; it commits to the "
              f"current branch only.")
        return 2

    lease = None
    try:
        lease = acquire_runtime_lease(holder="improve_loop")
    except RuntimeLeaseBusy as exc:
        print(f"[LOOP] BLOCKED: {exc}")
        append_ledger({"outcome": "blocked_runtime_lease", "error": str(exc), "dry_run": args.dry_run})
        return 3

    print(f"[LOOP] branch={br} iterations={args.iterations} "
          f"smoke_battles={args.smoke_battles} dry_run={args.dry_run}")
    try:
        for i in range(args.iterations):
            print(f"\n[LOOP] ===== iteration {i+1}/{args.iterations} =====")
            try:
                one_iteration(num_battles=args.num_battles, dry_run=args.dry_run,
                              smoke_battles=args.smoke_battles)
            except subprocess.TimeoutExpired as e:
                append_ledger({"outcome": "timeout", "error": str(e)})
            except Exception as e:
                append_ledger({"outcome": "error", "error": repr(e)})
                print(f"[LOOP] iteration error: {e!r}")
    finally:
        if lease is not None:
            lease.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
