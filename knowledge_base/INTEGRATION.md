# Knowledge Base Integration Guide

How to integrate the Knowledge Base into Fouler Play's MCTS decision-making.

## Current State

MCTS scoring currently uses hardcoded constants from `constants.py`:
- `POKEMON_COMMONLY_UNAWARE` sets
- `STATUS_INFLICTING_MOVES` sets
- `ABILITY_PENALTY_*` values
- Move type lists (WATER_TYPE_MOVES, ELECTRIC_TYPE_MOVES, etc.)

## Integration Points

### 1. Move Lookup

**Before (constants.py):**
```python
WATER_TYPE_MOVES = {
    "scald", "surf", "hydropump", "waterfall", ...
}
```

**After (knowledge_base):**
```python
from knowledge_base import kb

# Get all Water-type moves
water_moves = {
    name for name, data in kb.moves.items()
    if data.get("type") == "water"
}

# Or check if specific move is Water-type
move_data = kb.get_move("scald")
is_water = move_data and move_data.get("type") == "water"
```

### 2. Ability Detection

**Current:**
Hardcoded Pokemon sets for each ability check:
```python
POKEMON_COMMONLY_FOCUS_SASH = {"breloom", "alakazam", ...}
```

**With KB:**
```yaml
# abilities/focus_sash.yaml
focus_sash:
  type: item
  effect: survive_ohko
  common_holders:
    - breloom
    - alakazam
    - rampardos
```

```python
# In code
sash_holders = kb.get_item("focus_sash").get("common_holders", [])
has_sash = pokemon_name in sash_holders
```

### 3. Matchup-Based Scoring

**New capability:**
```python
# In MCTS scoring
def score_switch_candidate(our_pokemon, opponent_active):
    our_type = get_primary_type(our_pokemon)
    opp_type = get_primary_type(opponent_active)
    
    # Look up matchup pattern
    matchup_key = f"{our_type}_vs_{opp_type}"
    matchup = kb.get_matchup(matchup_key)
    
    if matchup:
        # Boost score if we have offensive advantage
        if matchup.get("offensive_effectiveness", 1.0) > 1.0:
            score += 0.2
        
        # Warn about common switches
        if opponent_likely_to_switch(matchup.get("common_switches", [])):
            score -= 0.1
        
        # Consider dangerous coverage
        if has_coverage(opponent_active, matchup.get("dangerous_coverage", [])):
            score -= 0.15
    
    return score
```

### 4. Battle Phase Strategy

**New capability:**
```python
def adjust_aggression_for_phase(turn_count, score):
    # Determine current battle phase
    if turn_count <= 5:
        phase = kb.get_strategy("early_game")
        # Early game: prioritize hazards and scouting
        if is_hazard_move(candidate):
            score += 0.15
    elif turn_count <= 15:
        phase = kb.get_strategy("mid_game")
        # Mid game: win key matchups
        if matchup_advantage:
            score += 0.1
    else:
        phase = kb.get_strategy("late_game")
        # Late game: preserve wincon, calculate exact damage
        if is_wincon_pokemon(our_active):
            # Be more conservative
            if risky_move:
                score -= 0.2
    
    return score
```

## Migration Strategy

### Phase 1: Parallel Systems (Safe)
- Keep existing constants
- Load KB alongside
- Use KB for NEW features only
- Validate KB data matches constants

### Phase 2: Gradual Replacement
- Move one constant set at a time to KB
- Test after each migration
- Keep fallbacks to constants if KB missing data

### Phase 3: Full Integration
- Remove redundant constants
- KB becomes single source of truth
- Constants.py only has tuning values (penalties, thresholds)

## Benefits

1. **Editability** - Domain experts can update move/ability data without touching code
2. **Extensibility** - Easy to add new knowledge types (items, strategies)
3. **Transparency** - YAML files are human-readable and version-controlled
4. **Testing** - Can swap KB files for unit tests (mock knowledge)
5. **Analysis** - Can analyze what knowledge the bot is actually using

## Performance Considerations

- **Lazy loading** - KB caches YAML files after first load
- **Dict lookups** - O(1) access time same as constants
- **No overhead** - Reading from KB is as fast as reading from constants dict

## Example: Adding New Ability Knowledge

When a new Pokemon/ability combo becomes popular:

**Old way:** Edit constants.py, test, commit, deploy
```python
POKEMON_COMMONLY_FOCUS_SASH.add("new_pokemon")
```

**New way:** Edit YAML, commit (no code change needed)
```yaml
# abilities/focus_sash.yaml
focus_sash:
  common_holders:
    - breloom
    - alakazam
    - new_pokemon  # Just add here!
```

## Next Steps

1. ✅ Create KB structure and demo
2. ⏳ Populate full move database
3. ⏳ Add ability knowledge
4. ⏳ Add item knowledge
5. ⏳ Implement matchup scoring (Phase 1)
6. ⏳ Implement battle phase adjustment (Phase 1)
7. ⏳ Run A/B tests (KB vs constants)
8. ⏳ Gradual migration if successful
