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

**Step 1 — Generate the report:**
```bash
python replay_analysis/team_performance.py
```
Study the output. Key things to look for:
- Per-Pokemon faint rates: which of our Pokemon die most often? A wall dying every game means the bot isn't recovering or is staying in bad matchups.
- Worst matchups: which opponent Pokemon cause the most losses? This tells you what the bot can't handle.
- Is one team performing much worse than others? (fat-team-2-pivot is 44% — worst of the three)

**Step 2 — Read 3-5 loss replays manually:**
Look in `replay_analysis/` for `.json` replay files. Parse the `log` field — search for `|move|` and `|switch|` lines involving our side (p1 or p2 depending on `|player|` line matching "ALL CHUNG"). Look for these common fat/stall mistakes:
- **Not using recovery:** Walls like Gliscor/Toxapex/Blissey should Recover/Roost when below ~60% HP. If the bot attacks instead of healing and then dies, recovery weighting is too low.
- **Switching too aggressively:** Fat teams pivot, but excessive switching into hazards bleeds HP. Check if the bot switches more than ~40% of turns.
- **Resisted moves:** Using Earthquake into a Flying type, or Fire move into Flash Fire. The MCTS should handle this but check if it's happening.
- **No early hazards:** Stealth Rock should go up in the first 2-3 turns almost always with fat. If the bot never leads with its rocker, hazard priority is wrong.
- **Not using status:** Toxic/Will-O-Wisp are core stall tools. If the bot rarely uses them, status move weighting may be too low for FAT playstyle.

**Step 3 — Make ONE fix:**
Based on what you find, fix the single most impactful pattern. Examples:
- If recovery is underused: boost recovery weight in the FAT playstyle config or in the penalty system
- If switching is excessive: check how `switch_penalty_multiplier` interacts with `apply_switch_penalties()`
- If hazards aren't set: add early-game hazard priority logic
- If status moves are ignored: check if Toxic/WoW are being deprioritized somewhere

**Step 4 — Verify and push:**
- [ ] Run team_performance.py, study the report for patterns
- [ ] Review 3-5 recent losses — identify the #1 recurring mistake
- [ ] Implement a targeted fix for that mistake pattern
- [ ] `python -m pytest tests/ -v` — tests pass
- [ ] `python -c "from fp.search.main import find_best_move; print('OK')"` — imports work
- [ ] Update this TASKBOARD.md (check boxes, note what you found/fixed)
- [ ] `git push origin foulest-play`

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

## COLLABORATIVE: Fix Streaming Overlay

**Both DEKU and BAKUGO** need to work on this. The stream is partially broken.

**Accounts:** ALL CHUNG (BAKUGO/Windows) and BUGINTHECODE (DEKU/Linux). Both play on ladder. Stream runs on BAKUGO.

### Target Layout (2 battles, not 3)

We run **2 concurrent battles** (`MAX_CONCURRENT_BATTLES=2`). The stream is a 3-column layout:

```
┌──────────────────┬──────────────────┬──────────────────┐
│   BATTLE 1       │   STATS CENTER   │   BATTLE 2       │
│   (Worker 1)     │                  │   (Worker 2)     │
│                  │   ELO            │                  │
│   [PS iframe]    │   ALL CHUNG 1198 │   [PS iframe]    │
│                  │   BUGINTHECODE   │                  │
│                  │   1139           │                  │
│   vs Opponent    │                  │   vs Opponent    │
│                  │   TODAY 23-12    │                  │
│                  │   Winrate 66%    │                  │
│                  │   Streak 2L      │                  │
│                  │                  │                  │
│                  │   NEXT FIX       │                  │
│                  │   [from todo]    │                  │
└──────────────────┴──────────────────┴──────────────────┘
Top bar: ALL CHUNG | Active: N | Session: NW-NL | GEN 9 OU | status
Bottom bar: vs Opponent1, Opponent2 | [W][W][L][W][L] pips | streak
```

**The center column is NOT a battle slot.** It is the stats dashboard. There is no Worker 3. Remove it entirely from `obs_battles.html`.

### What's Wrong (from stream screenshot)

