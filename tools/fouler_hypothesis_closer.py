#!/usr/bin/env python3
"""hermes-fouler-hypothesis-closer — drive the hypothesis lifecycle.

AUTHORITATIVE SOURCE: fouler-play repo, tools/fouler_hypothesis_closer.py
Installed to ~/.hermes/current/hermes/bin/hermes-fouler-hypothesis-closer, which
is what the systemd user timer executes. Edit the repo copy and reinstall; do not
hand-edit the installed copy.

The hypothesis ledger (replay_analysis/hypothesis_ledger.py inside the fouler-play
release) opens records when autoresearch surfaces an issue. This script progresses
each open hypothesis through its lifecycle and RECORDS the transition. It does not
mutate fouler-play state (no auto-revert, no auto-commit).

    open -> implemented   a commit AFTER the hypothesis opened mentions the
                          failure class
    implemented -> kept / reverted / measured-indeterminate
                          once enough battles have accumulated to say anything

=============================================================================
2026-07-20 CORRECTNESS REWRITE — what was wrong and why it mattered
=============================================================================

1. NO --since FILTER.  _git_commit_for() accepted a `since` argument and
   deliberately ignored it, with a comment arguing the lifecycle is
   "event-driven, not time-windowed". The consequence: every hypothesis
   instantly resolved to commit c4621284 (2026-05-20) — "ground autoresearch
   in competitive-Pokemon strategic catalog" — which is the commit that ADDED
   the detectors. Hypotheses opened 2026-07-18/19/20 were all marked
   `implemented` by a commit two months older than the hypothesis. A hypothesis
   can only be implemented by work that happened AFTER it was opened.

2. BATTLE STORE DID NOT EXIST AT THE PATH READ.  BATTLE_STATS pointed at
   $FOULER_REPO/battle_stats.json. That file is not present on DEKU at all; the
   live store is synced by devstream-reporter to ~/devstream-reporter/cache/
   battle_stats.json. _battles_since() therefore always returned [], the
   `>= 30 battles` window was never satisfied, and `measured`/`kept`/`reverted`
   were unreachable BY CONSTRUCTION. (VENTURE_OPERATING_RULES rule 8.)

3. GIT LOG SEARCHED THE WRONG LINEAGE.  `git log` ran against the fouler-play
   working tree, which sits on whatever branch happens to be checked out —
   currently fix/loopback-surface-declaration-20260720, which does NOT contain
   the deployed release 94b98153. Commits on the deployed lineage were invisible.
   Now searches --all.

4. THE 30-BATTLE ELO VERDICT WAS NOISE.  With K~=18-20 and a ~50% win rate the
   standard deviation of ELO drift over 30 battles is ~55 points, so the old
   `deltaELO < -50 => reverted` rule fired on pure noise roughly a third of the
   time, and `deltaELO >= 0 => kept` blessed changes it could not measure. Worse,
   _elo_delta() compared the mean of the FIRST 5 to the LAST 5 rated battles in
   the window, an estimator with even higher variance than the endpoints.

   This version refuses to pretend. It reports `measured-indeterminate` with the
   sample size that WOULD be required, and only calls kept/reverted when the
   evidence actually supports it.

=============================================================================
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOCAL_LIB = Path(__file__).resolve().parent / "_lib"
PYLIB_DIR = os.environ.get("HERMES_PYLIB_DIR")
if PYLIB_DIR:
    sys.path.insert(0, PYLIB_DIR)
elif LOCAL_LIB.exists():
    sys.path.insert(0, str(LOCAL_LIB))
else:
    sys.path.insert(0, os.path.expanduser("~/lib/python3"))
import hermes_lib as hl

FOULER_REPO = Path(os.environ.get("FOULER_REPO", "/home/ryan/projects/fouler-play"))

# The live battle store. devstream-reporter's sync-fouler-from-jiggly.sh pulls
# C:/ProgramData/HERMES/state/fouler/battle_stats.json from JIGGLYPUFF into this
# cache every 5 minutes, deliberately OUTSIDE any git tree so no git operation can
# rewrite its mtime. Candidates are tried in order; the first that exists wins.
BATTLE_STATS_CANDIDATES = [
    Path(p) for p in [
        os.environ.get("FOULER_BATTLE_STATS", ""),
        os.path.expanduser("~/devstream-reporter/cache/battle_stats.json"),
        str(FOULER_REPO / "battle_stats.json"),
    ] if p
]

LEDGER_DIR = Path(os.environ.get(
    "FOULER_HYPOTHESIS_LEDGER",
    str(hl.PATHS.operator / "fouler-hypotheses")))
LATEST_OUT = hl.PATHS.operator / "fouler-hypothesis-closer" / "latest.json"

MEASURE_WINDOW = int(os.environ.get("FOULER_MEASURE_WINDOW", "30"))
REVERT_THRESHOLD = int(os.environ.get("FOULER_REVERT_THRESHOLD", "-50"))

# Typical Showdown K-factor in the 1200-1400 band. Used only to state how much
# ELO drift is attributable to noise; not used to grade anything.
ELO_K = 18.0


def resolve_battle_stats() -> Path | None:
    for candidate in BATTLE_STATS_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


BATTLE_STATS = resolve_battle_stats()


# ---------------------------------------------------------------------------
# Honest measurement helpers
# ---------------------------------------------------------------------------

def elo_noise_sd(n_battles: int) -> float:
    """SD of cumulative ELO drift over n battles for a player at equilibrium.

    Each rated game moves the rating by about +/-K/2 around the expectation, so
    the drift is a random walk with per-step SD ~= K/2 and cumulative SD
    ~= (K/2)*sqrt(n). At n=30, K=18 this is ~49 ELO — which is why the inherited
    `deltaELO < -50 => revert` rule was indistinguishable from a coin flip.
    """
    return (ELO_K / 2.0) * math.sqrt(max(n_battles, 1))


def battles_required_for_winrate_delta(delta: float, power: float = 0.80) -> int:
    """Sample size to detect a win-rate shift of `delta` from 50%.

    Two-proportion z-test at alpha=0.05 two-sided. z_a=1.96, z_b=0.84 at 80%
    power. n = 2 * (z_a + z_b)^2 * p(1-p) / delta^2.
    """
    if delta <= 0:
        return 0
    z_alpha, z_beta = 1.959964, 0.841621 if power == 0.80 else 1.281552
    return int(math.ceil(2.0 * (z_alpha + z_beta) ** 2 * 0.25 / (delta ** 2)))


def poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam). Exact, small k."""
    if lam <= 0:
        return 1.0
    total = 0.0
    term = math.exp(-lam)
    for i in range(0, k + 1):
        if i:
            term *= lam / i
        total += term
    return min(total, 1.0)


