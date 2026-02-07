# Phase 3 & 4 Implementation Status

**Purpose**: Track what's been implemented and what remains for decision-making improvements.

## Completed (already in codebase — do NOT re-implement)

- **3.1 Win Condition Awareness** — `is_likely_wincon()`, `our_active_is_wincon`, `opponent_active_is_threat` in `fp/search/main.py`
- **3.3 Momentum Tracking** — `calculate_momentum()` in `fp/search/main.py`, categorizes momentum levels
- **4.1 Endgame Solver** — `fp/search/endgame.py`, solves 1v1 deterministically, integrates before MCTS

## Remaining: Phase 3.2 PP Tracking

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

**Constants to add** (in `constants_pkg/strategy.py`):
```python
# PHASE 3.2: PP Tracking
BOOST_STALL_NO_RECOVERY_PP = 1.4     # +40% stall when opponent recovery exhausted
BOOST_DEFENSIVE_LOW_PP = 1.3         # +30% defensive when opponent STAB low
```

## Remaining: Phase 2 Bayesian Set Inference

See TASKBOARD.md Phase 2 section for details:
- Speed range narrowing
- Bayesian updating as moves/items revealed
- Track revealed information to update set probabilities

## Remaining: Phase 3 Switch Prediction

- PP tracking (above)
- OpponentModel passive/sack tendencies
- Switch prediction from type matchups

## Remaining: Phase 4 Archetype + Adaptive

- Team archetype classification
- Game-phase awareness
- Dynamic team selection

## Implementation Pattern

For each improvement, follow this pattern:

1. **Add constants** to `constants_pkg/strategy.py`
2. **Add fields** to `OpponentAbilityState` dataclass in `fp/search/main.py`
3. **Add detection** in `detect_opponent_abilities()` function
4. **Add penalties/boosts** in `apply_ability_penalties()` function
5. **Update logging** in `find_best_move()` detected_abilities list
6. **Test syntax**: `python -c "import ast; ast.parse(open('fp/search/main.py').read())"`
7. **Test imports**: `python -c "from fp.search.main import find_best_move; print('OK')"`
