# Implementation Priorities - Based on Expert Feedback

## HIGH PRIORITY (This Week)

### 1. Ogerpon-Wellspring Handling ⚠️
**Why:** One of the strongest Pokemon in the tier, unclearable stat boost

**Implementation:**
```python
# Detect Ogerpon-Wellspring on opponent team
if opponent.name == "Ogerpon-Wellspring":
    threat_level = "CRITICAL"
    
# When it Tera's, mark permanent boost
if opponent.name == "Ogerpon-Wellspring" and opponent.tera_active:
    opponent.permanent_spdef = 1.5  # +1 stage = 1.5x Sp.Def
    
# Adjust damage calculations
if defender.name == "Ogerpon-Wellspring" and defender.tera_used:
    if move.category == "special":
        expected_damage *= 0.67  # 1/1.5 = ~0.67
        
# Strategic adjustments
if opponent_has_tera_wellspring:
    physical_move_value *= 1.3  # Prefer physical attacks
    haze_value = 0  # Don't waste Haze, it doesn't work
```

**Files to modify:**
- `fp/mcts_agent.py` - Threat assessment
- `fp/battle_state.py` - Stat tracking
- `fp/damage_calc.py` - Damage calculations

---

### 2. Hazard Priority Logic
**Why:** Expert says "sometimes vs SR-weak Pokemon or teams with no hazard control"

**Implementation:**
```python
# Count Stealth Rock weak Pokemon (4x: Volcarona, Charizard, etc.)
sr_weak_threats = [
    "Volcarona", "Charizard", "Moltres", "Ho-Oh",
    "Articuno", "Zapdos-Galar", "Butterfree"
]

sr_weak_count = sum(1 for p in opponent_team if p.name in sr_weak_threats)

# Check if opponent has hazard removal
has_removal = any(
    "rapidspin" in p.moves or "defog" in p.moves or "courtchange" in p.moves
    for p in opponent_team
)

# Adjust hazard priority
if sr_weak_count >= 2:
    hazard_priority = 2.5  # High priority
elif not has_removal:
    hazard_priority = 2.0  # Medium-high
else:
    hazard_priority = 1.2  # Normal
    
# Early game boost
if turn_number <= 5 and hazard_priority > 1.5:
    stealth_rock_score *= hazard_priority
```

**Files to modify:**
- `fp/move_scoring.py` - Hazard value
- `fp/team_analysis.py` - Opponent threat detection

---

### 3. Speed Tier: Base 110 (Ogerpon-Wellspring)
**Why:** Expert identified this as critical speed tier

**Implementation:**
```python
CRITICAL_SPEED_TIERS = {
    110: "Ogerpon-Wellspring / Darkrai / Latios / Latias",
    130: "Dragapult / Meowscarada",
    148: "Zamazenta",
    # Add more as needed
}

# Speed comparison bonus
def speed_tier_advantage(our_speed, opp_speed):
    if our_speed > 110 and opp_speed <= 110:
        return 1.4  # Outspeeding Wellspring is valuable
    elif our_speed > 130 and opp_speed <= 130:
        return 1.3
    elif our_speed > 148:
        return 1.5  # Fastest tier
    else:
        return 1.0
```

**Files to modify:**
- `fp/speed_calc.py` - Speed comparisons
- `fp/move_scoring.py` - Attack priority based on speed

---

## MEDIUM PRIORITY (Next Week)

### 4. Movepool Tracking (Physical vs Special)
**Why:** Expert feedback - "Gholdengo special bulk irrelevant vs Gliscor (purely physical)"

**Implementation:**
```python
# Build movepool database from observations
KNOWN_MOVEPOOLS = {
    "Gliscor": {
        "category": "physical_only",
        "common_moves": ["earthquake", "facade", "knockoff", "toxic", "protect", "swordsdance"]
    },
    "Gholdengo": {
        "category": "special_only",
        "common_moves": ["shadowball", "hex", "nastyplot", "thunderwave", "recover"]
    },
    # etc.
}

# When evaluating switches
if opponent.category == "physical_only":
    special_defense_value = 0  # Irrelevant
    physical_defense_value *= 2.0  # Critical
elif opponent.category == "special_only":
    physical_defense_value = 0
    special_defense_value *= 2.0
```

**Files to modify:**
- `fp/movepool_database.py` - New module
- `fp/switch_evaluation.py` - Use movepool data
- `fp/threat_assessment.py` - Category-based threat scoring

---

### 5. Fat Team Switch Frequency
**Why:** Expert says "switch when taking too much damage or can improve positioning"

**Current issue:** Need better damage prediction and positioning evaluation

**Implementation:**
```python
# For fat teams specifically
if self.playstyle == "fat":
    # Calculate predicted damage
    predicted_damage = estimate_opponent_damage(opponent_move, our_active)
    hp_percent = our_active.hp / our_active.max_hp
    
    # Switch logic
    if predicted_damage > (hp_percent * 0.4):  # Would take >40% HP
        switch_score += 50  # High switch priority
    
    # Positioning improvement
    matchup_score = calculate_matchup(our_bench, opponent_active)
    if matchup_score > current_matchup_score + 30:
        switch_score += 30  # Better matchup available
```

**Files to modify:**
- `fp/playstyle_config.py` - Fat team switch thresholds
- `fp/damage_prediction.py` - Better damage estimates

---

### 5. Setup Timing Improvements
**Why:** Expert says "all counters should be eliminated, very matchup dependent"

**Current issue:** Too simplistic - just checks opponent mon count

**Implementation:**
```python
# More nuanced setup check
def safe_to_setup(setup_move, game_state):
    # Count actual counters, not just healthy mons
    counters = identify_counters(our_active, opponent_team)
    
    # Check if counters are eliminated or weakened
    active_counters = [c for c in counters if c.hp > 0.5 * c.max_hp]
    
    if len(active_counters) == 0:
        return True  # Safe to setup
    
    # Check for phazers (Whirlwind, Roar, Dragon Tail)
    if any(has_phazing_move(c) for c in active_counters):
        return False  # NEVER setup vs active phazer
    
    # Check for priority
    if any(has_priority_move(c) for c in active_counters):
        setup_value *= 0.7  # Risky vs priority
    
    return False  # Default: not safe
```

**Files to modify:**
- `fp/setup_evaluation.py` - New module for setup logic
- `fp/threat_analysis.py` - Counter identification

---

## LOW PRIORITY (Week 3+)

### 6. Item Inference
- Track move usage to infer Choice items
- Detect Assault Vest from status move fails
- Heavy-Duty Boots from hazard immunity

### 7. Opponent Modeling
- Track opponent switching patterns
- Predict switches based on matchups
- Adapt to player tendencies

### 8. Win Condition Tracking
- Identify team's win condition
- Track progress toward win condition
- Bias decisions toward win condition

---

## Next Actions

1. **This week:** Implement Ogerpon handling + hazard priority + speed tier 110
2. **Test on ladder** with Team 1 (fat stall)
3. **Collect feedback** via turn review system
4. **Iterate** based on actual battle data

**Goal:** Get from current ~1200 ELO → 1500+ with these improvements