def mechanical_verdict(baseline_rate: float, events_after: int,
                       battles_after: int) -> dict:
    """Poisson test on a mechanical event-rate metric.

    This is the gate that can actually discriminate at n=30. A mechanical counter
    (e.g. "failed consecutive-Protect turns") has a far higher signal-to-noise
    ratio than ladder ELO because the event is directly caused by the code path
    under test rather than mediated by the opponent, the matchup and the ladder.

    Worked example, the consecutive-Protect fix (2026-07-20):
        baseline_rate = 0.201 events/battle (38 events over 189 replays)
        expected over 30 battles = 6.03
        observing 0 events => p = e^-6.03 = 0.0024  -> decisive
    """
    expected = baseline_rate * battles_after
    p_value = poisson_cdf(events_after, expected)
    return {
        "baselineRatePerBattle": round(baseline_rate, 4),
        "battlesAfter": battles_after,
        "expectedEvents": round(expected, 2),
        "observedEvents": events_after,
        "pValue": round(p_value, 6),
        "significantReduction": bool(p_value < 0.05 and events_after < expected),
    }


# ---------------------------------------------------------------------------


def _git_commit_for(failure_class: str, since: str | None) -> dict | None:
    """Find a commit mentioning the failure class, committed AFTER `since`.

    Accepts underscore and hyphen forms (fouler-play messages use kebab-case
    `hazard-pressure`; autoresearch keys are snake_case `hazard_pressure`).

    Searches --all: the working tree's checked-out branch is not necessarily the
    lineage that is deployed, and a hypothesis can legitimately be implemented on
    a release branch that HEAD does not contain.
    """
    flexible = failure_class.replace("_", "[-_]")
    args = ["git", "-C", str(FOULER_REPO), "log", "--all",
            "--grep", f"fouler-auto.*{flexible}|{flexible}",
            "--extended-regexp",
            "--pretty=format:%H|%aI|%s", "--max-count=1"]
    if since:
        # A hypothesis cannot be implemented by work that predates it.
        args.insert(5, f"--since={since}")
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    sha, iso, subject = cp.stdout.strip().split("|", 2)
    return {"sha": sha, "committedAt": iso, "subject": subject,
            "matchedSince": since}


