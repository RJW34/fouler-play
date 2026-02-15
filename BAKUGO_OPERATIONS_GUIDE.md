# BAKUGO Operations Guide — Fouler Play (ALL CHUNG Account)

## Quick Start

**Status check:** Are we running?
```powershell
Get-Process | Select-String python
# OR
tasklist | findstr python
```

**Restart ALL CHUNG:**
```powershell
# Stop the process
Stop-Process -Name python -Force
# Wait 5 seconds
Start-Sleep -Seconds 5
# Restart (command to be verified on MAGNETON)
```

**Check logs (real-time):**
```powershell
Get-Content -Path "path-to-logs\bot.log" -Tail 50 -Wait
```

---

## Current Configuration (Feb 15, 2026)

### Bot Details
- **Account:** ALL CHUNG (on Pokémon Showdown)
- **Format:** gen9ou (VGC gen 9 OU ladder)
- **Teams:** 3 rotating (fat-team-1-stall, fat-team-2-pivot, fat-team-3-dondozo)
- **Machine:** MAGNETON (192.168.1.181)
- **Concurrency:** 1 battle max (--max-concurrent-battles 1)
- **Target ELO:** 1700 (currently ~1341, +359 needed)

### What NEEDS to Exist on MAGNETON
- [ ] Fouler Play repository (fork from upstream 55fa9b4)
- [ ] Python environment with dependencies (numpy, websocket-client, etc.)
- [ ] Configuration file with ALL CHUNG credentials (username/password)
- [ ] `/logs/` directory for battle logs
- [ ] Team list at `/teams/deku-team.list` (3 teams, rotating)
- [ ] Systemd service or scheduled task to keep bot running

### Expected Behavior
- Bot connects to Pokémon Showdown gen9ou ladder
- Searches for battles, claims them, plays them
- Logs all battles with replay links + ELO
- After every 30 battles, generates improvement plan (analysis by local LLM)
- Every 2-6 hours, watcher triggers analysis and posts to Discord

---

## Diagnostics

### Is the bot running?
```powershell
tasklist | findstr "python"
# If you see a Python process with fouler-play in the path, ✅ RUNNING
```

### Is ALL CHUNG online on Showdown?
- Go to https://pokemonshowdown.com/
- Search for "ALL CHUNG" in the user list
- If online, ✅ CONNECTED

### Check recent ELO
- Showdown user page: https://pokemonshowdown.com/users/ALL%20CHUNG
- Look for "gen9ou" rating
- Should be climbing toward 1700

### Is the bot stuck?
- Check logs: Look for "Waiting for battle to start" repeated for >30 min
- Check if Showdown has battle glitches (rare, but happens)
- **Fix:** Restart the bot

---

## Common Issues & Fixes

### Issue: Bot not connecting to Showdown
**Cause:** Credentials wrong, Showdown down, or network issue
**Fix:**
1. Verify ALL CHUNG password is correct in config
2. Check internet connection: `ping 8.8.8.8`
3. Verify Showdown is up: https://pokemonshowdown.com/
4. Restart bot, watch logs for connection errors

### Issue: Bot claims battle but never moves
**Cause:** Format mismatch (claiming gen9randombattle instead of gen9ou) or battle server glitch
**Fix:**
1. Check recent logs for format validation errors
2. If present, issue was fixed (Feb 14) — restart bot with latest code
3. If not, report to DEKU

### Issue: ELO not climbing despite battles
**Cause:** 
- Only your bot running (DEKU's side stalled)
- Rating on Showdown is stale
- Opponent selection bias (too many strong opponents)
**Fix:**
1. Verify DEKU's bot (BugInTheCode) is also running on ubunztu
2. Check Showdown rating directly (might be ahead of local tracker)
3. Continue grinding — ELO will stabilize over 50+ battles

### Issue: Logs not updating
**Cause:** Bot crashed silently or process died
**Fix:**
1. Check if Python process still exists: `tasklist | findstr python`
2. If gone, restart
3. Check event logs for system errors

---

## Monitoring Checklist (Do This Daily)

- [ ] Bot process is running: `tasklist | findstr python`
- [ ] ALL CHUNG is online on Showdown
- [ ] ELO is at or above last known value
- [ ] No errors in recent logs
- [ ] Battles are completing (not stuck waiting)

---

## Configuration Details (TO BE FILLED IN)

**Questions for BAKUGO — please update this section:**

1. **Where is fouler-play installed?** (Full path, e.g., `C:\Users\...\fouler-play\`)
2. **How is the bot started?** (Batch script? Scheduled task? Manual command?)
3. **Where are logs stored?** (Full path)
4. **Credentials location?** (Config file path, or env variables?)
5. **Service/task name?** (If using systemd/scheduler, what's it called?)
6. **Startup command?** (Exact command to start ALL CHUNG)

Once you answer these, I'll create a quick-reference cheat sheet.

---

## Discord Integration

**Where BAKUGO reports go:** <#1467359048650330316> (deku-bakugo-sync)

**What you'll see posted to <#1466691161363054840> (project-fouler-play):**
- Battle results (wins/losses with replays)
- ELO updates every ~10 battles
- Improvement plans every 30 battles

**If reporting looks broken:**
- DM DEKU in sync channel — might be a bug in the Discord bot
- Keep grinding; reporting is secondary to data collection

---

## Emergency Contact

If ALL CHUNG is down or ELO drops suspiciously:
1. **First:** Restart the bot
2. **Second:** Check logs for errors
3. **Third:** Post in <#1467359048650330316> with the error message + last battle timestamp

Don't wait 24 hours if the bot crashes.

---

## Future Improvements (TBD)

- Automated restarts if bot hangs >30 min
- Real-time Showdown rating sync (eliminate tracker staleness)
- Per-team ELO tracking (optimize team rotation strategy)
- Video intake for strategy calibration

---

**Last Updated:** Feb 15, 2026 by DEKU
**Next Review:** After 100 battles on the account (monitor stability)
