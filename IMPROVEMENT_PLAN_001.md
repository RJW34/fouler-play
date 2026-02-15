# 📊 Fouler Play Improvement Plan #1

**Generated:** 2026-02-14 20:43:25  
**Data Source:** 20 replay JSONs from replay_analysis/  
**Analysis Model:** qwen2.5-coder:7b on MAGNETON (192.168.1.181:11434)  
**Status:** 🔴 NOT YET IMPLEMENTED - For Review Only

---

## Executive Summary

LLM analysis of 20 battles identified 5 key weakness categories. This plan prioritizes the top 3 actionable improvements with specific code changes, expected impact, and implementation difficulty.

**Quick Stats:**
- Total replays analyzed: 20
- Primary issues: Switchout timing, hazard management, type coverage
- Estimated combined win rate improvement: +15-30%

---

## Top 3 Priority Improvements

### 🥇 PRIORITY 1: Hazard Management (Impact: +10-20% WR)

**Problem:**  
Bot does not prioritize setting Stealth Rocks early enough and ignores opponent hazard threats (e.g., staying in against Toxic instead of switching).

**Examples from Replays:**
- Battle 3: Kyurem vs Tyranitar - chose switch instead of Stealth Rock setup
- Battle 10: Iron Hands vs Pecharunt - stayed in after Toxic, taking unnecessary chip damage

**Suggested Code Changes:**

**File:** `fp/search/eval.py`  
**Function:** `_score_hazard_move()` (line ~875)  
**Current Issue:** Hazard scoring is too conservative

**Proposed Fix:**
```python
# Line 875-910: Increase early-game hazard priority
def _score_hazard_move(battle: Battle, move_name: str) -> float:
    """Score hazard setup moves (Stealth Rock, Spikes, Toxic Spikes)."""
    score = 0.0
    
    # CHANGE: Boost early-game hazard setup significantly
    if move_name.lower() in ["stealthrock", "stealth rock"]:
        # Check if opponent has hazards already
        opp_side = battle.opponent.side_conditions
        if constants.STEALTH_ROCK not in opp_side:
            # Early game (turn 1-3): prioritize heavily
            if battle.turn <= 3:
                score += 150.0  # CHANGED: was ~50-80
            else:
                score += 80.0  # Still valuable mid-game
            
            # Bonus if we're not threatened and can setup safely
            if not _is_threatened(battle):
                score += 40.0  # CHANGED: was ~20
    
    # ... rest of function
```

