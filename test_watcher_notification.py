#!/usr/bin/env python3
"""Test the watcher notification system without requiring Ollama."""

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import Pipeline

def create_test_battle_data():
    """Create test battle_stats.json for cross-referencing."""
    # Generate 30 test battles with realistic distribution
    test_battles = []
    
    # Hazard-related losses (8 battles, fat-team-2-pivot)
    hazard_losses = [
        "2539805111", "2539810279", "2539815551", "2539820415",
        "2539825928", "2539832577", "2539839423", "2539845374"
    ]
    for i, battle_id in enumerate(hazard_losses):
        test_battles.append({
            "battle_id": f"battle-gen9ou-{battle_id}",
            "replay_id": f"battle-gen9ou-{battle_id}",
            "team_file": "fat-team-2-pivot",
            "result": "loss",
            "timestamp": f"2026-02-14T{10+i:02d}:00:00+00:00"
        })
    
    # Steel-type losses (4 battles, fat-team-1-stall)
    steel_losses = ["2539822342", "2539829275", "2539836046", "2539842797"]
    for i, battle_id in enumerate(steel_losses):
        test_battles.append({
            "battle_id": f"battle-gen9ou-{battle_id}",
            "replay_id": f"battle-gen9ou-{battle_id}",
            "team_file": "fat-team-1-stall",
            "result": "loss",
            "timestamp": f"2026-02-14T{18+i:02d}:00:00+00:00"
        })
    
    # Over-switching losses (2 battles, mixed teams)
    test_battles.extend([
        {
            "battle_id": "battle-gen9ou-2539850284",
            "replay_id": "battle-gen9ou-2539850284",
            "team_file": "fat-team-3-dondozo",
            "result": "loss",
            "timestamp": "2026-02-14T22:00:00+00:00"
        },
        {
            "battle_id": "battle-gen9ou-2539855112",
            "replay_id": "battle-gen9ou-2539855112",
            "team_file": "fat-team-1-stall",
            "result": "loss",
            "timestamp": "2026-02-14T23:00:00+00:00"
        }
    ])
    
    # Add 16 wins to make record 18-12
    win_teams = [
        "fat-team-1-stall", "fat-team-1-stall", "fat-team-1-stall", "fat-team-1-stall",
        "fat-team-1-stall", "fat-team-1-stall",  # 6 wins for stall
        "fat-team-2-pivot", "fat-team-2-pivot", "fat-team-2-pivot", "fat-team-2-pivot",
        "fat-team-2-pivot",  # 5 wins for pivot
        "fat-team-3-dondozo", "fat-team-3-dondozo", "fat-team-3-dondozo", "fat-team-3-dondozo",
        "fat-team-3-dondozo"  # 5 wins for dondozo
    ]
    
    for i, team in enumerate(win_teams):
        test_battles.append({
            "battle_id": f"battle-gen9ou-2539{860000 + i}",
            "replay_id": f"battle-gen9ou-2539{860000 + i}",
            "team_file": team,
            "result": "win",
            "timestamp": f"2026-02-15T{i:02d}:00:00+00:00"
        })
    
    # Save to temporary location for test
    test_stats_file = PROJECT_ROOT / "battle_stats_TEST.json"
    with open(test_stats_file, 'w') as f:
        json.dump({"battles": test_battles}, f, indent=2)
    
    print(f"✅ Created {len(test_battles)} test battles (14 losses, 16 wins)")
    return test_stats_file

