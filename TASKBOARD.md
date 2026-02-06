# TASKBOARD.md - Fouler Play Coordination

**Mission:** Reach 1700 ELO in gen9ou  
**Branch:** foulest-play  
**Updated:** 2026-02-06

## Architecture

```
DEKU (Linux/ubunztu)          BAKUGO (Windows)
├── Code analysis             ├── Bot operation (player loop)
├── Strategy improvements     ├── OBS/Twitch streaming
├── Replay analysis           ├── poke-engine builds
├── Upstream merge            ├── Battle data collection
└── Decision system tuning    └── ELO monitoring
```

## Current Status

### 🔴 ACTIVE: Fresh Fork + Port (DEKU)
Starting from clean upstream (pmariglia/foul-play latest: 55fa9b4), porting our improvements.

**What upstream gives us (that we were missing):**
- poke-engine 0.0.46
- Volatile status duration tracking
- Better mega handling (Legends ZA filtering, alive checks)
- Zoroark edge cases
- Gen1-4 specific fixes
- Guest login support
- Team-list argument (rotate teams)
- Guaranteed moves via pokedex.json
- Better hidden power handling
- Speed range stat tracking
- Impossible abilities tracking per Pokemon

**What we're porting:**
- [ ] constants_pkg/ → penalty system (abilities, moves, strategy constants)
- [ ] fp/search/main.py → MCTS + ability detection + penalty system + timeout protection
- [ ] fp/search/endgame.py → endgame solver
- [ ] fp/team_analysis.py → win condition identification
- [ ] fp/decision_trace.py → decision logging
- [ ] fp/opponent_model.py → opponent tendencies
- [ ] fp/movepool_tracker.py → move tracking
- [ ] fp/playstyle_config.py → team playstyle tuning
- [ ] fp/search/move_validators.py → move validation
- [ ] fp/battle.py additions → snapshot(), null checks, PP tracking
- [ ] fp/battle_modifier.py additions → time parsing, movepool tracking
- [ ] fp/run_battle.py extensions → streaming, battle tracking, traces
- [ ] fp/search/standard_battles.py → weighted sampling
- [ ] fp/search/helpers.py → sample_weight
- [ ] infrastructure/ → player/developer loops, elo watchdog
- [ ] streaming/ → OBS/Twitch integration
- [ ] replay_analysis/ → analysis pipeline
- [ ] teams/ → fat-teams, vert-screens

### 🟡 PENDING: BAKUGO Setup
- [ ] Register LADDERANNIHILATOR account
- [ ] Configure .env per .env.example
- [ ] Set up OBS browser source names
- [ ] Install Rust toolchain for poke-engine builds
- [ ] Pull fresh fork once DEKU pushes

## 4-Phase Roadmap

### Phase 1: Analytics + Tuning (1350→1450) ✅ MOSTLY DONE
- [x] Penalty system for ability-aware decisions
- [x] Timeout protection
- [x] Focus Sash detection
- [x] Setup vs Phazer awareness
- [x] Substitute awareness
- [x] Contact move penalties

### Phase 2: Bayesian Set Inference (1450→1550) 🔄 PARTIAL
- [x] Weighted sampling by set count
- [ ] Speed range narrowing (upstream has infrastructure, need to USE it)
- [ ] Bayesian updating as moves/items revealed
- [ ] Track revealed information to update set probabilities

### Phase 3: Switch Prediction (1550→1650) 🔄 PARTIAL
- [x] Win condition awareness
- [x] Momentum tracking
- [ ] PP tracking (infrastructure built, needs battle_modifier integration)
- [ ] OpponentModel passive/sack tendencies
- [ ] Switch prediction from type matchups

### Phase 4: Archetype + Adaptive (1650→1700) ⬜ NOT STARTED
- [x] Endgame solver
- [ ] Team archetype classification
- [ ] Game-phase awareness
- [ ] Dynamic team selection

## Communication Protocol

- Push code changes to `foulest-play` branch
- Update this TASKBOARD.md when completing items
- DEKU pushes code, BAKUGO pushes battle data
- Check `battle_stats.json` for performance tracking