def _to_utc(value: str):
    """Parse an ISO timestamp to an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _battles_since(iso_timestamp: str) -> list[dict]:
    """Return battle_stats entries recorded at or after iso_timestamp.

    Compares real datetimes, not strings. git's %aI renders the author's local
    offset ("2026-07-20T18:22:03-04:00") while battle_stats timestamps are UTC
    ("2026-07-20T21:35:50+00:00"). Lexical comparison of those two put 57 battles
    inside a window that had barely opened, because "21" > "18" as text.
    """
    if BATTLE_STATS is None:
        return []
    data = hl.read_json(BATTLE_STATS)
    if not data:
        return []
    cutoff = _to_utc(iso_timestamp)
    if cutoff is None:
        return []
    out = []
    for b in data.get("battles", []):
        ts = _to_utc(b.get("timestamp", ""))
        if ts is not None and ts >= cutoff:
            out.append(b)
    return out


def _elo_endpoints(window: list[dict]) -> tuple[float | None, float | None, float | None]:
    """First vs last rated ELO in the window, plus the delta.

    Endpoints, not 5-battle means: the mean of the first 5 and last 5 is a
    higher-variance estimator of a random walk's displacement than simply reading
    the endpoints, and the old code used it to make revert decisions.
    """
    rated = [b for b in window if b.get("rating") is not None]
    if len(rated) < 2:
        return None, None, None
    before = float(rated[0]["rating"])
    after = float(rated[-1]["rating"])
    return before, after, after - before


def _win_rate(window: list[dict]) -> float | None:
    decided = [b for b in window if b.get("result") in ("win", "loss")]
    if not decided:
        return None
    return sum(1 for b in decided if b["result"] == "win") / len(decided)


def _close_with(record: dict, *, status: str, measurement: dict | None = None,
                note: str = "") -> dict:
    record["status"] = status
    record["closedAt"] = hl.now_iso()
    if measurement:
        record["measurement"] = measurement
    if note:
        record["closeNote"] = note
    return record


def process(record_path: Path, dry_run: bool = False) -> dict:
    record = hl.read_json(record_path)
    if not record:
        return {"path": str(record_path), "skip": "parse-error"}
    original = json.loads(json.dumps(record, sort_keys=True))

    status = record.get("status", "open")
    failure_class = record.get("failureClass")
    if not failure_class:
        return {"path": str(record_path), "skip": "no failureClass"}

    if status in ("kept", "reverted", "skipped"):
        return {"id": record.get("id"), "noop": f"already {status}",
                "status": status}

    opened_at = record.get("openedAt")
    transition = None

    # MIGRATION (v1 -> v2): the old closer had no --since filter, so it stamped
    # records with commits that PREDATE the hypothesis. c4621284 (2026-05-20,
    # "ground autoresearch in competitive-Pokemon strategic catalog") was matched
    # as the implementation of hypotheses opened 2026-07-18/19/20 — it is in fact
    # the commit that added the detectors that raised them. Any stored
    # implementation older than the hypothesis is fiction; drop it and reopen.
    impl = record.get("implementation") or {}
    impl_at = impl.get("committedAt")
    _impl_dt, _open_dt = _to_utc(impl_at), _to_utc(opened_at)
    if _impl_dt and _open_dt and _impl_dt < _open_dt:
        record["invalidatedImplementation"] = {
            **impl,
            "invalidatedAtUtc": hl.now_iso(),
            "reason": ("commit predates hypothesis openedAt; matched only because "
                       "the v1 closer ignored its own --since argument"),
        }
        record.pop("implementation", None)
        record.pop("measurement", None)
        record["status"] = "open"
        status = "open"
        transition = "implemented -> open (bogus implementation invalidated)"

    # open -> implemented?
    if status == "open":
        commit = _git_commit_for(failure_class, since=opened_at)
        if commit:
            record["status"] = "implemented"
            record["implementation"] = commit
            transition = "open -> implemented"

    # implemented -> kept / reverted / measured-indeterminate?
    if record.get("status") in ("implemented", "measured-indeterminate"):
        commit = record.get("implementation") or {}
        committed_at = commit.get("committedAt")
        if committed_at and BATTLE_STATS is not None:
            window = _battles_since(committed_at)
            n = len(window)
            before, after, delta = _elo_endpoints(window)

            noise_sd = elo_noise_sd(n)
            measurement = {
                "battlesAfterDeploy": n,
                "battleStatsPath": str(BATTLE_STATS),
                "eloBefore": before,
                "eloAfter": after,
                "deltaELO": round(delta, 2) if delta is not None else None,
                "winRateAfter": _win_rate(window),
                "eloNoiseSd": round(noise_sd, 1),
                "computedAtUtc": hl.now_iso(),
            }

            # A mechanical metric, when the hypothesis carries one, is the gate
            # that can actually decide at this sample size.
            mech = record.get("mechanicalMetric") or {}
            baseline_rate = mech.get("baselineRatePerBattle")
            observed = mech.get("observedEventsAfter")
            if baseline_rate is not None and observed is not None and n > 0:
                measurement["mechanical"] = mechanical_verdict(
                    float(baseline_rate), int(observed), n)

            if n < MEASURE_WINDOW:
                record["measurement"] = measurement
                record["status"] = "implemented"
            else:
                mechv = measurement.get("mechanical")
                if mechv and mechv["significantReduction"]:
                    record = _close_with(
                        record, status="kept", measurement=measurement,
                        note=(f"mechanical metric decisive: {mechv['observedEvents']} "
                              f"observed vs {mechv['expectedEvents']} expected "
                              f"(p={mechv['pValue']}), n={n}"))
                    transition = "implemented -> kept"
                elif delta is not None and delta < -2 * noise_sd:
                    # Only revert on a move too large to be drift.
                    record = _close_with(
                        record, status="reverted", measurement=measurement,
                        note=(f"deltaELO {delta:.1f} exceeds 2x noise SD "
                              f"({2*noise_sd:.0f}) over n={n}"))
                    transition = "implemented -> reverted"
                else:
                    # The honest outcome: we cannot tell.
                    required = battles_required_for_winrate_delta(0.05)
                    measurement["verdict"] = "indeterminate"
                    measurement["reason"] = (
                        f"deltaELO {delta if delta is None else round(delta,1)} is "
                        f"within the +/-{noise_sd:.0f} ELO drift expected from noise "
                        f"at n={n}. Ladder ELO cannot resolve this change at this "
                        f"sample size.")
                    measurement["battlesRequiredForWinRateDelta5pct"] = required
                    measurement["battlesRequiredForWinRateDelta10pct"] = (
                        battles_required_for_winrate_delta(0.10))
                    if record.get("status") != "measured-indeterminate":
                        transition = "implemented -> measured-indeterminate"
                    record["status"] = "measured-indeterminate"
                    record["measurement"] = measurement

    # Persist whenever anything changed, not only on a transition. The v1 closer
    # wrote only on transition, so a recomputed measurement (e.g. after the
    # timezone fix below shrank a bogus 57-battle window to its true 0) was
    # silently discarded and the stale numbers stayed on disk.
    changed = json.loads(json.dumps(record, sort_keys=True)) != original
    if changed and not dry_run:
        hl.atomic_write(record_path, record)
    if transition and not dry_run:
        body = f"Hypothesis `{record.get('failureClass')}` transitioned: {transition}."
        m = record.get("measurement") or {}
        if m.get("eloBefore") is not None and m.get("eloAfter") is not None:
            body += (f" ELO {m['eloBefore']:.0f} -> {m['eloAfter']:.0f} "
                     f"({m.get('deltaELO', 0):+.1f}, n={m.get('battlesAfterDeploy')}).")
        if m.get("verdict") == "indeterminate":
            body += (f" VERDICT: indeterminate — "
                     f"{m.get('battlesRequiredForWinRateDelta5pct')} battles would be "
                     f"needed to resolve a 5-point win-rate change.")
        mech = m.get("mechanical")
        if mech:
            body += (f" Mechanical: {mech['observedEvents']} observed vs "
                     f"{mech['expectedEvents']} expected (p={mech['pValue']}).")
        hl.drop_receipt(
            receipt_id=f"hypothesis-{record['id']}-{record['status']}",
            category="fouler-play",
            body=body,
            tag_ryan=(record["status"] == "reverted"),
            title="hypothesis lifecycle",
        )

    return {"id": record.get("id"), "transition": transition or "no-op",
            "status": record.get("status")}


def main() -> int:
    parser = hl.standard_argparser(
        "advance fouler-play hypothesis lifecycle (open -> kept/reverted)",
        default_out=LATEST_OUT)
    args = parser.parse_args()

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for record_path in sorted(LEDGER_DIR.glob("fouler-hypo-*.json")):
        try:
            results.append(process(record_path, dry_run=args.dry_run))
        except Exception as exc:
            results.append({"path": str(record_path), "exception": str(exc)})

    summary = {
        "schemaVersion": "deku-fouler-hypothesis-closer/v2",
        "checkedAtUtc": hl.now_iso(),
        "battleStatsPath": str(BATTLE_STATS) if BATTLE_STATS else None,
        "battleStatsResolved": BATTLE_STATS is not None,
        "ledgerCount": len(results),
        "transitions": [r for r in results if r.get("transition") not in
                        (None, "no-op")],
        "results": results,
    }
    hl.atomic_write(args.out, summary)
    if args.emit_json or args.print_payload:
        json.dump(summary, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    hl.run_main(main)
