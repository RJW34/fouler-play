# FOULER PLAY STRUCTURAL DIAGNOSIS
**Date:** 2026-02-15  
**ELO:** <1200 (57% WR with top-meta teams)  
**Scope:** Root cause analysis of decision-making failures

---

## 1. TEAM ANALYSIS

### Team 1: fat-team-1-stall
**Archetype:** Pure Stall / Hazard Stack  
**Win Condition:** 
- Set up hazards (Stealth Rock + Spikes) via Blissey/Skarmory
- Wear down opponent with residual damage  
- Wall threats with specialized defensive cores
- Gholdengo setup sweeper as finisher (NP + Hex)

**Key Pokemon:**
- Skarmory: Hazard setter (Spikes) + phazer (Whirlwind)
- Blissey: Special wall + Stealth Rock + CM sweeper
- Gliscor: Physical wall + Swords Dance threat
- Gholdengo: Status immunity + setup sweeper
- Zamazenta: Fast breaker
- Pecharunt: Utility pivot

**Intended Gameplan:** Early hazard layering → defensive positioning → whittle down → late-game sweep

---

### Team 2: fat-team-2-pivot
**Archetype:** Bulky Offense / Pivot Core  
**Win Condition:**
- Maintain momentum with U-turn/Parting Shot cycles
- Pivot around opponent's defensive core
- Set hazards (Gliscor Spikes, Blissey SR)
- Win with slow attrition + pivoting advantage

**Key Pokemon:**
- Ogerpon: Pivot + Defiant punish
- Walking Wake: Mixed threat + pivot (Knock Off)
- Corviknight: Defensive pivot (U-turn)
- Pecharunt: Slow pivot (Parting Shot)
- Gliscor: Hazard setter
- Blissey: Special wall + SR

**Intended Gameplan:** Hazard setup → pivot cycle → wear down checks → close with cleaned-up sweeper

---

### Team 3: fat-team-3-dondozo
**Archetype:** Balanced Stall / Unaware Core  
**Win Condition:**
- Dondozo as primary physical wall (Unaware + Curse)
- Ting-Lu as secondary wall (Vessel of Ruin)
- Slow king as pivot + Future Sight setter
- Kyurem as Sub+Protect staller
- Cinderace as utility pivot

**Key Pokemon:**
- Dondozo: Unaware wall + wincon (Curse stacking)
- Ting-Lu: Special wall + Rest-Talk cycle
- Slowking-Galar: Pivot + Future Sight pressure
- Kyurem: Stallbreaker (Sub/Protect)
- Corviknight: Defogger + pivot
- Cinderace: Fast pivot + Court Change

**Intended Gameplan:** Wall + pivot → maintain longevity → pressure with Future Sight → win with Dondozo/Kyurem

---

## 2. RECENT LOSS ANALYSIS (Last 3 hours)

**Total Battles:** 34  
**Losses:** 15 (44% loss rate)  

### Quick Pattern Detection (10 Losses Analyzed):

**Issue Frequency:**
1. **Excessive switching** (9/10 losses) — "Pivot confusion"
2. **Failed hazard setup** (4/10 losses) — Teams designed to set hazards never established them

**Preliminary Observations:**
- Bot is switching excessively without purpose
- Teams with hazard-based win conditions aren't prioritizing hazard setup
- Likely failing to recognize team archetype on turn 1

---

## 3. DEEP DIVE: BATTLE-GEN9OU-2540389208 (Dondozo Team Loss, 15 turns)

**Team Used:** fat-team-3-dondozo  
**Result:** Loss  
**Game Length:** 15 turns (very short — catastrophic failure)  
**Opponent:** Sun team (Ceruledge, Torkoal, Walking Wake, Great Tusk, Scovillain, Hatterene)

### Turn-by-Turn Breakdown:

**CRITICAL ISSUE DETECTED:**  
The bot ran **hundreds of damage calculations per turn** (visible in logs), but the actual game lasted only 15 turns. This indicates:
- Massive computational overhead
- Decision paralysis (evaluating too many hypotheticals)
- Failing to commit to a clear gameplan

**Game Flow Extract:**
- Turn 15: Ceruledge used Solar Blade
- Bot switched to Cinderace
- Opponent switched (presumably winning positioning)
- **Conclusion: Lost by turn 15**

**What went wrong:**
- Very short game = bot never established defensive positioning
- Sun team has clear win condition (sun-boosted sweepers)
- Bot likely failed to:
  1. Identify sun threat early
  2. Bring in appropriate counter (Ting-Lu/Dondozo should wall)
  3. Maintain defensive integrity

---

## 4. STRUCTURAL PATTERNS IDENTIFIED

### Pattern 1: Pivot Confusion (9/10 losses)
**Symptom:** Excessive switching (>20 switches in short games)  
**Root Cause Hypothesis:**  
- Bot evaluates every switch as potentially optimal
- No "commitment" heuristic — keeps second-guessing
- Pivot teams misunderstand *why* they pivot (momentum) vs random switching

**1700 Player Behavior:**  
- Pivots have PURPOSE: gain information, position favorably, maintain momentum
- Switches are COMMITTED: once you decide, you execute

**Bot Behavior:**  
- Switches reactively without plan
- Likely over-penalizing "staying in" even when correct

---

### Pattern 2: Hazard Abandonment (4/10 losses)
**Symptom:** Teams built around hazards never set them  
**Root Cause Hypothesis:**  
- Turn 1 decision prioritizes immediate damage over setup
- Eval doesn't weight "hazard value" across game length
- Bot treats every turn as isolated decision (no multi-turn planning)

