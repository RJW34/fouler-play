# Fouler Play Diagnostic Integration

## ✅ What's Ready

### Linux (DEKU) - `/home/ryan/projects/fouler-play/diagnostics/`
- ✅ `heartbeat-diagnostic.sh` - Quick status (stream, bot, overlay, elo)
- ✅ `stream-health-check.sh` - Full diagnostic with stream capture
- ✅ `auto-fix.sh` - Auto-recovery for dead processes
- ✅ Syntax validated and executable
- ✅ Integrated into HEARTBEAT.md

### Windows (BAKUGO) - `diagnostics/windows/`
- ✅ `heartbeat-diagnostic.ps1` - OBS/overlay status
- ✅ `stream-health-check.ps1` - Full diagnostic with screenshots
- ✅ `auto-fix.ps1` - OBS/overlay recovery
- ✅ Ready for BAKUGO to copy to workspace

## Division of Labor

**DEKU (Linux) monitors:**
- ffmpeg stream process
- Bot backend (Node.js)
- Pokemon Showdown API
- Overlay HTTP server (localhost:3001)
- Elo scraping from Showdown ladder

**BAKUGO (Windows) monitors:**
- OBS process and streaming status
- Overlay browser source rendering (Chrome CEF)
- OBS window screenshots
- Desktop capture health
- Visual stream quality

## Heartbeat Integration

### DEKU's Heartbeat (every 30 min)
```bash
cd /home/ryan/projects/fouler-play/diagnostics && ./heartbeat-diagnostic.sh
```

Output: `Stream: ✅ | Bot: ✅ | Overlay: ✅ | Elo: 1523 (+12)`

### BAKUGO's Heartbeat (every 30 min)
```powershell
cd C:\Users\Ryan\projects\BAKUGO\workspace\fouler-play
.\heartbeat-diagnostic.ps1
```

Output: `OBS: ✅ | Overlay: ✅ | Stream: ✅ | Elo: 1523 (+12)`

## Shared State

**Elo Tracking:**
- Both scripts read from Pokemon Showdown ladder API
- Cache previous elo to calculate change (+/- points)
- Could share cache file via network drive in future

**Coordination Channel:**
- Both agents post results to `#fouler-play` Discord channel
- Alerts posted when auto-fix triggers
- Visual evidence (screenshots/logs) shared when needed

## Auto-Recovery Logic

**Linux (DEKU):**
1. Detect dead ffmpeg stream → restart via `start-stream.sh`
2. Detect stuck bot (low CPU) → flag for investigation
3. Detect dead overlay server → restart service

**Windows (BAKUGO):**
1. Detect OBS not running → launch OBS
2. Detect overlay browser missing → trigger manual refresh notification
3. Detect low OBS CPU (idle) → alert for investigation

## Testing Workflow

1. **DEKU runs:** `./stream-health-check.sh`
   - Captures stream snapshot via streamlink
   - Checks all Linux-side services
   - Logs to `health-check.log`

2. **BAKUGO runs:** `.\stream-health-check.ps1`
   - Takes OBS window screenshot
   - Verifies overlay rendering
   - Logs to `health-check.log`

3. **Both compare results** in coordination channel
4. **Visual verification:** Compare screenshots for stream quality

## Next Steps

- [ ] BAKUGO copies PowerShell scripts to workspace
- [ ] BAKUGO adds heartbeat-diagnostic.ps1 to heartbeat cycle
- [ ] Test full diagnostic run (both platforms simultaneously)
- [ ] Set up shared elo tracking database/file
- [ ] Add OBS WebSocket API integration for programmatic control
- [ ] Build historical elo graph from combined data

## Cross-Platform Translation

See `/home/ryan/projects/deku-tools/docs/cross-platform-translation.md` for framework.

**Key Principle:** Each platform does what it's best at. Coordinate results, don't duplicate work.
