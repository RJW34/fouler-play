#!/usr/bin/env python3
"""
Fouler Play Replay Analyzer
Automatically analyzes lost battles to identify improvement opportunities
"""

import json
import re
from datetime import datetime
from pathlib import Path
import requests
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class BattleTurn:
    """Represents a single turn in a battle"""
    turn_num: int
    bot_move: str
    bot_pokemon: str
    opp_move: str
    opp_pokemon: str
    bot_hp_before: int
    bot_hp_after: int
    opp_hp_before: int
    opp_hp_after: int
    field_conditions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class MistakePattern:
    """Represents a detected mistake pattern"""
    category: str  # "setup_vs_phazer", "ignored_immunity", "bad_switch", etc.
    turn: int
    description: str
    severity: str  # "critical", "major", "minor"
    suggested_fix: str


class ReplayAnalyzer:
    """Analyzes Pokemon Showdown replays to identify mistakes"""
    
    MISTAKE_PATTERNS = {
        "setup_vs_phazer": {
            "description": "Used setup move against known phazer",
            "severity": "major",
            "phaze_moves": ["whirlwind", "roar", "dragontail", "circlethrow"]
        },
        "setup_vs_unaware": {
            "description": "Used stat-boosting move vs Unaware",
            "severity": "critical",
            "setup_moves": ["swordsdance", "dragondance", "calmmind", "nastyplot", "bulkup"]
        },
        "status_vs_substitute": {
            "description": "Used status move vs active Substitute",
            "severity": "major"
        },
        "contact_vs_punish": {
            "description": "Used contact move vs contact-punishing ability/item",
            "severity": "minor",
            "punishers": ["ironbarbs", "roughskin", "rockyhelmet"]
        },
        "ignored_immunity": {
            "description": "Used move that opponent is immune to",
            "severity": "critical"
        },
        "wasted_coverage": {
            "description": "Clicked coverage move that does less damage than STAB",
            "severity": "minor"
        }
    }
    
    def __init__(self):
        self.losses_dir = Path("/home/ryan/projects/fouler-play/replay_analysis/losses")
        self.reports_dir = Path("/home/ryan/projects/fouler-play/replay_analysis/reports")
        self.patterns_dir = Path("/home/ryan/projects/fouler-play/replay_analysis/patterns")
        
    def fetch_replay(self, replay_url: str) -> Optional[Dict]:
        """Download and parse a replay from Pokemon Showdown"""
        try:
            # Convert viewer URL to JSON URL
            if "replay.pokemonshowdown.com" in replay_url:
                replay_id = replay_url.split("/")[-1]
                json_url = f"https://replay.pokemonshowdown.com/{replay_id}.json"
            else:
                json_url = replay_url
                
            response = requests.get(json_url, timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Failed to fetch replay: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error fetching replay: {e}")
            return None
    
    def parse_replay_log(self, log_lines: List[str]) -> List[BattleTurn]:
        """Parse the replay log into structured turns"""
        turns = []
        current_turn = None
        bot_name = "LEBOTJAMESXD001"
        
        for line in log_lines:
            line = line.strip()
            
            # Detect turn start
            if line.startswith("|turn|"):
                turn_num = int(line.split("|")[2])
                current_turn = {
                    "turn_num": turn_num,
                    "moves": [],
                    "switches": [],
                    "damage": [],
                    "field": []
                }
                
            # Detect moves
            elif line.startswith("|move|") and current_turn:
                parts = line.split("|")
                pokemon = parts[2].split(":")[1].strip()
                move = parts[3].lower()
                current_turn["moves"].append((pokemon, move))
                
            # Detect switches
            elif line.startswith("|switch|") and current_turn:
                parts = line.split("|")
                pokemon = parts[3].split(",")[0].strip()
                current_turn["switches"].append(pokemon)
                
            # Detect field conditions
            elif line.startswith("|-fieldstart|") and current_turn:
                condition = line.split("|")[2]
                current_turn["field"].append(condition)
                
        return turns
    
    def detect_mistakes(self, replay_data: Dict) -> List[MistakePattern]:
        """Analyze a replay and detect mistake patterns"""
        mistakes = []
        log_lines = replay_data.get("log", "").split("\n")
        
        # Parse replay into structured format
        # This is a simplified version - full implementation would track:
        # - Active Pokemon each turn
        # - Revealed movesets/abilities/items
        # - HP percentages
        # - Field conditions
        
        bot_name = "LEBOTJAMESXD001"
        current_turn = 0
        opp_revealed_moves = set()
        opp_revealed_ability = None
        opp_has_substitute = False
        
        for line in log_lines:
            line = line.strip()
            
            if line.startswith("|turn|"):
                current_turn = int(line.split("|")[2])
                
            # Track opponent's revealed moves
            elif line.startswith("|move|"):
                parts = line.split("|")
                pokemon = parts[2]
                move = parts[3].lower()
                
                if bot_name not in pokemon:
                    opp_revealed_moves.add(move)
                    
                    # Check for setup vs phazer
                    setup_moves = ["swordsdance", "dragondance", "calmmind", "nastyplot", "bulkup", "curse"]
                    phaze_moves = ["whirlwind", "roar", "dragontail", "circlethrow"]
                    
                    if move in setup_moves and any(pm in opp_revealed_moves for pm in phaze_moves):
                        mistakes.append(MistakePattern(
                            category="setup_vs_phazer",
                            turn=current_turn,
                            description=f"Used {move} when opponent has phazing move",
                            severity="major",
                            suggested_fix="Add/increase penalty for setup vs revealed phazers"
                        ))
                        
            # Track abilities
            elif "|-ability|" in line and bot_name not in line:
                parts = line.split("|")
                if len(parts) >= 3:
                    opp_revealed_ability = parts[3].lower()
                    
            # Track Substitute
            elif "|-start|" in line and "Substitute" in line:
                if bot_name not in line:
                    opp_has_substitute = True
            elif "|-end|" in line and "Substitute" in line:
                if bot_name not in line:
                    opp_has_substitute = False
                    
        return mistakes
    
    def save_loss_replay(self, replay_url: str, replay_data: Dict, mistakes: List[MistakePattern]):
        """Save a lost replay with analysis"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        replay_id = replay_url.split("/")[-1]
        
        analysis = {
            "timestamp": timestamp,
            "replay_url": replay_url,
            "replay_id": replay_id,
            "mistakes_found": len(mistakes),
            "mistakes": [
                {
                    "category": m.category,
                    "turn": m.turn,
                    "description": m.description,
                    "severity": m.severity,
                    "suggested_fix": m.suggested_fix
                }
                for m in mistakes
            ]
        }
        
        output_file = self.losses_dir / f"loss_{timestamp}_{replay_id}.json"
        with open(output_file, 'w') as f:
            json.dump(analysis, f, indent=2)
            
        print(f"Saved loss analysis: {output_file}")
        return analysis
    
    def generate_report(self) -> str:
        """Generate a summary report of all analyzed losses"""
        loss_files = list(self.losses_dir.glob("loss_*.json"))
        
        if not loss_files:
            return "No losses analyzed yet."
            
        # Aggregate statistics
        total_losses = len(loss_files)
        mistake_categories = {}
        total_mistakes = 0
        
        for loss_file in loss_files:
            with open(loss_file) as f:
                data = json.load(f)
                for mistake in data.get("mistakes", []):
                    cat = mistake["category"]
                    mistake_categories[cat] = mistake_categories.get(cat, 0) + 1
                    total_mistakes += 1
                    
        # Generate report
        report = f"""
# Fouler Play Loss Analysis Report
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Summary
- Total losses analyzed: {total_losses}
- Total mistakes detected: {total_mistakes}
- Avg mistakes per loss: {total_mistakes / total_losses:.1f}

## Mistake Categories (by frequency)
"""
        
        for category, count in sorted(mistake_categories.items(), key=lambda x: -x[1]):
            percentage = (count / total_mistakes) * 100
            report += f"- **{category}**: {count} ({percentage:.1f}%)\n"
            
        report += "\n## Recommended Improvements\n"
        
        # Generate recommendations based on most common mistakes
        if mistake_categories:
            top_category = max(mistake_categories, key=mistake_categories.get)
            report += f"\n### Priority: Address '{top_category}' pattern\n"
            report += f"This accounts for {mistake_categories[top_category]} mistakes across {total_losses} losses.\n"
            
        return report
    
    def analyze_loss(self, replay_url: str):
        """Full pipeline: fetch, analyze, save"""
        print(f"Analyzing loss: {replay_url}")
        
        replay_data = self.fetch_replay(replay_url)
        if not replay_data:
            print("Failed to fetch replay")
            return None
            
        mistakes = self.detect_mistakes(replay_data)
        print(f"Found {len(mistakes)} potential mistakes")
        
        analysis = self.save_loss_replay(replay_url, replay_data, mistakes)
        
        # Generate updated report
        report = self.generate_report()
        report_file = self.reports_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(report_file, 'w') as f:
            f.write(report)
        print(f"Updated report: {report_file}")
        
        return analysis


if __name__ == "__main__":
    analyzer = ReplayAnalyzer()
    
    # Example usage
    import sys
    if len(sys.argv) > 1:
        replay_url = sys.argv[1]
        analyzer.analyze_loss(replay_url)
    else:
        print("Usage: python analyzer.py <replay_url>")
        print("\nGenerating report from existing data...")
        print(analyzer.generate_report())
