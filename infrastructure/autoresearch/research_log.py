"""
Research Log — records all autoresearch activities to JSONL.

Each entry captures:
- What was researched (topic, source)
- What was found (findings summary)
- What action was taken (code change, config update)
- Impact measurement (ELO delta, win rate change)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "data" / "autoresearch"
LOG_FILE = LOG_DIR / "research_log.jsonl"


def ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_research(
    research_type: str,
    topic: str,
    source: str,
    findings: str,
    action_taken: Optional[str] = None,
    commit_hash: Optional[str] = None,
    elo_before: Optional[int] = None,
    elo_after: Optional[int] = None,
    win_rate_before: Optional[float] = None,
    win_rate_after: Optional[float] = None,
    metadata: Optional[dict] = None,
):
    """Log a research activity.

    Args:
        research_type: Category — "smogon_usage", "replay_analysis",
                       "matchup_study", "penalty_tuning", "meta_shift"
        topic: What was researched — e.g. "Gholdengo matchup"
        source: Where data came from — e.g. "smogon.com/stats/2026-02"
        findings: Summary of what was found
        action_taken: What code/config change was made (if any)
        commit_hash: Git commit of the change (if any)
        elo_before: ELO before the change
        elo_after: ELO after validation games
        win_rate_before: Win rate before
        win_rate_after: Win rate after
        metadata: Any additional structured data
    """
    ensure_log_dir()

    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": research_type,
        "topic": topic,
        "source": source,
        "findings": findings,
        "action_taken": action_taken,
        "commit_hash": commit_hash,
        "elo_before": elo_before,
        "elo_after": elo_after,
        "win_rate_before": win_rate_before,
        "win_rate_after": win_rate_after,
        "metadata": metadata or {},
    }

    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"Research logged: [{research_type}] {topic}")
    except Exception as e:
        logger.warning(f"Failed to write research log: {e}")

    return entry


def get_recent_research(n: int = 10) -> list[dict]:
    """Get the N most recent research entries."""
    if not LOG_FILE.exists():
        return []

    entries = []
    try:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []

    return entries[-n:]


def get_research_summary() -> dict:
    """Get summary statistics of all research activities."""
    entries = get_recent_research(n=9999)
    if not entries:
        return {"total": 0, "by_type": {}, "with_impact": 0}

    by_type: dict[str, int] = {}
    with_impact = 0
    elo_deltas = []

    for e in entries:
        t = e.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        if e.get("elo_before") and e.get("elo_after"):
            with_impact += 1
            elo_deltas.append(e["elo_after"] - e["elo_before"])

    return {
        "total": len(entries),
        "by_type": by_type,
        "with_impact": with_impact,
        "avg_elo_delta": (
            round(sum(elo_deltas) / len(elo_deltas), 1)
            if elo_deltas
            else None
        ),
        "first_entry": entries[0].get("timestamp") if entries else None,
        "last_entry": entries[-1].get("timestamp") if entries else None,
    }
