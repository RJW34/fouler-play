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
- [x] Run team_performance.py, study the report for patterns
- [x] Review 3-5 recent losses — identify the #1 recurring mistake
- [x] Implement a targeted fix for that mistake pattern
- [x] `python -m pytest tests/ -v` — tests pass (82/82)
- [x] `python -c "from fp.search.main import find_best_move; print('OK')"` — imports work
- [x] Update this TASKBOARD.md (check boxes, note what you found/fixed)
- [ ] `git push origin foulest-play`

**Diagnosis findings (2026-02-07):**
- 76% of losses had NO Stealth Rock set — the #1 problem for fat/stall teams
- Bot treated turn 1 identically to turn 30 — no early-game hazard priority
- Recovery moves underused: avg 1.7/game in wins vs needed much more in losses
- Switch rate in losses was 46% (too high) vs 32% in wins
- MCTS inherently undervalues non-damaging moves (no immediate HP impact)

**Fix applied:** Early-game hazard urgency + FAT/STALL recovery boost
- Turns 1-3: Stealth Rock gets 2-3x weight boost for FAT/STALL teams, 1.8x for balance
- FAT/STALL recovery moves get 2.5x boost when HP ≤40%, 1.8x when ≤60%
- Also fixed: hiddenpower60 KeyError in standard_battles.py, Bayesian test API mismatch

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

### CRITICAL: How Battle Display Works (NO IFRAMES)

Pokemon Showdown **blocks iframes** with `X-Frame-Options: DENY`. You CANNOT embed PS battle URLs in iframes. The working approach uses the **spectator invite workaround**:

1. A **dedicated spectator account** (e.g., `FoulerPlayViewer`) is logged into OBS browser sources on BAKUGO
2. When a battle starts, the bot sends `/invite {spectator_username}` (already coded in `fp/run_battle.py:1254-1256`)
3. `serve_obs_page.py` updates the OBS browser source URLs via **OBS WebSocket** (`streaming/obs_websocket.py`) to navigate to the battle URL
4. Because the spectator account is invited and logged in, the OBS browser source can view the battle

**DO NOT try to use iframes for battles. They will not work.**

### Target Layout (2 battles, not 3)

We run **2 concurrent battles** (`MAX_CONCURRENT_BATTLES=2`). OBS has these sources:

```
┌──────────────────┬──────────────────┬──────────────────┐
│   BATTLE 1       │   STATS CENTER   │   BATTLE 2       │
│                  │                  │                  │
│   OBS Browser    │   OBS Browser    │   OBS Browser    │
│   Source #1      │   Source #3      │   Source #2      │
│   (spectator     │   (localhost)    │   (spectator     │
│    account)      │                  │    account)      │
│                  │   ELO            │                  │
│   URL set by     │   ALL CHUNG 1198 │   URL set by     │
│   serve_obs via  │   BUGINTHECODE   │   serve_obs via  │
│   OBS WebSocket  │   1139           │   OBS WebSocket  │
│                  │                  │                  │
│                  │   TODAY 23-12    │                  │
│                  │   Winrate 66%    │                  │
│                  │   Streak 2L      │                  │
│                  │                  │                  │
│                  │   NEXT FIX       │                  │
│                  │   [from todo]    │                  │
└──────────────────┴──────────────────┴──────────────────┘
Top bar overlay: ALL CHUNG | Active: N | Session: NW-NL | GEN 9 OU
Bottom bar overlay: vs Opponent1, Opponent2 | [W][W][L][W][L] pips
```

- **Battle sources (left + right):** OBS browser sources logged into the spectator PS account. `serve_obs_page.py` changes their URLs via OBS WebSocket when battles start/end. When idle, they show `http://localhost:8777/idle`.
- **Stats center:** OBS browser source pointing at `http://localhost:8777/overlay` (local content, no iframe issue). Shows both account ELOs, today's record, streak, next fix.
- **Top/bottom bars:** Part of the overlay page or separate thin browser sources from `http://localhost:8777/overlay?mode=bottom`.
- **No Worker 3.** Only 2 battle slots.

### What's Wrong (from stream screenshot)

1. **Center panel shows OBS default page** — browser source URL not set
2. **Worker 1 shows "Unknown" opponent** — opponent name not propagating from battle hooks
3. **ELO panel needs both accounts** — `serve_obs_page.py` only fetches one account's ELO. Need both ALL CHUNG and BUGINTHECODE
4. **Stats mismatch** — header shows 24W-14L but Today panel shows 23-12. Root cause: `overlay.html` uses localStorage which drifts from `daily_stats.json`
5. **Battles don't always hook in** — `send_stream_event()` in `run_battle.py:424` silently fails if `serve_obs_page.py` isn't running. Also `SPECTATOR_USERNAME` may not be set, so spectator never gets invited

