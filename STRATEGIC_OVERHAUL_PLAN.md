# FOULER PLAY STRATEGIC OVERHAUL PLAN
**Date:** 2026-02-15  
**Target:** From 57% WR (move-by-move poker) → 70%+ WR (strategic chess)  
**Scope:** Add strategic layer to decision pipeline

---

## PROBLEM STATEMENT

Bot plays move-by-move isolation, not multi-turn sequences. Results:
- Hazard teams skip hazard setup
- Pivot teams switch randomly
- Stall teams play reactive
- Games lost in 15 turns on archetype failure

**Root Cause:** Decision eval operates in vacuum (current turn only). No concept of "how do I WIN with this team?"

---

## ARCHITECTURE: STRATEGIC DECISION LAYER

### Current Pipeline (Broken)
```
Battle State → Threat Recognition → Damage Calc → Eval Scoring → Move Selection
                                                    (isolated per turn)
```

### New Pipeline (Fixed)
```
Battle State → Team Archetype → Win Condition → Multi-turn Planning
                    ↓               ↓                  ↓
              [stall/pivot/       [what we         [3-5 turn
               hazard/setup]      win by]          lookahead]
                                                     ↓
            Threat Recognition → Game Phase Eval → Move Filter
                                  (early/mid/late)  (strategic)
                                    ↓
            Damage Calc → Penalty System → Strategic Eval → Move Selection
            (existing)     (existing)      (NEW LAYER)    (commit!)
```

---

## PHASE 1: ARCHETYPE RECOGNITION (2-3 hours)

**File:** `fp/archetype_analyzer.py` (NEW)

### 1.1 Archetype Classification Engine
```python
class ArchetypeAnalyzer:
    def classify_team(self, team: Team) -> TeamArchetype
    # Input: 6 Pokemon
    # Output: Archetype enum + confidence + win condition
    
    # Returns:
    # {
    #   "archetype": "HazardStack" | "Pivot" | "StallCore" | "SetupSweeper" | "HO" | "Balanced",
    #   "confidence": 0.0-1.0,
    #   "primary_wincondition": str,  # "set rocks early" | "maintain momentum" | "wall + status"
    #   "secondary_wincondition": str,
    #   "critical_pokemon": [str],  # ["Blissey", "Skarmory", "Gholdengo"]
    #   "mandatory_setup": [str],  # ["Stealth Rock", "Spikes"]
    #   "prohibited_swaps": [(str, str)],  # Switches that lose game
    # }
```

### 1.2 Detection Rules
```
HAZARD STACK:
  - Detects: Multiple hazard setters (Stealth Rock + Spikes/Toxic Spikes)
  - Indicators: Blissey/Skarmory, defensive core, wall sweeper finisher
  - Win Condition: "Set all hazards turn 1-4, then wall/wear"
  - Mandatory: Both hazard types up within 4 turns
  
PIVOT / MOMENTUM:
  - Detects: 3+ U-turn/Parting Shot/Teleport pivots
  - Indicators: Weak individual stats, strong as ensemble
  - Win Condition: "Pivot cycles, gain positioning advantage"
  - Mandatory: Never switch into bad matchup unprepared
  - Anti-Pattern: >5 switches in 8 turns = failure state
  
STALL CORE:
  - Detects: 3+ pure walls (0 offensive moves), Rest Talk, status immunity
  - Indicators: Blissey/Ting-Lu/Dondozo, slow sweeper finisher
  - Win Condition: "Survive indefinitely, wear with chip/status"
  - Mandatory: Establish defensive wall within turn 5
  - Anti-Pattern: Offensive player is still alive turn 15+ = losing
  
SETUP SWEEPER:
  - Detects: Physical/Special sweeper, setup move (Swords Dance/Nasty Plot)
  - Indicators: Unaware/Dragonite-like, fragile core
  - Win Condition: "Setup -> sweep"
  - Mandatory: Preserve sweeper HP until setup turn
  
BALANCED / HO:
  - Detects: Mixed offensive/defensive, no clear singular strategy
  - Indicators: All pokemon 60+ attack/spdef
  - Win Condition: "Pressure opponent, execute what's available"
```

