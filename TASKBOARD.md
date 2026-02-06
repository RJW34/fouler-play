# TASKBOARD.md - Fouler Play Coordination

**Mission:** Reach 1700 ELO in gen9ou
**Branch:** foulest-play
**Updated:** 2026-02-06

---

## Machine Ownership

### DEKU (Linux) owns:
- Code analysis + strategy improvements
- Replay analysis pipeline (`replay_analysis/`)
- Developer loop (`infrastructure/linux/developer_loop.sh`)
- Test suite maintenance
- Upstream merge management
- Porting improvements onto fresh fork

### BAKUGO (Windows) owns:
- Bot operation (`infrastructure/windows/player_loop.bat`)
- OBS + Twitch streaming (`streaming/`)
- Battle data collection + push
- poke-engine builds (Rust toolchain)
- ELO monitoring + watchdog
- Environment/credentials setup

---

## DEKU Action Items

### Port Checklist (fresh fork from upstream 55fa9b4)
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

### Build Missing Systems
- [ ] replay_analysis/team_performance.py → reads battle_stats.json, outputs per-team win rates and weakness analysis
- [ ] replay_analysis/ pipeline → feeds into developer loop analysis prompt

### Phase 2: Bayesian Set Inference (1450→1550)
- [x] Weighted sampling by set count
- [ ] Speed range narrowing (upstream has infrastructure, need to USE it)
- [ ] Bayesian updating as moves/items revealed
- [ ] Track revealed information to update set probabilities

### Phase 3: Switch Prediction (1550→1650)
- [x] Win condition awareness
- [x] Momentum tracking
- [ ] PP tracking (infrastructure built, needs battle_modifier integration)
- [ ] OpponentModel passive/sack tendencies
- [ ] Switch prediction from type matchups

### Phase 4: Archetype + Adaptive (1650→1700)
- [x] Endgame solver
- [ ] Team archetype classification
- [ ] Game-phase awareness
- [ ] Dynamic team selection

---

## BAKUGO Action Items

### Streaming Pipeline (streaming/ is EMPTY — build it)
- [ ] streaming/serve_obs_page.py → HTTP server + obs-websocket client that sets OBS Browser Source URLs to live battle pages
- [ ] streaming/obs_battles.html → fallback multi-battle display page (note: iframes blocked by Showdown, prefer direct OBS Browser Sources)
- [ ] streaming/stream_overlay.html → ELO display, win/loss counter, team info for stream overlay
- [ ] Verify OBS Browser Sources named "Battle Slot 1", "Battle Slot 2", "Battle Slot 3" exist in OBS scene
- [ ] Verify .env has all OBS_* variables from .env.example
- [ ] Test that battles display correctly on stream (no "Please visit showdown directly" errors)
- [ ] URL format must be `https://play.pokemonshowdown.com/battle-gen9ou-XXXXXXX` — NEVER use `~~showdown`

### Bot Operation
- [ ] Verify player_loop.bat is running and playing games
- [ ] Install scheduled task: `infrastructure\windows\install_task.bat` (run as Admin)
- [ ] Verify battle_stats.json is being pushed to GitHub after each batch
- [ ] Verify elo_watchdog.py runs after deploys

### Environment
- [ ] Ensure .env matches .env.example (all vars present)
- [ ] Rust toolchain installed for poke-engine builds
- [ ] Python venv set up with all requirements

---

## Bug Reports
<!-- When either machine finds a bug, note it here with date and description. The owning machine fixes it. -->

---

## Communication Protocol

- Push code/data to `foulest-play` branch
- Update this TASKBOARD.md when completing items (check the box: `[x]`)
- DEKU pushes code changes, BAKUGO pushes battle data
- Check `battle_stats.json` for performance tracking
- If you need the other machine to act, write it under their Action Items section and push

## Phase 1 (DONE)
- [x] Penalty system for ability-aware decisions
- [x] Timeout protection
- [x] Focus Sash detection
- [x] Setup vs Phazer awareness
- [x] Substitute awareness
- [x] Contact move penalties
