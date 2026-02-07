# Phase 3 & 4 Implementation Handoff Document

> **STATUS AS OF 2026-02-07:** Phases 3.1, 3.3, and 4.1 are ALREADY IMPLEMENTED in the codebase.
> The only remaining item from this doc is **Phase 3.2 PP Tracking** (see bottom of file).
> Do NOT re-implement anything marked as DONE below — it's already in the code.

**Purpose**: If the previous agent ran out of tokens, read this document and continue implementing Phases 3 and 4 of the decision-making improvements.

## Completed Work

### Phase 1 (DONE)
- 1.1 Positive Boosts Expansion
- 1.2 Trick Room Awareness
- 1.3 Screens Awareness
- 1.4 Weather/Terrain Synergies

### Phase 2 (DONE)
- 2.1 Switch Evaluation System (`apply_switch_penalties()` function)
- 2.2 Entry Hazard Calculus
- 2.3 Tera Prediction

---

## Phase 3: New State Tracking (TO IMPLEMENT)

### 3.1 Win Condition Awareness
**Goal**: Identify which Pokemon are win conditions and protect them.

**Implementation Steps**:
1. Create `fp/team_analysis.py` if it doesn't exist, or add to existing
2. Add function `identify_win_conditions(team)`:
   - Score each Pokemon: +3 if has setup move, +2 if speed >100, +2 if threatens 3+ opponent Pokemon
   - Pokemon with score >= 4 are win conditions
3. Add function `identify_critical_checks(our_team, opponent_pokemon)`:
   - Find which of our Pokemon can check a specific threat
4. Add to `OpponentAbilityState`:
   ```python
   our_active_is_wincon: bool = False
   our_active_is_only_check: bool = False  # Only check to a major threat
   ```
5. Add detection in `detect_opponent_abilities()`:
   - Check if active Pokemon is in win condition list
   - Check if active is the only counter to opponent's ace
6. Add penalties/boosts in `apply_ability_penalties()`:
   - If active is wincon: -40% risky plays, +30% safe plays
   - If active is only check to threat: -50% trading/sacking
   - If opponent's wincon is active: +40% revenge killing moves

**Constants to add** (in `constants_pkg/strategy.py`):
```python
# PHASE 3.1: Win Condition Awareness
PENALTY_RISKY_WITH_WINCON = 0.6      # -40% risky plays when active is wincon
BOOST_SAFE_WITH_WINCON = 1.3         # +30% safe plays
PENALTY_SACK_ONLY_CHECK = 0.5        # -50% trading when only check
BOOST_REVENGE_KILL_THREAT = 1.4      # +40% revenge killing opponent wincon
```

---

### 3.2 PP Tracking
**Goal**: Track opponent's move PP usage for stall situations.

**Implementation Steps**:
1. Add to `fp/battle.py` Pokemon class:
   ```python
   opponent_move_uses: dict[str, int] = field(default_factory=dict)

   def record_opponent_move(self, move_name: str):
       self.opponent_move_uses[move_name] = self.opponent_move_uses.get(move_name, 0) + 1

   def estimate_pp_remaining(self, move_name: str) -> int:
       from data import all_move_json
       move_data = all_move_json.get(move_name, {})
       max_pp = move_data.get("pp", 5)  # Default 5 if unknown
       used = self.opponent_move_uses.get(move_name, 0)
       return max(0, max_pp - used)
   ```

2. Update `fp/battle_modifier.py`:
   - In the handler for `|move|` messages, call `record_opponent_move()`

3. Add to `OpponentAbilityState`:
   ```python
   opponent_low_pp_moves: list[str] = field(default_factory=list)  # Moves at <3 PP
   opponent_recovery_low_pp: bool = False  # Recovery move at low PP
   ```

4. Add detection in `detect_opponent_abilities()`:
   - Check opponent's tracked moves for low PP
   - Flag if recovery move is running low

5. Add boosts in `apply_ability_penalties()`:
   - If opponent's only recovery is at 0 PP: +40% stall tactics
   - If opponent's main STAB at low PP: +30% defensive plays