### 1.3 Implementation
```python
# In battle_decision.py, BEFORE eval:
archetype = analyzer.classify_team(my_team)
gameplan = generate_gameplan(archetype)

# Pass to eval:
eval_result = eval_position(..., archetype=archetype, gameplan=gameplan)
```

---

## PHASE 2: GAMEPLAN GENERATION (2 hours)

**File:** `fp/gameplan_generator.py` (NEW)

### 2.1 Gameplan Structure
```python
@dataclass
class Gameplan:
    archetype: str
    primary_win_condition: str  # "hazard wear"
    secondary_win_condition: str  # "setup sweep finisher"
    
    # Critical milestones
    early_game_goal: str  # e.g., "both hazards up by turn 5"
    mid_game_goal: str   # e.g., "defend and pivot"
    late_game_goal: str  # e.g., "execute with Gholdengo"
    
    # Resource constraints
    critical_pokemon: list  # ["Blissey", "Skarmory", "Gholdengo"]
    hp_minimums: dict      # {"Blissey": 0.7, "Gholdengo": 0.8}  (can't drop below 70% HP)
    mandatory_moves: dict  # {"Skarmory": ["Stealth Rock", "Spikes"]}
    
    # Anti-patterns
    prohibited_switches: list  # [(from, to, reason)]
    switch_budget: int  # "don't exceed 4 switches in 8 turns"
    
    # Decision filters
    must_happen_by_turn: dict  # {"Stealth Rock": 4, "Spikes": 5}
```

### 2.2 Gameplan Rules Engine
```python
def generate_gameplan(archetype: str, team: Team) -> Gameplan:
    if archetype == "HazardStack":
        return Gameplan(
            archetype="HazardStack",
            primary_win_condition="Establish hazards early, wear via defensive core",
            critical_pokemon=["Blissey", "Skarmory", "Gholdengo"],
            hp_minimums={"Blissey": 0.7, "Gholdengo": 0.8},
            mandatory_moves={"Skarmory": ["Stealth Rock", "Spikes"]},
            must_happen_by_turn={"Stealth Rock": 4, "Spikes": 5},
            prohibited_switches=[
                (Gholdengo, opponent_Dark, "no Dark resistance"),
            ]
        )
    
    elif archetype == "Pivot":
        return Gameplan(
            archetype="Pivot",
            primary_win_condition="Maintain momentum through pivot cycles",
            switch_budget=6,  # Allow more switches, but purposeful
            prohibited_switches=[
                (Ogerpon, wall_normal, "Defiant breaks walling"),
                (Corviknight, Lightning_threat, "no good switch-in"),
            ]
        )
    # ... etc for all archetypes
```

---

## PHASE 3: STRATEGIC MOVE FILTERING (3-4 hours)

**File:** `fp/strategic_filter.py` (NEW)

### 3.1 Pre-Eval Filtering
```python
def filter_moves_strategically(
    available_moves: list,
    game_state: GameState,
    gameplan: Gameplan,
    turn_number: int
) -> FilteredMoves:
    """
    Remove moves that contradict the gameplan BEFORE they reach eval.
    This is a hard constraint, not a penalty.
    """
    
    filtered = available_moves.copy()
    
    # RULE 1: Mandatory moves come first
    if gameplan.must_happen_by_turn["Stealth Rock"] >= turn_number:
        mandatory = [m for m in filtered if m.name == "Stealth Rock" and not game_state.has_hazard("stealth_rock")]
        if mandatory:
            return mandatory  # FORCE this move
    
    # RULE 2: Commit to Pokemon if injured
    if active_pokemon.current_hp < gameplan.hp_minimums[active_pokemon.name]:
        filtered = [m for m in filtered if not m.makes_switch]  # Don't switch injured critical
    
    # RULE 3: Prevent prohibited switches
    for (from_poke, to_poke, reason) in gameplan.prohibited_switches:
        if current_pokemon == from_poke and any(s.target == to_poke for s in filtered):
            filtered = [m for m in filtered if m.target != to_poke]
    
    # RULE 4: Excessive switching penalty
    if game_state.switch_count_last_8_turns > gameplan.switch_budget:
        # Remove all switches except critical
        filtered = [m for m in filtered if not m.makes_switch or m.reason == "critical"]
    
    return filtered
```

