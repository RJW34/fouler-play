"""
Fouler-Play ELO Watchdog
=========================
Monitors ELO after deploys and auto-reverts if a significant drop is detected.

Can run standalone:
    python infrastructure/elo_watchdog.py

Or be imported:
    from infrastructure.elo_watchdog import check_and_revert
    reverted = check_and_revert()
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
DEPLOY_LOG_PATH = SCRIPT_DIR / "deploy_log.json"
BATTLE_STATS_PATH = REPO_DIR / "battle_stats.json"
GUARDRAILS_PATH = SCRIPT_DIR / "guardrails.json"


def load_json(path: Path):
    """Load a JSON file, returning None if it does not exist or is invalid."""
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: Could not read {path}: {e}", file=sys.stderr)
        return None


def get_elo_threshold() -> int:
    """Read the max ELO drop threshold from guardrails.json."""
    guardrails = load_json(GUARDRAILS_PATH)
    if guardrails and "safety" in guardrails:
        return guardrails["safety"].get("max_elo_drop_before_revert", 50)
    return 50  # default


def get_current_elo(battle_stats) -> float | None:
    """Extract the most recent ELO from battle_stats data."""
    if battle_stats is None:
        return None

    if isinstance(battle_stats, list) and len(battle_stats) > 0:
        last = battle_stats[-1]
        return last.get("elo", last.get("rating"))
    elif isinstance(battle_stats, dict):
        return battle_stats.get("elo", battle_stats.get("rating"))

    return None


def get_latest_deploy(deploy_log: list) -> dict | None:
    """Get the most recent deploy entry from the deploy log."""
    if not deploy_log or not isinstance(deploy_log, list):
        return None

    # Find the most recent 'deploy' type entry
    deploys = [e for e in deploy_log if e.get("type") == "deploy"]
    if not deploys:
        return None

    return deploys[-1]


# Minimum post-deploy battles before ELO is even considered (single-point reverts
# over 3 battles are pure noise -- the whole reason the loop "edited but never
# climbed"). Win-rate is the primary progress metric; ELO only matters once the
# Glicko deviation is small.
MIN_BATTLES_FOR_JUDGMENT = int(os.getenv("ELO_WATCHDOG_MIN_BATTLES", "30"))
MAX_GLICKO_DEVIATION_FOR_ELO = float(os.getenv("ELO_WATCHDOG_MAX_RD", "50"))


def get_post_deploy_battles(battle_stats, deploy_timestamp_str: str) -> list:
    """Return the battle entries recorded strictly after the deploy timestamp."""
    if not isinstance(battle_stats, list) or not deploy_timestamp_str:
        return []
    try:
        deploy_time = datetime.fromisoformat(deploy_timestamp_str)
    except (ValueError, TypeError):
        return []
    out = []
    for battle in battle_stats:
        battle_time_str = battle.get("timestamp", battle.get("time", ""))
        if not battle_time_str:
            continue
        try:
            battle_time = datetime.fromisoformat(battle_time_str)
        except (ValueError, TypeError):
            continue
        if battle_time > deploy_time:
            out.append(battle)
    return out


def win_rate(battles: list) -> tuple[float, int]:
    """Return (win_rate, n) over decisive (win/loss) battles."""
    decisive = [b for b in battles if b.get("result") in ("win", "loss")]
    n = len(decisive)
    if n == 0:
        return 0.0, 0
    wins = sum(1 for b in decisive if b.get("result") == "win")
    return wins / n, n


def latest_glicko_deviation(battles: list) -> float | None:
    """Most recent Glicko deviation (rprd) recorded post-deploy, if available."""
    for battle in reversed(battles):
        rd = battle.get("rprd", battle.get("deviation"))
        if rd is not None:
            try:
                return float(rd)
            except (TypeError, ValueError):
                continue
    return None


def get_post_deploy_elo(battle_stats, deploy_timestamp_str: str) -> float | None:
    """Most recent post-deploy ELO (kept for back-compat / logging)."""
    battles = get_post_deploy_battles(battle_stats, deploy_timestamp_str)
    elos = [
        float(b.get("elo", b.get("rating")))
        for b in battles
        if b.get("elo", b.get("rating")) is not None
    ]
    if len(elos) < MIN_BATTLES_FOR_JUDGMENT:
        return None
    return elos[-1]


def _current_branch() -> str:
    """Push to the branch the runtime is actually on, not a hardcoded master.

    The live runtime tracks a codex/devstream-fouler-sync-* branch; pushing to a
    hardcoded 'master' would fail or target the wrong ref.
    """
    try:
        branch = subprocess.run(
            ["git", "-C", str(REPO_DIR), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        return branch or "HEAD"
    except Exception:
        return "HEAD"


def git_revert(commit_hash: str) -> bool:
    """Revert a specific commit using git revert."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_DIR), "revert", "--no-edit", commit_hash],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if result.returncode == 0:
            print(f"Successfully reverted commit {commit_hash[:8]} (local working tree restored)")
            # Push only if explicitly opted-in. The live bot runs from THIS local working
            # tree, so the local revert already protects it; the push is just remote sync.
            # Default OFF to avoid an unrequested outward push (Claude/DEKU 2026-06-16).
            if os.getenv("ELO_WATCHDOG_PUSH", "0") == "1":
                push_result = subprocess.run(
                    ["git", "-C", str(REPO_DIR), "push", "origin", _current_branch()],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                if push_result.returncode == 0:
                    print("Revert pushed to remote.")
                else:
                    print(
                        f"WARNING: Failed to push revert: {push_result.stderr}",
                        file=sys.stderr,
                    )
            else:
                print("Push skipped (ELO_WATCHDOG_PUSH != 1); local revert protects the live runtime.")
            return True
        else:
            print(
                f"ERROR: git revert failed: {result.stderr}",
                file=sys.stderr,
            )
            return False
    except subprocess.TimeoutExpired:
        print("ERROR: git revert timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"ERROR: git revert failed: {e}", file=sys.stderr)
        return False


def log_revert_event(deploy_entry: dict, elo_at_deploy: float, current_elo: float):
    """Write a revert event to deploy_log.json and build manifest."""
    deploy_log = load_json(DEPLOY_LOG_PATH)
    if deploy_log is None:
        deploy_log = []

    revert_entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "revert",
        "reverted_commit": deploy_entry.get("post_commit", "unknown"),
        "reverted_to": deploy_entry.get("pre_commit", "unknown"),
        "elo_at_deploy": elo_at_deploy,
        "elo_at_revert": current_elo,
        "elo_drop": round(elo_at_deploy - current_elo, 1) if elo_at_deploy and current_elo else None,
    }

    deploy_log.append(revert_entry)

    try:
        with open(DEPLOY_LOG_PATH, "w") as f:
            json.dump(deploy_log, f, indent=2)
        print(f"Revert event logged to {DEPLOY_LOG_PATH}")
    except OSError as e:
        print(f"WARNING: Could not write revert event: {e}", file=sys.stderr)

    # Update build manifest
    try:
        from infrastructure.build_manifest import get_manifest
        m = get_manifest(REPO_DIR)
        elo_drop = round(elo_at_deploy - current_elo, 1) if elo_at_deploy and current_elo else None
        m.record_revert(
            reverted_sha=deploy_entry.get("post_commit", "unknown"),
            reason=f"ELO drop: {elo_drop} (threshold: {get_elo_threshold()})",
        )
        print("Build manifest updated with revert.")
    except Exception as e:
        print(f"WARNING: Could not update build manifest: {e}", file=sys.stderr)


def check_and_revert() -> bool:
    """
    Check if the latest deploy caused an ELO drop beyond the threshold.
    If so, revert it.

    Returns:
        True if a revert was performed, False otherwise.
    """
    # Load data
    deploy_log = load_json(DEPLOY_LOG_PATH)
    battle_stats = load_json(BATTLE_STATS_PATH)
    threshold = get_elo_threshold()

    # Get latest deploy
    latest_deploy = get_latest_deploy(deploy_log)
    if latest_deploy is None:
        print("No deploy entries found. Nothing to check.")
        return False

    # Get ELO at time of deploy
    elo_at_deploy = latest_deploy.get("elo_at_deploy")
    if elo_at_deploy is None:
        print("No ELO recorded at deploy time. Cannot evaluate.")
        return False

    elo_at_deploy = float(elo_at_deploy)

    deploy_timestamp = latest_deploy.get("timestamp")
    post_battles = get_post_deploy_battles(battle_stats, deploy_timestamp)
    n_battles = len([b for b in post_battles if b.get("result") in ("win", "loss")])

    print(f"Deploy: {latest_deploy.get('post_commit', 'unknown')[:8]}")
    print(f"Post-deploy decisive battles: {n_battles} (need >= {MIN_BATTLES_FOR_JUDGMENT})")

    # PRIMARY progress metric is WIN-RATE, not a single noisy ELO point.
    if n_battles < MIN_BATTLES_FOR_JUDGMENT:
        print("Not enough post-deploy battles to judge (win-rate needs a sample). "
              "Skipping -- do NOT revert on noise.")
        return False

    wr_after, _ = win_rate(post_battles)
    wr_before = latest_deploy.get("win_rate_at_deploy")
    rd = latest_glicko_deviation(post_battles)
    post_deploy_elo = get_post_deploy_elo(battle_stats, deploy_timestamp)

    print(f"Win-rate (post-deploy): {wr_after:.1%}"
          + (f"  vs at-deploy {float(wr_before):.1%}" if wr_before is not None else ""))
    print(f"Glicko deviation (rprd): {rd if rd is not None else 'unknown'} "
          f"(ELO trusted only when < {MAX_GLICKO_DEVIATION_FOR_ELO})")
    print(f"Current ELO: {post_deploy_elo}")

    # Win-rate regression is the trigger. A meaningful drop (> 8 pts) AND a
    # below-coinflip absolute win-rate is required.
    wr_regressed = (
        wr_before is not None
        and (float(wr_before) - wr_after) > 0.08
        and wr_after < 0.50
    )

    # ELO drop is only a corroborating/secondary signal, and ONLY once the rating
    # is established (deviation < threshold). During placement (high rd) ELO swings
    # are noise and must never trigger a revert on their own.
    elo_trusted = rd is not None and rd < MAX_GLICKO_DEVIATION_FOR_ELO
    elo_regressed = (
        elo_trusted
        and post_deploy_elo is not None
        and (elo_at_deploy - float(post_deploy_elo)) > threshold
    )

    if wr_regressed or (elo_regressed and wr_after < 0.50):
        reason = "win-rate regression" if wr_regressed else "established-ELO drop"
        print(f"\nWARNING: {reason} detected post-deploy. Initiating revert...")
        commit_to_revert = latest_deploy.get("post_commit")
        if not commit_to_revert:
            print("ERROR: No commit hash found for revert.", file=sys.stderr)
            return False
        log_revert_event(latest_deploy, elo_at_deploy, post_deploy_elo or 0.0)
        if git_revert(commit_to_revert):
            print("Revert completed successfully.")
            return True
        print("ERROR: Revert failed. Manual intervention required.", file=sys.stderr)
        return False

    print("Win-rate/ELO within acceptable range (or rating not yet established). "
          "No action needed.")
    return False


def main():
    """Entry point for standalone execution."""
    reverted = check_and_revert()
    sys.exit(1 if reverted else 0)


if __name__ == "__main__":
    main()
