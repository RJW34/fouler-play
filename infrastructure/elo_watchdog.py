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


def get_post_deploy_elo(battle_stats, deploy_timestamp_str: str) -> float | None:
    """
    Get the average ELO from battles that occurred after the deploy timestamp.
    Returns None if there are not enough post-deploy battles.
    """
    if not isinstance(battle_stats, list) or not deploy_timestamp_str:
        return None

    try:
        deploy_time = datetime.fromisoformat(deploy_timestamp_str)
    except (ValueError, TypeError):
        return None

    post_deploy_elos = []
    for battle in battle_stats:
        battle_time_str = battle.get("timestamp", battle.get("time", ""))
        if not battle_time_str:
            continue
        try:
            battle_time = datetime.fromisoformat(battle_time_str)
        except (ValueError, TypeError):
            continue

        if battle_time > deploy_time:
            elo = battle.get("elo", battle.get("rating"))
            if elo is not None:
                post_deploy_elos.append(float(elo))

    if len(post_deploy_elos) < 3:
        # Not enough post-deploy data to make a judgment
        return None

    # Return the most recent post-deploy ELO
    return post_deploy_elos[-1]


def git_revert(commit_hash: str) -> bool:
    """Revert a specific commit using git revert."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_DIR), "revert", "--no-edit", commit_hash],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print(f"Successfully reverted commit {commit_hash[:8]}")
            # Push the revert
            push_result = subprocess.run(
                ["git", "-C", str(REPO_DIR), "push", "origin", "master"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if push_result.returncode == 0:
                print("Revert pushed to remote.")
            else:
                print(
                    f"WARNING: Failed to push revert: {push_result.stderr}",
                    file=sys.stderr,
                )
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
    """Write a revert event to deploy_log.json."""
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

    # Get post-deploy ELO
    deploy_timestamp = latest_deploy.get("timestamp")
    post_deploy_elo = get_post_deploy_elo(battle_stats, deploy_timestamp)

    if post_deploy_elo is None:
        # Also try the current ELO as a fallback
        current_elo = get_current_elo(battle_stats)
        if current_elo is None:
            print("Not enough post-deploy data to evaluate. Skipping.")
            return False
        post_deploy_elo = float(current_elo)

    elo_drop = elo_at_deploy - post_deploy_elo

    print(f"Deploy: {latest_deploy.get('post_commit', 'unknown')[:8]}")
    print(f"ELO at deploy: {elo_at_deploy}")
    print(f"Current ELO:   {post_deploy_elo}")
    print(f"ELO change:    {-elo_drop:+.1f}")
    print(f"Threshold:     -{threshold}")

    if elo_drop > threshold:
        print(f"\nWARNING: ELO dropped by {elo_drop:.1f} (threshold: {threshold})!")
        print("Initiating revert...")

        commit_to_revert = latest_deploy.get("post_commit")
        if not commit_to_revert:
            print("ERROR: No commit hash found for revert.", file=sys.stderr)
            return False

        # Log the revert event first
        log_revert_event(latest_deploy, elo_at_deploy, post_deploy_elo)

        # Perform the revert
        if git_revert(commit_to_revert):
            print("Revert completed successfully.")
            return True
        else:
            print("ERROR: Revert failed. Manual intervention required.", file=sys.stderr)
            return False
    else:
        print("ELO within acceptable range. No action needed.")
        return False


def main():
    """Entry point for standalone execution."""
    reverted = check_and_revert()
    sys.exit(1 if reverted else 0)


if __name__ == "__main__":
    main()
