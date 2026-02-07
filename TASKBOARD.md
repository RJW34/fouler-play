# TASKBOARD.md - Fouler Play Coordination

**Mission:** Reach 1700 ELO in gen9ou
**Branch:** foulest-play
**Bot Account:** ALL CHUNG (ELO: 1142, GXE: 44.1%)
**Updated:** 2026-02-07 18:15 EST

---

## Current Assessment

207 real battles played. 49% overall WR — we're coin-flipping. All major systems (penalty, Bayesian, PP tracking, momentum, endgame solver) are implemented and wired in. The bottleneck is decision quality tuning, not missing features.

**Team Performance:**
- fat-team-1-stall: 37W-36L (51%) — mediocre
- fat-team-2-pivot: 30W-38L (44%) ⚠️ — dragging ELO down
- fat-team-3-dondozo: 35W-31L (53%) — best performer

---

## Machine Ownership

### DEKU (Linux) owns:
- Code analysis + strategy improvements
- Replay analysis pipeline
- Developer loop
- Test suite maintenance
- Decision logic tuning

### BAKUGO (Windows) owns:
- Bot operation (player_loop.bat)
- OBS + Twitch streaming
- Battle data collection + push
- poke-engine builds
- ELO monitoring

---

## DEKU Action Items

### Port Checklist — ALL DONE ✅
- [x] constants_pkg/ → penalty system
- [x] fp/search/main.py → MCTS + ability detection + penalty system + timeout
- [x] fp/search/endgame.py → endgame solver
- [x] fp/team_analysis.py → win condition identification
- [x] fp/decision_trace.py → decision logging
- [x] fp/opponent_model.py → opponent tendencies
- [x] fp/movepool_tracker.py → move tracking
- [x] fp/playstyle_config.py → team playstyle tuning
- [x] fp/search/move_validators.py → move validation
- [x] fp/battle.py → snapshot(), null checks, PP tracking
- [x] fp/battle_modifier.py → time parsing, movepool tracking, PP recording
- [x] fp/run_battle.py → streaming, battle tracking, traces
- [x] fp/search/standard_battles.py → weighted + Bayesian sampling
- [x] fp/search/helpers.py → sample_weight
- [x] replay_analysis/team_performance.py → working pipeline
- [x] fp/bayesian_sets.py → Bayesian set inference with speed range narrowing

### Phase 2: Bayesian Set Inference — DONE ✅
- [x] Weighted sampling by set count
- [x] Speed range narrowing (check_speed_ranges in battle_modifier → bayesian_sets)
- [x] Bayesian updating as moves/items revealed (fp/bayesian_sets.py)
- [x] Track revealed information to update set probabilities

### Phase 3: Switch Prediction (current focus)
- [x] Win condition awareness (3.1)
- [x] PP tracking (3.2 — battle_modifier records, main.py uses)
- [x] Momentum tracking (3.3)
- [ ] **ACTIVE: Investigate fat-team-2-pivot 44% WR — switch logic tuning**
- [ ] OpponentModel passive/sack tendency integration
- [ ] Switch prediction from type matchups

### Phase 4: Archetype + Adaptive (1650→1700)
- [x] Endgame solver (fp/search/endgame.py)
- [ ] Team archetype classification
- [ ] Game-phase awareness
- [ ] Dynamic team selection (drop/replace underperforming teams)

### Build / Fix
- [x] bot_monitor username fix (reads from .env now)
- [x] Developer loop wired (infrastructure/linux/)
- [x] ProcessPoolExecutor zombie leak fixed
- [x] Stream pipeline re-enabled
- [ ] battle_stats.json needs richer data from BAKUGO (opponent_pokemon missing in real battles)

---

## BAKUGO Action Items

### CRITICAL — Battle Stats Data Quality
The bot is writing minimal battle_stats.json entries. Real battles are missing:
- `opponent_pokemon` — needed for matchup analysis
- `rating_before` / `rating_after` — needed for ELO tracking
- `replay_id` in correct format

**Action:** Check bot_monitor.py's `record_batch_result()` and the state_store code. Ensure all fields are being populated when battles complete. The mock data has the right schema — real data should match.

### Keep Bot Running
- [ ] player_loop.bat must be running continuously
- [ ] Push battle_stats.json after each batch: `git add battle_stats.json && git commit -m "data: battle stats" && git push origin foulest-play`
- [ ] If bot crashes, check logs and restart

### Streaming (lower priority)
- [ ] Verify streaming server: `python streaming/serve_obs_page.py` → http://localhost:8777/status
- [ ] OBS Browser Sources named "Battle Slot 1/2/3"

---

## Bug Reports
- 2026-02-07: battle_stats.json real entries missing opponent_pokemon and rating fields (BAKUGO to fix data collection)
- 2026-02-06: bot_monitor.py URL space issue — FIXED (allchung not all chung)
- 2026-02-06: ProcessPoolExecutor zombie processes — FIXED (global singleton)

---

## Communication Protocol
- Push code/data to `foulest-play` branch
- Update this TASKBOARD.md when completing items
- DEKU pushes code, BAKUGO pushes battle data

## Phase 1 (DONE)
- [x] Penalty system for ability-aware decisions
- [x] Timeout protection
- [x] Focus Sash detection
- [x] Setup vs Phazer awareness
- [x] Substitute awareness
- [x] Contact move penalties
