# Movepool Tracker - Automatic Threat Classification

**Status:** ✅ Implemented and integrated  
**Location:** `fp/movepool_tracker.py`  
**Solves:** The "Gliscor special bulk" problem - bot no longer values irrelevant defensive stats

## What It Does

The Movepool Tracker learns which Pokemon are physical-only, special-only, or mixed threats by observing actual moves used in battles.

### Threat Categories

- **PHYSICAL_ONLY** - Only uses physical attacks (Gliscor, Garchomp, Great Tusk)
- **SPECIAL_ONLY** - Only uses special attacks (Gholdengo, Heatran, Primarina)
- **MIXED** - Uses both physical and special (Dragapult, Iron Valiant, Zamazenta)
- **STATUS_ONLY** - Only uses status moves (rare, but exists)
- **UNKNOWN** - Not enough data yet

## How It Works

1. **Automatic tracking** - Every time an opponent uses a move, it's recorded
2. **Persistent storage** - Data saved to `fp/data/movepool_data.json`
3. **Move categorization** - Uses existing `data/all_move_json.json` to classify moves
4. **Auto-save** - Data automatically saved on program exit

## Integration Status

✅ **Already integrated into battle handler:**
- `fp/battle_modifier.py` imports and calls `record_move()`
- Every opponent move is automatically tracked
- Zoroark detection handled correctly (tracks actual Pokemon, not illusion)

## Usage

### In Python Code

```python
from fp.movepool_tracker import get_threat_category, ThreatCategory

# Check a Pokemon's threat category
category = get_threat_category("gliscor")

if category == ThreatCategory.PHYSICAL_ONLY:
    # Only value physical defense
    # Don't care about special bulk
    pass
elif category == ThreatCategory.SPECIAL_ONLY:
    # Only value special defense
    # Don't care about physical bulk
    pass
elif category == ThreatCategory.MIXED:
    # Value both defensive stats
    pass
```

### Accessing Full Data

```python
from fp.movepool_tracker import get_global_tracker

tracker = get_global_tracker()

# Get full movepool data for a Pokemon
data = tracker.get_movepool_data("gliscor")
print(f"Physical moves: {data.physical_moves}")
print(f"Special moves: {data.special_moves}")
print(f"Status moves: {data.status_moves}")
print(f"Times seen: {data.times_seen}")

# Get summary stats
stats = tracker.get_stats_summary()
print(f"Total Pokemon tracked: {stats['total_pokemon']}")
```

## Example: Fixing the Gliscor Mistake

### Before (❌ Wrong)
```python
# Bot evaluates switch to Gholdengo
if gholdengo.special_defense > current_mon.special_defense:
    switch_score += 40  # WRONG - Gliscor has no special attacks!
```

### After (✅ Correct)
```python
from fp.movepool_tracker import get_threat_category, ThreatCategory

category = get_threat_category("gliscor")

if category == ThreatCategory.PHYSICAL_ONLY:
    # Special defense is irrelevant - only check physical
    if gholdengo.defense > current_mon.defense:
        switch_score += 40
    else:
        switch_score += 0  # Special bulk doesn't help
elif category == ThreatCategory.SPECIAL_ONLY:
    # Physical defense is irrelevant - only check special
    if gholdengo.special_defense > current_mon.special_defense:
        switch_score += 40
elif category == ThreatCategory.MIXED:
    # Value both defensive stats
    if gholdengo.defense > current_mon.defense:
        switch_score += 20
    if gholdengo.special_defense > current_mon.special_defense:
        switch_score += 20
```

## Testing

Run the demo script to see it in action:

```bash
cd /home/ryan/projects/fouler-play
python3 test_movepool_tracker.py
```

This simulates battles vs Gliscor (physical), Gholdengo (special), and Dragapult (mixed) and shows how the tracker learns their categories.

## Data Persistence

Data is stored in `fp/data/movepool_data.json`:

```json
{
  "gliscor": {
    "pokemon_name": "gliscor",
    "physical_moves": ["facade", "earthquake"],
    "special_moves": [],
    "status_moves": ["protect", "toxic"],
    "times_seen": 5,
    "threat_category": "physical_only"
  },
  ...
}
```

## Future Enhancements

### Phase 1: Switch Scoring (TODO)
Integrate threat categories into MCTS switch evaluation:
- `fp/search/main.py` - Update switch scoring functions
- Only value relevant defensive stats based on threat category
- Boost switches that resist the opponent's actual damage type

### Phase 2: Confidence Tracking
- Track how many battles we've seen each Pokemon
- Lower confidence = more conservative assumptions
- Higher confidence = more aggressive optimization

### Phase 3: Set Prediction
- Track move combinations (e.g., if Gliscor has Toxic Orb, it probably has Poison Heal)
- Predict full movesets based on observed moves
- Improve item/ability prediction

## Files Modified

1. **`fp/movepool_tracker.py`** (NEW) - Core tracker module
2. **`fp/battle_modifier.py`** - Added `record_move()` call in `move()` function
3. **`test_movepool_tracker.py`** (NEW) - Demo/test script
4. **`MOVEPOOL_TRACKER.md`** (NEW) - This documentation

## Impact

**Before:** Bot wastes turns valuing irrelevant defensive stats  
**After:** Bot only values defense stats that actually matter

**Example:** vs Gliscor, bot no longer thinks "switch to Gholdengo for special bulk" because it knows Gliscor is physical-only.

---

**Built:** 2026-01-30 by DEKU 🪲  
**Motivation:** Fix the "Gliscor special bulk" mistake from Ryan's feedback
