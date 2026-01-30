# Critical Bugs - Immediate Fixes Needed

## 🚨 BUG #1: Whirlwind on Good as Gold Gholdengo (Turn 57)

**Reported:** 2026-01-30 15:26 by Ryan
**Severity:** HIGH - Wasted turn, ability ignorance

**What happened:**
Bot used Whirlwind on Gholdengo (Good as Gold ability)

**Why it's wrong:**
- **Good as Gold** blocks ALL status moves
- Whirlwind is a status move (phazing)
- Move fails 100% of the time
- Completely wasted turn

**Good as Gold blocks:**
- Whirlwind, Roar, Dragon Tail (phazing)
- Toxic, Thunder Wave, Will-O-Wisp (status)
- Stealth Rock, Spikes, Sticky Web (hazards)
- Taunt, Encore, Trick (other status)

**What bot should do:**
- ❌ NEVER click status moves vs Good as Gold
- ✅ Only use attacking moves
- ✅ Switch out if no good attacking moves

### Root Cause

**Missing ability-move interaction check**

Current code likely doesn't check:
```python
if opponent.ability == "goodasgold" and move.category == "status":
    move_is_blocked = True
    move_score = 0  # Don't even consider it
```

### Fix Required

**Location:** `fp/move_scoring.py` or `fp/battle_state.py`

**Implementation:**
```python
# Ability-based move blocking
MOVE_BLOCKING_ABILITIES = {
    "goodasgold": {
        "blocks": ["status"],  # Blocks all status moves
        "description": "Gholdengo - blocks all status moves"
    },
    "bulletproof": {
        "blocks": ["bullet", "bomb"],  # Blocks ball/bomb moves
        "moves": ["shadowball", "energyball", "seedbomb", "weatherball", "etc"]
    },
    "soundproof": {
        "blocks": ["sound"],
        "moves": ["boomburst", "hypervoice", "perishsong", "etc"]
    },
    "queenlymajesty": {
        "blocks": ["priority"],
        "description": "Blocks priority moves"
    },
    "dazzling": {
        "blocks": ["priority"],
        "description": "Blocks priority moves"
    },
    "aromaveil": {
        "blocks": ["mental"],
        "moves": ["taunt", "encore", "disable", "etc"]
    }
}

def can_move_hit(move, opponent):
    """Check if move can hit opponent based on ability"""
    opponent_ability = opponent.ability.replace("-", "").lower()
    
    if opponent_ability not in MOVE_BLOCKING_ABILITIES:
        return True  # No blocking ability
    
    blocking_data = MOVE_BLOCKING_ABILITIES[opponent_ability]
    
    # Check category blocking (e.g., Good as Gold blocks all status)
    if "blocks" in blocking_data:
        if move.category in blocking_data["blocks"]:
            return False  # Move is blocked!
    
    # Check specific move blocking
    if "moves" in blocking_data:
        if move.name.lower() in blocking_data["moves"]:
            return False
    
    return True  # Move can hit

# In move scoring:
for move in available_moves:
    if not can_move_hit(move, opponent_active):
        move_score = -999  # NEVER select blocked moves
        continue
```

### Testing

After fix, verify:
1. ✅ Never uses status moves vs Good as Gold Gholdengo
2. ✅ Never uses priority moves vs Queenly Majesty/Dazzling
3. ✅ Never uses sound moves vs Soundproof
4. ✅ Logs when a move is blocked by ability

### Related Issues

**Other common ability blocks:**
- **Wonder Guard:** Only super-effective moves hit
- **Levitate:** Ground moves miss
- **Flash Fire:** Fire moves heal instead of damage
- **Volt Absorb / Water Absorb:** Heal from type
- **Sap Sipper:** Immune to Grass, raises Attack

**These should also be checked!**

---

## Implementation Priority

**CRITICAL - Fix before next deployment**

This is basic ability awareness. Should be in the core move evaluation logic.

**Files to modify:**
1. `fp/move_scoring.py` - Add ability block checks
2. `fp/abilities.py` - Database of ability effects (if exists)
3. `fp/battle_state.py` - Track known opponent abilities

**Test case:**
- Opponent: Gholdengo (Good as Gold)
- Our Pokemon: Skarmory (has Whirlwind)
- Expected: Whirlwind score = -999 (never selected)
- Expected: Only attacking moves considered (Body Press)