**Constants to add**:
```python
# PHASE 3.2: PP Tracking
BOOST_STALL_NO_RECOVERY_PP = 1.4     # +40% stall when opponent recovery exhausted
BOOST_DEFENSIVE_LOW_PP = 1.3         # +30% defensive when opponent STAB low
```

---

### 3.3 Momentum Tracking
**Goal**: Track who has momentum and adjust risk tolerance.

**Implementation Steps**:
1. Add function `calculate_momentum(battle) -> float`:
   ```python
   def calculate_momentum(battle: Battle) -> float:
       momentum = 0.0

       # HP advantage (sum of HP percentages)
       our_hp = sum(p.hp / max(p.max_hp, 1) for p in battle.user.reserve + [battle.user.active] if p and p.hp > 0)
       opp_hp = sum(p.hp / max(p.max_hp, 1) for p in battle.opponent.reserve + [battle.opponent.active] if p and p.hp > 0)
       momentum += (our_hp - opp_hp) * 0.5

       # Pokemon count advantage
       our_alive = sum(1 for p in battle.user.reserve + [battle.user.active] if p and p.hp > 0)
       opp_alive = sum(1 for p in battle.opponent.reserve + [battle.opponent.active] if p and p.hp > 0)
       momentum += (our_alive - opp_alive) * 1.5

       # Hazard advantage
       our_hazards = battle.user.side_conditions.get(constants.STEALTH_ROCK, 0) * 2 + battle.user.side_conditions.get(constants.SPIKES, 0)
       opp_hazards = battle.opponent.side_conditions.get(constants.STEALTH_ROCK, 0) * 2 + battle.opponent.side_conditions.get(constants.SPIKES, 0)
       momentum += (opp_hazards - our_hazards) * 0.5

       return momentum  # Positive = we have momentum
   ```

2. Add to `OpponentAbilityState`:
   ```python
   momentum: float = 0.0  # Positive = we have momentum
   momentum_level: str = "neutral"  # "strong_positive", "positive", "neutral", "negative", "strong_negative"
   ```

3. Add detection in `detect_opponent_abilities()`:
   - Call `calculate_momentum()`
   - Categorize: >5 = strong_positive, 1-5 = positive, -1 to 1 = neutral, -5 to -1 = negative, <-5 = strong_negative

4. Add penalties/boosts in `apply_ability_penalties()`:
   - Strong positive momentum: +30% aggressive/setup moves
   - Positive momentum: +20% pressure moves
   - Negative momentum: +20% pivots, safe switches
   - Strong negative momentum: +30% high-risk/high-reward plays

**Constants to add**:
```python
# PHASE 3.3: Momentum Tracking
BOOST_AGGRESSIVE_STRONG_MOMENTUM = 1.3   # +30% aggressive when strong momentum
BOOST_PRESSURE_MOMENTUM = 1.2            # +20% pressure when positive momentum
BOOST_PIVOT_NEGATIVE_MOMENTUM = 1.2      # +20% pivots when negative momentum
BOOST_HIGHRISK_DESPERATE = 1.3           # +30% high-risk when very behind
```

---

## Phase 4: Advanced Reasoning (TO IMPLEMENT)

### 4.1 Endgame Solver
**Goal**: For 2-3 Pokemon endgames, calculate deterministic optimal play.

