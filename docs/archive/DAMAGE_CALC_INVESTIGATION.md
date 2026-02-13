# Damage Calculation Investigation

## Hex Status Doubling Issue

**Hypothesis:** Timeouts may be caused by Hex's damage doubling on statused opponents not being properly accounted for in damage predictions.

**Problem:**
- Hex deals 2x damage if the target has a status condition (burn, paralysis, poison, sleep, freeze)
- If our damage calculations don't account for this, predictions will be wrong
- Wrong predictions → MCTS explores invalid game states → divergence → timeout

## Investigation Results (2026-01-31)

### ✅ poke_engine DOES handle Hex correctly

**Location:** `/tmp/poke-engine/src/genx/choice_effects.rs:232`

```rust
Choices::HEX => {
    if defending_side.get_active_immutable().status != PokemonStatus::NONE {
        attacker_choice.base_power *= 2.0;
    }
}
```

**Confirmed:** The Rust engine correctly doubles Hex's base power (65 → 130) when the defender has any status.

### Other Conditional Damage Moves Also Implemented

All checked and confirmed in `choice_effects.rs`:

- ✅ **Hex** (2x when target has status) - IMPLEMENTED
- ✅ **Facade** (2x when user is statused) - IMPLEMENTED at line 530
- ✅ **Acrobatics** (2x when user has no item) - IMPLEMENTED at line 467
- ❓ **Brine** (2x when target HP < 50%) - NOT FOUND in grep
- ❓ **Venoshock** (2x when target is poisoned) - NOT FOUND in grep

### Root Cause Identified (2026-01-31)

**NOT a damage calculation bug - it's a MOVE SELECTION bug!**

**What Ryan observed:**
1. Gholdengo used Thunder Wave on opponent's Chansey (Normal-type)
2. Thunder Wave **SUCCEEDED** - Chansey got paralyzed ✅
3. Bot then chose **Hex** (Ghost-type) next turn
4. **Hex did 0 damage** - Ghost moves are immune against Normal-types ❌

**The actual bug:**
- Bot saw: "Chansey is paralyzed → Hex gets 2x multiplier!"
- Bot IGNORED: "Hex is Ghost-type → 0x effectiveness vs Normal"
- **Calculation: 65 base × 2 (status) × 0 (type immunity) = 0 damage**
- Bot chose Hex because it prioritized status multiplier over type immunity

**This is a move evaluation bug, not a state tracking bug.**

### Impact Analysis

This could cause:
- ❌ **Choosing immune moves with status multipliers** (Hex on Normal-types)
- ❌ **Wasted turns dealing 0 damage**
- ❌ **MCTS exploring bad branches** (evaluates Hex as high-damage when it's actually 0)
- ❌ Potential timeout from exploring too many invalid high-scoring moves

### Where to Fix

**poke_engine handles this correctly!** From `choice_effects.rs`:
```rust
Choices::HEX => {
    if defending_side.get_active_immutable().status != PokemonStatus::NONE {
        attacker_choice.base_power *= 2.0;  // 65 → 130
    }
}
```

But type effectiveness is calculated separately in `damage_calc.rs`:
```rust
damage_modifier *= _type_effectiveness_modifier(&choice.move_type, &defender_types);
```

**The question:** Does poke_engine calculate damage correctly (130 BP × 0 type effectiveness = 0)?  
**OR:** Does MCTS evaluate move BEFORE damage calculation and see "130 BP = good"?

### Root Cause Hypothesis

**THE ACTUAL BUG:** Visit count vs score imbalance in MCTS + Python's 80/20 weighting.

**What happens in poke_engine MCTS:**
1. ✅ Hex base power correctly doubles to 130 (status multiplier)
2. ✅ Damage calculation correctly applies 0x type effectiveness
3. ✅ Final damage = 0
4. ✅ Rollouts score Hex poorly (0 damage = bad)
5. ❌ **BUT** MCTS still VISITS Hex frequently (because UCB1 exploration sees "high base power")

**What happens in Python move selection** (`select_move_from_mcts_results`):
```python
blended_policy[move] = visit_weight * (0.8 + 0.2 * score_bonus)
```
- **80% weight on visit count** ← This is the problem!
- 20% weight on average score
- Hex has HIGH visits (explored frequently) but LOW score (0 damage rollouts)
- 80% of HIGH >> 20% of LOW
- Hex gets selected despite bad performance

**Why UCB1 explores Hex:**
- UCB1 formula: `score + exploration_bonus`
- Early on, Hex is unexplored → gets exploration_bonus boost
- MCTS tries it, sees 0 damage, scores it low
- But other moves ALSO need exploration
- MCTS keeps coming back to Hex because it hasn't been "ruled out" by enough visits
- Visit count accumulates even though every visit scores poorly

### Other Potential Similar Issues

Any move with conditional multipliers vs type immunity:
- Facade (2x when statused) against Ghost-types
- Venoshock (2x when poisoned) against Steel-types  
- Any super-effective move with status bonus vs immune type

### Investigation Update

**poke_engine IS CORRECT:** ✅  
Found in `generate_instructions.rs:745-752` - Electric-types are immune to paralysis (gen 6+).  
Fouler Play uses `terastallization` feature (gen 9), so this immunity IS active.

**Python battle_modifier.py:**  
- Has `status()` function that applies status from `-status|` messages  
- Has `immune()` and `fail()` handlers but they don't prevent status application  
- **Key insight:** Pokemon Showdown only sends `-status|` if status actually applies  

**Possible Causes:**

1. **Sampling Bug:**  
   - Bot samples possible opponent teams/sets  
   - Maybe sampled a Chansey with wrong typing?  
   - Or sampled it as Electric-type somehow?  

2. **State Desync:**  
   - poke_engine correctly predicts Thunder Wave fails  
   - But Python side incorrectly applies status anyway?  
   - Would need to see actual battle logs  

3. **Message Parsing Order:**  
   - Maybe status message gets parsed before immune message?  
   - Python applies status, then sees immune, but doesn't undo it?  

### Next Steps to Verify

1. ✅ **Create test case:**
   ```python
   # Setup: Gholdengo vs Chansey
   # Chansey is paralyzed
   # Available moves: Hex (Ghost) and other options
   # Expected: Bot should NEVER choose Hex (0 damage)
   # Actual: Did bot choose Hex?
   ```

2. ✅ **Check MCTS results for this scenario:**
   - What are Hex's visits vs other moves?
   - What is Hex's average score?
   - Is visit count disproportionately high compared to score?

3. ✅ **Verify damage calculation:**
   ```python
   from poke_engine import calculate_damage
   # State: Chansey (Normal, paralyzed)
   # Move: Hex (Ghost, base 65)
   # Expected damage: [0, 0]
   ```

4. ✅ **Check if this is sampling-related:**
   - Does bot sample Chansey with wrong types?
   - Check battle.opponent.active.types during that turn

5. ✅ **Consider 80/20 weighting adjustment:**
   - If visit count dominates selection, maybe shift to 60/40 or 50/50
   - Or add hard filter: "if avg_score < threshold, ignore high visits"

### Immediate Action Items

1. Add test case to verify Hex selection on immune target
2. Log MCTS results (visits + scores) for all moves when this scenario occurs
3. Add assertion: "Ghost move vs Normal type → damage must be 0"
4. Consider adding move pre-filtering: "Don't explore immune type matchups"

**Reported by:** Ryan (2026-01-31)  
**Investigated by:** DEKU (2026-01-31)