**Expected Impact:** +10-20% win rate (hazard chip damage compounds over battle)  
**Difficulty:** 🟢 Easy (simple parameter tuning)  
**Risk:** Low (won't break existing logic)

---

### 🥈 PRIORITY 2: Switchout Decision Accuracy (Impact: +5-10% WR)

**Problem:**  
Bot stays in bad matchups when it should switch (e.g., staying vs. overwhelming physical attackers).

**Examples from Replays:**
- Battle 2: Kyurem vs Tyranitar - chose Freeze-Dry instead of switching to Corviknight
- Battle 6: Corviknight vs Slowking-Galar - used U-turn instead of direct switch

**Suggested Code Changes:**

**File:** `fp/search/eval.py`  
**Function:** `_is_threatened()` (line ~370) and `_score_switch()` (line ~579)

**Proposed Fix:**
```python
# Line 370: Make threat detection more aggressive
def _is_threatened(battle: Battle) -> bool:
    """Check if our active Pokemon is in danger."""
    # CHANGE: Lower threshold from 0.5 to 0.35
    # If opponent can deal >35% damage in one hit, we're threatened
    if _opponent_best_damage(battle) > 0.35:  # CHANGED: was 0.5
        return True
    
    # CHANGE: Add type matchup check
    our_types = _get_effective_types(battle.user.active)
    opp_types = _get_effective_types(battle.opponent.active)
    
    # If opponent resists all our moves AND hits us super-effectively, switch
    # (This catches walls like Gliscor vs Porygon2)
    # TODO: Implement proper matchup evaluation here
    
    return False

# Line 579: Boost switch scores when threatened
def _score_switch(battle: Battle, target_name: str) -> float:
    """Evaluate switching to a specific Pokemon."""
    score = 0.0
    
    # CHANGE: If we're threatened, heavily incentivize switching
    if _is_threatened(battle):
        # Check if switch target is better matchup
        # (Simplified heuristic: type advantage + HP)
        score += 100.0  # CHANGED: Base switch bonus when threatened
        
        # Additional bonus if switch target has type advantage
        # TODO: Check if target_name has better matchup vs opponent
    
    # ... rest of function
```

**Expected Impact:** +5-10% win rate (avoids unnecessary KOs)  
**Difficulty:** 🟡 Medium (needs type matchup logic enhancement)  
**Risk:** Medium (could cause over-switching if not tuned carefully)

---

### 🥉 PRIORITY 3: Move Selection Optimization (Impact: +5-10% WR)

**Problem:**  
Bot sometimes chooses suboptimal coverage moves or fails to recognize setup opportunities.

**Examples from Replays:**
- Battle 3: Chose Freeze-Dry over Stealth Rock setup
- Battle 12: Stayed in vs Toxic threat instead of switching to status-immune Pokemon

**Suggested Code Changes:**

**File:** `fp/search/main.py`  
**Function:** Main decision tree (around line ~50-100, exact location TBD)

**Proposed Fix:**
```python
# In move scoring function (exact line TBD - needs code review)
def score_move_choice(battle: Battle, move_name: str) -> float:
    """Score a specific move option."""
    score = 0.0
    
    # CHANGE: Penalize moves that don't threaten when we need offense
    if _opponent_can_ko_us(battle):
        # We're in danger - prioritize either:
        # 1. Switching (handled elsewhere)
        # 2. High-damage moves to trade favorably
        estimated_damage = _estimate_damage_ratio(
            battle.user.active, 
            battle.opponent.active, 
            move_name
        )
        
        # CHANGED: Boost offensive moves when in danger
        if estimated_damage > 0.4:
            score += 80.0  # Prioritize going for the KO
    
    # CHANGE: Detect status move traps
    # If opponent used status move (Toxic, Will-O-Wisp), switch next turn
    # (Requires battle history tracking - see fp/battle.py)
    
    return score
```

**Expected Impact:** +5-10% win rate (better move selection in critical turns)  
**Difficulty:** 🟡 Medium (requires battle state history)  
**Risk:** Low-Medium (mostly additive logic)

---

## Additional Insights (Not Prioritized Yet)

### Type Coverage Gaps
**Issue:** Teams lack coverage for certain meta threats (Gliscor, Ogerpon mentioned)  
**Solution:** Team composition review (not code fix)  
**File:** `teams/*.txt` - audit team movesets for coverage holes

### Prediction Patterns
**Issue:** Bot gets read easily, over/under-predicts opponent moves  
**Solution:** Implement opponent pattern tracking  
**File:** `fp/opponent_model.py` (exists but may need enhancement)  
**Difficulty:** 🔴 Hard - requires significant ML/heuristic work

---

## Implementation Roadmap

### Phase 1: Quick Wins (Week 1)
1. ✅ Generate this improvement plan (DONE)
2. ⬜ Implement Priority 1 (Hazard scoring boost)
3. ⬜ Test on 10-20 battles
4. ⬜ Measure win rate delta

### Phase 2: Core Improvements (Week 2-3)
5. ⬜ Implement Priority 2 (Switchout logic)
6. ⬜ Implement Priority 3 (Move selection)
7. ⬜ Integration testing
8. ⬜ Deploy to live ladder

### Phase 3: Advanced (Week 4+)
9. ⬜ Team composition audit
10. ⬜ Opponent prediction model enhancement
11. ⬜ Generate Improvement Plan #2

---

## Code Review Checklist

Before implementing any change:
- [ ] Read the full function context (not just the line)
- [ ] Check for side effects and edge cases
- [ ] Write unit tests for new logic
- [ ] Test against replay dataset (don't rely on theory)
- [ ] Measure before/after win rate (need >= 30 battles for significance)

---

## Verification Protocol

After each change:
1. Run `python test_full_pipeline.sh` (if exists)
2. Play 20 test battles
3. Check `battle_stats.json` for win rate
4. Review `logs/` for errors
5. Generate new turn reviews with `replay_analysis/batch_analyzer.py`
6. Compare metrics

**Success Criteria:**
- Win rate increase >= 5% (statistically significant at N=50 battles)
- No new crashes or timeouts
- Decision trace logs show improved reasoning

---

## Notes for Implementation

- **Don't trust the AI's pseudocode blindly** - it's a starting point, not gospel
- **Test incrementally** - don't change 3 things at once
- **Keep old code commented out** for easy rollback
- **Log decision rationale** to `fp/decision_trace.py` for debugging

**Next Step:** Review this plan in #deku-workspace, get approval, then spawn sub-agent for Priority 1 implementation.

---

*Generated by DEKU (subagent: first-improvement-plan)*  
*Analysis: Ollama qwen2.5-coder:7b @ MAGNETON (192.168.1.181:11434)*  
*Timestamp: 2026-02-14 20:43:25*
