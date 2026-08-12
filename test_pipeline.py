#!/usr/bin/env python3
"""Test the pipeline using existing replay JSON files."""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.batch_analyzer import BatchAnalyzer

def test_with_local_replays():
    """Test analysis using local replay JSON files."""
    
    replay_dir = PROJECT_ROOT / "replay_analysis"
    replay_files = [
        path for path in sorted(replay_dir.glob("gen9ou-*.json"))
        if not path.stem.endswith("_gameplan")
    ][:5]
    
    if not replay_files:
        pytest.skip("No local replay files found")
    
    print(f"Found {len(replay_files)} local replay files for testing")
    
    analyzer = BatchAnalyzer()
    
    # Manually build reviews from local files
    reviews = []
    for replay_file in replay_files:
        replay_id = replay_file.stem
        replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
        
        print(f"Processing {replay_id}...")
        
        with replay_file.open(encoding="utf-8") as handle:
            replay_data = json.load(handle)
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

    assert reviews, "Expected at least one local replay review"
    assert "BATTLE REVIEWS:" in prompt
    assert "DO NOT hallucinate Pokemon knowledge" in prompt
    assert "Format response as structured improvement report" in prompt

if __name__ == "__main__":
    try:
        test_with_local_replays()
    except pytest.skip.Exception as exc:
        print(f"Skipped: {exc}")
    sys.exit(0)