def create_test_report():
    """Create a test report for notification testing."""
    reports_dir = PROJECT_ROOT / "replay_analysis" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_num = len(list(reports_dir.glob("batch_*.md"))) + 1
    report_file = reports_dir / f"batch_{batch_num:04d}_{timestamp}_TEST.md"
    
    # Create a realistic test report with improvements and code suggestions
    # Include battle IDs for cross-referencing
    report_content = f"""# Fouler Play Analysis Report - Batch {batch_num}

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Battles Analyzed:** 30 (WATCHER TEST)
**Record:** 18-12 (60.0% WR)

## Team Performance

- fat-team-1-stall: 8-2 (80.0%)
- fat-team-2-pivot: 5-5 (50.0%)
- fat-team-3-dondozo: 5-5 (50.0%)

## AI Analysis

### 1. TEAM COMPOSITION - Hazard Removal Critical

fat-team-2-pivot shows **hazard weakness** - no Defog/Rapid Spin. Lost 8/12 battles where opponent set up Stealth Rock + Spikes.

**Battles demonstrating this:** battle-gen9ou-2539805111, battle-gen9ou-2539810279, battle-gen9ou-2539815551, battle-gen9ou-2539820415, battle-gen9ou-2539825928, battle-gen9ou-2539832577, battle-gen9ou-2539839423, battle-gen9ou-2539845374

**Quick fix:**

```diff
# In teams/fat-team-2-pivot.txt
- Ability: Regenerator
+ Ability: Heavy Duty Boots

OR add dedicated remover:
+ Corviknight @ Leftovers
+ Ability: Pressure
+ - Defog
+ - Roost
+ - Brave Bird
+ - U-turn
```

Expected impact: **+15% WR** (eliminates 8/12 losses)

### 2. MATCHUP WEAKNESSES - Steel-Type Threats

**Steel-type wallbreakers** (Gholdengo, Kingambit) consistently break through fat-team-1-stall. The team lacks reliable answers to boosted attacks after Iron Defense. 

**Battles:** battle-gen9ou-2539822342, battle-gen9ou-2539829275, battle-gen9ou-2539836046, battle-gen9ou-2539842797

**Suggested code change:**

```python
# In battle_logic.py, add steel-type threat detection
def should_prioritize_counterplay(self, threat_pokemon):
    if threat_pokemon.type1 == 'Steel' and threat_pokemon.has_boost('defense'):
        # Use special attacker or phaser immediately
        return self.get_special_attackers() or self.get_phasers()
```

Expected impact: **+5% WR**

### 3. RECURRING MISTAKES - Over-Switching

The bot shows a pattern of **switching too conservatively** in winning positions. In battles battle-gen9ou-2539850284 and battle-gen9ou-2539855112, the bot maintained defensive switches even when holding a 2-Pokemon advantage.

**Recommendation:** Implement momentum tracking to identify when to press offensive advantage.

Expected impact: **+8% WR** (requires refactor)

### TOP 3 IMPROVEMENTS (Ranked by Impact)

1. **Add hazard removal to fat-team-2-pivot** - Expected +15% WR (8 losses → wins)
2. **Implement momentum-based aggression logic** - Expected +8% WR (requires code refactor)
3. **Add steel-type counterplay to fat-team-1-stall** - Expected +5% WR (4 losses affected)

---

*Analysis powered by qwen2.5-coder:7b on MAGNETON*
*This is a TEST report for notification verification*
"""
    
    report_file.write_text(report_content)
    print(f"✅ Test report created: {report_file}")
    return report_file

def main():
    print("🧪 Testing Fouler Play Watcher Enhanced Notification System\n")
    
    # Create test battle data
    test_stats_file = create_test_battle_data()
    
    # Create test report
    report_path = create_test_report()
    
    # Test Discord notification with test battle data
    print("\n📤 Testing enhanced Discord notification...")
    
    # Temporarily swap battle stats file for testing
    import pipeline as pipeline_module
    original_stats_file = pipeline_module.BATTLE_STATS_FILE
    pipeline_module.BATTLE_STATS_FILE = test_stats_file
    
    try:
        pipeline = Pipeline()
        pipeline.send_discord_notification(report_path)
        print("\n✅ Test complete! Check #deku-workspace for enhanced notification.")
        print("   Expected: 3 issue embeds with impact metrics, team breakdown, and example links")
    except Exception as e:
        print(f"\n❌ Notification test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Restore original stats file
        pipeline_module.BATTLE_STATS_FILE = original_stats_file
        # Clean up test file
        if test_stats_file.exists():
            test_stats_file.unlink()
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
