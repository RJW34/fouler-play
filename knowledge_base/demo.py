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
    psychic = kb.get_move("psychic")
    if psychic:
        print(f"   Psychic: {psychic['power']} power, {psychic['type']} type")
        print(f"   Effects: {psychic.get('effects', [])}")
        print(f"   Common holders: {psychic.get('common_holders', [])}")
    print()
    
    # 2. Load matchup pattern
    print("2. Matchup Pattern:")
    matchup = kb.get_matchup("psychic_vs_fighting")
    if matchup:
        print(f"   Effectiveness: {matchup.get('offensive_effectiveness')}x")
        print(f"   Common switches: {matchup.get('common_switches', [])}")
        print(f"   Notes: {matchup.get('strategy_notes', '').strip()}")
    print()
    
    # 3. Load battle phase strategy
    print("3. Battle Phase Strategy:")
    early_game = kb.get_strategy("early_game")
    if early_game:
        print(f"   Turns: {early_game.get('turns')}")
        print(f"   Priorities: {early_game.get('priorities', [])}")
        print(f"   Common patterns: {early_game.get('common_patterns', [])}")
    print()
    
    # 4. Find moves by tag
    print("4. Moves Tagged 'setup':")
    setup_moves = kb.find_moves_with_tag("setup")
    for move_name in setup_moves:
        print(f"   - {move_name}")
    print()
    
    # 5. Show integration potential
    print("5. Integration Example:")
    print("   In decision logic, you could:")
    print("   - Check if opponent can switch to a counter type")
    print("   - Identify battle phase and adjust aggression")
    print("   - Recognize move tags for strategic scoring")
    print("   - Load common holders to predict sets")
    print()
    
    print("✅ Knowledge Base loaded successfully!")
    print(f"   Moves: {len(kb.moves)}")
    print(f"   Matchups: {len(kb.matchups)}")
    print(f"   Strategies: {len(kb.strategies)}")


if __name__ == "__main__":
    main()
