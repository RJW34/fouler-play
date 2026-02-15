# Fouler Play Architecture & Bottleneck Analysis
**Analysis Date:** 2026-02-14  
**Analyzed By:** DEKU Sub-agent (fouler-analyzer)  
**Commit HEAD:** 9281cc7 (Fix 4 decision engine bugs, add Thunder Wave speed-control awareness)

---

## 1. Bot Architecture Overview

### 1.1 Decision-Making Flow

```
Battle Event
    ↓
Process State Update (battle_modifier.py)
    ↓
find_best_move() (search/main.py)
    ├→ Forced Lines Detection (forced_lines.py)
    │   └→ If confidence ≥ 0.70: Return immediately
    ├→ Eval Engine (eval.py)
    │   ├→ 1-ply state evaluation
    │   ├→ Apply 9 penalty layers
    │   └→ Return scored candidates
    └→ Hybrid Policy (hybrid_policy.py) [optional]
        ├→ Rerank top-K candidates via OpenAI
        └→ Return final choice
    ↓
Submit Move to Pokemon Showdown
    ↓
Log Decision Trace (decision_trace.py)
```

**Key Subsystems:**

1. **State Management** (`fp/battle.py`, `fp/battle_modifier.py`)
   - Parses Pokemon Showdown protocol messages
   - Updates Battle object with current game state
   - Tracks: HP, stats, boosts, status, items, abilities, revealed moves

2. **Forced Lines Engine** (`fp/search/forced_lines.py`)
   - Detects obvious tactical sequences:
     - We outspeed + OHKO available (conf: 0.95)
     - Opponent OHKOs us → switch to resist (conf: 0.85)
     - Opponent at +2 → use phaze (conf: 0.80)
     - Safe hazard opportunities (conf: 0.75)
     - Toxic stall win conditions (conf: 0.75)
   - **Bypasses eval engine** when confidence threshold met
   - Significantly reduces latency for obvious turns

