# SINGLE-BATTLE DEEP-DIVE LOOP
**Start:** 2026-02-15 20:06 EST  
**Method:** Play 1 battle → analyze → report → improve → repeat

---

## WORKFLOW

### Cycle N:
1. **Play 1 battle** with strategic layer active
   - Log decision pipeline (archetype, gameplan, filtered moves, eval scores, commitment heuristic)
   - Save replay

2. **Extract battle data**
   - Replay JSON (turn-by-turn)
   - Decision logs (why each move was chosen)
   - Final result (W/L)

3. **Analyze locally** (Ollama on ubunztu)
   - Run `replay_analysis/turn_review.py` on single replay
   - Extract: key decision points, mistakes, what went right
   - Identify: pattern, root cause, fix direction

4. **Report findings**
   - Decision-by-decision breakdown
   - "This is what the bot did"
   - "This is what a 1700 player would do"
   - "Why the difference?"

5. **Implement improvement**
   - Targeted fix (not full overhaul)
   - Test hypothesis in next battle
   - Measure impact

6. **Repeat**
   - Move to next battle
   - Track cumulative improvements

---

## WHAT GETS CAUGHT

**Batch mode misses:**
- Edge cases in specific matchups
- Subtle decision order issues (archetype detection fails on edge teams)
- Gameplan conflicts (two win conditions compete)
- Integration bugs (strategic layer + eval + penalties interact badly)

**Single-battle mode catches:**
- "Wait, why did it NOT set hazards when we said mandatory?"
- "The commitment heuristic broke this sequence"
- "Pivot team is supposed to maintain momentum but switched 4 times in 3 turns"
- "Late game finisher was at 30% HP — this was preventable"

---

## EXPECTED PATTERN

Cycles 1-10:
- Catch & fix integration bugs
- "Strategic layer not activating correctly"
- "Gameplan detection works but decision filter has bug"
- WR: Volatile, may improve or regress (catching real issues)

Cycles 11-30:
- Edge case fixes
- "Hazard stacking has off-by-one in turn count"
- "Pivot momentum calc double-counting"
- WR: Trending upward
- Confidence: Growing

Cycles 30+:
- Refinement
- "Minor heuristic tweak for pivot speed decision"
- "Game phase boundary adjustment"
- WR: Stable 65%+

---

## TOOLING ALREADY IN PLACE

✅ **Local Ollama** (qwen2.5-coder:3b) on ubunztu:11434
- Fast, free, good for code/decision analysis
- Use for: turn-by-turn breakdown, why analysis, pattern spotting

✅ **turn_review.py** — Already extracts turn details
- Modify to output: decision tree, hypothetical alternatives

✅ **Strategic layer** — Phases 1-5 completed
- Just needs integration into battle_decision.py
- Unit tests pass, real team validation works

✅ **Logging** — Bot already logs decisions
- Add strategic layer logs (archetype detected, gameplan applied, moves filtered, etc.)

---

## FIRST STEPS

1. **Integrate strategic layer** into `fp/battle_decision.py`
   - Merge archetype_analyzer, gameplan_generator, strategic_filter calls
   - Make logging comprehensive (what was filtered, why)
   - ~30 min to 1 hour

2. **Start 1 battle**
   - Play with new strategic layer live
   - Capture logs + replay

3. **Analyze battle 1**
   - Did archetype detection work?
   - Did gameplan apply correctly?
   - Were moves filtered as expected?
   - Did strategic layer help or hurt?

4. **Report + iterate**

---

## INTEGRATION CHECKLIST

- [ ] Add imports in `battle_decision.py`: archetype_analyzer, gameplan_generator, strategic_filter, multi_turn_planner
- [ ] Initialize analyzers once per battle
- [ ] Call archetype classification on preview
- [ ] Cache gameplan per battle
- [ ] Insert strategic filter BEFORE eval
- [ ] Insert multi-turn planner scoring AFTER eval
- [ ] Insert commitment heuristic POST-eval
- [ ] Log each step (decision tree)
- [ ] Test 1 battle manually
- [ ] Verify logs are readable + complete

---

## READY TO BEGIN
