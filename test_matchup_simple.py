#!/usr/bin/env python3
"""
Simple matchup analyzer test using heuristic fallback (fast, reliable).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fp.matchup_analyzer import analyze_matchup

# Test case 1: Balanced vs Hyper Offense
print("Test 1: Balanced vs Hyper Offense")
print("=" * 60)
our_team = [
    {"species": "Corviknight", "moves": ["Brave Bird", "Defog", "Roost", "U-turn"], 
     "item": "Rocky Helmet", "ability": "Pressure"},
    {"species": "Dondozo", "moves": ["Wave Crash", "Earthquake", "Order Up", "Rest"],
     "item": "Leftovers", "ability": "Unaware"},
    {"species": "Kyurem", "moves": ["Freeze-Dry", "Earth Power", "Substitute", "Protect"],
     "item": "Leftovers", "ability": "Pressure"},
    {"species": "Ting-Lu", "moves": ["Stealth Rock", "Earthquake", "Whirlwind", "Ruination"],
     "item": "Leftovers", "ability": "Vessel of Ruin"},
    {"species": "Slowking-Galar", "moves": ["Chilly Reception", "Sludge Bomb", "Flamethrower", "Psychic"],
     "item": "Assault Vest", "ability": "Regenerator"},
    {"species": "Cinderace", "moves": ["Pyro Ball", "U-turn", "Court Change", "Will-O-Wisp"],
     "item": "Heavy-Duty Boots", "ability": "Libero"},
]

opp_team = [
    {"species": "Iron Valiant", "moves": ["Close Combat", "Knock Off", "Destiny Bond", "Encore"],
     "item": "Booster Energy", "ability": "Quark Drive"},
    {"species": "Dragapult", "moves": ["Dragon Darts", "U-turn", "Will-O-Wisp", "Hex"],
     "item": "Life Orb", "ability": "Infiltrator"},
    {"species": "Raging Bolt", "moves": ["Thunderclap", "Draco Meteor", "Volt Switch", "Thunderbolt"],
     "item": "Booster Energy", "ability": "Protosynthesis"},
    {"species": "Roaring Moon", "moves": ["Knock Off", "Earthquake", "Dragon Dance", "Acrobatics"],
     "item": "Choice Band", "ability": "Protosynthesis"},
]

gameplan = analyze_matchup(our_team, opp_team, use_cache=False)
print(json.dumps(gameplan.to_dict(), indent=2))

print("\n\nTest 2: Stall vs Balance")
print("=" * 60)

our_stall = [
    {"species": "Blissey", "moves": ["Soft-Boiled", "Seismic Toss", "Toxic", "Stealth Rock"],
     "item": "Heavy-Duty Boots", "ability": "Natural Cure"},
    {"species": "Toxapex", "moves": ["Scald", "Recover", "Haze", "Toxic Spikes"],
     "item": "Black Sludge", "ability": "Regenerator"},
    {"species": "Corviknight", "moves": ["Brave Bird", "Defog", "Roost", "U-turn"],
     "item": "Leftovers", "ability": "Pressure"},
    {"species": "Ting-Lu", "moves": ["Stealth Rock", "Earthquake", "Whirlwind", "Ruination"],
     "item": "Leftovers", "ability": "Vessel of Ruin"},
]

opp_balance = [
    {"species": "Landorus-Therian", "moves": ["Stealth Rock", "Earthquake", "U-turn", "Stone Edge"],
     "item": "Rocky Helmet", "ability": "Intimidate"},
    {"species": "Great Tusk", "moves": ["Earthquake", "Ice Spinner", "Rapid Spin", "Knock Off"],
     "item": "Leftovers", "ability": "Protosynthesis"},
    {"species": "Gholdengo", "moves": ["Shadow Ball", "Make It Rain", "Nasty Plot", "Recover"],
     "item": "Air Balloon", "ability": "Good as Gold"},
]

gameplan2 = analyze_matchup(our_stall, opp_balance, use_cache=False)
print(json.dumps(gameplan2.to_dict(), indent=2))

print("\n✅ Both tests completed successfully!")
print("📊 Gameplans generated using heuristic analyzer (fallback system)")
print("🔧 LLM integration available but currently experiencing performance issues")
print("   Run with MATCHUP_ANALYZER_TIMEOUT=60 for better LLM success rate")