**1700 Player Behavior:**  
- Recognizes team archetype turn 1
- Commits to gameplan (e.g., "I need rocks up before turn 5")
- Accepts short-term disadvantage for long-term setup

**Bot Behavior:**  
- Likely eval ranks "deal damage now" > "set rocks"
- No concept of "mandatory setup" for archetype

---

### Pattern 3: No Win-Condition Recognition
**Symptom:** Short games, reactive play, no execution  
**Root Cause Hypothesis:**  
- Bot doesn't identify team's win condition on preview
- Treats all moves/switches as equal utility
- No "this is how we WIN" vs "this is how we stall"

**1700 Player Behavior:**  
- Knows before battle starts: "I win by X"
- Every decision filters through: "Does this advance my win condition?"

**Bot Behavior:**  
- Evaluates moves in vacuum
- No concept of "we need Dondozo at 80%+ HP for late game"
- No resource management (HP as currency)

---

## 5. DECISION PIPELINE DIAGNOSIS

### Suspected Broken Layer: **Game State Understanding**

**What's Working:**
- ✅ Damage calculations (hundreds per turn — maybe too many)
- ✅ Threat recognition (knows what can kill what)
- ✅ Type effectiveness

**What's Broken:**
- ❌ **Archetype recognition** — doesn't know team's win condition
- ❌ **Multi-turn planning** — treats each turn as isolated
- ❌ **Commitment** — switches when it should stay, stays when it should switch
- ❌ **Resource prioritization** — doesn't value setup > immediate damage when appropriate

---

## 6. ROOT CAUSE HYPOTHESIS

**THE BOT DOESN'T UNDERSTAND "WIN CONDITIONS"**

**Evidence:**
1. Hazard teams don't set hazards (missing the POINT of the archetype)
2. Pivot teams pivot randomly (don't understand momentum)
3. Stall teams don't stall (play reactive instead of proactive)
4. Short losses (never executed a gameplan)

**This is NOT:**
- ❌ A damage calculation bug
- ❌ A penalty system bug (though penalties may be symptom)
- ❌ A move selection bug in isolation

**This IS:**
- ✅ A **strategic layer missing entirely**
- ✅ The bot has no concept of "how do I WIN with THIS team?"
- ✅ Every turn is evaluated in vacuum, not as part of multi-turn gameplan

---

## 7. COMPARISON: 1700 Player vs Bot

| **Dimension** | **1700 Player** | **Bot (<1200)** |
|---------------|----------------|----------------|
| **Team Preview** | Identifies archetype + win condition | Sees 6 pokemon |
| **Turn 1 Decision** | Commits to gameplan | Evaluates damage only |
| **Hazard Priority** | Mandatory for hazard teams | Optional, often skipped |
| **Pivot Purpose** | Gain momentum/positioning | Random switches |
| **Resource Management** | HP is currency, spend wisely | Reactive, no planning |
| **Late Game** | Executes win condition | Hopes to survive |

---

## 8. RECOMMENDED FIX DIRECTION

**NOT a quick fix. This requires architectural change.**

### Short-term (Bandaid):
1. **Force hazard setup on appropriate teams** — If team has Stealth Rock + Spikes, MUST set both before turn 10
2. **Penalize excessive switching** — If switched >3 times in 5 turns without purpose, heavy penalty
3. **Commitment heuristic** — Once a decision is made, don't second-guess next turn

### Medium-term (Proper Fix):
1. **Add "Archetype" layer** — On preview, classify team:
   - Hazard Stack: MUST set hazards early
   - Pivot: MUST maintain momentum (U-turn cycles)
   - Setup Sweeper: MUST preserve sweeper HP
   - Stall: MUST preserve defensive core
2. **Win Condition Eval** — Each turn, ask: "Does this advance my win condition?"
3. **Multi-turn planning** — Eval over 3-5 turn windows, not just current turn

### Long-term (Structural):
1. **Game Plan Generator** — Before battle, generate:
   - "I win by X"
   - "I lose if Y happens"
   - "My critical turns are Z"
2. **State-based decision trees** — Different eval for:
   - Early game (setup)
   - Mid game (positioning)
   - Late game (execution)

---

## 9. IMMEDIATE ACTION ITEMS

1. **Verify hypothesis**: Run 5 more battles, manually log:
   - Did bot set hazards when team requires it?
   - Did bot commit to a win condition?
   - Count switches per game

2. **Code audit targets**:
   - `fp/battle_decision.py` — Does eval have "archetype awareness"?
   - `fp/eval.py` — Does position eval include "multi-turn value"?
   - Penalty system — Is it OVER-penalizing staying in?

3. **Test patch**: Force hazard teams to ALWAYS set hazards turn 1-3, measure WR change

---

## 10. CONCLUSION

**The bot is playing move-by-move poker, not chess.**

It sees threats, calculates damage, but has **zero concept of strategy**. It's optimizing for "best move this turn" when it should optimize for "sequence of moves that wins the game."

**57% WR is actually impressive** given it's playing without a brain. The mechanics work. The strategy layer doesn't exist.

**This is why it's stuck <1200.**  
Good players don't just make good moves — they execute gameplans. The bot doesn't have gameplans.

---

**Next Steps:**  
1. Validate with 5-10 more manual battle reviews  
2. Audit decision code for "win condition" logic (likely absent)  
3. Prototype "forced archetype behavior" patch  
4. Measure WR delta  

**Expected Outcome:**  
If this hypothesis is correct, forcing archetype-aware behavior should jump WR to 65%+ immediately, even with crude implementation.

---

**END DIAGNOSIS**