**Implementation Steps**:
1. Create new file `fp/search/endgame.py`:
   ```python
   from dataclasses import dataclass
   from typing import Optional, Tuple
   from fp.battle import Battle
   import constants
   from fp.helpers import type_effectiveness_modifier

   @dataclass
   class EndgameResult:
       best_move: str
       expected_outcome: float  # 1.0 = win, 0.0 = lose, 0.5 = draw/uncertain
       is_deterministic: bool
       depth_searched: int

   def is_endgame(battle: Battle) -> bool:
       """Check if we're in a simple endgame scenario."""
       our_alive = sum(1 for p in battle.user.reserve + [battle.user.active] if p and p.hp > 0)
       opp_alive = sum(1 for p in battle.opponent.reserve + [battle.opponent.active] if p and p.hp > 0)
       return our_alive <= 3 and opp_alive <= 3

   def can_outspeed(our_pokemon, opp_pokemon) -> bool:
       """Check if we outspeed opponent."""
       our_speed = our_pokemon.stats.get(constants.SPEED, 50)
       opp_speed = opp_pokemon.stats.get(constants.SPEED, 50)
       return our_speed > opp_speed

   def estimate_damage(attacker, defender, move_name) -> float:
       """Rough damage estimate as fraction of defender HP."""
       from data import all_move_json
       move_data = all_move_json.get(move_name, {})
       base_power = move_data.get("basePower", 0)
       if base_power == 0:
           return 0.0

       move_type = move_data.get("type", "normal")
       category = move_data.get("category", "Status")

       if category == "Physical":
           atk = attacker.stats.get(constants.ATTACK, 100)
           def_ = defender.stats.get(constants.DEFENSE, 100)
       elif category == "Special":
           atk = attacker.stats.get(constants.SPECIAL_ATTACK, 100)
           def_ = defender.stats.get(constants.SPECIAL_DEFENSE, 100)
       else:
           return 0.0

       # Type effectiveness
       effectiveness = type_effectiveness_modifier(move_type, defender.types)

       # STAB
       stab = 1.5 if attacker.has_type(move_type) else 1.0

       # Rough damage formula (simplified)
       damage = (((2 * 100 / 5 + 2) * base_power * atk / def_) / 50 + 2) * effectiveness * stab
       damage_ratio = damage / max(defender.max_hp, 1)

       return min(damage_ratio, 1.0)

   def can_ko(attacker, defender) -> Tuple[bool, str]:
       """Check if attacker can KO defender, return (can_ko, best_move)."""
       best_damage = 0.0
       best_move = None

       for move in attacker.moves:
           move_name = move.name if hasattr(move, "name") else str(move)
           damage = estimate_damage(attacker, defender, move_name)
           if damage > best_damage:
               best_damage = damage
               best_move = move_name

       defender_hp_ratio = defender.hp / max(defender.max_hp, 1)
       return best_damage >= defender_hp_ratio, best_move

   def solve_1v1(our_pokemon, opp_pokemon) -> EndgameResult:
       """Solve a 1v1 endgame."""
       we_outspeed = can_outspeed(our_pokemon, opp_pokemon)
       we_can_ko, our_best_move = can_ko(our_pokemon, opp_pokemon)
       they_can_ko, _ = can_ko(opp_pokemon, our_pokemon)

       if we_outspeed and we_can_ko:
           return EndgameResult(our_best_move, 1.0, True, 1)
       elif not we_outspeed and they_can_ko:
           # They KO us first, we lose
           # But we should still attack
           return EndgameResult(our_best_move or "struggle", 0.0, True, 1)
       elif we_outspeed and not we_can_ko and they_can_ko:
           # We attack first but can't KO, they KO us back
           return EndgameResult(our_best_move or "struggle", 0.0, True, 1)
       else:
           # Complex situation, not deterministic
           return EndgameResult(our_best_move or "struggle", 0.5, False, 1)

   def solve_endgame(battle: Battle, max_depth: int = 4) -> Optional[EndgameResult]:
       """
       Attempt to solve an endgame position.
       Returns None if too complex or not an endgame.
       """
       if not is_endgame(battle):
           return None

       our_alive = [p for p in battle.user.reserve + [battle.user.active] if p and p.hp > 0]
       opp_alive = [p for p in battle.opponent.reserve + [battle.opponent.active] if p and p.hp > 0]

       # 1v1 is solvable
       if len(our_alive) == 1 and len(opp_alive) == 1:
           return solve_1v1(our_alive[0], opp_alive[0])

       # 2v1 or 1v2 - simplified heuristic
       if len(our_alive) == 1 and len(opp_alive) == 2:
           # We're down, try to trade favorably
           return EndgameResult(None, 0.3, False, 1)  # Likely losing

       if len(our_alive) == 2 and len(opp_alive) == 1:
           # We're up, play safe
           result = solve_1v1(battle.user.active, opp_alive[0])
           if result.expected_outcome >= 0.5:
               return result
           # If current matchup is bad, consider switching
           return EndgameResult(None, 0.7, False, 1)  # Likely winning

       # More complex endgames - return None to let MCTS handle
       return None
   ```

