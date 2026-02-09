#!/usr/bin/env python3
"""
Automated Replay Analyzer for Fouler-Play
Runs periodically to analyze loss patterns every 10 battles
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import requests
from collections import Counter, defaultdict

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
STATE_FILE = PROJECT_ROOT / "infrastructure" / "replay_analyzer_state.json"
LOGS_DIR = PROJECT_ROOT / "logs"


class ReplayAnalyzer:
    """Analyzes batches of replays for decision-making patterns"""
    
    def __init__(self):
        self.state = self.load_state()
        
    def load_state(self) -> Dict:
        """Load last-analyzed battle count"""
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
        return {"last_analyzed_battle": 0, "total_batches_analyzed": 0}
    
    def save_state(self):
        """Save current state"""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def get_battle_stats(self) -> List[Dict]:
        """Load battle stats"""
        if not BATTLE_STATS_FILE.exists():
            print(f"❌ Battle stats file not found: {BATTLE_STATS_FILE}")
            return []
        
        with open(BATTLE_STATS_FILE) as f:
            data = json.load(f)
            return data.get("battles", [])
    
    def load_local_replay_log(self, battle_id: str) -> Optional[str]:
        """Load replay log from local logs directory"""
        # Battle logs are stored as battle_id_opponent.log
        # Find matching log file
        log_pattern = f"{battle_id}_*.log"
        
        import glob
        matching_logs = glob.glob(str(LOGS_DIR / log_pattern))
        
        if not matching_logs:
            # Try without opponent name
            direct_log = LOGS_DIR / f"{battle_id}_None.log"
            if direct_log.exists():
                matching_logs = [str(direct_log)]
        
        if not matching_logs:
            print(f"⚠️  No local log found for {battle_id}")
            return None
        
        # Use the first matching log
        log_file = matching_logs[0]
        
        try:
            with open(log_file, 'r') as f:
                return f.read()
        except Exception as e:
            print(f"⚠️  Error reading log {log_file}: {e}")
            return None
    
    def analyze_loss_replay(self, replay_log: str, battle_info: Dict) -> Dict:
        """Analyze a single loss replay for decision-making patterns"""
        
        patterns = {
            "bad_switches": [],
            "missed_ko_lines": [],
            "status_misplays": [],
            "setup_errors": [],
            "hazard_issues": []
        }
        
        # Parse log lines - handle both raw log and JSON format
        if isinstance(replay_log, dict):
            log_lines = replay_log.get("log", "").split("\n")
        else:
            log_lines = replay_log.split("\n")
        
        # Tracking state
        current_turn = 0
        bot_active = None
        opp_active = None
        bot_moves = defaultdict(list)  # Pokemon -> moves used
        opp_moves = defaultdict(set)
        field_hazards = {"bot": set(), "opp": set()}
        faints = []
        
        for line in log_lines:
            line = line.strip()
            
            # Track turns
            if line.startswith("|turn|"):
                current_turn = int(line.split("|")[2])
            
            # Track switches
            elif line.startswith("|switch|") or line.startswith("|drag|"):
                parts = line.split("|")
                player = parts[2].split(":")[0]
                pokemon = parts[2].split(":")[1].split(",")[0].strip()
                
                if player == "p1":  # Bot
                    bot_active = pokemon
                else:
                    opp_active = pokemon
            
            # Track moves
            elif line.startswith("|move|"):
                parts = line.split("|")
                player = parts[2].split(":")[0] if ":" in parts[2] else None
                pokemon = parts[2].split(":")[1].strip() if ":" in parts[2] else parts[2].strip()
                move = parts[3].lower().replace(" ", "")
                
                if player == "p1":  # Bot's move
                    bot_moves[pokemon].append((current_turn, move))
                elif player == "p2":  # Opponent's move
                    opp_moves[pokemon].add(move)
            
            # Track hazards
            elif "|-sidestart|" in line:
                if "Stealth Rock" in line or "Spikes" in line or "Toxic Spikes" in line:
                    if "p1:" in line:
                        field_hazards["bot"].add(line.split("|")[-1].strip())
                    else:
                        field_hazards["opp"].add(line.split("|")[-1].strip())
            
            # Track faints
            elif line.startswith("|faint|"):
                parts = line.split("|")
                player = parts[2].split(":")[0] if ":" in parts[2] else None
                pokemon = parts[2].split(":")[1].strip() if ":" in parts[2] else parts[2].strip()
                
                if player == "p1":
                    faints.append((current_turn, pokemon))
        
        # Pattern detection
        
        # 1. Hazard analysis
        if not field_hazards["opp"]:
            patterns["hazard_issues"].append("Never set up hazards despite opportunity")
        
        if field_hazards["bot"] and "defog" not in str(bot_moves) and "rapidspin" not in str(bot_moves):
            patterns["hazard_issues"].append("Opponent set hazards, never removed them")
        
        # 2. Setup move analysis
        setup_moves = {"swordsdance", "dragondance", "calmmind", "nastyplot", "bulkup", "curse"}
        for pokemon, moves in bot_moves.items():
            for turn, move in moves:
                if move in setup_moves:
                    # Check if setup was used at a bad time
                    if turn < 5:
                        patterns["setup_errors"].append(f"T{turn}: {pokemon} used {move} too early")
                    
                    # Check if opponent had phaze moves
                    phaze_moves = {"whirlwind", "roar", "dragontail", "circlethrow"}
                    if opp_active and any(pm in opp_moves.get(opp_active, set()) for pm in phaze_moves):
                        patterns["setup_errors"].append(f"T{turn}: {pokemon} setup vs known phazer")
        
        # 3. Status move analysis
        status_moves = {"willowisp", "thunderwave", "toxic", "spore", "sleeppowder"}
        for pokemon, moves in bot_moves.items():
            for turn, move in moves:
                if move in status_moves:
                    # Very basic check - could be enhanced with substitute detection
                    if "substitute" in str(opp_moves.get(opp_active, set())):
                        patterns["status_misplays"].append(f"T{turn}: Used {move} when opp has substitute")
        
        # 4. Switch timing
        total_switches = sum(1 for moves in bot_moves.values() if len(moves) > 0)
        if total_switches < 3 and current_turn > 15:
            patterns["bad_switches"].append(f"Only {total_switches} switches in {current_turn} turns (too passive)")
        
        # 5. Early faints
        for turn, pokemon in faints:
            if turn < 10:
                patterns["bad_switches"].append(f"T{turn}: {pokemon} fainted early (preserve better?)")
        
        return {
            "replay_id": battle_info.get("replay_id"),
            "team": battle_info.get("team_file"),
            "timestamp": battle_info.get("timestamp"),
            "patterns": patterns,
            "total_turns": current_turn,
            "total_faints": len(faints)
        }
    
    def analyze_batch(self, battles: List[Dict], start_idx: int, end_idx: int) -> Tuple[List[Dict], Dict]:
        """Analyze a batch of battles"""
        
        batch_battles = battles[start_idx:end_idx]
        losses = [b for b in batch_battles if b.get("result") == "loss"]
        
        print(f"\n📊 Batch analysis: battles {start_idx+1}-{end_idx}")
        print(f"   Total: {len(batch_battles)} | Losses: {len(losses)} | Win rate: {((len(batch_battles)-len(losses))/len(batch_battles)*100):.1f}%")
        
        analyses = []
        pattern_summary = defaultdict(list)
        
        for loss in losses:
            battle_id = loss.get("battle_id")
            if not battle_id:
                continue
            
            print(f"   Analyzing {battle_id}...")
            
            # Load local log
            replay_log = self.load_local_replay_log(battle_id)
            if not replay_log:
                continue
            
            analysis = self.analyze_loss_replay(replay_log, loss)
            analyses.append(analysis)
            
            # Aggregate patterns
            for category, issues in analysis["patterns"].items():
                if issues:
                    pattern_summary[category].extend(issues)
        
        # Summary statistics
        summary = {
            "batch_size": len(batch_battles),
            "losses_analyzed": len(analyses),
            "win_rate": ((len(batch_battles) - len(losses)) / len(batch_battles) * 100) if batch_battles else 0,
            "patterns": {
                category: len(issues) for category, issues in pattern_summary.items()
            },
            "top_patterns": pattern_summary
        }
        
        return analyses, summary
    
    def format_discord_message(self, summary: Dict, analyses: List[Dict]) -> str:
        """Format analysis as concise Discord message"""
        
        msg = "🔬 **Replay Analysis Complete**\n\n"
        
        # Batch stats
        msg += f"**Batch**: {summary['batch_size']} battles | "
        msg += f"{summary['losses_analyzed']} losses | "
        msg += f"{summary['win_rate']:.1f}% WR\n\n"
        
        # Pattern summary
        if summary['patterns']:
            msg += "**Decision Patterns Found:**\n"
            
            sorted_patterns = sorted(summary['patterns'].items(), key=lambda x: x[1], reverse=True)
            
            for category, count in sorted_patterns:
                if count > 0:
                    category_name = category.replace("_", " ").title()
                    msg += f"• **{category_name}**: {count} instances\n"
            
            msg += "\n**Key Findings:**\n"
            
            # Show top issues
            for category, issues in summary['top_patterns'].items():
                if issues and category in [p[0] for p in sorted_patterns[:3]]:  # Top 3 categories
                    category_name = category.replace("_", " ").title()
                    
                    # Count unique issues
                    issue_counts = Counter(issues)
                    top_issue = issue_counts.most_common(1)[0]
                    
                    msg += f"• {category_name}: `{top_issue[0]}`"
                    if top_issue[1] > 1:
                        msg += f" ({top_issue[1]}x)"
                    msg += "\n"
        else:
            msg += "✅ No major decision patterns detected (or replays unavailable)\n"
        
        # Team analysis
        team_losses = Counter([a['team'] for a in analyses if a.get('team')])
        if team_losses:
            msg += f"\n**Team Performance:**\n"
            for team, count in team_losses.most_common(3):
                msg += f"• {team}: {count} losses\n"
        
        return msg.strip()
    
    def run(self) -> Optional[str]:
        """Main execution: check for new battles and analyze if needed"""
        
        battles = self.get_battle_stats()
        if not battles:
            return None
        
        total_battles = len(battles)
        last_analyzed = self.state["last_analyzed_battle"]
        new_battles = total_battles - last_analyzed
        
        print(f"📈 Total battles: {total_battles}")
        print(f"📌 Last analyzed: {last_analyzed}")
        print(f"🆕 New battles: {new_battles}")
        
        # Check if we have 10+ new battles
        if new_battles < 10:
            print(f"⏳ Waiting for more battles ({new_battles}/10)")
            return None
        
        # Analyze the batch
        start_idx = last_analyzed
        end_idx = min(last_analyzed + 10, total_battles)
        
        analyses, summary = self.analyze_batch(battles, start_idx, end_idx)
        
        # Update state
        self.state["last_analyzed_battle"] = end_idx
        self.state["total_batches_analyzed"] += 1
        self.state["last_run"] = datetime.now().isoformat()
        self.save_state()
        
        # Generate Discord message
        message = self.format_discord_message(summary, analyses)
        
        return message
    
    def post_to_discord(self, message: str, webhook_url: str):
        """Post message to Discord webhook"""
        
        try:
            response = requests.post(
                webhook_url,
                json={"content": message},
                timeout=10
            )
            
            if response.status_code == 204:
                print("✅ Posted to Discord")
            else:
                print(f"⚠️  Discord post failed: HTTP {response.status_code}")
        except Exception as e:
            print(f"❌ Error posting to Discord: {e}")


def main():
    """Main entry point"""
    
    analyzer = ReplayAnalyzer()
    
    # Run analysis
    message = analyzer.run()
    
    if not message:
        print("✅ No analysis needed at this time")
        sys.exit(0)
    
    print("\n" + "="*60)
    print("ANALYSIS RESULT:")
    print("="*60)
    print(message)
    print("="*60)
    
    # Get webhook URL from environment
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    
    webhook_url = os.getenv("DISCORD_BATTLES_WEBHOOK_URL")
    
    if not webhook_url:
        print("\n⚠️  No webhook URL found in .env")
        print("   Set DISCORD_BATTLES_WEBHOOK_URL to enable Discord posting")
        sys.exit(0)
    
    # Post to Discord
    analyzer.post_to_discord(message, webhook_url)
    
    print("\n✅ Analysis complete")


if __name__ == "__main__":
    main()
