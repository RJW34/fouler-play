"""
Performance Analyzer — extracts improvement targets from battle data.

Analyzes battle_stats.json and decision traces to identify:
1. Most common loss patterns (which Pokemon/matchups cause losses)
2. Decision quality issues (repeated suboptimal moves)
3. Team-specific weaknesses
4. Priority improvement targets for the next autoresearch cycle

This is what DEKU reads to decide what to research next.
"""

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATS_FILE = PROJECT_ROOT / "battle_stats.json"
TRACES_DIR = PROJECT_ROOT / "logs" / "decision_traces"
LOSSES_DIR = PROJECT_ROOT / "replay_analysis" / "losses"


def analyze_loss_patterns() -> dict:
    """Analyze loss replays to find recurring patterns.

    Returns dict with:
    - worst_matchups: Pokemon that appear most in losses
    - common_mistakes: Repeated decision errors
    - team_weaknesses: Per-team loss rates
    """
    result = {
        "worst_matchups": [],
        "team_weaknesses": {},
        "total_losses_analyzed": 0,
        "improvement_targets": [],
    }

    if not LOSSES_DIR.exists():
        return result

    loss_files = list(LOSSES_DIR.glob("*.json"))
    if not loss_files:
        return result

    opponent_pokemon = Counter()
    team_losses = Counter()
    fainted_pokemon = Counter()

    for f in loss_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        result["total_losses_analyzed"] += 1

        # Track opponent Pokemon in losses
        for poke in data.get("opponent_team", []):
            name = poke if isinstance(poke, str) else poke.get("name", "")
            if name:
                opponent_pokemon[name] += 1

        # Track which of our Pokemon fainted
        for poke in data.get("fainted_pokemon", []):
            name = poke if isinstance(poke, str) else poke.get("name", "")
            if name:
                fainted_pokemon[name] += 1

        # Track team losses
        team = data.get("team_name", "unknown")
        team_losses[team] += 1

    # Worst matchups (opponent Pokemon appearing most in losses)
    total = max(result["total_losses_analyzed"], 1)
    result["worst_matchups"] = [
        {"pokemon": name, "loss_count": count, "loss_pct": round(count / total * 100)}
        for name, count in opponent_pokemon.most_common(10)
    ]

    # Team weaknesses
    result["team_weaknesses"] = dict(team_losses)

    # Most-fainted Pokemon (weak links in our teams)
    weak_links = [
        {"pokemon": name, "faint_count": count}
        for name, count in fainted_pokemon.most_common(5)
    ]

    # Generate improvement targets
    targets = []
    if result["worst_matchups"]:
        top_threat = result["worst_matchups"][0]
        targets.append({
            "priority": 1,
            "type": "matchup",
            "target": top_threat["pokemon"],
            "reason": (
                f"Appears in {top_threat['loss_pct']}% of losses "
                f"({top_threat['loss_count']} games)"
            ),
            "research_action": (
                f"Research {top_threat['pokemon']} counters on Smogon, "
                f"check if penalty pipeline handles this matchup correctly"
            ),
        })

    if weak_links:
        top_weak = weak_links[0]
        targets.append({
            "priority": 2,
            "type": "team_weakness",
            "target": top_weak["pokemon"],
            "reason": f"Fainted in {top_weak['faint_count']} losses",
            "research_action": (
                f"Analyze why {top_weak['pokemon']} faints so often — "
                f"bad switches? Bad recovery timing? Wrong move selection?"
            ),
        })

    result["improvement_targets"] = targets
    return result


def analyze_decision_quality() -> dict:
    """Analyze decision traces for repeated suboptimal choices.

    Returns dict with:
    - total_traces: number of traces analyzed
    - low_confidence_decisions: decisions where the engine was unsure
    - repeated_mistakes: same bad choice made multiple times
    """
    result = {
        "total_traces": 0,
        "low_confidence_ratio": 0,
        "common_penalties": Counter(),
    }

    if not TRACES_DIR.exists():
        return result

    trace_files = sorted(TRACES_DIR.glob("*.json"))[-50:]  # Last 50 traces
    if not trace_files:
        return result

    total = 0
    low_confidence = 0

    for f in trace_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        decisions = data if isinstance(data, list) else [data]
        for decision in decisions:
            total += 1
            # Check if top two choices were close in score
            scores = decision.get("scores", {})
            if isinstance(scores, dict) and len(scores) >= 2:
                sorted_scores = sorted(scores.values(), reverse=True)
                if len(sorted_scores) >= 2:
                    gap = sorted_scores[0] - sorted_scores[1]
                    if gap < 0.1:  # Very close decision
                        low_confidence += 1

    result["total_traces"] = total
    result["low_confidence_ratio"] = (
        round(low_confidence / total, 3) if total > 0 else 0
    )

    return result


def get_improvement_brief() -> str:
    """Generate a concise brief for DEKU on what to work on next.

    Returns a text brief suitable for including in a DEKU dispatch prompt.
    """
    losses = analyze_loss_patterns()
    quality = analyze_decision_quality()

    lines = [
        "## Fouler-Play Improvement Brief",
        "",
        f"Losses analyzed: {losses['total_losses_analyzed']}",
        f"Decision traces: {quality['total_traces']}",
        f"Low-confidence decision ratio: {quality['low_confidence_ratio']:.1%}",
        "",
    ]

    if losses["worst_matchups"]:
        lines.append("### Worst Matchups (opponent Pokemon in our losses):")
        for m in losses["worst_matchups"][:5]:
            lines.append(
                f"  - {m['pokemon']}: {m['loss_pct']}% of losses "
                f"({m['loss_count']} games)"
            )
        lines.append("")

    if losses["team_weaknesses"]:
        lines.append("### Per-Team Losses:")
        for team, count in losses["team_weaknesses"].items():
            lines.append(f"  - {team}: {count} losses")
        lines.append("")

    if losses["improvement_targets"]:
        lines.append("### Priority Improvement Targets:")
        for t in losses["improvement_targets"]:
            lines.append(
                f"  P{t['priority']} [{t['type']}] {t['target']}: "
                f"{t['reason']}"
            )
            lines.append(f"    -> {t['research_action']}")
        lines.append("")

    return "\n".join(lines)
