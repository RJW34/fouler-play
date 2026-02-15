# OBS 2-Bot Streaming Layout

## Scene: "Fouler Play - 2 Bots"

### Sources (in render order, top to bottom):

| # | Name | Type | URL | Position | Size | Notes |
|---|------|------|-----|----------|------|-------|
| 1 | **Background** | Color Source | N/A | (0, 0) | 1920x1080 | #0a0a12 (dark blue-gray) |
| 2 | **ALL CHUNG Battle** | Browser | `http://localhost:8777/obs?slot=1` | (20, 100) | 640x480 | YOUR bot (MAGNETON) |
| 3 | **BugInTheCode Battle** | Browser | `http://192.168.1.40:8777/obs?slot=1` | (1260, 100) | 640x480 | DEKU bot (ubunztu) |
| 4 | **Stats Overlay** | Browser | `http://localhost:8777/overlay/hybrid` | (0, 0) | 1920x1080 | Stats bar + telemetry |

---

## Browser Source Settings (ALL 3 SOURCES):

- **Width:** 640px (battles) / 1920px (overlay)
- **Height:** 480px (battles) / 1080px (overlay)
- **FPS:** 30
- **Shutdown source when not visible:** OFF
- **Refresh browser when scene becomes active:** ON
- **CSS:** (leave blank)
- **Use custom frame rate:** ON (30 FPS)

---

## What to Delete:

1. **Battle Slot 3** (redundant, overlaps Slot 2)
2. **BizHawk Emerald** (dead source, not used)
3. **Battle Telemetry** (if it's duplicate of Stats Overlay)

---

## Steps:

### 1. Create New Scene
1. OBS → Scenes → + → "Fouler Play - 2 Bots"

### 2. Add Sources (in order):

**A. Background:**
- Sources → + → Color Source → Name: "Background"
- Color: #0a0a12
- Width/Height: 1920x1080

**B. ALL CHUNG Battle:**
- Sources → + → Browser → Name: "ALL CHUNG Battle"
- URL: `http://localhost:8777/obs?slot=1`
- Width: 640, Height: 480
- Position: X=20, Y=100
- FPS: 30
- Check: "Refresh browser when scene becomes active"

**C. BugInTheCode Battle:**
- Sources → + → Browser → Name: "BugInTheCode Battle"
- URL: `http://192.168.1.40:8777/obs?slot=1`
- Width: 640, Height: 480
- Position: X=1260, Y=100
- FPS: 30
- Check: "Refresh browser when scene becomes active"

**D. Stats Overlay:**
- Sources → + → Browser → Name: "Stats Overlay"
- URL: `http://localhost:8777/overlay/hybrid`
- Width: 1920, Height: 1080
- Position: X=0, Y=0
- FPS: 30
- Check: "Refresh browser when scene becomes active"

### 3. Test:
- Start a battle on ALL CHUNG → should appear in left slot
- Check DEKU's battle appears in right slot
- Stats overlay should show combined data (if hybrid supports it)

---

## Troubleshooting:

**If ALL CHUNG battle doesn't appear:**
- Check `http://localhost:8777/state` in browser → should show active battle
- Restart serve_obs_page: `python streaming/serve_obs_page.py`

**If BugInTheCode battle doesn't appear:**
- Verify DEKU's serve_obs_page is running on ubunztu
- Check `http://192.168.1.40:8777/state` in browser
- Verify firewall allows port 8777 from MAGNETON

**If stats are stale:**
- Check .env has `SHOWDOWN_ACCOUNTS=allchung` (or buginthecode for DEKU)
- Restart serve_obs_page to reload .env
- ELO refreshes every 60sec by default

---

## ENV Vars to Check (in `.env`):

```bash
# On MAGNETON (BAKUGO)
OBS_SERVER_PORT=8777
SHOWDOWN_ACCOUNTS=allchung
PS_FORMAT=gen9ou
DEKU_STATE_URL=http://192.168.1.40:8777/state  # (optional, for future cross-machine aggregation)

# On ubunztu (DEKU) - same settings
OBS_SERVER_PORT=8777
SHOWDOWN_ACCOUNTS=buginthecode
PS_FORMAT=gen9ou
```

---

## Screenshot:
Take screenshot once configured and post to #deku-bakugo-sync for verification.
