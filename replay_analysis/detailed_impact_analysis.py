#!/usr/bin/env python3
"""Extract detailed impact metrics from replays for improvement plan."""

import json
import sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.turn_review import TurnReviewer

def analyze_replay_for_issues(replay_file: Path, reviewer) -> dict:
    """Analyze a single replay for specific issue patterns."""
    try:
        with open(replay_file, 'r') as f:
            replay_data = json.load(f)
        
        replay_id = replay_file.stem
        result = "unknown"
        team = "unknown"
        
        # Extract result
        if "log" in replay_data:
            log = replay_data["log"]
            if "BugInTheCode won" in log or "|win|BugInTheCode" in log:
                result = "win"
            elif "won the battle!" in log:
                result = "loss"
        
        # Try to infer team from replay (simplified)
        # In real analysis, would need to parse team data
        
        # Extract turns
        turns = reviewer.extract_full_turns(replay_data, f"https://replay.pokemonshowdown.com/{replay_id}")
        if not turns:
            return None
        
        issues = {
            "replay_id": replay_id,
            "result": result,
            "team": team,
            "hazard_fail": False,
            "bad_switch": False,
            "coverage_gap": False,
            "examples": []
        }
        
        # Analyze turn-by-turn for patterns
        stealth_rock_turn = None
        for i, turn in enumerate(turns):
            context = turn.why_critical.lower()
            choice = turn.bot_choice.lower()
            
            # HAZARD MANAGEMENT
            # Check if SR was set late or not at all
            if "stealth" in choice or "stealth rock" in choice:
                stealth_rock_turn = turn.turn_number
            
            # If we're at turn 10+ and never set hazards (and it's a loss)
            if i >= 9 and stealth_rock_turn is None and result == "loss":
                issues["hazard_fail"] = True
                issues["examples"].append({
                    "issue": "hazard_management",
                    "turn": turn.turn_number,
                    "context": f"Turn {turn.turn_number}: Never set Stealth Rock (opponent may have gained advantage)"
                })
            
            # BAD SWITCH DECISION
            # Look for staying in when HP is low and opponent threatens
            if turn.bot_hp_percent < 40 and turn.opp_hp_percent > 70:
                if "switch" not in choice and turn.turn_number < 15:
                    issues["bad_switch"] = True
                    issues["examples"].append({
                        "issue": "switchout",
                        "turn": turn.turn_number,
                        "context": f"Turn {turn.turn_number}: {turn.bot_active} ({turn.bot_hp_percent:.0f}% HP) stayed vs {turn.opp_active} ({turn.opp_hp_percent:.0f}% HP)"
                    })
            
            # COVERAGE GAP
            # Detect type disadvantage patterns (simplified heuristic)
            if result == "loss" and i > 5:
                # If same opponent mon is dominating multiple turns
                if i > 0 and turns[i-1].opp_active == turn.opp_active:
                    if turn.bot_hp_percent < turns[i-1].bot_hp_percent - 25:
                        issues["coverage_gap"] = True
                        issues["examples"].append({
                            "issue": "type_coverage",
                            "turn": turn.turn_number,
                            "context": f"Turn {turn.turn_number}: {turn.opp_active} dominated, took {turns[i-1].bot_hp_percent - turn.bot_hp_percent:.0f}% damage"
                        })
        
        return issues
        
    except Exception as e:
        print(f"Error analyzing {replay_file.name}: {e}")
        return None

def main():
    reviewer = TurnReviewer(bot_username="BugInTheCode")
    replay_dir = Path(__file__).parent
    replay_files = sorted(replay_dir.glob("gen9ou-*.json"))[-30:]  # Last 30 battles
    
    # Load battle_stats for team info
    battle_stats_file = PROJECT_ROOT / "battle_stats.json"
    team_map = {}
    if battle_stats_file.exists():
        with open(battle_stats_file, 'r') as f:
            data = json.load(f)
            for battle in data.get("battles", []):
                replay_id = battle.get("replay_id", "")
                team = battle.get("team_file", "unknown")
                team_map[replay_id] = team
    
    # Aggregate results
    stats = {
        "total": 0,
        "losses": 0,
        "hazard_fails": 0,
        "bad_switches": 0,
        "coverage_gaps": 0,
        "team_breakdown": defaultdict(lambda: {"losses": 0, "hazard_fails": 0, "bad_switches": 0, "coverage_gaps": 0}),
        "examples": {
            "hazard_management": [],
            "switchout": [],
            "type_coverage": []
        }
    }
    
    for replay_file in replay_files:
        analysis = analyze_replay_for_issues(replay_file, reviewer)
        if not analysis:
            continue
        
        stats["total"] += 1
        
        # Get team info
        team = team_map.get(analysis["replay_id"], "unknown")
        analysis["team"] = team
        
        if analysis["result"] == "loss":
            stats["losses"] += 1
            stats["team_breakdown"][team]["losses"] += 1
        
        if analysis["hazard_fail"]:
            stats["hazard_fails"] += 1
            stats["team_breakdown"][team]["hazard_fails"] += 1
            # Store first 3 examples
            if len(stats["examples"]["hazard_management"]) < 3:
                stats["examples"]["hazard_management"].append({
                    "replay_id": analysis["replay_id"],
                    "team": team,
                    "details": analysis["examples"][0]["context"] if analysis["examples"] else "No SR setup"
                })
        
        if analysis["bad_switch"]:
            stats["bad_switches"] += 1
            stats["team_breakdown"][team]["bad_switches"] += 1
            if len(stats["examples"]["switchout"]) < 3:
                for ex in analysis["examples"]:
                    if ex["issue"] == "switchout" and len(stats["examples"]["switchout"]) < 3:
                        stats["examples"]["switchout"].append({
                            "replay_id": analysis["replay_id"],
                            "team": team,
                            "details": ex["context"]
                        })
        
        if analysis["coverage_gap"]:
            stats["coverage_gaps"] += 1
            stats["team_breakdown"][team]["coverage_gaps"] += 1
            if len(stats["examples"]["type_coverage"]) < 3:
                for ex in analysis["examples"]:
                    if ex["issue"] == "type_coverage" and len(stats["examples"]["type_coverage"]) < 3:
                        stats["examples"]["type_coverage"].append({
                            "replay_id": analysis["replay_id"],
                            "team": team,
                            "details": ex["context"]
                        })
    
    # Calculate percentages
    if stats["losses"] > 0:
        stats["hazard_fail_pct"] = (stats["hazard_fails"] / stats["losses"]) * 100
        stats["bad_switch_pct"] = (stats["bad_switches"] / stats["losses"]) * 100
        stats["coverage_gap_pct"] = (stats["coverage_gaps"] / stats["losses"]) * 100
    
    # Output JSON
    print(json.dumps(stats, indent=2, default=str))

if __name__ == "__main__":
    main()
