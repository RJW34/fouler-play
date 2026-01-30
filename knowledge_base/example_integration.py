#!/usr/bin/env python3
"""
Example integration of Knowledge Base into MCTS scoring.

This is a DEMONSTRATION of how KB data could enhance move selection.
NOT production code - just shows the patterns.
"""

from . import kb


def example_1_focus_sash_detection(opponent_pokemon, opponent_hp_percent):
    """
    Example: Detect Focus Sash and adjust move scoring.
    
    In actual MCTS, this would be called during move scoring.
    """
    # Check if opponent might have Focus Sash
    sash_data = kb.get_item("focus_sash")
    if not sash_data:
        return None
    
    # Check if opponent is common Sash holder and at full HP
    common_holders = sash_data.get("common_holders", [])
    has_sash_likely = opponent_pokemon.lower() in common_holders
    at_full_hp = opponent_hp_percent >= 1.0
    
    if has_sash_likely and at_full_hp:
        print(f"⚠️  {opponent_pokemon} likely has Focus Sash!")
        print(f"   Boost score for: {sash_data.get('countered_by', [])}")
        return {
            "situation": "focus_sash_detected",
            "boost_moves": ["multi_hit", "priority"],
            "reasoning": "Sash will survive first hit"
        }
    
    return None


def example_2_mold_breaker_detection(our_pokemon):
    """
    Example: Check if we have Mold Breaker and should ignore immunities.
    """
    # Get our Pokemon's ability (in real code, from battle state)
    mold_breaker = kb.get_ability("mold_breaker")
    if not mold_breaker:
        return False
    
    common_holders = mold_breaker.get("common_holders", [])
    has_mold_breaker = our_pokemon.lower() in common_holders
    
    if has_mold_breaker:
        ignored_abilities = mold_breaker.get("ignores", [])
        print(f"✓ {our_pokemon} has Mold Breaker!")
        print(f"   Ignoring {len(ignored_abilities)} abilities")
        return True
    
    return False


def example_3_situation_recognition(our_hp_percent, speed_comparison):
    """
    Example: Recognize battle situation and get optimal response.
    """
    # Check for "low HP + outsped" situation
    if our_hp_percent < 0.3 and speed_comparison == "slower":
        situation = kb.get_situation("low_hp_outsped")
        if situation:
            optimal = situation.get("optimal_response", [])
            if optimal:
                print(f"⚠️  Situation detected: Low HP + Outsped")
                print(f"   Optimal: {optimal[0].get('priority')}")
                print(f"   Reasoning: {situation.get('reasoning')}")
                return optimal[0].get('priority')
    
    return None


def example_4_matchup_scoring(our_type, opponent_type):
    """
    Example: Use matchup patterns to adjust switch scoring.
    """
    matchup_key = f"{our_type}_vs_{opponent_type}"
    matchup = kb.get_matchup(matchup_key)
    
    if matchup:
        effectiveness = matchup.get("offensive_effectiveness", 1.0)
        common_switches = matchup.get("common_switches", [])
        
        print(f"📊 Matchup: {our_type} vs {opponent_type}")
        print(f"   Effectiveness: {effectiveness}x")
        print(f"   Expect switches to: {', '.join(common_switches)}")
        
        # In real MCTS, would adjust switch candidate scores
        return {
            "effectiveness": effectiveness,
            "predicted_switches": common_switches,
            "boost_score": 0.2 if effectiveness > 1.0 else 0.0
        }
    
    return None


def example_5_battle_phase_adjustment(turn_count):
    """
    Example: Adjust aggression based on battle phase.
    """
    if turn_count <= 5:
        phase = kb.get_strategy("early_game")
        strategy = "set_hazards"
    elif turn_count <= 15:
        phase = kb.get_strategy("mid_game")
        strategy = "win_key_matchups"
    else:
        phase = kb.get_strategy("late_game")
        strategy = "preserve_wincon"
    
    if phase:
        priorities = phase.get("priorities", [])
        print(f"📅 Turn {turn_count}: {phase.get('turns')} ({strategy})")
        print(f"   Priorities: {', '.join(priorities[:3])}")
        return strategy
    
    return None


def example_6_move_tag_scoring(move_name):
    """
    Example: Score moves based on their tags.
    """
    move = kb.get_move(move_name)
    if not move:
        return 0.0
    
    tags = move.get("tags", [])
    score = 0.0
    
    # Example scoring rules
    if "reliable" in tags:
        score += 0.1
    if "priority" in tags:
        score += 0.15
    if "setup" in tags:
        score += 0.2  # Valuable in right situation
    if "risky" in tags:
        score -= 0.1
    
    print(f"🎯 {move_name}: tags={tags}, score_boost={score:+.2f}")
    return score


def run_examples():
    """Run all examples."""
    print("=== Knowledge Base Integration Examples ===\n")
    
    # Example 1: Focus Sash
    print("1. Focus Sash Detection")
    example_1_focus_sash_detection("Breloom", 1.0)
    print()
    
    # Example 2: Mold Breaker
    print("2. Mold Breaker Detection")
    example_2_mold_breaker_detection("Excadrill")
    print()
    
    # Example 3: Situation Recognition
    print("3. Situation Recognition")
    example_3_situation_recognition(0.25, "slower")
    print()
    
    # Example 4: Matchup Scoring
    print("4. Matchup Pattern Scoring")
    example_4_matchup_scoring("psychic", "fighting")
    print()
    
    # Example 5: Battle Phase
    print("5. Battle Phase Adjustment")
    example_5_battle_phase_adjustment(3)
    print()
    
    # Example 6: Move Tag Scoring
    print("6. Move Tag-Based Scoring")
    example_6_move_tag_scoring("extreme_speed")
    example_6_move_tag_scoring("scald")
    print()
    
    print("✅ All examples completed!")
    print("\nThese patterns can be integrated into fp/search/main.py")
    print("to enhance MCTS move scoring with domain knowledge.")


if __name__ == "__main__":
    run_examples()