3. **Eval Engine** (`fp/search/eval.py`, `fp/search/main.py`)
   - **1-ply position evaluation** (no look-ahead)
   - Base move scores from `constants_pkg/strategy.py`
   - **9 Penalty/Boost Layers:**
     1. **Ability-aware penalties** (Unaware, Guts, Poison Heal, Flash Fire, etc.)
     2. **Type effectiveness** (STAB, resistances, immunities)
     3. **Status threat detection** (Toxic vs Poison Heal, WoW vs Guts)
     4. **Hazard calculus** (SR weak opponents, hazard removal timing)
     5. **Switch evaluation** (Intimidate, hazard damage, recovery moves)
     6. **Momentum tracking** (aggressive when ahead, safe when behind)
     7. **Win condition awareness** (don't sack only checks)
     8. **Setup vs Phazing** (Whirlwind when opponent at +2)
     9. **Speed order + priority** (Thunder Wave flips, priority moves)
   - **Data sources:**
     - `constants_pkg/move_flags.py` - Move properties from moves.json
     - `constants_pkg/pokemon_abilities.py` - Common abilities from pokedex.json
     - `fp/movepool_tracker.py` - Tracks revealed moves per opponent

4. **Hybrid Policy (Optional)** (`fp/hybrid_policy.py`)
   - Re-ranks eval engine's top-K candidates
   - Uses OpenAI API (gpt-4o-mini or similar)
   - **Skips re-ranking if:**
     - Clear best engine choice (delta ≥ 0.15 or ratio ≥ 1.35)
     - Time pressure (<20s on clock)
     - Non-eval decision (forced line already chosen)
     - Rate limited (429 backoff)
   - **Fallback:** Always uses eval-only if API fails
   - Provides reasoning in decision trace

5. **Opponent Model** (`fp/opponent_model.py`, `fp/movepool_tracker.py`)
   - Bayesian set prediction (`fp/bayesian_sets.py`)
   - Tracks revealed moves → narrows possible sets
   - Categorizes threats: OFFENSIVE, STATUS, SETUP, DEFENSIVE

6. **Battle Loop** (`fp/run_battle.py`)
   - Websocket connection to Pokemon Showdown
   - Message timeout: 120s (configurable)
   - Stale battle detection (2 strikes → terminate)
   - Logs battles to `logs/battle-{id}_{opponent}.log`
   - Writes decision traces to `logs/decision_traces/`
   - Updates OBS overlay state via `streaming/state_store.py`

---

## 2. Current Bottlenecks & Known Issues

### 2.1 Recent Bug Fixes (from git log)

**Commit 9281cc7 (Feb 14, 2026) - 4 CRITICAL BUGS:**

1. **Threat Classification Immunity Bug (CRITICAL)**
   - **Issue:** `opponent_active_is_threat` flagged all revealed moves as threats, even if they couldn't hit our active Pokemon
   - **Example:** Skarmory Body Press (Fighting) triggered threat flag vs Ghost-type
   - **Fix:** Now checks type effectiveness before flagging threat
   - **Impact:** Prevents unnecessary defensive switches when immune

2. **Contact Recoil at Critical HP (MAJOR)**
   - **Issue:** Contact moves vs Rocky Helmet/Iron Barbs got 0.5x penalty even when we're at ≤17% HP (recoil would KO us)
   - **Fix:** Reduced penalty to 0.1x when recoil is lethal
   - **Impact:** Prevents suicide plays

3. **Pivot Penalty When Last Pokemon (MAJOR)**
   - **Issue:** U-turn/Volt Switch got same score even when our_alive_count ≤ 1
   - **Fix:** 0.25x penalty when nothing to pivot into
   - **Impact:** Stops bot from clicking U-turn with no team left

4. **Thunder Wave Speed-Flip Awareness**
   - **Issue:** Thunder Wave undervalued (0.3 base eval) even when it flips speed matchup
   - **Fix:** Boosted to 0.55 when paralysis enables safe Recover/switch next turn
   - **Impact:** Enables speed control → safe recovery setups

**Commit 8258df2 (Feb 9, 2026):**
- Dead battle room reclaim bug (blacklist not applied in resume path)
- Decision engine fixes (specifics in commit diff)
- Log cleanup improvements

**Earlier Critical Fixes:**
- **Type immunity for status threats** (Commit 019de60): Status moves now respect type immunities
- **Poison Heal vulnerability** (Commit 87c96b7): Fixed Poison Heal Pokémon being vulnerable to non-Toxic status
- **Null pointer crashes** (Commit e0c96c9): Boost/unboost functions crashed on None active pokemon
- **1hr hard timeout** (Commit 7f8a277): Prevent battle workers from hanging forever
- **Blanket sweep penalty** (Commit da2c514): Replaced with smart counterplay logic

### 2.2 Architectural Bottlenecks

**A. 1-Ply Evaluation Depth**
- **Current:** Eval engine only looks at immediate state
- **Limitation:** Cannot predict 2-turn sequences (setup → sweep, hazard chip → revenge kill)
- **Example:** Fails to recognize that opponent's Stealth Rock → Zapdos switch-in will chip 50%
- **Mitigation:** Forced lines handle some 2-turn patterns (e.g., "we 2HKO and they can't")

**B. Opponent Movepool Uncertainty**
- **Current:** Unrevealed moves conservatively treated as threats
- **Limitation:** Overdefensive early-game (switches out when opponent has no coverage)
- **Example:** Switches Skarmory vs +2 Dragonite (unrevealed Fire Punch) even if set predictor says Outrage/EQ/ESpeed/Dragon Dance

**C. Speed Order Calculation**
- **Current:** `assess_speed_order()` in `speed_order.py` estimates outspeeding probability
- **Known Issues:**
  - Doesn't account for all speed-boosting items (Choice Scarf detection incomplete)
  - Trick Room handling added in Phase 1.2 (recent improvement)
  - Weather speed (Swift Swim, Chlorophyll, etc.) added in Phase 1.4

**D. Tera Type Prediction**
- **Current:** Defensive/offensive tera type lists in `constants_pkg/strategy.py`
- **Limitation:** Static heuristics, no real-time opponent tera pattern learning
- **Improvement Opportunity:** Analyze opponent team composition → infer likely tera types

**E. Hazard Damage Accumulation**
- **Current:** Switch evaluation applies per-layer penalties
- **Limitation:** Doesn't project multi-turn chip damage sequences
- **Example:** Fails to recognize that opponent will spam hazards + chip to put us in revenge kill range

**F. Hybrid Policy Latency**
- **Current:** OpenAI API calls add 500-2000ms latency
- **Mitigation:** Skips re-ranking when time < 20s or clear best move
- **Improvement Opportunity:** Local LLM (Ollama on MAGNETON) for faster re-ranking

---

## 3. Replay Format & Available Data

### 3.1 Replay File Structure

**Location:** `replay_analysis/*.json`

**Schema:**
```json
{
  "id": "gen9ou-2532435943",
  "format": "[Gen 9] OU",
  "players": ["player1", "player2"],
  "log": "|j|★player1\n|switch|p1a: Pokemon|...\n|move|p2a: Pokemon|...\n...",
  "uploadtime": 1770176521,
  "views": 0,
  "formatid": "gen9ou",
  "rating": 1154,
  "private": 1,
  "password": "..."
}
```

**Key Field: `log`**
- Pokemon Showdown protocol (pipe-separated)
- Line types:
  - `|switch|p1a: Name|Species|HP%` - Pokemon switches in
  - `|move|p1a: Name|MoveName|target` - Move used
  - `|-damage|p1a: Name|HP%` - Damage taken
  - `|-boost|p1a: Name|stat|amount` - Stat boost
  - `|-status|p1a: Name|status` - Status inflicted
  - `|-supereffective|`, `|-resisted|`, `|-immune|` - Type effectiveness
  - `|turn|N` - Turn number
  - `|faint|p1a: Name` - Pokemon faints
  - `|win|player1` - Battle result

### 3.2 Decision Trace Files

**Location:** `logs/decision_traces/{battle_tag}_turn{N}_{timestamp}.json`

**Schema:**
```json
{
  "battle_tag": "gen9ou-2539747440",
  "worker_id": 0,
  "turn": 12,
  "format": "gen9ou",
  "timestamp": "2026-02-14T19:30:42.123Z",
  "reason": "eval",
  "snapshot": {
    "user_active": {...},
    "opponent_active": {...},
    "user_reserve": [...],
    "opponent_reserve": [...],
    "weather": null,
    "field": null,
    "trick_room": false,
    "side_conditions": {...}
  },
  "choice": "thunderbolt",
  "eval_scores_raw": {
    "thunderbolt": 1.45,
    "voltswitch": 1.32,
    "switch garganacl": 0.85
  },
  "decision_mode": "eval",
  "confidence": null,
  "hybrid_metadata": {...}
}
```

**Available for Analysis:**
- **Every decision** the bot made
- **State snapshot** at decision time
- **Eval scores** for all candidate moves
- **Forced line reasoning** (if applicable)
- **Hybrid policy override** (if applicable)

### 3.3 Battle Logs

**Location:** `logs/battle-{id}_{opponent}.log`

**Contents:**
- Raw Pokemon Showdown protocol messages
- Bot's internal state updates
- Rotating logs (keeps last 60 battles)

---

## 4. Integration Points for Improvements

### 4.1 Switchout Logic

**Current Implementation:**
- `_score_candidate()` in `search/main.py`
- Switch penalties in `constants_pkg/strategy.py`:
  - `PENALTY_SWITCH_INTO_HAZARDS_PER_LAYER = 0.2`
  - `PENALTY_SWITCH_LOW_HP = 0.3`
  - `PENALTY_SWITCH_WEAK_TO_OPPONENT = 0.4`
  - `BOOST_SWITCH_RESISTS_STAB = 0.2`
  - `BOOST_SWITCH_HAS_RECOVERY = 0.15`
  - `BOOST_SWITCH_COUNTERS = 0.25`

**Integration Point:**
```python
# fp/search/main.py ~line 800
def _apply_switch_penalties(score, switch_target, battle):
    # Apply hazard damage penalty
    # Apply HP penalty
    # Apply matchup penalty
    # Apply counter boost
    return adjusted_score
```

**Improvement Opportunities:**
1. **Multi-turn projection:** Calculate expected chip over next 2-3 turns
2. **Revenge kill detection:** Recognize when opponent has priority/fast move that KOs
3. **Setup sequence recognition:** Switch to wall when opponent is setting up, not after
4. **Hazard removal timing:** Prioritize switching to spinner when hazards are heavy

**Replay Indicators of Weakness:**
- Switches into predicted super-effective moves
- Stays in when opponent sets up (instead of switching to Unaware/phaze)
- Switches unnecessarily when hazards are up (should remove first)
- Switches to low-HP Pokemon vs offensive threats

### 4.2 Hazard Awareness

**Current Implementation:**
- Hazard setting penalties in `constants_pkg/strategy.py`:
  - `BOOST_SET_HAZARDS_NO_HAZARDS = 0.3`
  - `BOOST_SET_HAZARDS_SR_WEAK_OPP = 0.2`
  - `PENALTY_SET_HAZARDS_ALREADY_UP = 0.5`
- Hazard removal boosts:
  - `BOOST_REMOVE_HAZARDS_HEAVY = 0.4`
  - `PENALTY_REMOVE_HAZARDS_NONE = 0.3`

**Integration Point:**
```python
# fp/search/main.py ~line 1200
def _apply_hazard_penalties(score, move, battle):
    # Check if hazards already set
    # Check if opponent is SR-weak
    # Boost/penalize accordingly
    return adjusted_score
```

**Improvement Opportunities:**
1. **Chip damage projection:** Sum up (Rocks + Spikes layers) × switch frequency
2. **Priority vs opponent composition:** Higher boost if opponent has Volcarona/Charizard (4x SR weak)
3. **Hazard stack timing:** Don't spam Spikes if opponent has Rapid Spin user
4. **Removal urgency:** Higher boost when our team is SR-weak

**Replay Indicators of Weakness:**
- Sets hazards when opponent already has them (wastes turn)
- Doesn't remove hazards when taking 25%+ per switch
- Doesn't set hazards against 4x SR weak opponents (free chip)
- Removes hazards when opponent has none (wastes turn)

### 4.3 Speed Calculation

**Current Implementation:**
- `assess_speed_order()` in `fp/search/speed_order.py`
- Returns probability we outspeed
- Accounts for:
  - Base stats
  - Boosts (e.g., +1 Speed from Dragon Dance)
  - Choice Scarf (incomplete detection)
  - Trick Room
  - Paralysis (recent Thunder Wave fix)
  - Weather abilities (Swift Swim, Chlorophyll, etc.)

**Integration Point:**
```python
# fp/search/speed_order.py
def assess_speed_order(our_mon, opp_mon, battle):
    # Calculate effective speeds
    # Account for modifiers
    # Return probability
    return prob_we_outspeed
```

**Improvement Opportunities:**
1. **Choice Scarf prediction:** Use opponent model to infer Scarf usage
2. **Priority move tracking:** Don't assume we outspeed if opponent has Aqua Jet
3. **Weather speed boosting:** Better detection of weather abilities
4. **Tailwind/Sticky Web:** Account for field conditions

**Replay Indicators of Weakness:**
- Assumes we outspeed, but opponent has Choice Scarf → gets KO'd
- Uses setup move when opponent outspeeds + has OHKO
- Doesn't leverage paralysis speed control effectively (recent fix addresses this)
- Ignores priority moves in speed calculations

### 4.4 Status Move Timing

**Current Implementation:**
- Toxic stall win condition detection in `forced_lines.py`
- Status threat penalties in `constants_pkg/strategy.py`:
  - Guts/Marvel Scale/Poison Heal awareness
  - Type immunity checks (recent fix)
  - Ability-based backfire penalties

**Integration Point:**
```python
# fp/search/main.py ~line 900
def _apply_status_penalties(score, move, battle):
    # Check opponent ability
    # Check type immunities
    # Apply backfire penalties
    return adjusted_score
```

**Improvement Opportunities:**
1. **Early-game Toxic:** Boost Toxic when opponent lacks cleric/Natural Cure
2. **Thunder Wave → setup:** Recognize paralysis enables safe setup (recent fix helps)
3. **Status immunity prediction:** Use revealed moves to infer Lum Berry/Safeguard
4. **Status spreading:** Don't spam status on same target

**Replay Indicators of Weakness:**
- Uses Toxic on Poison Heal/Guts/Immune types (recent fix addresses this)
- Wastes status moves when opponent has Lum Berry
- Doesn't capitalize on paralysis for setup opportunities
- Uses status when opponent has cleric (Heal Bell/Aromatherapy)

### 4.5 Tera Timing

**Current Implementation:**
- Defensive/offensive tera type lists in `constants_pkg/strategy.py`
- Tera prediction heuristics:
  - `BOOST_COVERAGE_VS_LIKELY_TERA = 0.15`
  - `BOOST_STAY_FOR_TERA = 0.1`

**Integration Point:**
```python
# fp/search/main.py ~line 1500
def _apply_tera_penalties(score, move, battle):
    # Check likely opponent tera types
    # Boost coverage moves
    # Penalize ineffective moves
    return adjusted_score
```

**Improvement Opportunities:**
1. **Team composition analysis:** Infer tera types from opponent's team (e.g., Water-type on sun team → likely Tera Grass)
2. **STAB tera prediction:** Assume STAB tera unless evidence otherwise
3. **Defensive tera timing:** Recognize when opponent will tera to avoid OHKO
4. **Our tera optimization:** Calculate optimal tera type per matchup

**Replay Indicators of Weakness:**
- Predicts wrong tera type → uses ineffective move
- Doesn't adapt to revealed tera type
- Wastes our tera at suboptimal time
- Doesn't recognize opponent defensive tera setups

### 4.6 Setup vs Phasing

**Current Implementation:**
- Forced line: "Opponent at +2 and we have phaze → use it" (conf: 0.80)
- Setup move penalties vs phazing moves in eval engine
- Constants:
  - `PHAZING_MOVES` - Whirlwind, Roar, Dragon Tail, etc.
  - `SETUP_MOVES` - Swords Dance, Nasty Plot, etc.

**Integration Point:**
```python
# fp/search/forced_lines.py ~line 200
def detect_forced_phaze(battle):
    if opponent_boosts >= 2 and we_have_phaze:
        return ForcedLine(move="whirlwind", confidence=0.80, ...)
```

**Improvement Opportunities:**
1. **Boost threshold tuning:** Sometimes +1 is enough to phaze (vs Belly Drum → +6)
2. **Unaware switch priority:** Recognize Unaware walls nullify setup
3. **Haze vs phasing:** Haze clears our boosts too, use selectively
4. **Setup anticipation:** Switch to phaser *before* opponent sets up

**Replay Indicators of Weakness:**
- Lets opponent set up to +2/+4 before phazing (should phaze at +1)
- Uses Haze when we also have boosts
- Doesn't switch to Unaware wall vs setup sweeper
- Stays in vs setup move instead of switching to counter

---

## 5. Subsystems Critical to Performance

### Priority Ranking (Impact × Frequency)

**1. Eval Engine Accuracy (HIGH IMPACT, HIGH FREQUENCY)**
- **Why Critical:** Every non-forced-line turn uses eval engine
- **Current Issues:** 1-ply depth, static penalties, type immunity bugs (recently fixed)
- **Improvement Leverage:** 10-20% win rate gain from better eval

**2. Forced Lines Detection (MEDIUM IMPACT, MEDIUM FREQUENCY)**
- **Why Critical:** Bypasses eval latency, forces obvious plays
- **Current Issues:** Conservative thresholds, limited patterns
- **Improvement Leverage:** 5-10% latency reduction, 2-5% win rate from new patterns

**3. Speed Order Calculation (HIGH IMPACT, MEDIUM FREQUENCY)**
- **Why Critical:** Dictates aggressive vs defensive play
- **Current Issues:** Choice Scarf detection, priority move awareness
- **Improvement Leverage:** 5-8% win rate (prevents suicide plays)

**4. Switch Evaluation (MEDIUM IMPACT, MEDIUM FREQUENCY)**
- **Why Critical:** Poor switches compound chip damage
- **Current Issues:** 1-ply, doesn't project multi-turn damage
- **Improvement Leverage:** 8-12% win rate (prevents hazard chip → sweep)

**5. Opponent Model (MEDIUM IMPACT, LOW FREQUENCY)**
- **Why Critical:** Narrows movepool → better predictions
- **Current Issues:** Bayesian update lag, conservative assumptions
- **Improvement Leverage:** 3-5% win rate (better coverage predictions)

**6. Hybrid Policy (LOW IMPACT, HIGH FREQUENCY - when enabled)**
- **Why Critical:** Re-ranks eval choices with LLM reasoning
- **Current Issues:** Latency, API costs, rate limits
- **Improvement Leverage:** 2-4% win rate, but adds 500-2000ms latency

---

## 6. Replay Patterns Indicating Subsystem Weaknesses

### 6.1 Eval Engine Issues

**Pattern Indicators:**
- ❌ **Uses setup move when opponent has phaze** → eval undervalues phaze threat
- ❌ **Stays in at low HP vs offensive threat** → eval overvalues KO chance, undervalues survival
- ❌ **Uses status move on immune/backfire abilities** → ability penalty bug (recently fixed)
- ❌ **Clicks coverage move with 0.5x damage** → type effectiveness miscalculation

**Replay Log Signatures:**
```
|move|p1a: OurMon|Swords Dance
|move|p2a: TheirMon|Whirlwind  <-- We set up, they phaze
```

### 6.2 Switch Logic Issues

**Pattern Indicators:**
- ❌ **Switches into predicted super-effective move** → switch eval doesn't predict opponent move
- ❌ **Switches low-HP mon into hazards** → hazard penalty too weak
- ❌ **Switches when opponent is setting up** → should switch *before* they set up
- ❌ **Stays in vs hard counter** → eval overvalues staying, undervalues switching

**Replay Log Signatures:**
```
|switch|p1a: Skarmory|100/100
|-damage|p1a: Skarmory|50/100  <-- Switched into Stealth Rock (50% chip)
|move|p2a: Heatran|Flamethrower  <-- Predicted coverage
|-supereffective|p1a: Skarmory
```

### 6.3 Speed Order Issues

**Pattern Indicators:**
- ❌ **Uses non-priority move, gets outsped and KO'd** → speed calc wrong
- ❌ **Assumes we outspeed, but opponent has Scarf** → Scarf detection failed
- ❌ **Ignores priority moves** → speed calc doesn't account for Aqua Jet/Extreme Speed

**Replay Log Signatures:**
```
|move|p2a: TheirMon|Thunderbolt  <-- Opponent moves first (we thought we outsped)
|-damage|p1a: OurMon|0 fnt
|faint|p1a: OurMon
```

### 6.4 Hazard Awareness Issues

**Pattern Indicators:**
- ❌ **Sets hazards when opponent already has them** → wastes turn
- ❌ **Doesn't remove hazards when taking 25%+ per switch** → hazard removal urgency low
- ❌ **Takes 50% chip from Stealth Rock repeatedly** → should switch to Heavy-Duty Boots user

**Replay Log Signatures:**
```
|switch|p1a: Charizard|100/100
|-damage|p1a: Charizard|50/100  <-- 4x SR weak, taking 50% per switch
|switch|p1a: Volcarona|100/100
|-damage|p1a: Volcarona|50/100  <-- Another 4x SR weak switch
```

### 6.5 Status Timing Issues

**Pattern Indicators:**
- ❌ **Uses Toxic on Poison Heal/Guts** → ability backfire (recently fixed)
- ❌ **Wastes Toxic when opponent has cleric** → doesn't predict Heal Bell
- ❌ **Doesn't use Thunder Wave for speed control** → undervalues paralysis (recently fixed)

**Replay Log Signatures:**
```
|move|p1a: OurMon|Toxic|p2a: Gliscor
|-status|p2a: Gliscor|tox
|-heal|p2a: Gliscor|100/100  <-- Poison Heal activates (backfire)
```

### 6.6 Tera Prediction Issues

**Pattern Indicators:**
- ❌ **Uses super-effective move, opponent teras to resist** → tera prediction failed
- ❌ **Wastes our tera at suboptimal time** → tera timing bad
- ❌ **Doesn't adapt to revealed opponent tera** → eval doesn't update

**Replay Log Signatures:**
```
|move|p1a: OurMon|Thunderbolt|p2a: Gyarados
|-terastallize|p2a: Gyarados|Ground  <-- Tera to immune
|-immune|p2a: Gyarados
```

---

## 7. Local LLM Analysis Focus Areas

### 7.1 High-Priority Analysis Tasks

**A. Turn-by-Turn Decision Review**
- **Input:** Decision trace JSON + replay log
- **Task:** For each loss, identify the critical mistake turn
- **Output:** "Turn X: Should have [correct move] instead of [chosen move] because [reason]"
- **Model Requirements:** Reasoning ability, game knowledge

**B. Matchup-Specific Weaknesses**
- **Input:** Batch of replays vs same opponent archetype (e.g., hyper offense, stall)
- **Task:** Identify recurring mistakes vs that archetype
- **Output:** "Bot loses to hyper offense because: [pattern 1], [pattern 2], [pattern 3]"
- **Model Requirements:** Pattern recognition, aggregation

**C. Team Composition Issues**
- **Input:** Team + win rate breakdown by matchup
- **Task:** Identify team weaknesses (e.g., no hazard removal, weak to Volcarona)
- **Output:** "Team is weak to [threat] because [missing coverage/role]"
- **Model Requirements:** Team building knowledge

**D. Ability/Item Inference**
- **Input:** Opponent revealed moves + usage patterns
- **Task:** Infer likely ability/item (e.g., Scarf, Poison Heal, Leftovers)
- **Output:** "Opponent likely has [ability/item] because [evidence]"
- **Model Requirements:** Inference, statistical reasoning

**E. Tera Type Prediction**
- **Input:** Opponent team composition + revealed moves
- **Task:** Predict likely tera type per Pokemon
- **Output:** "Opponent Gyarados likely Tera Ground (avoids Electric OHKO)"
- **Model Requirements:** Meta knowledge, prediction

### 7.2 Prompt Structure for Ollama

**Template:**
```
You are a competitive Pokemon battle analyst. Analyze this replay and identify the bot's critical mistake.

**Replay ID:** {replay_id}
**Result:** Loss
**Opponent:** {opponent_name}
**Rating:** {rating}

**Bot Team:** {team}
**Opponent Team:** {opponent_team}

**Battle Log Snippet (turns {start}-{end}):**
{log_snippet}

**Decision Traces:**
{decision_traces}

**Task:** Identify the turn where the bot made its critical mistake, explain what it should have done instead, and why.

**Format:**
Critical Mistake: Turn {N}
Chosen Move: {move}
Correct Move: {correct_move}
Reason: {reason}
Category: [switch_logic|speed_calc|hazard_awareness|status_timing|tera_prediction|other]
```

**Expected Output:**
```
Critical Mistake: Turn 12
Chosen Move: Thunderbolt
Correct Move: switch Gastrodon
Reason: Opponent's Rillaboom revealed Grassy Glide (priority move). Bot assumed it outsped, but priority moves ignore speed order. Switching to Gastrodon (Water Absorb) would have tanked the hit and forced opponent out.
Category: speed_calc
```

### 7.3 Aggregation & Reporting

**Weekly Summary:**
- Collect all critical mistakes from last N battles
- Group by category (switch_logic, speed_calc, etc.)
- Rank by frequency
- Generate actionable improvements

**Example Report:**
```markdown
## Bot Weaknesses - Week of 2026-02-14

### Top 3 Issues (by frequency)

1. **Switch Logic (32 occurrences)**
   - Pattern: Switches into predicted super-effective moves
   - Root Cause: Eval doesn't predict opponent move before switching
   - Recommendation: Add opponent move prediction layer before switch eval

2. **Speed Calculation (18 occurrences)**
   - Pattern: Assumes outspeed but opponent has priority/Scarf
   - Root Cause: Choice Scarf detection incomplete, priority moves ignored
   - Recommendation: Improve Scarf inference, account for priority in speed calc

3. **Hazard Awareness (14 occurrences)**
   - Pattern: Takes 25%+ chip per switch instead of removing hazards
   - Root Cause: Hazard removal urgency threshold too high
   - Recommendation: Boost hazard removal when cumulative chip > 20%
```

### 7.4 Integration with Existing Pipeline

**Current Pipeline:** `pipeline.py` → `batch_analyzer.py` → Ollama (MAGNETON)

**Enhancement:**
1. **Add decision trace injection:** Include decision traces in prompt
2. **Multi-turn context:** Show 3-5 turns before critical mistake
3. **Categorization:** Tag mistakes by subsystem (enables prioritization)
4. **Feedback loop:** Store analysis → update constants → re-test

**Example Command:**
```bash
# Analyze last 10 losses with decision traces
python pipeline.py analyze --losses-only --include-traces -n 10
```

---

## 8. Recommendations Summary

### 8.1 Quick Wins (Low Effort, High Impact)

1. ✅ **Type immunity checks** - DONE (Commit 019de60, 9281cc7)
2. ✅ **Poison Heal status backfire** - DONE (Commit 87c96b7)
3. ✅ **Thunder Wave speed control** - DONE (Commit 9281cc7)
4. ⚠️ **Choice Scarf detection** - Improve opponent model's item inference
5. ⚠️ **Priority move tracking** - Add priority flag to movepool tracker
6. ⚠️ **Hazard removal urgency** - Lower threshold for BOOST_REMOVE_HAZARDS_HEAVY

### 8.2 Medium Effort, High Impact

1. **Multi-turn damage projection** - Add 2-ply switch eval (hazard chip + predicted damage)
2. **Opponent move prediction** - Use movepool tracker to predict next move before switching
3. **Tera type inference** - Analyze opponent team → predict likely tera types
4. **Setup anticipation** - Switch to phaser/Unaware *before* opponent sets up

### 8.3 High Effort, High Impact

1. **2-ply eval depth** - Add look-ahead for 2-turn sequences
2. **Local LLM re-ranking** - Replace OpenAI with Ollama (latency reduction)
3. **Opponent team analysis** - Infer playstyle (offense/stall) → adjust aggression
4. **Automated constant tuning** - Use replay analysis to auto-adjust penalty values

### 8.4 Local LLM Analysis Focus

**Priority 1: Turn-by-Turn Mistake Identification**
- Analyze decision traces + replay logs
- Identify critical mistake turns
- Categorize by subsystem
- Generate weekly summary reports

**Priority 2: Matchup Pattern Recognition**
- Group losses by opponent archetype
- Identify recurring mistake patterns
- Recommend strategy adjustments

**Priority 3: Team Composition Analysis**
- Analyze team weaknesses
- Identify missing roles (hazard removal, special wall, etc.)
- Recommend team adjustments

---

## 9. File Reference Map

**Core Decision Engine:**
- `fp/search/main.py` - Decision orchestration, eval engine
- `fp/search/forced_lines.py` - Forced sequence detection
- `fp/search/eval.py` - 1-ply position evaluation
- `fp/search/speed_order.py` - Speed calculation
- `fp/search/move_validators.py` - Move legality checks

**State Management:**
- `fp/battle.py` - Battle object, Pokemon state
- `fp/battle_modifier.py` - Protocol parser, state updates
- `fp/run_battle.py` - Battle loop, websocket client

**Data & Constants:**
- `constants_pkg/strategy.py` - Penalty values, Pokemon/move sets
- `constants_pkg/move_flags.py` - Move properties (from moves.json)
- `constants_pkg/pokemon_abilities.py` - Common abilities (from pokedex.json)
- `data/moves.json` - Move database
- `data/pokedex.json` - Pokemon database

**Opponent Modeling:**
- `fp/opponent_model.py` - Opponent move prediction
- `fp/movepool_tracker.py` - Revealed move tracking
- `fp/bayesian_sets.py` - Bayesian set inference

**Hybrid Policy:**
- `fp/hybrid_policy.py` - OpenAI re-ranking
- `fp/decision_trace.py` - Decision logging

**Replay Analysis:**
- `infrastructure/replay_analyzer.py` - Batch replay analyzer
- `replay_analysis/batch_analyzer.py` - Analysis engine
- `pipeline.py` - Automated improvement pipeline

**Testing:**
- `tests/test_eval.py` - Eval engine tests
- `tests/test_threat_bias.py` - Ability/threat tests
- `tests/test_hybrid_policy.py` - Hybrid policy tests

---

**END OF ANALYSIS**

**Next Steps:**
1. Post this document to #deku-workspace
2. Prioritize quick wins (Scarf detection, priority moves)
3. Set up local LLM analysis pipeline (Ollama integration)
4. Begin weekly mistake categorization reports
