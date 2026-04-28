#!/usr/bin/env python3
"""
Post-commit script for autoresearch / builder crons.

Call after committing an improvement to update the build manifest.
Project-agnostic — works for any repo with infrastructure/build_manifest.py.

Usage:
    python3 infrastructure/autoresearch_post_commit.py

The autoresearch cron prompt should include:
    "After committing, run: python3 infrastructure/autoresearch_post_commit.py"
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from infrastructure.build_manifest import get_manifest


def get_battle_count() -> int:
    """Read current battle count from battle_stats.json (fouler-play specific)."""
    bs = REPO_DIR / "battle_stats.json"
    if not bs.exists():
        return 0
    try:
        with open(bs, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return len(data.get("battles", []))
        if isinstance(data, list):
            return len(data)
    except Exception:
        pass
    return 0


def get_files_changed() -> list:
    """Get files changed in the last commit."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_DIR), "diff", "--name-only", "HEAD~1"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().split("\n") if f]
    except Exception:
        pass
    return []


def main():
    m = get_manifest(REPO_DIR)
    progress = get_battle_count()
    files = get_files_changed()

    entry = m.record_commit(
        progress_count=progress,
        files_changed=files,
        source="autoresearch",
    )

    print(f"Build manifest recorded: {entry['sha']} (files: {len(files)}, progress: {progress})")

    # Stage the updated manifest so it gets included in the push
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "add", "data/build_manifest.json"],
        capture_output=True,
        timeout=10,
    )


if __name__ == "__main__":
    main()
