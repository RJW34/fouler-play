#!/usr/bin/env python3
"""
Autoresearch Cycle Runner — the missing glue.

Ties together:
  1. Battle bot (run.py) — plays 30 battles
  2. Pipeline (pipeline.py) — watches for batch completions + runs analysis
  3. Performance Analyzer — identifies improvement targets from loss data
  4. Research Protocol — generates structured improvement tasks
  5. Research Log — records everything

Usage:
  python -m infrastructure.autoresearch.run_cycle           # Run one full cycle
  python -m infrastructure.autoresearch.run_cycle --dry-run  # Analyze only, don't start battles
  python -m infrastructure.autoresearch.run_cycle --status   # Show current state
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.autoresearch.performance_analyzer import analyze_loss_patterns, get_improvement_brief
from infrastructure.autoresearch.research_protocol import generate_research_task
from infrastructure.autoresearch.research_log import log_research, get_recent_research, ensure_log_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
PIPELINE_STATE_FILE = PROJECT_ROOT / ".pipeline_state"
BATCH_SIZE = int(os.getenv("FOULER_BATCH_SIZE", "30"))


def get_battle_count() -> int:
    """Get total battles played."""
    if not BATTLE_STATS_FILE.exists():
        return 0
    try:
        with open(BATTLE_STATS_FILE) as f:
            data = json.load(f)
            return len(data.get("battles", []))
    except Exception:
        return 0


def get_win_rate(last_n: int = 30) -> tuple:
    """Get W/L record for last N battles."""
    if not BATTLE_STATS_FILE.exists():
        return 0, 0
    try:
        with open(BATTLE_STATS_FILE) as f:
            data = json.load(f)
            battles = data.get("battles", [])[-last_n:]
            wins = sum(1 for b in battles if b.get("result") == "win")
            losses = len(battles) - wins
            return wins, losses
    except Exception:
        return 0, 0


def show_status():
    """Show current autoresearch status."""
    total = get_battle_count()
    wins, losses = get_win_rate(30)
    wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    print(f"\n{'='*50}")
    print(f"  Fouler Play — Autoresearch Status")
    print(f"{'='*50}")
    print(f"  Total battles:     {total}")
    print(f"  Last 30 record:    {wins}W - {losses}L ({wr:.1f}%)")
    print(f"  Batch size:        {BATCH_SIZE}")
    print(f"  Bot account:       {os.getenv('PS_USERNAME', 'npctypebeat')}")

    # Pipeline state
    if PIPELINE_STATE_FILE.exists():
        with open(PIPELINE_STATE_FILE) as f:
            state = json.load(f)
        print(f"  Last analysis:     {state.get('last_analysis_timestamp', 'never')}")
        print(f"  Batches analyzed:  {state.get('current_batch', 0)}")
        print(f"  Last battle count: {state.get('last_battle_count', 0)}")
        battles_until = BATCH_SIZE - (total - state.get("last_battle_count", 0))
        print(f"  Until next batch:  {max(0, battles_until)} battles")
    else:
        print(f"  Pipeline state:    not initialized")

    # Research log
    recent = get_recent_research(n=3)
    if recent:
        print(f"\n  Recent research:")
        for r in recent:
            print(f"    - [{r.get('timestamp', '?')[:10]}] {r.get('topic', '?')}: {r.get('action_taken', 'no action')[:60]}")
    else:
        print(f"\n  Research log:      empty")

    # Loss analysis
    losses_data = analyze_loss_patterns()
    targets = losses_data.get("improvement_targets", [])
    if targets:
        print(f"\n  Top improvement targets:")
        for t in targets[:3]:
            print(f"    - {t.get('label', t.get('target', '?'))}: {t.get('count', '?')} occurrences")

    print(f"{'='*50}\n")


def run_analysis_cycle():
    """Run one analysis + research task generation cycle (no battles)."""
    ensure_log_dir()

    print("\n🔍 Running autoresearch analysis cycle...")

    # Step 1: Analyze loss patterns
    print("\n📊 Step 1: Analyzing loss patterns...")
    losses = analyze_loss_patterns()
    targets = losses.get("improvement_targets", [])

    if not targets:
        print("  No improvement targets found. Need more battle data.")
        return None

    print(f"  Found {len(targets)} improvement targets:")
    for t in targets[:5]:
        print(f"    - {t.get('label', t.get('target', '?'))}: {t.get('count', '?')} occurrences")

    # Step 2: Generate research task
    print("\n🧪 Step 2: Generating research task...")
    task = generate_research_task()

    if task.get("status") == "no_targets":
        print(f"  {task.get('reason', 'No targets')}")
        return None

    print(f"  Target: {task.get('target', {}).get('target', '?')}")
    print(f"  Type: {task.get('target', {}).get('type', '?')}")

    # Step 3: Get improvement brief
    print("\n📝 Step 3: Getting improvement brief...")
    brief = get_improvement_brief()
    print(f"  {brief[:200]}...")

    # Step 4: Log the research cycle
    log_research(
        research_type="autoresearch_cycle",
        topic=task.get("target", {}).get("target", "unknown"),
        source="performance_analyzer + research_protocol",
        findings=brief[:500],
        action_taken="analysis_only",
        win_rate_before=get_win_rate(30)[0] / max(1, sum(get_win_rate(30))) * 100,
        metadata={"targets": [t.get("target", "") for t in targets[:5]]},
    )

    print("\n✅ Analysis cycle complete. Task ready for implementation.")
    print(f"\n📋 Research Task Summary:")
    print(json.dumps(task, indent=2, default=str)[:2000])

    return task


def run_full_cycle(dry_run: bool = False):
    """Run a full autoresearch cycle: analyze → (optionally start battles)."""
    show_status()

    task = run_analysis_cycle()

    if dry_run:
        print("\n🏁 Dry run — skipping battle execution.")
        return

    if task and task.get("status") != "no_targets":
        print("\n🎮 The analysis is complete. To start battles:")
        print(f"  cd '{PROJECT_ROOT}'")
        print(f"  python run.py")
        print(f"\n  Or run the pipeline watcher:")
        print(f"  python pipeline.py watch")


def main():
    parser = argparse.ArgumentParser(description="Fouler Play Autoresearch Cycle")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only, don't start battles")
    parser.add_argument("--analyze", action="store_true", help="Run analysis cycle only")

    args = parser.parse_args()

    # Load .env
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    if args.status:
        show_status()
    elif args.analyze or args.dry_run:
        run_full_cycle(dry_run=True)
    else:
        run_full_cycle()


if __name__ == "__main__":
    main()