### 3.2 Post-Eval Filtering (Commitment)
```python
def apply_commitment_heuristic(
    eval_scores: dict,  # {move: score}
    last_decision: str,
    game_state: GameState
) -> dict:
    """
    Reduce indecision: if you chose to stay in, commit for 1-2 more turns.
    This prevents the "switch, switch back, switch away" spiral.
    """
    
    # If we're in an attacking pokemon, boost attack moves vs switches
    if last_decision == "attack" and turns_in_current_pokemon < 2:
        for move in eval_scores:
            if not move.makes_switch:
                eval_scores[move] *= 1.15  # Boost non-switch by 15%
            else:
                eval_scores[move] *= 0.85  # Penalize switch by 15%
    
    return eval_scores
```

---

## PHASE 4: MULTI-TURN PLANNING (4-5 hours)

**File:** `fp/multi_turn_planner.py` (NEW)

### 4.1 3-Turn Lookahead
```python
class MultiTurnPlanner:
    def evaluate_sequence(
        self,
        current_state: GameState,
        candidate_move: Move,
        gameplan: Gameplan,
        depth: int = 3
    ) -> float:
        """
        Instead of evaluating move in isolation, evaluate the SEQUENCE.
        
        "If I make this move, what 3-turn path opens up?"
        "Does it advance my win condition?"
        """
        
        sequence_value = 0.0
        
        # Simulate next 3 turns
        simulated_state = current_state.copy()
        
        for turn_ahead in range(1, depth + 1):
            simulated_state = simulated_state.apply_move(candidate_move)
            
            # What's the best response I can make?
            best_follow_up = self.find_best_follow_up(simulated_state, gameplan)
            
            # Does this follow-up advance gameplan?
            gameplan_alignment = self.alignment_score(best_follow_up, gameplan, turn_number + turn_ahead)
            
            sequence_value += gameplan_alignment * (0.5 ** turn_ahead)  # Discount future
        
        return sequence_value
    
    def alignment_score(self, move: Move, gameplan: Gameplan, turn: int) -> float:
        """
        How well does this move align with the gameplan?
        
        For hazard teams: Setting hazards = 1.0, anything else = 0.3-0.7
        For pivot teams: Maintaining momentum = 0.9, random switch = 0.4
        For stall teams: Recovering/walling = 0.9, switching = 0.6
        """
        
        if gameplan.archetype == "HazardStack":
            if move.name in ["Stealth Rock", "Spikes"] and not game_state.has_hazard(move.name):
                return 1.0
            elif move.recovers_hp:
                return 0.7
            elif move.makes_switch:
                return 0.5
            else:
                return 0.6
        
        # ... etc for other archetypes
```

### 4.2 Game Phase Evaluation
```python
def get_game_phase(game_state: GameState) -> str:
    """Early / Mid / Late game"""
    
    total_hp = sum(p.current_hp / p.max_hp for p in game_state.all_pokemon)
    avg_hp = total_hp / 12  # 6 my team + 6 opp team
    
    if avg_hp > 0.7:
        return "early"  # Teams mostly fresh
    elif avg_hp > 0.4:
        return "mid"    # Significant damage taken
    else:
        return "late"   # Endgame
```

