"""
Performance Analyzer — extracts improvement targets from battle data.

Analyzes battle_stats.json and replay_analysis/losses/ to identify:
1. Most common mistake categories in losses (no_hazards_set, pokemon_fainted, etc.)
2. Per-team win rates from battle_stats.json
3. Decision quality issues from decision traces when available
4. Priority improvement targets for the next autoresearch cycle

Loss file format (actual):
    {
        "timestamp": "20260130_230835",
        "replay_url": "...",
        "replay_id": "gen9ou-XXXXXXX",
        "mistakes_found": 2,
        "mistakes": [
            {
                "category": "no_hazards_set",
                "turn": 16,
                "description": "...",
                "severity": "major",
                "suggested_fix": "Increase hazard priority in scoring"
            },
            ...
        ]
    }

Mistake categories observed:
    - pokemon_fainted       (switching errors — we're losing Pokemon unnecessarily)
    - no_hazards_set        (never set up Stealth Rock / Spikes)
    - hazards_not_removed   (opponent's hazards left up too long)
    - early_setup           (boosting too early before opponent is weakened)
    - insufficient_switching (not pivoting enough)
"""

import json
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATS_FILE = PROJECT_ROOT / "battle_stats.json"
TRACES_DIR = PROJECT_ROOT / "logs" / "decision_traces"
LOSSES_DIR = PROJECT_ROOT / "replay_analysis" / "losses"

# Map mistake categories to human-readable labels and code locations
CATEGORY_META = {
    "pokemon_fainted": {
        "label": "Poor switching (Pokemon fainted unnecessarily)",
        "code_hint": "fp/search/main.py switch scoring, fp/search/eval.py matchup penalties",
        "suggested_fix": "Improve switching logic to preserve key Pokemon",
    },
    "no_hazards_set": {
        "label": "Hazards never set (missed Stealth Rock / Spikes)",
        "code_hint": "fp/search/main.py hazard priority scoring",
        "suggested_fix": "Increase hazard priority in scoring",
    },
    "hazards_not_removed": {
        "label": "Opponent hazards not cleared (no Defog / Rapid Spin)",
        "code_hint": "fp/search/main.py defog/rapidspin priority",
        "suggested_fix": "Add defog/rapid spin priority when hazards are up",
    },
    "early_setup": {
        "label": "Boosting too early (setup before opponent weakened)",
        "code_hint": "fp/search/main.py setup move gating",
        "suggested_fix": "Delay setup until opponent is sufficiently weakened",
    },
    "insufficient_switching": {
        "label": "Not pivoting enough (low switch count per game)",
        "code_hint": "fp/search/main.py switch penalty constants",
        "suggested_fix": "Reduce switch penalty, increase matchup awareness",
    },
}


