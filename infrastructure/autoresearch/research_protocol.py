"""
Research Protocol — structured research-implement-validate cycle.

This defines the autoresearch loop that DEKU agents follow:

1. ANALYZE: Run performance_analyzer to identify the #1 improvement target
2. RESEARCH: Fetch relevant online data (Smogon stats, counter lists, move data)
3. PLAN: Determine the specific code change needed
4. IMPLEMENT: Make the change (penalty tuning, eval weight, new check)
5. VALIDATE: Run tests, syntax check, import check
6. DEPLOY: Push to master, restart bot
7. MEASURE: Wait for N games, compare ELO/win rate before vs after
8. LOG: Record the full cycle in research_log.jsonl

The protocol output is a structured dict that DEKU dispatches as a sub-agent task.
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from .performance_analyzer import analyze_loss_patterns, get_improvement_brief
from .research_log import get_recent_research, log_research
from .smogon_fetcher import get_pokemon_counters, get_top_pokemon

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def generate_research_task() -> dict:
    """Generate the next autoresearch task for a DEKU agent.

    Returns a structured task dict with:
    - target: what to improve
    - context: battle data, Smogon stats, recent research
    - constraints: what NOT to change, test requirements
    - success_criteria: how to measure improvement
    """
    # Step 1: Analyze current performance
    losses = analyze_loss_patterns()
    targets = losses.get("improvement_targets", [])

    if not targets:
        return {
            "status": "no_targets",
            "reason": "No loss data available for analysis",
            "action": "Run more battles to collect data",
        }

    # Step 2: Pick the highest-priority target not recently researched
    recent = get_recent_research(n=5)
    recent_topics = {r.get("topic", "") for r in recent}

    target = None
    for t in targets:
        if t["target"] not in recent_topics:
            target = t
            break

    if target is None:
        target = targets[0]  # Fall back to top priority even if recently researched

    # Step 3: Fetch relevant Smogon data
    smogon_context = {}
    if target["type"] == "matchup":
        pokemon_name = target["target"]
        counters = get_pokemon_counters(pokemon_name)
        top_meta = get_top_pokemon(n=10)
        smogon_context = {
            "target_pokemon": pokemon_name,
            "counters": counters[:5],
            "meta_top_10": [p["name"] for p in top_meta],
        }

    # Step 4: Build the task
    brief = get_improvement_brief()

    task = {
        "status": "ready",
        "generated_at": datetime.now().isoformat(),
        "target": target,
        "improvement_brief": brief,
        "smogon_context": smogon_context,
        "recent_research": [
            {"topic": r.get("topic"), "type": r.get("type"), "when": r.get("timestamp")}
            for r in recent
        ],
        "constraints": {
            "never_modify": [
                "config.py",
                "run.py",
                ".env",
                "teams/**",
            ],
            "test_before_push": True,
            "max_files_changed": 3,
            "one_improvement_only": True,
        },
        "validation_steps": [
            "python -c \"import ast; ast.parse(open('fp/search/main.py').read())\"",
            "python -c \"from fp.search.main import find_best_move; print('OK')\"",
            "python -m pytest tests/ -v",
        ],
        "success_criteria": {
            "tests_pass": True,
            "min_games_before_measure": 15,
            "elo_must_not_drop": 50,
        },
    }

    return task


def record_research_cycle(
    target: str,
    research_type: str,
    source: str,
    findings: str,
    action_taken: str,
    commit_hash: Optional[str] = None,
    elo_before: Optional[int] = None,
) -> dict:
    """Record a completed research cycle.

    Call this after implementing and pushing a change.
    The elo_after will be filled in during the measurement phase.
    """
    entry = log_research(
        research_type=research_type,
        topic=target,
        source=source,
        findings=findings,
        action_taken=action_taken,
        commit_hash=commit_hash,
        elo_before=elo_before,
    )

    return entry


def get_current_elo() -> Optional[int]:
    """Read current ELO from battle_stats.json."""
    stats_file = PROJECT_ROOT / "battle_stats.json"
    if not stats_file.exists():
        return None
    try:
        data = json.loads(stats_file.read_text())
        return data.get("current_elo")
    except (json.JSONDecodeError, OSError):
        return None