### 4.3 Phase-Specific Eval Adjustments
```python
def eval_position_with_gameplan(
    state: GameState,
    gameplan: Gameplan,
    game_phase: str
) -> dict:
    """
    Early game: Prioritize setup/positioning
    Mid game: Maintain advantage, execute offense
    Late game: Finisher cleanup, resource management
    """
    
    base_eval = eval_position(state)  # Existing eval
    
    if game_phase == "early":
        # Boost setup moves, hazard setup
        for move in base_eval:
            if move.name in gameplan.mandatory_moves.get(state.active_pokemon, []):
                base_eval[move] *= 1.5
    
    elif game_phase == "mid":
        # Neutral — existing eval is good here
        pass
    
    elif game_phase == "late":
        # Boost finisher execution, reduce defensive moves
        if state.active_pokemon in gameplan.critical_pokemon:
            for move in base_eval:
                if move.is_offensive:
                    base_eval[move] *= 1.2
    
    return base_eval
```

---

## PHASE 5: INTEGRATION INTO DECISION PIPELINE (2-3 hours)

**File:** `fp/battle_decision.py` (MODIFY)

### 5.1 Decision Flow
```python
def decide_next_move(game_state: GameState) -> Move:
    """Main decision entry point"""
    
    # Step 1: Classify archetype (done once per battle)
    if not game_state.has_cached_archetype:
        archetype = ArchetypeAnalyzer().classify_team(my_team)
        gameplan = GameplanGenerator().generate(archetype, my_team)
        game_state.cache_archetype(archetype, gameplan)
    else:
        archetype = game_state.cached_archetype
        gameplan = game_state.cached_gameplan
    
    # Step 2: Get available moves
    available_moves = get_available_moves(game_state)
    
    # Step 3: STRATEGIC FILTERING (hard constraints)
    filtered_moves = strategic_filter.filter_moves(
        available_moves,
        game_state,
        gameplan,
        turn_number=game_state.turn_count
    )
    
    # Step 4: Existing eval (threats, damage, etc.)
    eval_scores = eval_position(game_state, filtered_moves)
    
    # Step 5: Multi-turn lookahead
    game_phase = multi_turn_planner.get_game_phase(game_state)
    for move in filtered_moves:
        sequence_value = multi_turn_planner.evaluate_sequence(
            game_state, move, gameplan, depth=3
        )
        eval_scores[move] *= (0.6 + 0.4 * sequence_value)  # Blend with existing
    
    # Step 6: Phase-specific adjustments
    phase_adjusted = eval_position_with_gameplan(
        game_state, gameplan, game_phase
    )
    for move in filtered_moves:
        eval_scores[move] *= phase_adjusted.get(move, 1.0)
    
    # Step 7: Commitment heuristic (reduce switching indecision)
    eval_scores = commitment_heuristic.apply(eval_scores, last_decision, game_state)
    
    # Step 8: SELECT BEST MOVE and COMMIT
    best_move = max(filtered_moves, key=lambda m: eval_scores[m])
    log_decision(best_move, eval_scores[best_move], gameplan, game_phase)
    
    return best_move
```

---

## PHASE 6: TESTING & VALIDATION (4-5 hours)

### 6.1 Unit Tests
```python
# tests/test_archetype_analyzer.py
def test_hazard_stack_detection():
    team = load_team("fat-team-1-stall")
    archetype = ArchetypeAnalyzer().classify_team(team)
    assert archetype.archetype == "HazardStack"
    assert "Stealth Rock" in archetype.mandatory_setup
    assert archetype.critical_pokemon.contains("Blissey")

def test_pivot_momentum_detection():
    team = load_team("fat-team-2-pivot")
    archetype = ArchetypeAnalyzer().classify_team(team)
    assert archetype.archetype == "Pivot"
    assert archetype.primary_wincondition.contains("momentum")

# tests/test_gameplan.py
def test_gameplan_forces_hazard_setup():
    gameplan = GameplanGenerator().generate("HazardStack", team)
    filtered = strategic_filter.filter_moves(
        all_moves, game_state, gameplan, turn=1
    )
    assert filtered == [stealth_rock_move]  # Only Stealth Rock available

# tests/test_multi_turn.py
def test_sequence_evaluation():
    seq_value = planner.evaluate_sequence(
        state, candidate_move, gameplan, depth=3
    )
    # Verify sequences that advance gameplan score higher
    assert seq_value > 0.5  # Arbitrary threshold
```

