# CLAUDE.md - Fouler-Play Project Context

## Project Overview

**Fouler-Play** is a fork of [foul-play](https://github.com/pmariglia/foul-play), a Pokemon battle bot for [Pokemon Showdown](https://pokemonshowdown.com/). This fork adds ability-aware decision making, streaming infrastructure, and a continuous improvement pipeline targeting **1700 ELO in gen9ou**.

### Repository Structure

```
fouler-play/
├── run.py                    # Entry point
├── config.py                 # FoulPlayConfig + env loading
├── constants.py              # Shim → re-exports from constants_pkg/
├── constants_pkg/            # Modular constants (our addition)
│   ├── core.py               # Original upstream constants
│   ├── strategy.py           # Penalty/boost values, Pokemon/move sets
│   ├── pokemon_abilities.py  # Ability-related sets
│   └── move_flags.py         # Move categorization flags
├── fp/
│   ├── battle.py             # Battle/Battler/Pokemon classes
│   ├── battle_modifier.py    # Parses PS protocol, updates battle state
│   ├── run_battle.py         # Main battle loop + streaming + tracking
│   ├── websocket_client.py   # PS websocket + multi-battle routing
│   ├── helpers.py            # Utility functions
│   ├── decision_trace.py     # Decision logging for analysis
│   ├── movepool_tracker.py   # Tracks opponent movepool
│   ├── opponent_model.py     # Opponent tendency tracking
│   ├── playstyle_config.py   # Per-team playstyle tuning
│   ├── team_analysis.py      # Win condition identification
│   └── search/
│       ├── main.py           # MCTS + ability penalty system (3400+ lines)
│       ├── endgame.py        # Endgame solver (1v1, 2v1)
│       ├── standard_battles.py  # Battle sampling + weighted selection
│       ├── random_battles.py
│       ├── poke_engine_helpers.py
│       ├── helpers.py
│       └── move_validators.py
├── infrastructure/           # Automation loops
│   ├── README.md             # Architecture docs
│   ├── linux/                # DEKU's player/developer loops
│   ├── windows/              # BAKUGO's player loop + deploy
│   ├── guardrails.json       # Safety constraints
│   └── elo_watchdog.py       # Auto-revert bad deploys
├── streaming/                # OBS/Twitch integration
├── replay_analysis/          # Post-game analysis pipeline
├── data/                     # Smogon data, pokedex, moves
├── teams/                    # Team files
└── tests/                    # 517 tests (all passing)
```

## Upstream Base

Forked from [pmariglia/foul-play](https://github.com/pmariglia/foul-play) at commit `55fa9b4` (2026-02-06).
- Upstream remote: `upstream` → https://github.com/pmariglia/foul-play.git
- Origin remote: `origin` → git@github.com:RJW34/fouler-play.git
- Working branch: `foulest-play`

### Key Upstream Features Inherited
- poke-engine 0.0.46 with terastallization
- Volatile status duration tracking
- Mega evolution handling (Legends ZA filtering)
- Zoroark edge cases
- Gen1-4 specific fixes
- Guest login + avatar support
- Team-list argument for team rotation
- Guaranteed moves via pokedex.json
- Speed range stat tracking
- Impossible abilities tracking per Pokemon

## Our Improvements

### Ability-Aware Penalty System (fp/search/main.py)
The core improvement: after MCTS returns move weights, we apply penalties based on opponent abilities.

**Detection**: `detect_opponent_abilities(battle)` → `OpponentAbilityState` (91 fields)
- Checks known abilities first, falls back to Pokemon-commonly-has inference
- Tracks: Unaware, Guts, Poison Heal, type immunities, Magic Bounce, Competitive/Defiant, Focus Sash, Phazers, Substitute, contact punishers, and 30+ more

**Penalties**: `apply_ability_penalties(policy, ability_state)` modifies MCTS output
- SEVERE (0.1): Moves that actively help opponent (Toxic on Poison Heal)
- MEDIUM (0.3): Moves that backfire (stat drops on Defiant)
- LIGHT (0.5): Suboptimal moves (contact into Rocky Helmet)

### Phase 1: Analytics + Tuning ✅
- Positive boosts (Choice-locked resist, opponent statused, low HP priority)
- Trick Room awareness (penalize speed boosts in TR)
- Screens awareness (boost setup behind screens, boost screen breakers)
- Weather/Terrain synergies (Swift Swim, Chlorophyll, terrain moves)

### Phase 2: Switch Evaluation ✅
- Switch penalty system (hazard awareness, Intimidate vs Defiant)
- Entry hazard calculus
- Tera prediction

### Phase 3: State Tracking (Partial)
- ✅ Win condition awareness (identify and protect sweepers)
- ✅ Momentum tracking (adjust risk based on game state)
- ⬜ PP tracking (infrastructure built in battle.py, needs integration)

### Phase 4: Advanced Reasoning (Partial)
- ✅ Endgame solver (1v1 deterministic, 2v1/1v2 heuristic)
- ⬜ Team archetype classification
- ⬜ Game-phase awareness

### Timeout Protection
- Total time budget: 30s normal, 8s under pressure
- 3-tier time pressure: moderate (<60s), critical (<30s), emergency (<15s)
- Emergency fallback selection if MCTS times out

### Infrastructure
- Two-machine system: Linux (DEKU=code) + Windows (BAKUGO=games)
- Continuous improvement: developer loop analyzes replays → generates fixes
- ELO watchdog auto-reverts bad deploys
- Safety guardrails in `infrastructure/guardrails.json`

## Running

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt  # Needs Rust for poke-engine

# Run bot
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username 'YourUsername' \
  --ps-password 'YourPassword' \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --team-name gen9/ou \
  --search-time-ms 200 \
  --search-parallelism 4 \
  --run-count 100
```

## Testing

```bash
python -m pytest tests/ -v  # 517 tests, all passing
python -c "import ast; ast.parse(open('fp/search/main.py').read())"  # Syntax check
python -c "from fp.search.main import find_best_move; print('OK')"   # Import check
```

## Key Design Principles

1. **Penalties, not blocks**: Reduce weights, never remove options
2. **Known > Inferred**: Trust revealed abilities over Pokemon-commonly-has lists
3. **Severe for game-losing**: 90% penalty for moves that help the opponent
4. **Upstream first**: Core engine from upstream, improvements layered on top
5. **Data-driven**: battle_stats.json + replay analysis drive improvements
