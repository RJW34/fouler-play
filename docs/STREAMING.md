# Streaming Overlay Fix Tasks (Low Priority)

**Both DEKU and BAKUGO** need to work on this. The stream is partially broken.

**Accounts:** ALL CHUNG (BAKUGO/Windows) and BUGINTHECODE (DEKU/Linux). Both play on ladder. Stream runs on BAKUGO.

## CRITICAL: How Battle Display Works (NO IFRAMES)

Pokemon Showdown **blocks iframes** with `X-Frame-Options: DENY`. You CANNOT embed PS battle URLs in iframes. The working approach uses the **spectator invite workaround**:

1. A **dedicated spectator account** (e.g., `FoulerPlayViewer`) is logged into OBS browser sources on BAKUGO
2. When a battle starts, the bot sends `/invite {spectator_username}` (already coded in `fp/run_battle.py:1254-1256`)
3. `serve_obs_page.py` updates the OBS browser source URLs via **OBS WebSocket** (`streaming/obs_websocket.py`) to navigate to the battle URL
4. Because the spectator account is invited and logged in, the OBS browser source can view the battle

**DO NOT try to use iframes for battles. They will not work.**

## Target Layout (1 battle per machine, 2 total across DEKU + BAKUGO)

Each machine runs **1 concurrent battle** (`MAX_CONCURRENT_BATTLES=1`). OBS has these sources:

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

## What's Wrong (from stream screenshot)

1. **Center panel shows OBS default page** — browser source URL not set
2. **Worker 1 shows "Unknown" opponent** — opponent name not propagating from battle hooks
3. **ELO panel needs both accounts** — `serve_obs_page.py` only fetches one account's ELO. Need both ALL CHUNG and BUGINTHECODE
4. **Stats mismatch** — header shows 24W-14L but Today panel shows 23-12. Root cause: `overlay.html` uses localStorage which drifts from `daily_stats.json`
5. **Battles don't always hook in** — `send_stream_event()` in `run_battle.py:424` silently fails if `serve_obs_page.py` isn't running. Also `SPECTATOR_USERNAME` may not be set, so spectator never gets invited

## DEKU Tasks (Code Fixes)

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

## BAKUGO Tasks (OBS + Config)

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

## Verification
- [ ] `SPECTATOR_USERNAME` is set in `.env` and bot logs show "Inviting spectator: ..." on battle start
- [ ] `serve_obs_page.py` connects to OBS WebSocket (check logs for "OBS-WS" messages)
- [ ] Battle 1 and Battle 2 OBS sources update URLs when battles start
- [ ] Stats overlay shows both ALL CHUNG and BUGINTHECODE ELOs
- [ ] Today stats match between overlay panels
- [ ] No Worker 3 / slot 3 in the stream
- [ ] `python -m pytest tests/ -v` passes
