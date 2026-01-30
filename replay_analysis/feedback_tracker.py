#!/usr/bin/env python3
"""
Feedback Tracker - Records expert feedback on bot decisions
Converts feedback into actionable heuristic improvements
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional


class FeedbackTracker:
    """Tracks and analyzes expert feedback on bot decisions"""
    
    def __init__(self):
        self.feedback_dir = Path("/home/ryan/projects/fouler-play/replay_analysis/feedback")
        self.feedback_dir.mkdir(exist_ok=True)
        self.feedback_file = self.feedback_dir / "feedback_log.jsonl"
        
    def record_feedback(
        self,
        turn_number: int,
        replay_url: str,
        bot_choice: str,
        expert_says_correct: bool,
        expert_suggested: Optional[str] = None,
        expert_reasoning: Optional[str] = None
    ):
        """Record expert feedback on a specific turn"""
        
        feedback_entry = {
            "timestamp": datetime.now().isoformat(),
            "turn": turn_number,
            "replay_url": replay_url,
            "bot_choice": bot_choice,
            "correct": expert_says_correct,
            "suggested_alternative": expert_suggested,
            "reasoning": expert_reasoning
        }
        
        # Append to JSONL file
        with open(self.feedback_file, 'a') as f:
            f.write(json.dumps(feedback_entry) + '\n')
            
        return feedback_entry
    
    def get_feedback_summary(self) -> Dict:
        """Generate summary statistics from all feedback"""
        if not self.feedback_file.exists():
            return {"total": 0, "correct": 0, "incorrect": 0}
            
        total = 0
        correct = 0
        incorrect = 0
        common_mistakes = {}
        
        with open(self.feedback_file) as f:
            for line in f:
                if not line.strip():
                    continue
                entry = json.loads(line)
                total += 1
                
                if entry["correct"]:
                    correct += 1
                else:
                    incorrect += 1
                    # Track common mistake patterns
                    bot_choice = entry["bot_choice"]
                    if bot_choice not in common_mistakes:
                        common_mistakes[bot_choice] = 0
                    common_mistakes[bot_choice] += 1
        
        return {
            "total_reviews": total,
            "correct_decisions": correct,
            "incorrect_decisions": incorrect,
            "accuracy": (correct / total * 100) if total > 0 else 0,
            "common_mistakes": sorted(
                common_mistakes.items(),
                key=lambda x: -x[1]
            )[:5]  # Top 5 mistakes
        }
    
    def get_improvement_suggestions(self) -> List[str]:
        """
        Analyze feedback to generate actionable improvement suggestions
        """
        if not self.feedback_file.exists():
            return []
            
        suggestions = []
        switch_mistakes = 0
        setup_mistakes = 0
        staying_in_mistakes = 0
        
        with open(self.feedback_file) as f:
            for line in f:
                if not line.strip():
                    continue
                entry = json.loads(line)
                
                if entry["correct"]:
                    continue  # Only analyze mistakes
                
                bot_choice = entry["bot_choice"].lower()
                reasoning = (entry.get("reasoning") or "").lower()
                
                # Pattern detection
                if "switched" in bot_choice:
                    switch_mistakes += 1
                    
                if "swords dance" in bot_choice or "nasty plot" in bot_choice or "calm mind" in bot_choice:
                    setup_mistakes += 1
                    
                if "should have switched" in reasoning:
                    staying_in_mistakes += 1
        
        # Generate suggestions based on patterns
        if switch_mistakes > 3:
            suggestions.append(
                f"❌ Bot switching too aggressively ({switch_mistakes} bad switches) - "
                "increase switch penalty or improve matchup evaluation"
            )
            
        if setup_mistakes > 2:
            suggestions.append(
                f"❌ Bot setting up at wrong times ({setup_mistakes} bad setups) - "
                "add opponent team strength check before setup moves"
            )
            
        if staying_in_mistakes > 3:
            suggestions.append(
                f"❌ Bot staying in bad matchups ({staying_in_mistakes} cases) - "
                "reduce switch penalty or improve threat recognition"
            )
        
        return suggestions


if __name__ == "__main__":
    # Test
    tracker = FeedbackTracker()
    
    # Example feedback
    tracker.record_feedback(
        turn_number=7,
        replay_url="https://replay.pokemonshowdown.com/example",
        bot_choice="Switched to Skarmory",
        expert_says_correct=False,
        expert_suggested="Stay in with Gliscor and use Earthquake",
        expert_reasoning="Gliscor outspeeds and OHKOs Zamazenta with Earthquake"
    )
    
    print("Feedback Summary:")
    print(json.dumps(tracker.get_feedback_summary(), indent=2))
    
    print("\nImprovement Suggestions:")
    for suggestion in tracker.get_improvement_suggestions():
        print(f"  {suggestion}")
