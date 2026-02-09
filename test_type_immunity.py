#!/usr/bin/env python3
"""
Test type immunity in status threat detection.

Verifies that the bot correctly ignores status moves that can't affect us due to:
1. Type immunity (Ground vs Electric moves)
2. Powder immunity (Grass vs powder moves)
3. Status-type immunity (Electric can't be paralyzed, Fire can't be burned, etc.)
"""

import sys
from unittest.mock import MagicMock
from fp.search.main import get_opponent_status_threats

def test_type_immunities():
    """Test that type immunities are respected."""
    print("=" * 70)
    print("TYPE IMMUNITY TESTS")
    print("=" * 70)
    
    # Test 1: Ground type immune to Thunder Wave (Electric move)
    print("\n1. Gliscor (Ground/Flying) vs Thunder Wave")
    print("-" * 50)
    
    gliscor = MagicMock()
    gliscor.name = "Gliscor"
    gliscor.types = ["ground", "flying"]
    
    opponent = MagicMock()
    opponent.name = "Rotom-Wash"
    opponent.moves = []
    
    twave_move = MagicMock()
    twave_move.name = "Thunder Wave"
    opponent.moves = [twave_move]
    
    threats = get_opponent_status_threats(opponent, our_pokemon=gliscor)
    
    if "par" in threats:
        print("❌ FAIL: Thunder Wave incorrectly flagged as threat to Gliscor")
        print(f"   Threats detected: {threats}")
        return False
    else:
        print("✅ PASS: Thunder Wave correctly ignored (Ground immune to Electric)")
        print(f"   Threats detected: {threats}")
    
    # Test 2: Electric type can't be paralyzed
    print("\n2. Raichu (Electric) vs Thunder Wave")
    print("-" * 50)
    
    raichu = MagicMock()
    raichu.name = "Raichu"
    raichu.types = ["electric"]
    
    # Glare is Normal-type (not Electric), so it CAN hit Electric types
    # But Electric types still can't be paralyzed
    glare_move = MagicMock()
    glare_move.name = "Glare"
    opponent.moves = [glare_move]
    
    threats = get_opponent_status_threats(opponent, our_pokemon=raichu)
    
    if "par" in threats:
        print("❌ FAIL: Paralysis incorrectly flagged as threat to Electric type")
        print(f"   Threats detected: {threats}")
        return False
    else:
        print("✅ PASS: Paralysis correctly ignored (Electric type immunity)")
        print(f"   Threats detected: {threats}")
    
    # Test 3: Fire type can't be burned
    print("\n3. Charizard (Fire/Flying) vs Will-O-Wisp")
    print("-" * 50)
    
    charizard = MagicMock()
    charizard.name = "Charizard"
    charizard.types = ["fire", "flying"]
    
    wow_move = MagicMock()
    wow_move.name = "Will-O-Wisp"
    opponent.moves = [wow_move]
    
    threats = get_opponent_status_threats(opponent, our_pokemon=charizard)
    
    if "brn" in threats:
        print("❌ FAIL: Burn incorrectly flagged as threat to Fire type")
        print(f"   Threats detected: {threats}")
        return False
    else:
        print("✅ PASS: Burn correctly ignored (Fire type immunity)")
        print(f"   Threats detected: {threats}")
    
    # Test 4: Grass type immune to powder moves
    print("\n4. Venusaur (Grass/Poison) vs Spore")
    print("-" * 50)
    
    venusaur = MagicMock()
    venusaur.name = "Venusaur"
    venusaur.types = ["grass", "poison"]
    
    spore_move = MagicMock()
    spore_move.name = "Spore"
    opponent.moves = [spore_move]
    
    threats = get_opponent_status_threats(opponent, our_pokemon=venusaur)
    
    if "slp" in threats:
        print("❌ FAIL: Spore incorrectly flagged as threat to Grass type")
        print(f"   Threats detected: {threats}")
        return False
    else:
        print("✅ PASS: Spore correctly ignored (Grass immune to powder)")
        print(f"   Threats detected: {threats}")
    
    # Test 5: Steel type can't be poisoned
    print("\n5. Metagross (Steel/Psychic) vs Toxic")
    print("-" * 50)
    
    metagross = MagicMock()
    metagross.name = "Metagross"
    metagross.types = ["steel", "psychic"]
    
    toxic_move = MagicMock()
    toxic_move.name = "Toxic"
    opponent.moves = [toxic_move]
    
    threats = get_opponent_status_threats(opponent, our_pokemon=metagross)
    
    if "tox" in threats:
        print("❌ FAIL: Toxic incorrectly flagged as threat to Steel type")
        print(f"   Threats detected: {threats}")
        return False
    else:
        print("✅ PASS: Toxic correctly ignored (Steel type immunity)")
        print(f"   Threats detected: {threats}")
    
    # Test 6: Ice type can't be frozen
    print("\n6. Glaceon (Ice) vs Ice Beam")
    print("-" * 50)
    
    glaceon = MagicMock()
    glaceon.name = "Glaceon"
    glaceon.types = ["ice"]
    
    icebeam_move = MagicMock()
    icebeam_move.name = "Ice Beam"
    opponent.moves = [icebeam_move]
    
    threats = get_opponent_status_threats(opponent, our_pokemon=glaceon)
    
    if "frz" in threats:
        print("❌ FAIL: Freeze incorrectly flagged as threat to Ice type")
        print(f"   Threats detected: {threats}")
        return False
    else:
        print("✅ PASS: Freeze correctly ignored (Ice type immunity)")
        print(f"   Threats detected: {threats}")
    
    # Test 7: Poison type can't be poisoned
    print("\n7. Toxapex (Poison/Water) vs Toxic")
    print("-" * 50)
    
    toxapex = MagicMock()
    toxapex.name = "Toxapex"
    toxapex.types = ["poison", "water"]
    
    opponent.moves = [toxic_move]  # Reuse from Test 5
    
    threats = get_opponent_status_threats(opponent, our_pokemon=toxapex)
    
    if "tox" in threats:
        print("❌ FAIL: Toxic incorrectly flagged as threat to Poison type")
        print(f"   Threats detected: {threats}")
        return False
    else:
        print("✅ PASS: Toxic correctly ignored (Poison type immunity)")
        print(f"   Threats detected: {threats}")
    
    # Test 8: Normal type CAN be affected by Thunder Wave
    print("\n8. Snorlax (Normal) vs Thunder Wave (SHOULD be threatened)")
    print("-" * 50)
    
    snorlax = MagicMock()
    snorlax.name = "Snorlax"
    snorlax.types = ["normal"]
    
    opponent.moves = [twave_move]  # Reuse from Test 1
    
    threats = get_opponent_status_threats(opponent, our_pokemon=snorlax)
    
    if "par" not in threats:
        print("❌ FAIL: Thunder Wave should threaten Normal type")
        print(f"   Threats detected: {threats}")
        return False
    else:
        print("✅ PASS: Thunder Wave correctly threatens Normal type")
        print(f"   Threats detected: {threats}")
    
    return True


if __name__ == "__main__":
    print("Testing type immunity in status threat detection...")
    print()
    
    success = test_type_immunities()
    
    print("\n" + "=" * 70)
    if success:
        print("ALL TESTS PASSED ✅")
        print("=" * 70)
        sys.exit(0)
    else:
        print("SOME TESTS FAILED ❌")
        print("=" * 70)
        sys.exit(1)
