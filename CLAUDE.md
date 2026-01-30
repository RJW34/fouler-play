# CLAUDE.md - Fouler-Play Project Context

## Project Overview

**Fouler-Play** is a fork of [foul-play](https://github.com/pmariglia/foul-play), a Pokemon battle bot that plays competitive battles on [Pokemon Showdown](https://pokemonshowdown.com/). This fork adds critical reasoning improvements to fix decision-making gaps in the original bot.

### Core Architecture

```
fouler-play/
├── run.py                 # Entry point
├── config.py              # FoulPlayConfig settings
├── constants.py           # Game constants + NEW: ability penalty system
├── fp/
│   ├── battle.py          # Battle/Battler/Pokemon classes
│   ├── battle_modifier.py # Parses battle events, updates state
│   ├── run_battle.py      # Main battle loop
│   ├── helpers.py         # Utility functions
│   └── search/
│       ├── main.py        # MCTS search + NEW: ability penalty system
│       ├── standard_battles.py  # Battle sampling for standard formats
│       ├── random_battles.py    # Battle sampling for random battles
│       └── poke_engine_helpers.py  # Converts state to poke-engine format
├── data/
│   ├── pokedex.json       # Pokemon data (stats, types, abilities)
│   ├── pkmn_sets.py       # SmogonSets class for usage data
│   └── ...
└── tests/
```

### How the Bot Makes Decisions

1. **Battle State Tracking** (`fp/battle_modifier.py`): Parses Pokemon Showdown protocol messages, tracks HP, status, abilities, items, moves, etc.

2. **Battle Sampling** (`fp/search/standard_battles.py`): Creates multiple possible battle scenarios by sampling unknown opponent data (abilities, items, moves) from Smogon usage statistics.

3. **State Conversion** (`fp/search/poke_engine_helpers.py`): Converts Python battle state to `poke-engine` format.

4. **MCTS Search** (`fp/search/main.py`): Calls `poke-engine` (external Rust library) to run Monte Carlo Tree Search on each sampled battle.

5. **Move Selection** (`fp/search/main.py:select_move_from_mcts_results`): Aggregates MCTS results, applies ability-based penalties, selects best move.

### External Dependency: poke-engine

The bot uses [poke-engine](https://github.com/pmariglia/poke-engine), a Rust-based battle simulator with MCTS. This is where actual damage calculations and game state simulation happen.

**Critical limitation**: poke-engine doesn't properly account for many ability effects in its evaluation. This fork adds a penalty system in Python to compensate.

---

## Changes Made in This Fork

### Problem Identified

The original bot had a critical flaw: it would make obviously bad decisions because `poke-engine` doesn't properly evaluate certain "silent" abilities. For example:

- Using Swords Dance against Dondozo (Unaware ignores stat boosts)
- Using Toxic against Gliscor (Poison Heal makes poison heal them)
- Using Will-O-Wisp against Conkeldurr (Guts boosts Attack when burned)
- Using Water moves against Vaporeon (Water Absorb heals them)

### Solution Implemented

A **penalty system** that reduces the weight of counterproductive moves after MCTS returns results but before final move selection.

### Files Modified

#### 1. `constants.py` (Added ~400 lines)

**Pokemon Lists** - Sets of Pokemon that commonly have specific abilities:
- `POKEMON_COMMONLY_UNAWARE` - Dondozo, Clefable, Clodsire, etc.
- `POKEMON_COMMONLY_GUTS` - Conkeldurr, Obstagoon, etc.
- `POKEMON_COMMONLY_POISON_HEAL` - Gliscor, Breloom
- `POKEMON_COMMONLY_WATER_IMMUNE` - Vaporeon, Gastrodon, etc.
- `POKEMON_COMMONLY_ELECTRIC_IMMUNE` - Jolteon, Lanturn, etc.
- `POKEMON_COMMONLY_FLASH_FIRE` - Heatran, Chandelure, etc.
- `POKEMON_COMMONLY_LEVITATE` - Hydreigon, Rotom forms, etc.
- `POKEMON_COMMONLY_MAGIC_BOUNCE` - Hatterene, Espeon, etc.
- `POKEMON_COMMONLY_COMPETITIVE` - Milotic, Gothitelle
- `POKEMON_COMMONLY_DEFIANT` - Bisharp, Kingambit, etc.

**Move Lists** - Sets of moves in each category:
- `OFFENSIVE_STAT_BOOST_MOVES` - Swords Dance, Dragon Dance, etc.
- `STATUS_INFLICTING_MOVES` - Will-O-Wisp, Thunder Wave, Toxic, etc.
- `TOXIC_POISON_MOVES` - Toxic, Poison-specific moves
- `WATER_TYPE_MOVES`, `ELECTRIC_TYPE_MOVES`, `FIRE_TYPE_MOVES`, `GROUND_TYPE_MOVES`
- `MAGIC_BOUNCE_REFLECTED_MOVES` - Stealth Rock, status moves, etc.
- `STAT_LOWERING_MOVES` - Icy Wind, Parting Shot, etc.

**Penalty Values**:
- `ABILITY_PENALTY_SEVERE = 0.1` (90% reduction)
- `ABILITY_PENALTY_MEDIUM = 0.3` (70% reduction)
- `ABILITY_PENALTY_LIGHT = 0.5` (50% reduction)

#### 2. `fp/search/main.py` (Major refactor)

**New Components**:

```python
@dataclass
class OpponentAbilityState:
    """Tracks what abilities the opponent's active Pokemon has or likely has."""
    has_unaware: bool = False
    has_guts_like: bool = False
    has_poison_heal: bool = False
    has_water_immunity: bool = False
    has_electric_immunity: bool = False
    has_flash_fire: bool = False
    has_levitate: bool = False
    has_magic_bounce: bool = False
    has_competitive_defiant: bool = False
    # ... metadata fields
```

**Key Functions**:
- `detect_opponent_abilities(battle)` - Analyzes opponent, returns `OpponentAbilityState`
- `apply_ability_penalties(policy, ability_state)` - Applies all penalties to move weights
- `_check_ability_or_pokemon(...)` - Helper to check known ability or infer from Pokemon name

**Flow**:
1. `find_best_move()` calls `detect_opponent_abilities()` before MCTS
2. MCTS runs normally via `poke-engine`
3. `select_move_from_mcts_results()` applies penalties before final selection

---

## Implemented Improvements (Round 2)

### ✅ Timeout Protection (Critical)
- Added total time budget system (`MAX_DECISION_TIME_SECONDS = 30s`, `8s` under pressure)
- 3-tier time pressure detection: moderate (<60s), critical (<30s), emergency (<15s)
- Emergency fallback move selection if MCTS times out entirely
- Per-future timeout collection - partial results used if some battles timeout
- Dynamic search time reduction when budget is tight

### ✅ Mold Breaker Awareness
- Detects if our Pokemon has Mold Breaker/Teravolt/Turboblaze
- Skips type-immunity penalties (Levitate, Water Absorb, etc.) when we have Mold Breaker
- Correctly still applies Magic Bounce penalties (Mold Breaker doesn't bypass reflected moves in the same way)

### ✅ Focus Sash Detection
- Added `POKEMON_COMMONLY_FOCUS_SASH` set (common leads, frail sweepers)
- Added `MULTI_HIT_MOVES` and `PRIORITY_MOVES` sets
- Detects known Focus Sash or infers from common holders
- Only applies when opponent is at full HP (Sash requires full HP)

### ✅ Setup vs Phazers
- Detects revealed phazing moves (Roar, Whirlwind, Dragon Tail, Circle Throw, Yawn)
- Penalizes setup/boosting moves with `ABILITY_PENALTY_MEDIUM` when phazer detected

### ✅ Substitute Awareness
- Detects when opponent has Substitute volatile status
- Severely penalizes status-only moves that fail against Substitute

---

## Next Steps for Implementation

### 3. Contact Moves vs Rocky Helmet/Iron Barbs/Rough Skin (Medium Priority)

**Problem**: Bot uses contact moves (Close Combat) against Rocky Helmet holders or Ferrothorn (Iron Barbs), taking unnecessary recoil.

**Solution**:
- Track revealed Rocky Helmet, Iron Barbs, Rough Skin
- Add `CONTACT_MOVES` set
- Add `POKEMON_COMMONLY_IRON_BARBS` (Ferrothorn)
- Add `POKEMON_COMMONLY_ROUGH_SKIN` (Garchomp)
- When facing these, prefer non-contact alternatives if available
- Use lighter penalty (ABILITY_PENALTY_LIGHT) since contact moves still deal damage

### 4. Intimidate vs Defiant/Competitive (Medium Priority)

**Problem**: Switching in an Intimidate user against Defiant/Competitive Pokemon gives them +2 Attack/SpA.

**Solution**:
- This affects switch selection, not move selection
- Need to check if switch target has Intimidate
- If opponent has Defiant/Competitive, penalize switching to Intimidate users
- Add `POKEMON_WITH_INTIMIDATE` set

### 5. Choice Item Lock Exploitation (Lower Priority)

**Problem**: Bot doesn't recognize when opponent is Choice-locked into a resisted/ineffective move, missing setup opportunities.

**Solution**:
- Track opponent's `last_used_move` and `can_have_choice_item`
- If opponent is suspected Choice-locked into a move we resist:
  - Boost priority of setup moves
  - Consider free switches to counters
- This is more complex as it requires positive boosts, not just penalties

### 6. Mold Breaker Awareness (Lower Priority)

**Problem**: Bot doesn't realize its Mold Breaker Pokemon can hit Levitate users with Ground moves.

**Solution**:
- Check if OUR active Pokemon has Mold Breaker/Teravolt/Turboblaze
- If so, don't apply Levitate penalty to Ground moves
- Add `MOLD_BREAKER_ABILITIES` set

### 7. Substitute Awareness (Lower Priority)

**Problem**: Bot may use status moves against Substitute, which fail.

**Solution**:
- Check if opponent has Substitute volatile status
- Penalize status moves that don't go through Substitute
- Infiltrator ability bypasses this

---

## Testing

Tests require `poke-engine` which needs Rust to build:

```bash
# Install Rust first, then:
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -v
```

To verify syntax without poke-engine:
```bash
python -c "import ast; ast.parse(open('constants.py').read())"
python -c "import ast; ast.parse(open('fp/search/main.py').read())"
```

---

## Running the Bot

```bash
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username 'YourUsername' \
  --ps-password 'YourPassword' \
  --bot-mode search_ladder \
  --pokemon-format gen9ou
```

---

## Key Files to Understand

| File | Purpose |
|------|---------|
| `constants.py:233-680` | All ability/move penalty constants |
| `fp/search/main.py:50-285` | Ability detection and penalty system |
| `fp/search/main.py:378-452` | `find_best_move()` entry point |
| `fp/battle_modifier.py` | How battle state is parsed (abilities revealed here) |
| `fp/search/standard_battles.py` | How opponent Pokemon are sampled |
| `data/pkmn_sets.py` | SmogonSets class for usage data |

---

## Design Principles

1. **Penalties, not blocks**: Reduce move weights rather than removing options entirely. The MCTS might have valid reasons for a move.

2. **Known > Inferred**: If ability is known (revealed in battle), trust it. If unknown, use Pokemon-commonly-has lists.

3. **Severe for game-losing**: Moves that actively help the opponent (Toxic on Poison Heal) get 90% penalty.

4. **Medium for backfiring**: Moves that give opponent boosts (stat drops on Defiant) get 70% penalty.

5. **Light for suboptimal**: Moves that are just inefficient (contact into Rocky Helmet) get 50% penalty.

---

## Parent Project

This is a fork of: https://github.com/pmariglia/foul-play

The poke-engine library: https://github.com/pmariglia/poke-engine
