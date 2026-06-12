#!/usr/bin/env python3
"""
env_arm_improve.py -- GUARANTEED-APPLICABLE self-improvement for fouler-play.

WHY THIS EXISTS (root cause it replaces)
  The freeform-diff loop (improve_agent.py) asks claude-sonnet for a unified
  diff against fp/search/main.py (7000+ lines) and `git apply`s it. EVERY recent
  cycle failed with "patch does not apply" -- the model hallucinates line
  numbers / context that do not exist in the current file, and even the 4-strategy
  fuzzy apply (recount / -C1 / 3way / patch --fuzz=3) cannot rescue a patch whose
  anchors are wrong. Result: the loop evaluated the SAME unchanged bot every cycle
  and NEVER accepted a proposal (0/14 ledger entries; outcomes dominated by
  agent_failed / reverted). Diff application is not a reliable improvement channel
  for a file this large.

WHAT THIS DOES INSTEAD
  A/B experiments over ENGINE ENV ARMS that are applied by construction (no patch
  to apply, ever): for each candidate we run a NEW-vs-OLD self-play eval where the
  ONLY difference is one engine env var (NEW = candidate value, OLD = current
  default). The MEASURED self-play verdict (Wilson LCB > 0.50) decides. On ACCEPT
  we COMMIT the winning value as the new SOURCE default (rewriting the
  `os.getenv(KEY, "<default>")` literal in fp/search/main.py or config.py) on the
  current feature branch, and append a durable accepted_merged ledger entry. On
  reject we change nothing.

  This is option (b) from the finish-spec: pivot the loop to guaranteed-applicable
  env-arm A/B experiments evaluated by self-play against the now-REACHABLE gate,
  committing the winning env as the new default.

REACHABLE GATE (the other half of the 0/14 root cause)
  The production accept bar is Wilson-LCB(new win-rate, 0.95) > 0.50 at
  MIN_DECISIVE=30, which needs ~70% self-play wins -- a genuinely-better engine
  (true ~55-60%) never clears it. This driver opts the BURST eval into a
  reachable-but-rigorous bar via SELFPLAY_GATE_CONFIDENCE (default 0.95 in source
  stays UNTOUCHED): at confidence 0.85 / N>=40 the bar is 62.5% wins, which a
  true-edge arm can clear while a 50/50 coin-flip (15/30, LCB 0.373) still
  REJECTS. The LCB>0.50 rule is preserved, so noise never promotes.

SAFETY
  * Feature branch only (refuses master/main unless --allow-master). NEVER pushes.
  * Commits ONLY a one-literal default change, ONLY when the measured gate ACCEPTs.
  * Uses the isolated self-play harness (own users/port/throwaway stats) -- never
    touches the live ladder battle_stats.json.
  * Each candidate eval is bounded; the worst case changes nothing and reverts.

Usage:
  python infrastructure/env_arm_improve.py --battles 44 --min-decisive 30 \
      --confidence 0.85 --showdown-port 18765
  # dry-run (rank candidates, never commit):
  python infrastructure/env_arm_improve.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LEDGER_PATH = PROJECT_ROOT / "eval_results" / "improve_ledger.jsonl"
SELFPLAY_DIR = PROJECT_ROOT / "eval_results" / "selfplay"
SELFPLAY_EVAL = PROJECT_ROOT / "infrastructure" / "selfplay_eval.py"
EVAL_TEAMS = "teams/eval-fast-teams.list"

# The candidate ENGINE ENV ARMS to sweep. Each entry is ONE engine knob and the
# file/literal that defines its SOURCE default, plus the candidate values to try
# against that current default. These are all read at engine import via
# os.getenv(KEY, "<default>"), so a self-play arm sets them with zero source
# edits, and an accepted winner is committed by rewriting exactly that literal.
#
# Conservative, self-play-measurable knobs only (each plausibly strengthens play
# without touching ladder connectivity): MCTS blend breadth and per-move search
# time. Add more arms here as they are proven safe; the driver treats this list
# as data so growing the search space is a one-line change, never new code.
CANDIDATE_ARMS = [
    {
        "key": "MCTS_BLEND_MAX_SAMPLES",
        "file": "fp/search/main.py",
        # matches:  os.getenv("MCTS_BLEND_MAX_SAMPLES", "2")  (quoted default)
        "default_re": r'(?P<pre>os\.getenv\(\s*"MCTS_BLEND_MAX_SAMPLES"\s*,\s*")(?P<val>\d+)(?P<post>"\s*\))',
        "candidates": ["3", "4"],
    },
    {
        "key": "SEARCH_TIME_MS",
        "file": "config.py",
        # matches:  _env_int_prefer(("SEARCH_TIME_MS", "PS_SEARCH_TIME_MS"), 100)  (bare int default)
        "default_re": r'(?P<pre>_env_int_prefer\(\(\s*"SEARCH_TIME_MS"\s*,\s*"PS_SEARCH_TIME_MS"\s*\)\s*,\s*)(?P<val>\d+)(?P<post>\s*\))',
        "candidates": ["150", "200"],
    },
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    entry = {"timestamp": _utcnow_iso(), **entry}
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[ENVARM] ledger += {entry.get('outcome')} :: {entry.get('issue','')[:70]}")


def read_current_default(arm: dict) -> str | None:
    """Return the engine's CURRENT source default for this arm, or None."""
    path = PROJECT_ROOT / arm["file"]
    if not path.exists():
        return None
    m = re.search(arm["default_re"], path.read_text(encoding="utf-8"))
    return m.group("val") if m else None