def analyze_loss_patterns() -> dict:
    """Analyze loss replays to find recurring patterns.

    Reads replay_analysis/losses/*.json and battle_stats.json.

    Returns dict with:
    - mistake_categories: ranked breakdown of mistake types across all losses
    - fainted_pokemon: our Pokemon that faint most (from pokemon_fainted mistakes)
    - team_weaknesses: per-team W/L from battle_stats.json
    - total_losses_analyzed: number of loss files read
    - improvement_targets: prioritized list of what to fix
    """
    result = {
        "mistake_categories": [],
        "fainted_pokemon": [],
        "team_weaknesses": {},
        "total_losses_analyzed": 0,
        "improvement_targets": [],
    }

    # --- Parse loss files ---
    if LOSSES_DIR.exists():
        loss_files = list(LOSSES_DIR.glob("*.json"))
        categories = Counter()
        fainted = Counter()

        for f in loss_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            result["total_losses_analyzed"] += 1

            for mistake in data.get("mistakes", []):
                cat = mistake.get("category", "unknown")
                categories[cat] += 1

                # Extract Pokemon name from pokemon_fainted descriptions
                # Format: "Gholdengo fainted - possible switching error"
                if cat == "pokemon_fainted":
                    desc = mistake.get("description", "")
                    name = desc.split(" fainted")[0].strip()
                    if name:
                        fainted[name] += 1

        total = max(result["total_losses_analyzed"], 1)
        result["mistake_categories"] = [
            {
                "category": cat,
                "count": count,
                "pct": round(count / total * 100),
                "label": CATEGORY_META.get(cat, {}).get("label", cat),
                "suggested_fix": CATEGORY_META.get(cat, {}).get("suggested_fix", ""),
                "code_hint": CATEGORY_META.get(cat, {}).get("code_hint", ""),
            }
            for cat, count in categories.most_common()
        ]

        result["fainted_pokemon"] = [
            {"pokemon": name, "faint_count": count}
            for name, count in fainted.most_common(10)
        ]

    # --- Parse battle_stats.json for per-team record ---
    if STATS_FILE.exists():
        try:
            stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            battles = stats.get("battles", [])
            team_wins = Counter()
            team_losses = Counter()
            for b in battles:
                team = b.get("team_file", "unknown")
                if b.get("result") == "win":
                    team_wins[team] += 1
                else:
                    team_losses[team] += 1
            all_teams = set(list(team_wins.keys()) + list(team_losses.keys()))
            team_weaknesses = {}
            for t in sorted(all_teams):
                w = team_wins[t]
                l = team_losses[t]
                total_t = w + l
                pct = round(100 * w / total_t) if total_t > 0 else 0
                team_weaknesses[t] = {
                    "wins": w,
                    "losses": l,
                    "total": total_t,
                    "win_pct": pct,
                }
            result["team_weaknesses"] = team_weaknesses
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not parse battle_stats.json: %s", e)

    # --- Generate improvement targets ---
    targets = []

    # Priority 1: most common mistake category
    if result["mistake_categories"]:
        top_cat = result["mistake_categories"][0]
        targets.append({
            "priority": 1,
            "type": "mistake_category",
            "target": top_cat["category"],
            "reason": (
                f"{top_cat['label']} — appears {top_cat['count']} times "
                f"across {result['total_losses_analyzed']} losses "
                f"({top_cat['pct']}% loss rate)"
            ),
            "research_action": top_cat["suggested_fix"],
            "code_hint": top_cat["code_hint"],
        })

    # Priority 2: worst-performing team from battle_stats
    if result["team_weaknesses"]:
        worst_team = min(
            result["team_weaknesses"].items(),
            key=lambda kv: kv[1]["win_pct"],
        )
        team_name, team_stats = worst_team
        if team_stats["total"] >= 5:  # Only flag if enough games
            targets.append({
                "priority": 2,
                "type": "team_performance",
                "target": team_name,
                "reason": (
                    f"{team_name} is the worst-performing team at "
                    f"{team_stats['win_pct']}% win rate "
                    f"({team_stats['wins']}W {team_stats['losses']}L)"
                ),
                "research_action": (
                    f"Analyze {team_name} losses specifically — "
                    f"compare mistake categories vs other teams"
                ),
                "code_hint": "Cross-reference loss files with battle_stats.json team_file",
            })

    # Priority 3: most-fainted Pokemon (if different from top category)
    if result["fainted_pokemon"] and (
        not targets or targets[0]["type"] != "pokemon_fainted"
    ):
        top_fainted = result["fainted_pokemon"][0]
        targets.append({
            "priority": 3,
            "type": "pokemon_fainted",
            "target": top_fainted["pokemon"],
            "reason": (
                f"{top_fainted['pokemon']} fainted in "
                f"{top_fainted['faint_count']} losses — "
                f"bad switch-in or recovery timing?"
            ),
            "research_action": (
                f"Check matchup scoring for {top_fainted['pokemon']} — "
                f"is the bot keeping it in bad matchups or not healing in time?"
            ),
            "code_hint": "fp/search/main.py switch scoring, fp/search/eval.py",
        })

    result["improvement_targets"] = targets
    return result


def analyze_decision_quality() -> dict:
    """Analyze decision traces for repeated suboptimal choices.

    Returns dict with:
    - total_traces: number of traces analyzed
    - low_confidence_ratio: fraction of near-50/50 decisions
    """
    result = {
        "total_traces": 0,
        "low_confidence_ratio": 0,
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
    """Generate a concise brief on what to work on next.

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

    if losses["mistake_categories"]:
        lines.append("### Mistake Categories (across all loss files):")
        for m in losses["mistake_categories"]:
            lines.append(
                f"  - {m['category']}: {m['count']} times "
                f"({m['pct']}% of losses) — {m['label']}"
            )
        lines.append("")

    if losses["team_weaknesses"]:
        lines.append("### Per-Team Record (from battle_stats.json):")
        for team, stats in losses["team_weaknesses"].items():
            lines.append(
                f"  - {team}: {stats['wins']}W {stats['losses']}L "
                f"({stats['win_pct']}%)"
            )
        lines.append("")

    if losses["fainted_pokemon"]:
        lines.append("### Our Pokemon Fainted Most Often:")
        for p in losses["fainted_pokemon"][:5]:
            lines.append(
                f"  - {p['pokemon']}: fainted {p['faint_count']} times"
            )
        lines.append("")

    if losses["improvement_targets"]:
        lines.append("### Priority Improvement Targets:")
        for t in losses["improvement_targets"]:
            lines.append(
                f"  P{t['priority']} [{t['type']}] {t['target']}: "
                f"{t['reason']}"
            )
            lines.append(f"    -> {t['research_action']}")
            if t.get("code_hint"):
                lines.append(f"    -> Code: {t['code_hint']}")
        lines.append("")

    return "\n".join(lines)
