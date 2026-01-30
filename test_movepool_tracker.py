#!/usr/bin/env python3
"""
Demo/test script for the movepool tracker system

Shows how the tracker learns Pokemon threat categories from battle data.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from fp.movepool_tracker import MovepoolTracker, ThreatCategory


def demo_movepool_learning():
    """Demonstrate the movepool tracker learning Pokemon categories"""
    
    print("=" * 70)
    print("MOVEPOOL TRACKER DEMO - Learning Pokemon Threat Categories")
    print("=" * 70)
    print()
    
    # Create a fresh tracker (will use default data file or load existing)
    tracker = MovepoolTracker()
    
    print("📋 Simulating battle observations...")
    print()
    
    # Simulate observing Gliscor in a battle (physical-only)
    print("Battle 1: vs Gliscor")
    print("  - Turn 3: Gliscor used Earthquake")
    tracker.record_move("gliscor", "earthquake")
    print("  - Turn 5: Gliscor used Protect")
    tracker.record_move("gliscor", "protect")
    print("  - Turn 7: Gliscor used Toxic")
    tracker.record_move("gliscor", "toxic")
    print("  - Turn 9: Gliscor used Facade")
    tracker.record_move("gliscor", "facade")
    tracker.record_battle_appearance("gliscor")
    print()
    
    # Check what we learned about Gliscor
    category = tracker.get_threat_category("gliscor")
    data = tracker.get_movepool_data("gliscor")
    print(f"✅ Learned: Gliscor is {category.value}")
    print(f"   Physical moves: {data.physical_moves}")
    print(f"   Special moves: {data.special_moves}")
    print(f"   Status moves: {data.status_moves}")
    print()
    
    # Simulate observing Gholdengo (special-only)
    print("Battle 2: vs Gholdengo")
    print("  - Turn 2: Gholdengo used Shadow Ball")
    tracker.record_move("gholdengo", "shadowball")
    print("  - Turn 4: Gholdengo used Make It Rain")
    tracker.record_move("gholdengo", "makeitrain")
    print("  - Turn 6: Gholdengo used Recover")
    tracker.record_move("gholdengo", "recover")
    tracker.record_battle_appearance("gholdengo")
    print()
    
    category = tracker.get_threat_category("gholdengo")
    data = tracker.get_movepool_data("gholdengo")
    print(f"✅ Learned: Gholdengo is {category.value}")
    print(f"   Physical moves: {data.physical_moves}")
    print(f"   Special moves: {data.special_moves}")
    print(f"   Status moves: {data.status_moves}")
    print()
    
    # Simulate observing Dragapult (mixed)
    print("Battle 3: vs Dragapult")
    print("  - Turn 2: Dragapult used Dragon Darts")
    tracker.record_move("dragapult", "dragondarts")
    print("  - Turn 5: Dragapult used Shadow Ball")
    tracker.record_move("dragapult", "shadowball")
    print("  - Turn 8: Dragapult used U-turn")
    tracker.record_move("dragapult", "uturn")
    tracker.record_battle_appearance("dragapult")
    print()
    
    category = tracker.get_threat_category("dragapult")
    data = tracker.get_movepool_data("dragapult")
    print(f"✅ Learned: Dragapult is {category.value}")
    print(f"   Physical moves: {data.physical_moves}")
    print(f"   Special moves: {data.special_moves}")
    print(f"   Status moves: {data.status_moves}")
    print()
    
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    tracker.print_summary()
    
    # Save the data
    print()
    print(f"💾 Saving movepool data to {tracker.data_file}")
    tracker.save()
    print()
    
    # Show how to use this in switch logic
    print("=" * 70)
    print("USAGE IN SWITCH LOGIC")
    print("=" * 70)
    print()
    print("Example: Deciding whether to switch vs Gliscor")
    print()
    print("❌ BAD reasoning (before movepool tracking):")
    print("   'Switch to Gholdengo for better special bulk'")
    print("   -> Gliscor has NO special attacks, special bulk is irrelevant!")
    print()
    print("✅ GOOD reasoning (with movepool tracking):")
    category = tracker.get_threat_category("gliscor")
    print(f"   1. Check threat category: {category.value}")
    print("   2. Gliscor is PHYSICAL_ONLY")
    print("   3. Only value PHYSICAL defense in switch scoring")
    print("   4. Also consider type immunities (Flying immune to Ground)")
    print()
    print("This prevents the bot from making the 'Gholdengo special bulk' mistake!")
    print()


if __name__ == "__main__":
    demo_movepool_learning()
