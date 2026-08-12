#!/usr/bin/env python3
"""Recursive-cycle IMPROVE step for the DekuFoulerLab continuous ladder.

This is the real "learn" half of the play-30 -> IMPROVE -> play-30 loop driven by
``ladder_supervisor.py``. It closes the codebase's already-live loss-learning loop:

  live consume side  (ALREADY WIRED):
      fp/search/main.py  -> matchup_memory.bias_policy(policy, battle)
                         -> reads fp/matchup_weights.json every decision and
                            nudges the engine's own candidates toward pivoting
                            away from opponent species that historically beat us.

  produce side  (THIS FILE — was never wired to run automatically; the weights
                 file had been frozen since 2026-07-11 while the bot kept playing):
      recent replay_analysis/*.json
        -> replay_analysis.loss_learning.build_loss_artifact   (deterministic)
        -> fp.matchup_memory.update_weights_from_artifacts      (full rebuild
                                                                 over the window)
        -> fp.matchup_memory.write_weights -> fp/matchup_weights.json

So each IMPROVE step rebuilds the loss-derived bias from the most-recent games,
and the NEXT 30-battle batch (a fresh child process) loads the refreshed weights.
Observed losses -> updated weights -> changed live play. This is bounded, review-
able DATA (not generated code), using the exact substrate the live path reads.

Honest scope note: this tunes the loss-derived matchup bias (switch-pressure away
from repeat threats). It does NOT rewrite the MCTS engine or teams — that heavier
"auto-improve" path (infra/improve_agent.py, LLM code mutation) stays disabled.
This is the best REAL improvement the running substrate supports.

Deterministic, side-effect-bounded, and fail-safe: any error leaves the existing
weights untouched and returns a summary with ``ok=False``.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPLAY_DIR = ROOT / "replay_analysis"
WEIGHTS_PATH = ROOT / "fp" / "matchup_weights.json"
BACKUP_DIR = ROOT / ".codex_backups"

# Rolling window of most-recent replays fed to the rebuild. The established design
# (see replay_analysis/account_identity.py) is a ~500-replay window; stale species
# age out naturally as old replays leave the window.
DEFAULT_WINDOW = int(os.getenv("FOULER_IMPROVE_WINDOW", "500"))
# The account currently laddering (public name; NOT a secret). Drives side-detection
# so we never mislabel our own team as "threats".
BOT_USERNAME = os.getenv("FOULER_IMPROVE_BOT_USERNAME", "DekuFoulerLab")


def _recent_replays(window: int) -> list[Path]:
    try:
        files = [
            f for f in REPLAY_DIR.glob("gen9ou-*.json")
            if "_gameplan" not in f.name  # skip gameplan sidecars
        ]
    except OSError:
        return []
    files.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0.0), reverse=True)
    return files[: max(1, window)]


def _flagged_counts(weights: dict) -> dict:
    """Count species that pass the LIVE bias thresholds (what bias_policy acts on)."""
    from fp import matchup_memory

    problem = 0
    bad = 0
    examples: list[str] = []
    keys = set(weights.get("problem_pokemon", {})) | set(weights.get("bad_matchups", {}))
    for sid in keys:
        flag = matchup_memory.opponent_is_flagged(sid, weights)
        if not flag:
            continue
        if flag.get("kind") == "problem_pokemon":
            problem += 1
        else:
            bad += 1
        if len(examples) < 12:
            examples.append(f"{sid}:{flag.get('kind')}")
    return {"flagged_problem": problem, "flagged_bad": bad, "examples": sorted(examples)}


def run_improve(window: int = DEFAULT_WINDOW, bot_username: str = BOT_USERNAME, log=print) -> dict:
    started = time.time()
    summary: dict = {
        "ok": False,
        "ts": datetime.now(timezone.utc).isoformat(),
        "window_requested": window,
    }
    try:
        from replay_analysis.loss_learning import build_loss_artifact, load_replay
        from replay_analysis.account_identity import resolve_bot_accounts, _norm_account
        from fp import matchup_memory
    except Exception as exc:  # import failure -> leave weights untouched
        summary["error"] = f"import failed: {exc}"
        log(f"[improve] ABORT: {summary['error']}")
        return summary

    accounts = resolve_bot_accounts() | {_norm_account(bot_username)}
    files = _recent_replays(window)
    summary["window_used"] = len(files)
    if not files:
        summary["error"] = "no replay files found"
        log("[improve] ABORT: no replay files found")
        return summary

    artifacts: list[dict] = []
    losses = 0
    skipped = 0
    for f in files:
        try:
            data = load_replay(f)
            art = build_loss_artifact(data, bot_username=bot_username, bot_accounts=accounts)
            artifacts.append(art)
            if str(art.get("result")) == "loss":
                losses += 1
        except Exception:
            skipped += 1
            continue

    summary["artifacts_built"] = len(artifacts)
    summary["losses_in_window"] = losses
    summary["replays_skipped"] = skipped

    if not artifacts:
        summary["error"] = "no artifacts built from window"
        log("[improve] ABORT: no artifacts built")
        return summary

    try:
        prev = matchup_memory.load_weights()
        weights = matchup_memory.update_weights_from_artifacts(artifacts)
    except Exception as exc:
        summary["error"] = f"weight rebuild failed: {exc}"
        log(f"[improve] ABORT: {summary['error']}")
        return summary

    # Backup the current weights, then atomically write the refreshed ones.
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        if WEIGHTS_PATH.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(WEIGHTS_PATH, BACKUP_DIR / f"matchup_weights.json.improve-{stamp}")
        matchup_memory.write_weights(weights, WEIGHTS_PATH)
    except Exception as exc:
        summary["error"] = f"write failed: {exc}"
        log(f"[improve] ABORT: {summary['error']}")
        return summary

    prev_flags = _flagged_counts(prev)
    new_flags = _flagged_counts(weights)
    summary.update(
        {
            "ok": True,
            "prev_updated_at": prev.get("updated_at"),
            "new_updated_at": weights.get("updated_at"),
            "prev_bad_matchups": len(prev.get("bad_matchups", {})),
            "new_bad_matchups": len(weights.get("bad_matchups", {})),
            "prev_problem_pokemon": len(prev.get("problem_pokemon", {})),
            "new_problem_pokemon": len(weights.get("problem_pokemon", {})),
            "prev_flagged": {k: prev_flags[k] for k in ("flagged_problem", "flagged_bad")},
            "new_flagged": {k: new_flags[k] for k in ("flagged_problem", "flagged_bad")},
            "new_flagged_examples": new_flags["examples"],
            "elapsed_sec": round(time.time() - started, 1),
        }
    )
    log(
        "[improve] OK window=%d artifacts=%d losses=%d skipped=%d | "
        "weights updated_at %s -> %s | live-flagged problem %d->%d bad %d->%d | %.1fs"
        % (
            len(files), len(artifacts), losses, skipped,
            summary["prev_updated_at"], summary["new_updated_at"],
            summary["prev_flagged"]["flagged_problem"], summary["new_flagged"]["flagged_problem"],
            summary["prev_flagged"]["flagged_bad"], summary["new_flagged"]["flagged_bad"],
            summary["elapsed_sec"],
        )
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild loss-derived matchup weights from the recent replay window.")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--bot-username", default=BOT_USERNAME)
    parser.add_argument("--dry-run", action="store_true", help="Build + report but do NOT write weights.")
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    args = parser.parse_args(argv)

    if args.dry_run:
        # Report window + would-be artifact counts without touching the file.
        os.environ.setdefault("_IMPROVE_DRYRUN", "1")
        files = _recent_replays(args.window)
        print(f"[improve] DRY-RUN window={len(files)} newest={files[0].name if files else 'none'} "
              f"oldest={files[-1].name if files else 'none'}")
        return 0

    summary = run_improve(window=args.window, bot_username=args.bot_username)
    if args.json:
        print(json.dumps(summary, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
