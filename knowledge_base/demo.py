#!/usr/bin/env python3
"""
Demo script showing Knowledge Base usage.

Run: python -m knowledge_base.demo
"""

from knowledge_base import kb


def main():
    print("=== Fouler Play Knowledge Base Demo ===\n")
    
    # 1. Load move data
    print("1. Move Knowledge:")
    scald = kb.get_move("scald")
    if scald:
        print(f"   Scald: {scald['power']} power, {scald['type']} type")
        print(f"   Effects: Burn chance {scald.get('effects', [{}])[0].get('chance', 0)*100}%")
        print(f"   Tags: {', '.join(scald.get('tags', []))}")
    print()
    
    # 2. Priority moves
    print("2. Priority Move Lookup:")
    priority_moves = kb.find_moves_with_tag("priority")
    print(f"   Found {len(priority_moves)} priority moves")
    extreme_speed = kb.get_move("extreme_speed")
    if extreme_speed:
        print(f"   Extreme Speed: +{extreme_speed['priority']} priority, {extreme_speed['power']} power")
        print(f"   Common holders: {', '.join(extreme_speed.get('common_holders', []))}")
    print()
    
    # 3. Ability knowledge
    print("3. Ability Knowledge:")
    mold_breaker = kb.get_ability("mold_breaker")
    if mold_breaker:
        print(f"   Mold Breaker: {mold_breaker.get('description')}")
        print(f"   Common holders: {', '.join(mold_breaker.get('common_holders', []))}")
        print(f"   Ignores: {len(mold_breaker.get('ignores', []))} abilities")
    print()
    
    # 4. Item knowledge
    print("4. Item Knowledge:")
    focus_sash = kb.get_item("focus_sash")
    if focus_sash:
        print(f"   Focus Sash: {focus_sash.get('description')}")
        print(f"   Common holders: {', '.join(focus_sash.get('common_holders', [])[:5])}...")
        print(f"   Countered by: {', '.join(focus_sash.get('countered_by', []))}")
    print()
    
    # 5. Load matchup pattern
    print("5. Matchup Pattern:")
    matchup = kb.get_matchup("psychic_vs_fighting")
    if matchup:
        print(f"   Psychic vs Fighting: {matchup.get('offensive_effectiveness')}x")
        print(f"   Common switches: {', '.join(matchup.get('common_switches', []))}")
    print()
    
    # 6. Battle phase strategy
    print("6. Battle Phase Strategy:")
    early_game = kb.get_strategy("early_game")
    if early_game:
        print(f"   Early game ({early_game.get('turns')} turns)")
        print(f"   Priorities: {', '.join(early_game.get('priorities', []))}")
    print()
    
    # 7. Find moves by tag
    print("7. Tag-based Search:")
    setup_moves = kb.find_moves_with_tag("setup")
    print(f"   Setup moves: {', '.join(setup_moves.keys())}")
    pivot_moves = kb.find_moves_with_tag("pivot")
    print(f"   Pivot moves: {', '.join(pivot_moves.keys())}")
    print()
    
    # 8. Situation patterns
    print("8. Situation Recognition:")
    low_hp_outsped = kb.get_situation("low_hp_outsped")
    if low_hp_outsped:
        print(f"   Low HP + Outsped situation detected")
        print(f"   Optimal: {low_hp_outsped.get('optimal_response', [{}])[0].get('priority')}")
        print(f"   Reasoning: {low_hp_outsped.get('reasoning', '')[:60]}...")
    print()
    
    # 9. Show integration potential
    print("9. Integration Example:")
    print("   In MCTS scoring, you could:")
    print("   - Detect situation patterns (low HP + outsped → use priority)")
    print("   - Check if opponent likely has Focus Sash (common holders)")
    print("   - Avoid status moves vs Substitute (situation recognition)")
    print("   - Detect Mold Breaker and skip immunity penalties")
    print("   - Identify battle phase and adjust aggression")
    print("   - Score moves by tags (setup, priority, pivot)")
    print()
    
    print("✅ Knowledge Base loaded successfully!")
    print(f"   Moves: {len(kb.moves)}")
    print(f"   Abilities: {len(kb.abilities)}")
    print(f"   Items: {len(kb.items)}")
    print(f"   Matchups: {len(kb.matchups)}")
    print(f"   Strategies: {len(kb.strategies)}")
    print(f"   Situations: {len(kb.situations)}")


if __name__ == "__main__":
    main()
