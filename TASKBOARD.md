# TASKBOARD.md - Fouler Play Coordination

**This is the single source of truth for fouler-play operations.** If anything contradicts this file, this file wins. `fouler-play-v2` has been archived — it was a duplicate clone DEKU created. Only `/home/ryan/projects/fouler-play` exists now.

**Purpose:** Overnight team-testing service for a competitive Pokemon player (fat/stall teams in gen9ou)
**Branch:** `master` is the live deployment/base branch and now contains the latest Codex-readiness/doc cleanup merged on 2026-03-09 (`a8b2d31`). Treat older branch-specific notes as historical unless a newer branch is explicitly called out here.
**Bot Account:** BugInTheCode on DEKU, ALL CHUNG on BAKUGO
**Updated:** 2026-03-09

---

## Standing Rules (check every session — fix drift immediately)

1. **3 concurrent battles per machine.** DEKU runs `--max-concurrent-battles 3` with `--search-parallelism 2`. BAKUGO runs `--max-concurrent-battles 3` with same parallelism. Total: 6 battles across both machines.
2. **Analysis posts to #project-fouler-play.** Batch analysis reports (with AI insights) post to #project-fouler-play, NOT #deku-workspace. Discord delivery: use OpenClaw event queue.
3. **Analysis source: external reasoning agent, not local LLM.** Batch analysis should use a strong external reasoning model via OpenClaw/OpenAI-compatible tooling. Claude (Opus or Sonnet) remains the preferred default for Pokemon-competent batch analysis today; qwen2.5-coder:3b and similar local lightweight models are banned for Pokemon analysis (hallucinations).
4. **One fouler-play process per machine.** Run directly (not systemd) on DEKU. Start BAKUGO's bot manually when online. Check: `pgrep -c -f "run.py.*BugInTheCode"` should return 1 on DEKU, `pgrep -c -f "run.py.*ALL_CHUNG"` should return 1 on BAKUGO (when running).
5. **One source of truth.** This file. `fouler-play-v2/` is archived. `BOT_PROTOCOL.md` is supplementary. If they conflict, this file wins.

---

## Current Status

The bot has been overhauled from MCTS to a 1-ply eval engine with forced line detection (completed 2026-02-09). The new decision pipeline is: forced_lines -> eval -> penalty pipeline. Check `battle_stats.json` for current game count and ELO. The bot needs to reach 1700+ for matchup data to be meaningful.

**2026-03-09 repo hygiene note:** the docs/coding-agent cleanup has already been merged to `master` (`a8b2d31`). Agents can treat `master` as the current repo guidance baseline again. The working tree may still show unrelated churn in `fp/data/movepool_data.json`; treat that file as non-blocking for doc-only tasks unless you are intentionally refreshing movepool data.

**2026-03-02 changes:**
- Merged foulest-play branch (106 commits) into master, deleted old branch
- **Round system implemented:** All battle outcomes (win/loss/disconnect) now count toward PS_RUN_COUNT quota. When all workers finish, a ROUND COMPLETE summary prints per-team W/L/DC stats. The bot stops for human evaluation -- no auto-start of next round.
- **Disconnect resilience:** Dead battles are forcibly terminated after BATTLE_DISCONNECT_STRIKES (default 5 = 10 min) consecutive message timeouts, preventing infinite stale loops.
- Runtime artifacts (.bot.pid, battle_stats.json, etc.) cleaned out of git tracking

---

## NEXT ACTION (read this first)

**🔎 CURRENT TRUTH (updated 2026-03-09):** the repo is not in a "battles halted pending strategic overhaul" state anymore.

**Proof of drift that mattered:**
- `master` already absorbed the recent repo-guidance cleanup (`a8b2d31 merge: codex-readiness cleanup and repetition detection into master`)
- `battle_stats.json` contains fresh ladder results through `2026-03-09T12:56:55.902086+00:00`
- Body Press proof work landed in `f4ab71e test: fix Body Press regression proof`

**What agents should optimize for now:**
1. Treat `master` as the active truth branch unless this file explicitly says otherwise
2. Use fresh `battle_stats.json` / replay evidence to diagnose the current ELO problem instead of assuming the bot is intentionally halted
3. Keep improvements bounded and evidence-backed: one decision-quality fix, one report-quality fix, or one runtime-truth correction per cycle
4. Preserve the faithful fat/stall mission; do not substitute ladder cheese for useful overnight team-testing data

**Current DEKU focus:**
1. Investigate the next highest-value decision drift still wasting games at live ladder conditions
2. Keep report/taskboard/runtime truth aligned when fixes land
3. Run the relevant validation for any code change and cite proof in the project channel

**Current BAKUGO focus:**
1. Keep the bot producing fresh battle data
2. Push battle stats/replays and verify the loop is yielding useful outcomes, not just process uptime
3. Escalate only if autonomous recovery fails or useful output stalls abnormally

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

### Decision Engine Bugs (from 2026-02-14 battle analysis)

**Fixed this session:**
- [x] **Destiny Bond awareness** — Bot KO'd opponent's Ceruledge with its win-condition Gliscor when Destiny Bond was revealed and Ceruledge was at low HP. Fixed: `detect_odd_move()` now checks for revealed Destiny Bond + ≤40% HP and applies 85% penalty to damaging attacks.
- [x] **Toxic suppressed vs boosted threats when no offensive answer** — Blissey had Seismic Toss (immune to Ghost) as its only damaging move vs Gholdengo (+2 SpA). `apply_threat_switch_bias()` suppressed Toxic as "passive" even though it was the only progress line. Fixed: Added `no_offensive_answer` exemption for status moves when all attacks are weight-0.
- [x] **Calm Mind + fixed damage** — Blissey used Calm Mind to boost SpA when its only damaging move was Seismic Toss (fixed 100 damage, ignores SpA). Fixed: `detect_odd_move()` now flags setup moves when no non-fixed-damage attack uses the boosted stat.

**Documented for later:**
- [ ] **#3: Ghost-immune-to-Dark not recognized before committing** — Gholdengo spent 8 turns using Hex (Ghost) into Ting-Lu (Dark type, immune to Ghost). The type immunity wasn't caught until the move was already selected. Root cause unclear — may be in eval scoring or move data. Needs investigation of how type matchups are evaluated in `fp/search/eval.py` when the bot's moves are Ghost-type vs Dark-type opponents.
- [ ] **#5: Recover loop detection** — Blissey entered a 4-turn Recover loop vs Drain Punch Conkeldurr. The opponent was healing more than Blissey could stall out. Needs cross-turn state tracking to detect when we're in a losing Recover loop (opponent gains net HP per cycle). Architectural challenge: current system is 1-ply and doesn't track multi-turn patterns.
- [x] **#6: Body Press vs Waterfall type matchup** — Fixed and regression-proofed. `f4ab71e test: fix Body Press regression proof` documents the proof path so future agents should not keep treating this as an open mystery.
- [ ] **#7: Infinite switch loop detection** — Corviknight and Blissey alternated switches for 11 turns vs Tera Normal Dragonite without ever using Toxic. Needs cross-turn state tracking to detect when we're in a non-progressing switch loop. Similar architectural challenge to #5 — needs multi-turn awareness.

---

## Communication Protocol

- Push code/data to the branch that matches current repo reality. As of `a8b2d31`, `master` is again the deployment/base branch and the repo-guidance baseline.
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
- [x] Round system + disconnect tracking (2026-03-02): All battle outcomes count toward quota. Round-complete summary with per-team stats. Dead battle timeout (DISCONNECT_STRIKES). Branch consolidation (foulest-play -> master).
