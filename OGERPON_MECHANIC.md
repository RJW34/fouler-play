# Ogerpon Tera Mechanic (Critical Bot Knowledge)

## The Unique Mechanic

**When Ogerpon Terastallizes, it gets a permanent stat boost that persists through switches.**

### Boost by Form:
- **Teal Mask (Grass):** +1 Speed
- **Wellspring Mask (Water):** +1 Sp.Def
- **Hearthflame Mask (Fire):** +1 Attack
- **Cornerstone Mask (Rock):** +1 Defense

### How It Works:
1. Ogerpon Terastallizes → Gets +1 to form-specific stat
2. Ogerpon switches out → Boost **persists** (NOT removed like normal stat changes)
3. Ogerpon switches back in → Boost **reapplies automatically**
4. Boost **caps at +1** (doesn't stack on multiple switches)

### Key Differences from Normal Stat Boosts:
- ❌ Normal stat boosts (Swords Dance, Dragon Dance) reset when Pokemon switches out
- ✅ Ogerpon's Tera boost is **permanent for the rest of the battle**
- ✅ Switching out does NOT clear it
- ✅ No move required to "set up" after switching back in

## Bot Implementation Needed

### 1. Threat Assessment
```python
# When opponent Terastallizes Ogerpon, mark permanent boost
if opponent.name == "Ogerpon" and opponent.tera_active:
    if opponent.form == "Wellspring":
        opponent.permanent_spd_boost = 1  # +1 Sp.Def FOREVER
    elif opponent.form == "Teal":
        opponent.permanent_spe_boost = 1  # +1 Speed FOREVER
    elif opponent.form == "Hearthflame":
        opponent.permanent_atk_boost = 1
    elif opponent.form == "Cornerstone":
        opponent.permanent_def_boost = 1
```

### 2. Speed Calculations
```python
# When calculating speed vs Ogerpon-Teal (Grass)
if opponent.name == "Ogerpon-Teal" and opponent.tera_used:
    opponent_speed = opponent.base_speed * 1.5  # +1 stage = 1.5x
    
# Base 110 becomes effective Base 165 when Tera'd
```

### 3. Damage Calculations
```python
# When calculating special damage vs Ogerpon-Wellspring
if defender.name == "Ogerpon-Wellspring" and defender.tera_used:
    defender_spdef = defender.base_spdef * 1.5  # +1 Sp.Def boost
```

### 4. Switch Strategy
```python
# CANNOT force Ogerpon to lose boosts by forcing switches
# Unlike vs setup sweepers, phasing doesn't help
if opponent_is_tera_ogerpon:
    whirlwind_value = 0  # Phasing doesn't remove the boost!
```

## Critical Battle Scenarios

### Scenario 1: Ogerpon-Wellspring Tera's Early
**Turn 5:** Ogerpon-Wellspring Terastallizes → +1 Sp.Def
**Turn 6:** We force it to switch out (good damage)
**Turn 15:** Ogerpon switches back in → **STILL has +1 Sp.Def**

**Bot must remember:** That +1 Sp.Def is permanent. Can't "undo" it.

### Scenario 2: Speed Tiers vs Teal Mask
**Ogerpon-Teal stats:**
- Base Speed: 110
- After Tera: 110 * 1.5 = 165 effective speed

**Who it outspeeds after Tera:**
- ✅ Dragapult (Base 142)
- ✅ Zamazenta (Base 148)
- ✅ Nearly everything unboosted

**Bot implication:** If Ogerpon-Teal Tera's, assume it outspeeds our entire team unless we're scarfed.

### Scenario 3: Priority Move Interaction
**Question:** Do priority moves still work?
- ✅ YES - Sucker Punch, Aqua Jet, etc. still bypass speed

**Bot strategy:** If opponent has Tera'd Ogerpon-Teal (+1 Speed), priority moves become more valuable.

## Implementation Priority

### High Priority (Immediate):
1. ✅ Track Ogerpon Tera usage
2. ✅ Apply permanent stat boost to threat calculations
3. ✅ Update speed calculations for Teal Mask
4. ✅ Update special bulk for Wellspring Mask

### Medium Priority (Next Week):
1. ⏳ Don't waste phazing moves on Tera'd Ogerpon
2. ⏳ Adjust Tera usage vs Ogerpon (might need to Tera defensively)
3. ⏳ Priority move value increases vs +Speed Ogerpon

### Low Priority (Future):
1. 🔮 Predict when opponent will Tera Ogerpon
2. 🔮 Counter-strategy development

## Expert Answers (Ryan)

1. **Haze/Clear Smog:** ❌ Do NOT remove the boost - it's truly permanent
2. **Court Change:** ❌ Does not interact with it
3. **Meta Usage:**
   - **Ogerpon-Wellspring (Water):** ⚠️ **One of the strongest Pokemon in the tier**
   - **Ogerpon-Teal (Grass):** Not used much

### Critical Implication

**THE BOOST IS UNCLEARABLE.**

- No stat-clearing moves work (Haze, Clear Smog)
- No switching tricks work (Court Change)
- Once Ogerpon-Wellspring Tera's → +1 Sp.Def forever

**This means:**
- Physical attackers become more valuable vs Wellspring
- Special attackers struggle even more after Tera
- Can't rely on Haze/phasing to reset it
- Must KO or force out permanently

### Bot Priority Level

**CRITICAL:** Ogerpon-Wellspring is top-tier threat
- High usage in meta
- Permanent defensive boost
- No counterplay to remove boost
- Must track and respect

**LOW:** Ogerpon-Teal (grass form)
- Rare in current meta
- Less threatening

---

**This mechanic is CRITICAL because it breaks normal stat boost rules. Bot must handle it specially.**
