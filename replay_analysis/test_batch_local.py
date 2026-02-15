#!/usr/bin/env python3
"""
Test batch analyzer with local replay files.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.batch_analyzer import BatchAnalyzer
from replay_analysis.turn_review import TurnReviewer

REPLAY_DIR = PROJECT_ROOT / "replay_analysis"


def load_local_replays(num_replays: int = 5) -> List[tuple[str, Dict]]:
    """Load local replay JSON files."""
    replay_files = sorted(REPLAY_DIR.glob("gen9ou-*.json"))[:num_replays]
    
    replays = []
    for replay_file in replay_files:
        try:
            with open(replay_file, 'r') as f:
                data = json.load(f)
                replay_id = data.get("id", replay_file.stem)
                replays.append((replay_id, data))
        except Exception as e:
            print(f"Failed to load {replay_file}: {e}")
    
    return replays


def analyze_local_replays(num_replays: int = 5):
    """Test batch analysis with local replays."""
    print(f"Loading {num_replays} local replays...")
    replays = load_local_replays(num_replays)
    
    if not replays:
        print("No replays loaded!")
        return
    
    print(f"Loaded {len(replays)} replays\n")
    
    # Create analyzer components
    reviewer = TurnReviewer(bot_username="BugInTheCode")
    analyzer = BatchAnalyzer()
    
    # Extract turn reviews
    reviews = []
    stats = {"total": len(replays), "wins": 0, "losses": 0, "teams": {}}
    
    for replay_id, replay_data in replays:
        print(f"Analyzing {replay_id}...")
        
        # Extract turns
        try:
            turns = reviewer.extract_full_turns(replay_data, f"https://replay.pokemonshowdown.com/{replay_id}")
            
            if not turns:
                print(f"  No turns extracted")
                continue
            
            # Determine result from replay data
            result = "unknown"
            if "winner" in replay_data:
                winner = replay_data["winner"]
                if "BugInTheCode" in winner or "buginthecode" in winner.lower():
                    result = "win"
                    stats["wins"] += 1
                else:
                    result = "loss"
                    stats["losses"] += 1
            
            # Format review
            review_lines = [f"--- Battle: {replay_id} (Result: {result}) ---"]
            review_lines.append(f"Replay: https://replay.pokemonshowdown.com/{replay_id}")
            review_lines.append(f"\nTurn-by-turn breakdown:")
            
            for turn in turns[:10]:  # Limit to first 10 turns for brevity
                review_lines.append(
                    f"Turn {turn.turn_number}: {turn.bot_active} ({turn.bot_hp_percent:.0f}% HP) vs "
                    f"{turn.opp_active} ({turn.opp_hp_percent:.0f}% HP)"
                )
                review_lines.append(f"  Bot chose: {turn.bot_choice}")
                review_lines.append(f"  Context: {turn.why_critical}")
                review_lines.append("")
            
            reviews.append("\n".join(review_lines))
            print(f"  ✓ Extracted {len(turns)} turns")
            
        except Exception as e:
            print(f"  Error: {e}")
    
    if not reviews:
        print("\nNo reviews generated!")
        return
    
    print(f"\n{'='*60}")
    print(f"Collected {len(reviews)} reviews")
    print(f"Stats: {stats['wins']}-{stats['losses']}")
    print(f"{'='*60}\n")
    
    # Build prompt
    prompt = analyzer.build_analysis_prompt(reviews, stats)
    
    print("Sending to Ollama on MAGNETON...")
    print(f"Prompt length: {len(prompt)} chars\n")
    
    # Query Ollama
    analysis = analyzer.query_ollama(prompt)
    
    if not analysis:
        print("\n❌ Failed to get Ollama response")
        return
    
    print(f"\n{'='*60}")
    print("AI ANALYSIS RESULT:")
    print(f"{'='*60}\n")
    print(analysis)
    
    # Save test report
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = REPLAY_DIR / "reports" / f"test_local_{timestamp}.md"
    
    report_content = f"""# Fouler Play Test Analysis (Local Replays)

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Replays Analyzed:** {len(replays)}
**Record:** {stats['wins']}-{stats['losses']}

## AI Analysis

{analysis}

---

*Test analysis powered by qwen2.5-coder:7b on MAGNETON*
"""
    
    report_file.write_text(report_content)
    print(f"\n✅ Test report saved: {report_file}")
    print(f"\nView with: cat {report_file}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test batch analyzer with local replays")
    parser.add_argument("-n", "--num-replays", type=int, default=5,
                        help="Number of local replays to analyze")
    args = parser.parse_args()
    
    analyze_local_replays(args.num_replays)
