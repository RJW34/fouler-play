#!/usr/bin/env python3
"""Generate a test report using existing replay files."""

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.batch_analyzer import BatchAnalyzer

def main():
    analyzer = BatchAnalyzer()
    
    # Load existing replay JSONs
    replay_dir = PROJECT_ROOT / "replay_analysis"
    replay_files = sorted(replay_dir.glob("gen9ou-*.json"))[:5]
    
    if not replay_files:
        print("No replay files found")
        return None
    
    # Build reviews
    reviews = []
    wins = 0
    losses = 0
    
    for i, replay_file in enumerate(replay_files):
        replay_id = replay_file.stem
        replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
        
        replay_data = json.load(open(replay_file))
        turns = analyzer.reviewer.extract_full_turns(replay_data, replay_url)
        
        result = "win" if i % 2 == 0 else "loss"
        if result == "win":
            wins += 1
        else:
            losses += 1
        
        if turns:
            review_lines = [f"--- Battle: {replay_id} (Result: {result}) ---"]
            review_lines.append(f"Replay: {replay_url}")
            review_lines.append(f"\nTurn-by-turn breakdown:")
            
            for turn in turns[:8]:
                review_lines.append(
                    f"Turn {turn.turn_number}: {turn.bot_active} ({turn.bot_hp_percent:.0f}% HP) vs "
                    f"{turn.opp_active} ({turn.opp_hp_percent:.0f}% HP)"
                )
                review_lines.append(f"  Bot chose: {turn.bot_choice}")
                review_lines.append(f"  Context: {turn.why_critical[:80]}...")
                review_lines.append("")
            
            reviews.append("\n".join(review_lines))
    
    stats = {
        "total": len(replay_files),
        "wins": wins,
        "losses": losses,
        "teams": {
            "fat-team-1-stall": {"wins": 2, "losses": 0},
            "fat-team-2-pivot": {"wins": 0, "losses": 2},
            "fat-team-3-dondozo": {"wins": 1, "losses": 0}
        }
    }
    
    print(f"Collected {len(reviews)} reviews ({wins}-{losses})")
    
    # Build prompt and query Ollama
    prompt = analyzer.build_analysis_prompt(reviews, stats)
    print(f"Querying Ollama...")
    
    analysis = analyzer.query_ollama(prompt)
    
    if not analysis:
        print("Ollama query failed")
        return None
    
    print(f"Analysis received: {len(analysis)} chars")
    
    # Generate report
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    reports_dir = PROJECT_ROOT / "replay_analysis" / "reports"
    reports_dir.mkdir(exist_ok=True)
    
    batch_num = len(list(reports_dir.glob("batch_*.md"))) + 1
    report_file = reports_dir / f"batch_{batch_num:04d}_{timestamp}.md"
    
    report_content = f"""# Fouler Play Analysis Report - Batch {batch_num}

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Battles Analyzed:** {stats['total']} (TEST RUN)
**Record:** {wins}-{losses} ({wins/(wins+losses)*100:.1f}% WR)

## Team Performance

{analyzer._format_team_breakdown(stats['teams'])}

## AI Analysis

{analysis}

---

*Analysis powered by qwen2.5-coder:7b on MAGNETON*
*This is a TEST report using existing replay data*
"""
    
    report_file.write_text(report_content)
    print(f"\n✅ Report saved: {report_file}")
    return report_file

if __name__ == "__main__":
    report = main()
    sys.exit(0 if report else 1)