### 6.2 Integration Tests (on actual battles)
```
Run 20 test matches per archetype:
- stall team: Verify both hazards set by turn 5, WR >65%
- pivot team: Verify <6 switches per game, maintain momentum, WR >65%
- dondozo team: Verify defensive wall established turn 3, WR >65%

Success criteria:
- Each archetype WR individually >65%
- No excessive switching (>8 in 15 turns)
- Hazard setup reliable
- Late game execution clean
```

---

## TIMELINE & EFFORT

| Phase | Hours | Subtasks |
|-------|-------|----------|
| 1. Archetype Recognition | 2-3 | Detection rules, classification, tests |
| 2. Gameplan Generation | 2 | Rules engine, data structures |
| 3. Strategic Filtering | 3-4 | Hard constraints, commitment heuristic, tests |
| 4. Multi-turn Planning | 4-5 | 3-turn lookahead, phase eval, scoring |
| 5. Integration | 2-3 | Merge into battle_decision.py, logging |
| 6. Testing & Validation | 4-5 | Unit tests, 60 integration battles, debugging |
| **TOTAL** | **18-22 hours** | ~1 full workday + debugging |

---

## EXPECTED OUTCOMES

### Before Overhaul (Current)
- WR: 57% (220W-160L)
- Archetype-awareness: None
- Multi-turn planning: None
- Gameplan commitment: None

### After Phase 1-3 (Quick Wins)
- WR: 62-64% (hazard setup forced, excessive switching reduced)
- Hazard teams now set hazards reliably
- Switch spam reduced significantly

### After Phase 4-6 (Full Overhaul)
- WR: 70%+ (estimated, validated via testing)
- Multi-turn lookahead active
- Game phase awareness live
- Each archetype individually 65%+ WR

---

## BUILD ORDER (Recommended)

1. **Start with Phase 1 (Archetype Analysis)** — Foundation
2. **Immediately test with Phase 3 (Strategic Filtering)** — Quick wins, high impact
   - Force hazard setup
   - Reduce switch spam
   - Measure WR improvement
3. **Phase 2 (Gameplan Generation)** — Refine forced rules
4. **Phase 4 (Multi-turn Planning)** — Add depth
5. **Phase 5 & 6** — Integrate + test end-to-end

---

## RISK MITIGATION

**Risk:** Gameplan is too rigid, locks bot into bad strategies  
**Mitigation:** All "mandatory" moves are conditional on being beneficial. Strategic filter can be overridden by eval if position is clearly bad.

**Risk:** Multi-turn lookahead is too slow (timeout)  
**Mitigation:** Depth=3 is shallow. Use sampling, not exhaustive search.

**Risk:** Over-optimizing for archetype misses unique opportunities  
**Mitigation:** Gameplan is decision *filter*, not decision *maker*. Eval still ranks moves.

---

## METRICS TO TRACK

- Overall WR per archetype (stall/pivot/dondozo)
- Average switches per game
- Hazard setup success rate (% of games with both hazards by turn 5)
- Game duration (should increase as bot commits to longer plans)
- Decision time per turn (should stay <5s)

---

## CONCLUSION

This is not a tuning fix. It's a structural transformation from "isolated move selection" to "strategic gameplan execution."

The bot will learn to think in multi-turn sequences, recognize what each team is *for*, and execute accordingly.

**Expected jump: 57% → 70%+ WR**

If this doesn't work, the problem is elsewhere (damage calc, threat recognition, etc.). But diagnosis shows the bot is strategically blind — this layer will fix that.

---

**Status:** PLAN READY FOR IMPLEMENTATION  
**Next Step:** Begin Phase 1 (Archetype Analysis)
