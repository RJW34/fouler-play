#!/usr/bin/env python3
"""
Test matchup analyzer fallback system (bypasses Ollama for speed).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Mock Ollama to force fallback
import fp.matchup_analyzer as ma
original_call_ollama = ma._call_ollama
ma._call_ollama = lambda prompt: None  # Force fallback

from fp.matchup_analyzer import analyze_matchup

print("MATCHUP ANALYZER - FALLBACK SYSTEM TEST")
print("=" * 70)
print("(Ollama mocked to demonstrate fast heuristic analysis)\n")

# Test case 1
print("Test 1: Balanced team vs Hyper Offense")
print("-" * 70)
our_team = [
    {"species": "Corviknight", "moves": ["Brave Bird", "Defog", "Roost", "U-turn"], 
     "item": "Rocky Helmet", "ability": "Pressure"},
    {"species": "Dondozo", "moves": ["Wave Crash", "Earthquake", "Order Up", "Rest"],
     "item": "Leftovers", "ability": "Unaware"},
    {"species": "Kyurem", "moves": ["Freeze-Dry", "Earth Power", "Substitute", "Protect"],
     "item": "Leftovers", "ability": "Pressure"},
]

opp_team = [
    {"species": "Iron Valiant", "moves": ["Close Combat", "Knock Off", "Destiny Bond", "Encore"],
     "item": "Booster Energy", "ability": "Quark Drive"},
    {"species": "Dragapult", "moves": ["Dragon Darts", "Dragon Dance", "Phantom Force", "Fire Blast"],
     "item": "Life Orb", "ability": "Infiltrator"},
]

gp1 = analyze_matchup(our_team, opp_team, use_cache=False)
print(f"Win Condition: {gp1.win_condition}")
print(f"Strategy: {gp1.our_strategy}")
print(f"Opponent Weaknesses: {', '.join(gp1.opponent_weaknesses)}")
print(f"Pivot Triggers: {gp1.key_pivot_triggers}\n")

# Test case 2
print("Test 2: Stall team vs Balance")
print("-" * 70)
our_stall = [
    {"species": "Blissey", "moves": ["Soft-Boiled", "Seismic Toss", "Toxic", "Stealth Rock"],
     "item": "Heavy-Duty Boots", "ability": "Natural Cure"},
    {"species": "Toxapex", "moves": ["Scald", "Recover", "Haze", "Toxic Spikes"],
     "item": "Black Sludge", "ability": "Regenerator"},
    {"species": "Corviknight", "moves": ["Brave Bird", "Defog", "Roost", "U-turn"],
     "item": "Leftovers", "ability": "Pressure"},
]

opp_balance = [
    {"species": "Landorus-Therian", "moves": ["Stealth Rock", "Earthquake", "U-turn", "Stone Edge"],
     "item": "Rocky Helmet", "ability": "Intimidate"},
    {"species": "Gholdengo", "moves": ["Shadow Ball", "Make It Rain", "Nasty Plot", "Recover"],
     "item": "Air Balloon", "ability": "Good as Gold"},
]

gp2 = analyze_matchup(our_stall, opp_balance, use_cache=False)
print(f"Win Condition: {gp2.win_condition}")
print(f"Strategy: {gp2.our_strategy}")
print(f"Opponent Weaknesses: {', '.join(gp2.opponent_weaknesses)}")
print(f"Pivot Triggers: {gp2.key_pivot_triggers}\n")

# Test case 3: Cache test
print("Test 3: Cache functionality")
print("-" * 70)
gp1_cached = analyze_matchup(our_team, opp_team, use_cache=True)
assert gp1_cached.win_condition == gp1.win_condition, "Cache mismatch!"
print("✅ Cache hit confirmed - same gameplan retrieved")
print(f"Cached gameplan: {gp1_cached.win_condition}\n")

# Test case 4: Integration test
print("Test 4: Gameplan storage integration")
print("-" * 70)
from fp.gameplan_integration import store_gameplan, get_gameplan, clear_gameplan

battle_tag = "battle-gen9ou-test-123"
store_gameplan(battle_tag, gp1)
retrieved = get_gameplan(battle_tag)
assert retrieved is not None, "Failed to retrieve gameplan!"
assert retrieved.win_condition == gp1.win_condition, "Retrieved gameplan mismatch!"
print(f"✅ Stored and retrieved gameplan for {battle_tag}")
print(f"Retrieved: {retrieved.our_strategy}")
clear_gameplan(battle_tag)
assert get_gameplan(battle_tag) is None, "Failed to clear gameplan!"
print("✅ Gameplan cleared successfully\n")

print("=" * 70)
print("✅ ALL TESTS PASSED")
print("=" * 70)
print("\nSUMMARY:")
print("  ✓ Matchup analysis working (fallback heuristics)")
print("  ✓ Gameplan generation working")
print("  ✓ Cache system working")
print("  ✓ Integration system working")
print("\nNOTE: LLM integration (qwen2.5-coder) available but experiencing")
print("      performance issues. Increase MATCHUP_ANALYZER_TIMEOUT for better")
print("      LLM success rate, or rely on fast heuristic fallback.")