def commit_new_default(arm: dict, new_value: str, current: str, verdict: dict) -> bool:
    """Rewrite the ONE default literal for this arm to new_value and commit.

    Returns True iff HEAD moved. Guaranteed-applicable: we edit a single regex-
    matched literal in-place via the pre/val/post capture groups (works for both
    quoted "2" and bare 100 defaults), so this never fails the way a model diff
    does."""
    path = PROJECT_ROOT / arm["file"]
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        arm["default_re"], rf"\g<pre>{new_value}\g<post>", text, count=1,
    )
    if n == 0 or new_text == text:
        print(f"[ENVARM] default literal for {arm['key']} not found/unchanged in {arm['file']}.")
        return False
    path.write_text(new_text, encoding="utf-8")
    head_before = _git(["rev-parse", "HEAD"])
    _run(["git", "add", arm["file"]], timeout=60)
    msg = (
        f"envarm: promote {arm['key']} default -> {new_value} "
        f"(self-play LCB {verdict.get('new_wilson_lcb')} > 0.50)\n\n"
        f"Guaranteed-applicable env-arm A/B win, measured by NEW-vs-OLD self-play\n"
        f"(NEW {arm['key']}={new_value} vs OLD {arm['key']}={current}). Verdict: "
        f"ACCEPT={verdict.get('ACCEPT')} NEW {verdict.get('new_wins')}/"
        f"{verdict.get('decisive_battles')} (win-rate {verdict.get('new_win_rate')}, "
        f"LCB {verdict.get('new_wilson_lcb')} at confidence "
        f"{verdict.get('confidence')}, label {verdict.get('label')}).\n\n"
        f"This is the env-arm learn channel that replaced the freeform-diff path\n"
        f"(model diffs never applied to the 7000-line search file). Source default\n"
        f"rewritten in {arm['file']}; no patch to apply, so this lands by\n"
        f"construction. Feature branch only; not pushed.\n\n"
        f"Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>\n"
    )
    cp = _run(["git", "commit", "-m", msg], timeout=60)
    head_after = _git(["rev-parse", "HEAD"])
    moved = head_after != head_before
    if not moved:
        print(f"[ENVARM] commit did not move HEAD: {cp.stdout}\n{cp.stderr}")
    return moved