2. Integrate in `fp/search/main.py`:
   - In `find_best_move()`, before MCTS:
   ```python
   from fp.search.endgame import is_endgame, solve_endgame

   # Early in find_best_move():
   if is_endgame(battle):
       solution = solve_endgame(battle)
       if solution and solution.is_deterministic and solution.best_move:
           logger.info(f"Endgame solved: {solution.best_move} (outcome: {solution.expected_outcome})")
           return solution.best_move
   ```

---

## Implementation Pattern

For each improvement, follow this pattern:

1. **Add constants** to `constants_pkg/strategy.py`
2. **Add fields** to `OpponentAbilityState` dataclass in `fp/search/main.py`
3. **Add detection** in `detect_opponent_abilities()` function
4. **Add penalties/boosts** in `apply_ability_penalties()` function
5. **Update quick-exit check** at start of `apply_ability_penalties()`
6. **Update logging** in `find_best_move()` detected_abilities list
7. **Test syntax**: `python -c "import ast; ast.parse(open('fp/search/main.py').read())"`
8. **Test imports**: `python -c "from fp.search.main import find_best_move; print('OK')"`

---

## Key Files

| File | Purpose |
|------|---------|
| `fp/search/main.py` | Main decision logic, OpponentAbilityState, penalties |
| `constants_pkg/strategy.py` | All penalty/boost constants, Pokemon/move sets |
| `fp/battle.py` | Battle state classes |
| `fp/battle_modifier.py` | Protocol parsing, state updates |
| `fp/team_analysis.py` | Team analysis (may need to create/extend) |
| `fp/search/endgame.py` | New file for endgame solver |

---

## Testing

After implementing, verify:
```bash
cd C:/Users/mtoli/Documents/Code/fouler-play
python -c "import ast; ast.parse(open('fp/search/main.py').read()); print('main.py OK')"
python -c "import ast; ast.parse(open('constants_pkg/strategy.py').read()); print('strategy.py OK')"
python -c "from fp.search.main import find_best_move; print('imports OK')"
```

---

## Status Tracking

Mark as DONE when complete:
- [x] 3.1 Win Condition Awareness (IMPLEMENTED)
- [ ] 3.2 PP Tracking (NOT IMPLEMENTED - requires battle_modifier.py changes)
- [x] 3.3 Momentum Tracking (IMPLEMENTED)
- [x] 4.1 Endgame Solver (IMPLEMENTED - fp/search/endgame.py created)

## What Was Implemented

### Phase 3.1: Win Condition Awareness
- Added `is_likely_wincon()` function to identify win conditions
- Added `our_active_is_wincon` and `opponent_active_is_threat` to OpponentAbilityState
- Penalizes risky moves (-40%) when active is win condition
- Boosts safe moves (+30%) when active is win condition
- Boosts revenge killing (+40%) when opponent has boosted threat

### Phase 3.3: Momentum Tracking
- Added `calculate_momentum()` function
- Tracks HP advantage, Pokemon count, hazard advantage
- Categorizes momentum: strong_positive, positive, neutral, negative, strong_negative
- Boosts aggressive/setup (+30%) with strong positive momentum
- Boosts pressure (+20%) with positive momentum
- Boosts pivots (+20%) with negative momentum
- Boosts high-risk plays (+30%) when desperate (strong negative)

### Phase 4.1: Endgame Solver
- Created `fp/search/endgame.py` with full implementation
- Solves 1v1 endgames deterministically
- Handles 2v1 and 1v2 scenarios with heuristics
- Integrates into find_best_move() before MCTS
- Skips MCTS for solved endgames (faster, more accurate)

## What Remains: Phase 3.2 PP Tracking

This requires modifying `fp/battle_modifier.py` to track move usage. The implementation plan is in the document above.
