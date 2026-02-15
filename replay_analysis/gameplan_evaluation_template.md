# Gameplan Quality Evaluation Template

**Battle ID:** `<replay_id>`  
**Date:** `YYYY-MM-DD`  
**Result:** Win / Loss / Tie  
**ELO:** `before → after`

---

## 1. Pre-Battle Gameplan

### Opponent Win Condition (from analyzer)
> _What the analyzer predicted opponent would try to do_

```
<opponent_win_condition from gameplan.json>
```

### Our Strategy (from analyzer)
> _What the analyzer recommended we do_

```
<our_strategy from gameplan.json>
```

### Win Condition (from analyzer)
> _Our predicted path to victory_

```
<win_condition from gameplan.json>
```

### Key Pivot Triggers (from analyzer)
> _Situational switching recommendations_

```
<key_pivot_triggers from gameplan.json>
```

---

## 2. Execution Audit

### ✅ Did opener match gameplan?
- **Expected:** `<lead_preference or inferred from strategy>`
- **Actual:** `<actual lead Pokemon>`
- **Assessment:** ✅ Yes / ❌ No / ⚠️ Deviation justified
- **Notes:** 

### ✅ Pivots happened as predicted?
- **Trigger 1:** `<key_pivot_trigger[0]>`
  - **Occurred?** Yes / No / N/A
  - **Executed correctly?** ✅ Yes / ❌ No / ⚠️ Partial
  - **Turn(s):** 
- **Trigger 2:** `<key_pivot_trigger[1]>`
  - **Occurred?** Yes / No / N/A
  - **Executed correctly?** ✅ Yes / ❌ No / ⚠️ Partial
  - **Turn(s):** 

### ✅ Win condition pursued?
- **Primary win condition:** `<win_condition>`
- **Pursued?** ✅ Yes / ❌ No / ⚠️ Fallback used
- **Why/why not?** 
- **Alternative used?** 

### ✅ Deviations: tactical or strategic?
- **Deviation 1:**
  - **Turn:** 
  - **What happened:** 
  - **Type:** Tactical (good read) / Strategic (bad plan)
  - **Impact:** Positive / Neutral / Negative
- **Deviation 2:**
  - **Turn:** 
  - **What happened:** 
  - **Type:** Tactical (good read) / Strategic (bad plan)
  - **Impact:** Positive / Neutral / Negative

---

## 3. Opponent Analysis Accuracy

### ✅ Did opponent execute predicted win condition?
- **Prediction:** `<opponent_win_condition>`
- **Actual:** 
- **Accuracy:** ✅ Accurate / ⚠️ Partially / ❌ Wrong
- **Notes:** 

### ✅ Were opponent weaknesses exploited?
- **Predicted weaknesses:** `<opponent_weaknesses>`
- **Exploited?** ✅ Yes / ⚠️ Partially / ❌ No / N/A (not relevant)
- **How?** 

---

## 4. Scoring

### Gameplan Quality Score: `__/100`

**Breakdown:**
- **Opener alignment (0-20):** `__` — _Did we lead correctly?_
- **Pivot execution (0-20):** `__` — _Did we switch at the right times?_
- **Win condition pursuit (0-30):** `__` — _Did we follow the plan to victory?_
- **Opponent prediction (0-20):** `__` — _Was the opponent analysis accurate?_
- **Adaptability (0-10):** `__` — _Did deviations improve or hurt our position?_

**Total:** `__/100`

---

## 5. Recommendations

### What the gameplan got RIGHT:
1. 
2. 
3. 

### What the gameplan got WRONG:
1. 
2. 
3. 

### Suggested improvements:
1. **Analyzer calibration:** 
2. **Team-specific adjustments:** 
3. **Execution training:** 

---

## 6. Comparative Analysis

### Move Selection Quality (from existing analysis)
- **Score:** `__/100` (from turn review)
- **Top mistake:** 

### Gameplan vs Execution Gap
- **Gap size:** `|gameplan_score - move_score|`
- **Interpretation:**
  - Small gap (<10): Plan and execution aligned
  - Medium gap (10-30): Plan was good/bad but execution differed
  - Large gap (>30): Fundamental mismatch between strategy and tactics

### Root cause of loss/suboptimal play:
- [ ] Bad gameplan (analyzer misjudged matchup)
- [ ] Good plan, poor execution (decision layer ignored strategy)
- [ ] Correct plan AND execution, but opponent outplayed
- [ ] External factors (RNG, time pressure, lag)

---

## 7. Metadata

**Analyzer version:** `matchup_analyzer.py v1.0`  
**Model used:** `qwen2.5-coder:3b`  
**Cache hit:** Yes / No  
**Gameplan generation time:** `<timestamp>`  
**Reviewed by:** DEKU / MAGNETON / Human  
**Review date:** `YYYY-MM-DD`
