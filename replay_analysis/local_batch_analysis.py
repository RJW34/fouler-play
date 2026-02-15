#!/usr/bin/env python3
"""Quick batch analysis using local replay JSONs."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.turn_review import TurnReviewer

MAGNETON_HOST = "Ryan@192.168.1.181"
OLLAMA_MODEL = "qwen2.5-coder:7b"
REPLAY_DIR = Path(__file__).parent
REPORTS_DIR = REPLAY_DIR / "reports"

def analyze_local_replays(max_count: int = 20):
    """Analyze local replay JSONs."""
    reviewer = TurnReviewer(bot_username="BugInTheCode")
    replay_files = sorted(REPLAY_DIR.glob("gen9ou-*.json"))[-max_count:]
    
    print(f"Found {len(replay_files)} replay files to analyze")
    
    reviews = []
    stats = {"total": 0, "wins": 0, "losses": 0, "analyzed": 0}
    
    for replay_file in replay_files:
        try:
            with open(replay_file, 'r') as f:
                replay_data = json.load(f)
            
            replay_id = replay_file.stem
            replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
            
            # Extract turns
            turns = reviewer.extract_full_turns(replay_data, replay_url)
            if not turns:
                continue
            
            stats["analyzed"] += 1
            
            # Determine result from replay data
            result = "unknown"
            if "log" in replay_data:
                log = replay_data["log"]
                if "BugInTheCode won the battle!" in log or "|win|BugInTheCode" in log:
                    result = "win"
                    stats["wins"] += 1
                elif "won the battle!" in log:
                    result = "loss"
                    stats["losses"] += 1
            
            stats["total"] += 1
            
            # Format review
            review_lines = [f"--- Battle: {replay_id} (Result: {result}) ---"]
            review_lines.append(f"Replay: {replay_url}")
            review_lines.append("\nTurn-by-turn breakdown:")
            
            for turn in turns[:15]:  # Limit to first 15 turns
                review_lines.append(
                    f"Turn {turn.turn_number}: {turn.bot_active} ({turn.bot_hp_percent:.0f}% HP) vs "
                    f"{turn.opp_active} ({turn.opp_hp_percent:.0f}% HP)"
                )
                review_lines.append(f"  Bot chose: {turn.bot_choice}")
                if turn.why_critical:
                    review_lines.append(f"  Context: {turn.why_critical[:200]}")
            
            reviews.append("\n".join(review_lines))
            
        except Exception as e:
            print(f"Error processing {replay_file.name}: {e}")
            continue
    
    return reviews, stats

def build_prompt(reviews: List[str], stats: Dict) -> str:
    """Build analysis prompt for Ollama."""
    winrate = stats["wins"] / stats["total"] if stats["total"] > 0 else 0
    
    prompt = f"""You are analyzing Pokemon Showdown Gen 9 OU battle replays for a competitive bot named BugInTheCode.

BATCH STATISTICS:
- Total battles analyzed: {stats['analyzed']}
- Wins: {stats['wins']}
- Losses: {stats['losses']}
- Win rate: {winrate:.1%}

BATTLE REVIEWS:
{chr(10).join(reviews[:12])}

ANALYSIS TASK:
Identify concrete patterns and provide actionable code improvements. Focus on:

1. SWITCHOUT MISTAKES: Does the bot stay in when it should switch? Does it switch unnecessarily?
2. HAZARD MANAGEMENT: Does the bot set Stealth Rocks early? Does it respect opponent hazards?
3. TYPE COVERAGE GAPS: Which matchups consistently fail? What type weaknesses are exploited?
4. MOVE SELECTION: Any patterns of suboptimal move choices? (e.g., using wrong coverage move, not setting up)
5. PREDICTION PATTERNS: Does the bot get read easily? Does it over-predict or under-predict?

OUTPUT FORMAT:
Provide a structured analysis with:
- Top 3-5 identified weaknesses (cite specific turn examples)
- Suggested code changes (be specific about which evaluation functions need adjustment)
- Expected impact on win rate
- Implementation difficulty (easy/medium/hard)

Be concise and actionable. This is for a developer to implement improvements.
"""
    return prompt

def query_ollama(prompt: str) -> str:
    """Query Ollama on MAGNETON."""
    try:
        prompt_json = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False})
        
        ssh_cmd = [
            "ssh", MAGNETON_HOST,
            "curl -s -X POST http://localhost:11434/api/generate "
            "-H 'Content-Type: application/json' "
            "-d @-"
        ]
        
        print(f"Querying Ollama on MAGNETON...")
        print(f"Prompt size: {len(prompt)} chars")
        
        result = subprocess.run(
            ssh_cmd,
            input=prompt_json,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if not result.stdout or not result.stdout.strip():
            print(f"SSH error: {result.stderr}")
            return ""
        
        response_data = json.loads(result.stdout)
        return response_data.get("response", "")
        
    except Exception as e:
        print(f"Error querying Ollama: {e}")
        return ""

if __name__ == "__main__":
    REPORTS_DIR.mkdir(exist_ok=True)
    
    print("Analyzing local replays...")
    reviews, stats = analyze_local_replays(max_count=20)
    
    if not reviews:
        print("No reviews collected!")
        sys.exit(1)
    
    print(f"\n✅ Collected {len(reviews)} reviews")
    print(f"Stats: {stats['wins']}-{stats['losses']} ({stats['wins']/(stats['total'])*100:.1f}% WR)")
    
    prompt = build_prompt(reviews, stats)
    analysis = query_ollama(prompt)
    
    if not analysis:
        print("❌ Failed to get analysis from Ollama")
        sys.exit(1)
    
    # Save report
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = REPORTS_DIR / f"improvement_plan_001_{timestamp}.md"
    
    report = f"""# 📊 Fouler Play Improvement Plan #1

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Battles Analyzed:** {stats['analyzed']}
**Record:** {stats['wins']}-{stats['losses']} ({stats['wins']/(stats['total'])*100:.1f}% WR)

## AI Analysis

{analysis}

---

*Analysis powered by {OLLAMA_MODEL} on MAGNETON (192.168.1.181:11434)*
"""
    
    report_file.write_text(report)
    print(f"\n✅ Report saved: {report_file}")
    print("\n" + "="*60)
    print(report)