### DEKU Tasks (Code Fixes)

**1. Multi-account ELO in `streaming/serve_obs_page.py`:**
- Read new env var `SHOWDOWN_ACCOUNTS` (comma-separated PS usernames, e.g., `allchung,buginthecode`)
- Modify `fetch_showdown_elo()` to accept a `user_id` parameter
- Store ELO per account in `_ladder_cache` (change from `{elo: X}` to `{accounts: {name: elo, ...}}`)
- Modify `build_state_payload()` to include `accounts_elo: {"allchung": 1198, "buginthecode": 1139}`
- Keep existing single `elo` field for backward compatibility (use primary account)

**2. Multi-account ELO display in `streaming/overlay.html`:**
- Replace the single `mid-elo` div with a display of all accounts from `accounts_elo`
- Show each account name + ELO value (e.g., "ALL CHUNG 1198 | BUGINTHECODE 1139")
- Apply existing flash-up/flash-down animations per account

**3. Fix stats source of truth in `streaming/overlay.html`:**
- Do NOT use localStorage for win/loss counting
- Read `today_wins` / `today_losses` directly from WebSocket `status` payload (comes from `daily_stats.json`)
- Recent results pips should come from server, not inferred from ELO changes

**4. Fix opponent name in Worker pills:**
- Verify `active_battles.json` always includes `opponent` field when a battle starts
- Check `run_battle.py` line ~1279: `opponent_name = battle.opponent.account_name` — ensure this resolves before the entry is written

**5. Remove Worker 3 from `obs_battles.html`:**
- Remove slot 3 entirely from the HTML and JS
- Only 2 slots: left (Worker 1) and right (Worker 2)
- `serve_obs_page.py` should only manage 2 OBS battle sources, not 3

**6. Add retry to `send_stream_event()`** in `fp/run_battle.py:424`:
- Add 1 retry with 2s delay if first attempt fails
- Log a warning (not just debug) on failure

### BAKUGO Tasks (OBS + Config)

1. **Create a spectator PS account** if one doesn't exist (e.g., `FoulerPlayViewer`). This account must be DIFFERENT from ALL CHUNG and BUGINTHECODE.

2. **Set `.env` variables:**
   ```
   MAX_CONCURRENT_BATTLES=2
   SPECTATOR_USERNAME=FoulerPlayViewer
   SHOWDOWN_USER_ID=allchung
   SHOWDOWN_ACCOUNTS=allchung,buginthecode
   OBS_WS_HOST=localhost
   OBS_WS_PORT=4455
   OBS_WS_PASSWORD=your_obs_ws_password
   ```

3. **Enable OBS WebSocket Server** — in OBS: Tools → WebSocket Server Settings → Enable, set password, port 4455.

4. **Configure 3 OBS browser sources:**
   - **"Battle 1"** (left): Log into PS with spectator account. Initial URL: `http://localhost:8777/idle`. `serve_obs_page.py` will update this URL automatically when battles start.
   - **"Battle 2"** (right): Same spectator account login. Initial URL: `http://localhost:8777/idle`.
   - **"Stats Overlay"** (center): `http://localhost:8777/overlay`. No PS login needed — this is local content.
   - **Remove any "Battle 3" / "Worker 3" source.**

5. **Start `serve_obs_page.py` BEFORE OBS:**
   ```
   python streaming/serve_obs_page.py
   ```
   It will auto-detect the battle browser sources by name (looks for "battle", "worker", or "showdown" in source names).

6. **Test:** Start a battle. Check logs for `Inviting spectator: FoulerPlayViewer`. Verify OBS browser source URL updates and battle displays.

### Verification
- [ ] `SPECTATOR_USERNAME` is set in `.env` and bot logs show "Inviting spectator: ..." on battle start
- [ ] `serve_obs_page.py` connects to OBS WebSocket (check logs for "OBS-WS" messages)
- [ ] Battle 1 and Battle 2 OBS sources update URLs when battles start
- [ ] Stats overlay shows both ALL CHUNG and BUGINTHECODE ELOs
- [ ] Today stats match between overlay panels
- [ ] No Worker 3 / slot 3 in the stream
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
