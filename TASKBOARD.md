# TASKBOARD.md - Fouler Play Coordination

**Mission:** Reach 1700 ELO in gen9ou
**Branch:** foulest-play
**Bot Account:** ALL CHUNG (ELO: ~1141)
**Updated:** 2026-02-07

---

## NEXT ACTION (read this first)

**DEKU:** Implement Phase 2 Bayesian set inference — speed range narrowing and Bayesian updating as moves/items are revealed. See Phase 2 section below.

**BAKUGO:** Keep the bot playing. Verify `battle_stats.json` is being pushed after each batch. Fix any `LEBOTJAMESXD005` references in code to `ALL CHUNG` (see Bug Reports).

---

## What's Already Built (DO NOT REBUILD)

These systems are **complete and working**. Do not recreate them from scratch:
- `streaming/` — 23 files, full OBS integration. `serve_obs_page.py` is the main server (port 8777). Tested and functional.
- `replay_analysis/` — `team_performance.py`, `analyzer.py`, `turn_review.py`, 28 replay JSONs, generated reports.
- `infrastructure/linux/` — developer loop, analyze_performance.sh, systemd service. All working.
- `infrastructure/windows/` — player_loop.bat, deploy_update.bat, install_task.bat. All working.
- `infrastructure/elo_watchdog.py` — auto-revert on ELO drop. Working.
- Port checklist — ALL modules ported. See below for confirmation.

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
All modules ported and imports verified ✅
- [x] constants_pkg/ → penalty system (abilities, moves, strategy constants)
- [x] fp/search/main.py → MCTS + ability detection + penalty system + timeout protection
- [x] fp/search/endgame.py → endgame solver
- [x] fp/team_analysis.py → win condition identification
- [x] fp/decision_trace.py → decision logging
- [x] fp/opponent_model.py → opponent tendencies
- [x] fp/movepool_tracker.py → move tracking
- [x] fp/playstyle_config.py → team playstyle tuning
- [x] fp/search/move_validators.py → move validation
- [x] fp/battle.py additions → snapshot(), null checks, PP tracking
- [x] fp/battle_modifier.py additions → time parsing, movepool tracking
- [x] fp/run_battle.py extensions → streaming, battle tracking, traces
- [x] fp/search/standard_battles.py → weighted sampling
- [x] fp/search/helpers.py → sample_weight
- [x] replay_analysis/team_performance.py → per-team win rates and weakness analysis

### Build / Fix
- [ ] Fix all LEBOTJAMESXD005 references to ALL CHUNG (bot_monitor.py, streaming/auto_stream_*.py, replay_analysis/turn_review.py)
- [x] Verify developer loop (`infrastructure/linux/developer_loop.sh`) works end-to-end
- [x] Wire team_performance.py output into developer loop analysis prompt (analyze_performance.sh calls team_performance.py)

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

**READ THIS CAREFULLY — step-by-step instructions for getting the bot running on Windows.**

### 1. Pull Latest Code
```powershell
cd C:\Users\mtoli\Documents\Code\fouler-play
git checkout foulest-play
git pull origin foulest-play
```

### 2. Environment Setup
Copy `.env.example` to `.env` if you haven't, then fill in:
```
PS_USERNAME=ALL CHUNG
PS_PASSWORD=<check with Ryan or existing .env>
PS_WEBSOCKET_URI=wss://sim3.psim.us/showdown/websocket
PS_FORMAT=gen9ou
PS_BOT_MODE=search_ladder

# OBS streaming (if OBS is set up)
OBS_WS_HOST=localhost
OBS_WS_PORT=4455
OBS_BATTLE_SOURCES=Battle Slot 1,Battle Slot 2,Battle Slot 3

# Discord webhooks (ask Ryan or check existing .env)
DISCORD_WEBHOOK_URL=<project updates webhook>
DISCORD_BATTLES_WEBHOOK_URL=<battle notifications webhook>
DISCORD_FEEDBACK_WEBHOOK_URL=<turn review webhook>
```

### 3. Python Environment
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 4. poke-engine (Rust required)
```powershell
# Need Rust toolchain: https://rustup.rs/
pip install poke-engine
# If that fails, try: pip install poke-engine==0.3.0
```

### 5. Verify Bot Runs
```powershell
python run.py --ps-username "ALL CHUNG" --ps-password "<password>" --bot-mode search_ladder --pokemon-format gen9ou --team-name gen9/ou/fat-team-1-stall --search-time-ms 3000 --run-count 5 --save-replay always --log-level INFO
```

### 6. Install Player Loop as Scheduled Task
```powershell
# Run as Administrator:
infrastructure\windows\install_task.bat
schtasks /run /tn "FoulerPlayPlayerLoop"
```

### 7. Streaming Pipeline (streaming/ has files but needs OBS setup)
- Verify OBS has Browser Sources named "Battle Slot 1", "Battle Slot 2", "Battle Slot 3"
- URL format: `https://play.pokemonshowdown.com/battle-gen9ou-XXXXXXX`
- **NEVER** use `~~showdown` in URLs — causes "Please visit showdown directly" errors
- Test obs-websocket connection: `python streaming/obs_controller.py`
- Start stream server: `python streaming/serve_obs_page.py`

### 8. Verify Everything
- [x] Bot connects to Showdown and plays games — LIVE, 2-3 concurrent battles running (ALL CHUNG)
- [x] battle_stats.json is being written — 30 battles recorded as of 2026-02-07
- [x] Replays saved to replay_analysis/ — 28 replay JSONs present
- [ ] OBS shows live battles (if streaming) — streaming server works on :8777, OBS not yet configured
- [x] Player loop runs unattended — scheduled task FoulerPlayPlayerLoop installed
- [x] Push battle_stats.json after each batch

### Completed Setup Steps
- [x] Step 1: Pull latest code (at 9ca25a3)
- [x] Step 2: .env configured (ALL CHUNG, all OBS vars, ELO tracking)
- [x] Step 3: Python env ready (all deps installed)
- [x] Step 4: poke-engine 0.0.46 built with terastallization (Rust cargo 1.92.0, PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 for Python 3.14)
- [x] Step 5: Bot verified running — logged in, searching ladder, playing games
- [x] Step 6: Scheduled task installed (FoulerPlayPlayerLoop)
- [x] Step 7: Streaming server running (serve_obs_page.py on :8777), OBS not yet open

---

## Bug Reports
<!-- When either machine finds a bug, note it here with date and description. The owning machine fixes it. -->
- 2026-02-06: Multiple files still reference old bot accounts. Grep for "LEBOTJAMESXD" and fix to "ALL CHUNG":
  - `bot_monitor.py` — hardcoded USERNAME
  - `streaming/auto_stream_firefox.py` line 20 — default PS_USERNAME
  - `streaming/auto_stream_headless.py` line 20 — default PS_USERNAME
  - `streaming/auto_stream_stable.py` line 28 — hardcoded PS_USERNAME
  - `replay_analysis/turn_review.py` line 47 — hardcoded bot_name
- 2026-02-07: `infrastructure/windows/player_loop.bat` line 50 has password hardcoded in plaintext. Should read from .env instead.

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
