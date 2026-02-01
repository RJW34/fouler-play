# Fouler Play Diagnostics

Comprehensive monitoring and auto-recovery system for the Fouler Play stream.

**Cross-Platform Design:** Parallel Linux (DEKU) and Windows (BAKUGO) implementations.

## Linux Scripts (bash)

### `stream-health-check.sh`
Full diagnostic run - checks:
- ffmpeg process status
- Twitch live status via API
- Visual stream capture (screenshot)
- Bot activity on Pokemon Showdown ladder
- Current elo rating
- Overlay health

Usage: `./stream-health-check.sh`

Logs to: `health-check.log`
Screenshots saved to: `stream-snapshot-*.png`

### `heartbeat-diagnostic.sh`
Quick status check for regular heartbeat monitoring:
- Compact one-line status
- Elo tracking with change detection
- Auto-triggers fixes if issues detected

Usage: `./heartbeat-diagnostic.sh`

Output format: `Stream: ✅ | Bot: ✅ | Overlay: ✅ | Elo: 1523 (+12)`

### `auto-fix.sh`
Automated recovery for common failures:
- Restart dead stream process
- Detect stuck bot (low CPU usage)
- Restart overlay if down

Logs to: `auto-fix.log`

## Integration

### Heartbeat Cycle
Add to HEARTBEAT.md monitoring section:
```bash
cd /home/ryan/projects/fouler-play/diagnostics
./heartbeat-diagnostic.sh
```

### Manual Deep Check
When issues suspected:
```bash
cd /home/ryan/projects/fouler-play/diagnostics
./stream-health-check.sh
```

Review screenshots in diagnostics folder for visual verification.

## Elo Tracking

Current elo cached in `elo-cache.txt` and compared between runs.
Heartbeat shows change: `+12` (gained) or `-8` (lost).

## Visual Verification

Stream screenshots captured via streamlink for actual visual confirmation.
Requires: `pip install streamlink`

## Windows Scripts (PowerShell)

Located in `windows/` subdirectory for BAKUGO.

### `heartbeat-diagnostic.ps1`
Quick status from Windows perspective:
- OBS process health
- Overlay browser rendering (Chrome CEF)
- Stream live status
- Elo tracking (shared with Linux)

Output: `OBS: ✅ | Overlay: ✅ | Stream: ✅ | Elo: 1523 (+12)`

### `stream-health-check.ps1`
Full Windows diagnostic:
- OBS process and CPU usage
- Twitch live status verification
- Overlay browser detection
- OBS window screenshot capture
- Overlay HTTP endpoint check

### `auto-fix.ps1`
Windows-side recovery:
- Start OBS if not running
- Detect stuck overlay browser
- Log recovery actions

## Platform Coordination

**DEKU (Linux) monitors:**
- Bot backend process
- Network services
- Stream encoding (ffmpeg)
- Pokemon Showdown API

**BAKUGO (Windows) monitors:**
- OBS streaming application
- Overlay browser rendering
- Desktop window health
- Visual stream quality

Both agents run diagnostics in parallel and report to `#fouler-play` channel.

## TODO

- [ ] Add bot battle activity detection (parsing logs for recent moves)
- [ ] Integrate overlay auto-restart logic
- [ ] Add alerting for elo drops > 50 points
- [ ] Create historical elo graph (shared between platforms)
- [ ] Add Twitch viewer count monitoring
- [ ] Implement OBS WebSocket API for programmatic scene control
- [ ] Cross-platform shared elo database
