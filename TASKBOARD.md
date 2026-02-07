# TASKBOARD.md - Fouler Play Coordination

**Purpose:** Overnight team-testing service for a competitive Pokemon player (fat/stall teams in gen9ou)
**Branch:** foulest-play
**Bot Account:** ALL CHUNG (ELO: ~1200, stuck at 50% win rate)
**Updated:** 2026-02-07

---

## The Problem Right Now

The bot has played **219 games** and is stuck at **~1200 ELO with a 50% win rate**. At this level, opponents play poorly and the matchup data tells the player nothing useful about their teams. The bot needs to reach 1700+ for the data to be meaningful.

**Team performance (219 games):**
| Team | Games | W/L | Win Rate |
|------|-------|-----|----------|
| fat-team-1-stall | 73 | 37/36 | 51% |
| fat-team-2-pivot | 68 | 30/38 | 44% |
| fat-team-3-dondozo | 66 | 35/31 | 53% |

These win rates are indistinguishable from random at this sample size and ELO. The bot needs to play better.

---

## NEXT ACTION (read this first)

**DEKU:** The bot's 50% win rate at 1200 means fundamental play is wrong. Before adding clever features (Bayesian inference, etc.), audit whether the bot is playing fat/stall correctly:
1. Run `python replay_analysis/team_performance.py` and study the output
2. Look at the last 10 losses — is the bot switching when it should hold? Attacking when it should recover? Using the wrong move into obvious resists?
3. Make ONE improvement that fixes the most common mistake pattern
4. The Phase 2-4 roadmap items are still valid but only matter if the basics are right

**BAKUGO:** Keep the bot playing. Ensure `battle_stats.json` and replays are being pushed after each batch. Everything else is secondary.

---

## What's Already Built (DO NOT REBUILD)

These systems are **complete and working**. Do not recreate them from scratch:
- `streaming/` — OBS integration. `serve_obs_page.py` is the main server (port 8777). Low priority.
- `replay_analysis/` — `team_performance.py`, `analyzer.py`, `turn_review.py`, replay JSONs. This is the player-facing output.
- `infrastructure/linux/` — developer loop, analyze_performance.sh, systemd service.
- `infrastructure/windows/` — player_loop.bat, deploy_update.bat, install_task.bat.
- `infrastructure/elo_watchdog.py` — auto-revert on ELO drop.
- `fp/playstyle_config.py` — FAT/STALL playstyle tuning with switch/pivot/recovery/chip multipliers.
- `fp/search/endgame.py` — endgame solver for 1v1/2v1 scenarios.
- `fp/team_analysis.py` — win condition identification.

---

## Machine Ownership

### DEKU (Linux) owns:
- Decision-making improvements (make the bot play fat/stall correctly)
- Replay analysis quality (make the morning report useful for the player)
- Developer loop (`infrastructure/linux/developer_loop.sh`)
- Test suite maintenance
- Upstream merge management

### BAKUGO (Windows) owns:
- Bot operation (`infrastructure/windows/player_loop.bat`)
- Battle data collection + push (`battle_stats.json`, replays)
- poke-engine builds (Rust toolchain)
- ELO monitoring + watchdog
- Environment/credentials setup
- Streaming (low priority — only if everything else is running)

---

## DEKU Action Items

### Immediate: Diagnose the 50% Win Rate
- [ ] Run team_performance.py, study the report for patterns
- [ ] Review 10 recent losses — identify the #1 recurring mistake
- [ ] Implement a targeted fix for that mistake pattern
- [ ] Verify fix doesn't break tests, push

### Phase 2: Better Opponent Modeling (target: 1200 -> 1400)
- [x] Weighted sampling by set count
- [ ] Speed range narrowing (infrastructure exists, needs to be used)
- [ ] Bayesian updating as moves/items are revealed
- [ ] Track revealed information to update set probabilities

### Phase 3: Correct Fat/Stall Play (target: 1400 -> 1550)
- [x] Win condition awareness
- [x] Momentum tracking
- [ ] PP tracking (infrastructure built, needs battle_modifier integration)
- [ ] Switch prediction from type matchups
- [ ] Recovery timing — when to Recover vs when to attack
- [ ] Hazard awareness — prioritize Stealth Rock early, Defog/Rapid Spin when needed

### Phase 4: Advanced (target: 1550 -> 1700)
- [x] Endgame solver
- [ ] Team archetype classification of opponent's team
- [ ] Game-phase awareness (early/mid/late game strategy shifts)
- [ ] Matchup-aware lead selection

### Morning Report Improvements
- [ ] Add "key replays to watch" (closest losses, biggest upsets) to report output
- [ ] Add per-session summaries (not just all-time) — "last night: 15 games, 9-6"
- [ ] Add move-level analysis: "Gliscor used Earthquake into Corviknight 4 times" (misplays)
- [ ] Discord webhook delivery of morning summary (optional, uses DISCORD_WEBHOOK_URL from .env)

---

## BAKUGO Action Items

### Keep the Bot Running
1. Ensure `player_loop.bat` is running via scheduled task
2. After each batch, verify `battle_stats.json` has new entries and push
3. If the bot disconnects or crashes, check logs and restart

### Verified Setup
- [x] Bot connects to Showdown and plays games (ALL CHUNG)
- [x] battle_stats.json is being written (219 battles recorded)
- [x] Replays saved to replay_analysis/
- [x] Player loop runs unattended (scheduled task installed)
- [x] Push battle_stats.json after each batch

---

## Bug Reports
- 2026-02-06: Multiple files reference old bot accounts. Grep for "LEBOTJAMESXD" and fix to "ALL CHUNG":
  - `bot_monitor.py` — hardcoded USERNAME
  - `streaming/auto_stream_firefox.py` line 20
  - `streaming/auto_stream_headless.py` line 20
  - `streaming/auto_stream_stable.py` line 28
  - `replay_analysis/turn_review.py` line 47
- 2026-02-07: `infrastructure/windows/player_loop.bat` line 50 has password hardcoded in plaintext. Should read from .env instead.

---

## Communication Protocol

- Push code/data to `foulest-play` branch
- Update this TASKBOARD.md when completing items (check the box: `[x]`)
- DEKU pushes code changes, BAKUGO pushes battle data
- Check `battle_stats.json` for performance tracking
- If you need the other machine to act, write it under their Action Items section and push

## Completed Phases
- [x] Phase 1: Penalty system, timeout protection, Focus Sash detection, setup/phazer awareness, substitute awareness, contact move penalties
- [x] All porting work from upstream fork
- [x] Streaming pipeline (built, low priority)
- [x] Replay analysis pipeline (built, needs quality improvements)