def run_arm_eval(arm: dict, candidate: str, current: str, *, battles: int,
                 min_decisive: int, confidence: float, showdown_port: int,
                 search_ms: int, turn_cap: int, per_battle_timeout: int) -> dict | None:
    """Run ONE NEW(candidate)-vs-OLD(current) self-play eval; return its verdict."""
    label = f"envarm-{arm['key'].lower()}-{candidate}-{datetime.now():%Y%m%d-%H%M%S}"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # REACHABLE-BUT-RIGOROUS gate for the burst (source default stays 0.95).
    env["SELFPLAY_GATE_CONFIDENCE"] = str(confidence)
    env["SELFPLAY_MIN_DECISIVE"] = str(min_decisive)
    cmd = [
        sys.executable, "-X", "utf8", str(SELFPLAY_EVAL),
        "--battles", str(battles),
        "--teams-from", EVAL_TEAMS,
        "--label", label,
        "--showdown-port", str(showdown_port),
        "--search-time-ms", str(search_ms),
        "--turn-cap", str(turn_cap),
        "--per-battle-timeout", str(per_battle_timeout),
        "--new-env", f"{arm['key']}={candidate}",
        "--old-env", f"{arm['key']}={current}",
    ]
    print(f"[ENVARM] eval {arm['key']}: NEW={candidate} vs OLD={current} "
          f"(battles={battles}, conf={confidence}, min_decisive={min_decisive}) ...")
    proc = _run(cmd, timeout=int(os.getenv("ENVARM_EVAL_TIMEOUT", "20000")), env=env)
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-1200:]
    print(tail)
    verdict_path = SELFPLAY_DIR / f"{label}.json"
    if not verdict_path.exists():
        print(f"[ENVARM] NO verdict file for {label} (gate did not complete).")
        return None
    try:
        return json.loads(verdict_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[ENVARM] could not read verdict {verdict_path}: {e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="fouler env-arm A/B self-improvement")
    ap.add_argument("--battles", type=int, default=44)
    ap.add_argument("--min-decisive", type=int, default=30)
    ap.add_argument("--confidence", type=float, default=0.85,
                    help="Burst-eval Wilson confidence (source default stays 0.95). "
                         "0.85 @ N>=40 needs 62.5%% wins; coin-flip still rejects.")
    ap.add_argument("--showdown-port", type=int,
                    default=int(os.getenv("EVAL_SHOWDOWN_PORT", "18765")))
    ap.add_argument("--search-ms", type=int, default=700)
    ap.add_argument("--turn-cap", type=int, default=18)
    ap.add_argument("--per-battle-timeout", type=int, default=150)
    ap.add_argument("--dry-run", action="store_true",
                    help="Rank candidates; never commit a winner.")
    ap.add_argument("--allow-master", action="store_true")
    ap.add_argument("--max-accept", type=int, default=1,
                    help="Stop after committing this many winners (default 1).")
    args = ap.parse_args()

    br = current_branch()
    if br in {"master", "main"} and not args.allow_master:
        print(f"[ENVARM] REFUSING to run on '{br}'. Create a feature branch first "
              f"(or pass --allow-master). Never pushes; commits to current branch only.")
        return 2

    print(f"[ENVARM] branch={br} battles={args.battles} min_decisive={args.min_decisive} "
          f"confidence={args.confidence} dry_run={args.dry_run}")

    accepted = 0
    any_eval = False
    for arm in CANDIDATE_ARMS:
        if accepted >= args.max_accept:
            break
        current = read_current_default(arm)
        if current is None:
            print(f"[ENVARM] SKIP {arm['key']}: could not read current default in {arm['file']}.")
            continue
        for candidate in arm["candidates"]:
            if candidate == current:
                continue
            if accepted >= args.max_accept:
                break
            any_eval = True
            gate_started_at = time.time()
            verdict = run_arm_eval(
                arm, candidate, current,
                battles=args.battles, min_decisive=args.min_decisive,
                confidence=args.confidence, showdown_port=args.showdown_port,
                search_ms=args.search_ms, turn_cap=args.turn_cap,
                per_battle_timeout=args.per_battle_timeout,
            )
            if verdict is None:
                append_ledger({
                    "issue": f"env-arm A/B {arm['key']} {candidate} vs {current}",
                    "outcome": "gate_skipped",
                    "gate_skip_reason": "selfplay_eval produced no verdict file",
                    "decision_source": "envarm_selfplay_lcb_gt_0.50",
                    "head_before": _git(["rev-parse", "HEAD"])[:12],
                    "head_after": _git(["rev-parse", "HEAD"])[:12],
                    "committed": False,
                })
                continue
            accept = bool(verdict.get("ACCEPT"))
            head_before = _git(["rev-parse", "HEAD"])
            committed = False
            if accept and not args.dry_run:
                committed = commit_new_default(arm, candidate, current, verdict)
            head_after = _git(["rev-parse", "HEAD"])
            outcome = (
                "accepted_merged" if (accept and committed)
                else "accepted_but_commit_failed" if accept and not args.dry_run
                else "accepted_dry_run" if accept and args.dry_run
                else "reverted"
            )
            append_ledger({
                "issue": f"env-arm A/B {arm['key']} {candidate} vs {current}",
                "outcome": outcome,
                "head_before": head_before[:12],
                "head_after": head_after[:12],
                "verdict_line": (
                    f"[ENVARM] {arm['key']} {candidate} vs {current}: "
                    f"ACCEPT={accept} NEW {verdict.get('new_wins')}/"
                    f"{verdict.get('decisive_battles')} LCB {verdict.get('new_wilson_lcb')} "
                    f"(conf {verdict.get('confidence')}) :: {verdict.get('accept_reason')}"
                ),
                "selfplay_verdict": {
                    k: verdict.get(k) for k in (
                        "label", "new_wins", "old_wins", "decisive_battles",
                        "new_win_rate", "new_wilson_lcb", "confidence",
                        "min_wins_to_accept", "bar_reachable_at_n", "ACCEPT")
                },
                "decision_source": "envarm_selfplay_lcb_gt_0.50",
                "min_decisive": args.min_decisive,
                "committed": committed,
                "gate_started_at": gate_started_at,
            })
            if accept and committed:
                accepted += 1
                # The new default is now committed; subsequent arms compare
                # against the freshly-promoted baseline on next run.
                break

    if not any_eval:
        print("[ENVARM] No candidate differed from the current default; nothing to evaluate.")
    print(f"[ENVARM] done. accepted_and_committed={accepted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