1. **Center panel shows OBS default page** — it was configured as a 3rd battle slot browser source, but should be the built-in stats panel
2. **Worker 1 shows "Unknown" opponent** — opponent name not propagating from battle hooks
3. **ELO panel needs both accounts** — `serve_obs_page.py` only fetches one account's ELO. Need both ALL CHUNG and BUGINTHECODE
4. **Stats mismatch** — header shows 24W-14L but Today panel shows 23-12. Root cause: `overlay.html` uses localStorage for win/loss tracking which drifts from `daily_stats.json`. Server-side `daily_stats.json` should be the single source of truth
5. **Battles don't always hook in** — `send_stream_event()` in `run_battle.py` (line 424) silently fails if `serve_obs_page.py` isn't running

### DEKU Tasks (Code Fixes)

**1. Merge the two HTML pages into one.**
Currently there are two separate pages: `obs_battles.html` (battle iframes) and `overlay.html` (stats). These need to be ONE page served at `/obs`. The layout:
- Left column: Battle 1 iframe + Worker 1 pill (opponent name, status)
- Center column: Stats dashboard (ELO for both accounts, today W-L, winrate, streak, next fix). Take the stats content from `overlay.html`'s dead-card panels and put it here. This is NOT an iframe — it's part of the page.
- Right column: Battle 2 iframe + Worker 2 pill
- Top bar and bottom bar stay as-is from `overlay.html`
- **Remove Worker 3 entirely** — no third battle slot

**2. Multi-account ELO in `streaming/serve_obs_page.py`:**
- Read new env var `SHOWDOWN_ACCOUNTS` (comma-separated PS usernames, e.g., `allchung,buginthecode`)
- Modify `fetch_showdown_elo()` to accept a `user_id` parameter
- Store ELO per account in `_ladder_cache` (change from `{elo: X}` to `{accounts: {name: elo, ...}}`)
- Modify `build_state_payload()` to include `accounts_elo: {"allchung": 1198, "buginthecode": 1139}`
- Keep the existing single `elo` field in the payload for backward compatibility (use the primary account)

**3. Fix stats source of truth:**
- In the merged page, do NOT use localStorage for win/loss counting
- Read `today_wins` / `today_losses` directly from the WebSocket `status` payload (comes from `daily_stats.json`)
- Recent results pips (W/L squares in bottom bar) should come from the server, not inferred from ELO changes

**4. Fix opponent name in Worker pills:**
- In `updateOverlay()`, worker name reads from `activeBattles[i].opponent`
- Verify `active_battles.json` always includes the `opponent` field when a battle starts
- Check `run_battle.py` line ~1279: `opponent_name = battle.opponent.account_name` — ensure this resolves before the entry is written

**5. Add retry to `send_stream_event()`** in `fp/run_battle.py` (line 424):
- Add 1 retry with 2s delay if first attempt fails
- Log a warning (not just debug) on failure so issues are visible

### BAKUGO Tasks (OBS + Config)

1. **Ensure `serve_obs_page.py` is running BEFORE OBS launches.** Add to `player_loop.bat` or start manually:
   ```
   python streaming/serve_obs_page.py
   ```
2. **Set `MAX_CONCURRENT_BATTLES=2`** in `.env`
3. **Configure OBS browser sources** — you only need TWO browser sources now:
   - **Full stream page**: `http://localhost:8777/obs` (this is the merged page with battles + stats)
   - OR if using separate sources: Left battle `http://localhost:8777/obs?slot=1`, Right battle `http://localhost:8777/obs?slot=2`
   - **Remove the center browser source** — the center panel is built into the page, not a separate source
4. **Set `.env` variables** for multi-account ELO:
   ```
   SHOWDOWN_USER_ID=allchung
   SHOWDOWN_ACCOUNTS=allchung,buginthecode
   ```
5. **Verify** — open `http://localhost:8777/obs` in a browser. Confirm: 2 battle panels (left/right), stats center with both ELOs, correct today stats.

### Verification
After both agents push their changes:
- [ ] `serve_obs_page.py` starts without errors
- [ ] `http://localhost:8777/obs` shows 2 battle panels + center stats (no Worker 3)
- [ ] Center panel displays both ALL CHUNG and BUGINTHECODE ELOs
- [ ] Worker pills update with opponent names when battles start
- [ ] Today stats in center panel match session record in top bar
- [ ] `python -m pytest tests/ -v` passes

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
