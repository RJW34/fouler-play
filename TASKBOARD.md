# TASKBOARD.md - Fouler Play Coordination

**This is the single source of truth for fouler-play operations.** If anything contradicts this file, this file wins. `fouler-play-v2` has been archived — it was a duplicate clone DEKU created. Only `/home/ryan/projects/fouler-play` exists now.

**Purpose:** Overnight team-testing service for a competitive Pokemon player (fat/stall teams in gen9ou)
**Branch:** master
**Bot Account:** BugInTheCode on DEKU, ALL CHUNG on BAKUGO
**Updated:** 2026-02-12

---

## Standing Rules (check every session — fix drift immediately)

1. **1 battle per machine.** DEKU runs `--max-concurrent-battles 1`. BAKUGO runs `--max-concurrent-battles 1`. This is enforced in the systemd service file — do not override it.
2. **Discord battle reporting must work.** Completed results, replays, and ELO should post to #fouler-play-battles. If battles are happening but nothing's posting, fix it before doing anything else.
3. **One fouler-play process per machine.** Managed by systemd on DEKU. Check before any restart: `pgrep -c -f "run.py.*BugInTheCode"` should return 1.
4. **One source of truth.** This file. `fouler-play-v2/` is archived. `BOT_PROTOCOL.md` is supplementary. If they conflict, this file wins.

---

## Current Status

The bot has been overhauled from MCTS to a 1-ply eval engine with forced line detection (completed 2026-02-09). The new decision pipeline is: forced_lines -> eval -> penalty pipeline. Check `battle_stats.json` for current game count and ELO. The bot needs to reach 1700+ for matchup data to be meaningful.

---

## NEXT ACTION (read this first)

**DEKU:** The MCTS-to-eval overhaul is complete. Next steps:
1. Run games with the new eval engine and monitor ELO trend in `battle_stats.json`
2. Continue Phase 2-4 roadmap items below (opponent modeling, fat/stall play, advanced features)
3. Run `python replay_analysis/team_performance.py` to study loss patterns with the new engine
4. Make ONE improvement per cycle targeting the most common mistake pattern

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
- [x] battle_stats.json is being written (check file for current count)
- [x] Replays saved to replay_analysis/
- [x] Player loop runs unattended (scheduled task installed)
- [x] Push battle_stats.json after each batch

---

**Streaming:** See `docs/STREAMING.md` for overlay fix tasks (low priority).

---

## Bug Reports
- 2026-02-06: Some files still reference old bot account "LEBOTJAMESXD" (check `bot_monitor.py`, `replay_analysis/turn_review.py`, `register_ps_account.py`). Should use "ALL CHUNG" or read from .env.

---

## Communication Protocol

- Push code/data to `master` branch
- Update this TASKBOARD.md when completing items (check the box: `[x]`)
- DEKU pushes code changes, BAKUGO pushes battle data
- Check `battle_stats.json` for performance tracking
- If you need the other machine to act, write it under their Action Items section and push

## Completed Phases
- [x] Phase 1: Penalty system, timeout protection, Focus Sash detection, setup/phazer awareness, substitute awareness, contact move penalties
- [x] All porting work from upstream fork
- [x] Streaming pipeline (built, low priority)
- [x] Replay analysis pipeline (built, needs quality improvements)
- [x] Win-rate diagnosis (2026-02-07): 76% of losses had no Stealth Rock, recovery underused, excessive switching. Fixed with early-game hazard urgency + FAT/STALL recovery boost.
- [x] MCTS-to-Eval overhaul (2026-02-09): Replaced MCTS with 1-ply eval engine + forced line detection. Created `fp/search/eval.py`, `fp/search/forced_lines.py`. Pipeline: forced_lines -> eval -> penalty pipeline. All 9 penalty layers preserved.
