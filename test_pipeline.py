#!/usr/bin/env python3
"""Test the pipeline using existing replay JSON files."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.batch_analyzer import BatchAnalyzer

def test_with_local_replays():
    """Test analysis using local replay JSON files."""
    
    replay_dir = PROJECT_ROOT / "replay_analysis"
    replay_files = sorted(replay_dir.glob("gen9ou-*.json"))[:5]
    
    if not replay_files:
        print("No local replay files found!")
        return False
    
    print(f"Found {len(replay_files)} local replay files for testing")
    
    analyzer = BatchAnalyzer()
    
    # Manually build reviews from local files
    reviews = []
    for replay_file in replay_files:
        replay_id = replay_file.stem
        replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
        
        print(f"Processing {replay_id}...")
        
        replay_data = json.load(open(replay_file))
        turns = analyzer.reviewer.extract_full_turns(replay_data, replay_url)
        
        if turns:
            review_lines = [f"Replay: {replay_url}"]
            review_lines.append(f"\nTurn-by-turn breakdown:")
            
            for turn in turns[:10]:  # Limit to first 10 turns
                review_lines.append(
                    f"Turn {turn.turn_number}: {turn.bot_active} ({turn.bot_hp_percent:.0f}% HP) vs "
                    f"{turn.opp_active} ({turn.opp_hp_percent:.0f}% HP)"
                )
                review_lines.append(f"  Bot chose: {turn.bot_choice}")
                review_lines.append(f"  Context: {turn.why_critical[:100]}...")
                review_lines.append("")
            
            reviews.append("\n".join(review_lines))
    
    # Build test stats
    stats = {
        "total": len(replay_files),
        "wins": 2,
        "losses": 3,
        "teams": {
            "test-team-1": {"wins": 1, "losses": 1},
            "test-team-2": {"wins": 1, "losses": 2}
        }
    }
    
    print(f"\nCollected {len(reviews)} reviews")
    
    # Build prompt
    prompt = analyzer.build_analysis_prompt(reviews, stats)
    print(f"Prompt length: {len(prompt)} chars")
    
    # Test Ollama
    print("\n" + "="*60)
    print("Testing Ollama connection on MAGNETON...")
    print("="*60)
    
    analysis = analyzer.query_ollama(prompt)
    
    if analysis:
        print(f"\n✅ Ollama analysis received ({len(analysis)} chars)")
        print("\n" + "="*60)
        print("ANALYSIS PREVIEW:")
        print("="*60)
        print(analysis[:500] + "..." if len(analysis) > 500 else analysis)
        print("="*60)
        return True
    else:
        print("\n❌ Ollama analysis failed")
        return False

if __name__ == "__main__":
    success = test_with_local_replays()
    sys.exit(0 if success else 1)
