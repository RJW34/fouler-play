# OBS Scene Collection Specification
## Fouler Play - 6 Battle Monitor

**Scene Collection Name:** `Fouler Play - 6 Battles`
**Main Scene:** `Multi-Battle Monitor`
**Resolution:** 1920×1080 @ 60fps
**Layout:** 3×2 grid (6 tiles)

---

## Grid Layout

```
┌─────────────┬─────────────┬─────────────┐
│   640×360   │   640×360   │   640×360   │
│             │             │             │
│  DEKU-1     │  DEKU-2     │  DEKU-3     │
│  (Stall)    │  (Pivot)    │  (Dondozo)  │
│             │             │             │
├─────────────┼─────────────┼─────────────┤
│   640×360   │   640×360   │   640×360   │
│             │             │             │
│  CHUNG-1    │  CHUNG-2    │  CHUNG-3    │
│  (Stall)    │  (Pivot)    │  (Dondozo)  │
│             │             │             │
└─────────────┴─────────────┴─────────────┘
     1920×720 (main battle grid)
```

**Aggregate panel:** 1920×360 (bottom area, y=720-1080) — optional for phase 1

---

## Tile Positions

| Tile | X | Y | Width | Height | Bot | Team |
|------|---|---|-------|--------|-----|------|
| DEKU-1 | 0 | 0 | 640 | 360 | DEKU | Stall |
| DEKU-2 | 640 | 0 | 640 | 360 | DEKU | Pivot |
| DEKU-3 | 1280 | 0 | 640 | 360 | DEKU | Dondozo |
| CHUNG-1 | 0 | 360 | 640 | 360 | CHUNG | Stall |
| CHUNG-2 | 640 | 360 | 640 | 360 | CHUNG | Pivot |
| CHUNG-3 | 1280 | 360 | 640 | 360 | CHUNG | Dondozo |

---

## Per-Tile Source Stack (bottom to top)

### 1. Background Color
- **Type:** Color Source
- **Name:** `BG-{BOT}-{SLOT}` (e.g., `BG-DEKU-1`)
- **Color:**
  - DEKU: `#001a33` (dark blue)
  - CHUNG: `#330000` (dark red)
- **Size:** 640×360
- **Position:** (see table above)

### 2. Border
- **Type:** Color Source (thin rectangle)
- **Name:** `Border-{BOT}-{SLOT}`
- **Color:** `#333333` (gray)
- **Size:** 636×356 (4px margin)
- **Position:** +2 X, +2 Y from tile origin

### 3. Header Text (Static)
- **Type:** Text (FreeType 2)
- **Name:** `Header-{BOT}-{SLOT}`
- **Text:** `DEKU-1 | STALL` (uppercase)
- **Font:** Arial Black, 24pt, Bold
- **Color:** 
  - DEKU: `#4da6ff` (light blue)
  - CHUNG: `#ff6666` (light red)
- **Position:** Tile X+10, Tile Y+10
- **Alignment:** Left

### 4. Opponent Name (Dynamic)
- **Type:** Text (FreeType 2) — Read from file
- **Name:** `Opponent-{BOT}-{SLOT}`
- **Source:** Read from battle file, line 1 (extract after `opponent=`)
- **Font:** Arial, 32pt, Bold
- **Color:** `#FFFFFF`
- **Position:** Tile X+320, Tile Y+100 (centered horizontally)
- **Alignment:** Center
- **Wrap:** Enabled (max width 600px)

### 5. Status Badge (Dynamic)
- **Type:** Text (FreeType 2) — Read from file
- **Name:** `Status-{BOT}-{SLOT}`
- **Source:** Read from battle file, line 3 (extract after `status=`)
- **Font:** Arial, 20pt, Bold
- **Color:** 
  - `searching` → `#FFAA00` (orange)
  - `battling` → `#00FF00` (green)
  - `won` → `#00FFFF` (cyan)
  - `lost` → `#FF0000` (red)
- **Position:** Tile X+320, Tile Y+150 (centered)
- **Alignment:** Center
- **Transform:** UPPERCASE

### 6. ELO Display (Dynamic)
- **Type:** Text (FreeType 2) — Read from file
- **Name:** `ELO-{BOT}-{SLOT}`
- **Source:** Read from battle file, line 4 (extract after `elo=`)
- **Format:** `ELO: {value}`
- **Font:** Consolas, 24pt
- **Color:** `#FFD700` (gold)
- **Position:** Tile X+320, Tile Y+190 (centered)
- **Alignment:** Center

### 7. Turn Counter (Dynamic)
- **Type:** Text (FreeType 2) — Read from file
- **Name:** `Turns-{BOT}-{SLOT}`
- **Source:** Read from battle file, line 5 (extract after `turns=`)
- **Format:** `Turn: {value}`
- **Font:** Arial, 18pt
- **Color:** `#CCCCCC`
- **Position:** Tile X+320, Tile Y+230 (centered)
- **Alignment:** Center
- **Visible:** Only when turns > 0

### 8. Record (Dynamic)
- **Type:** Text (FreeType 2) — Read from file
- **Name:** `Record-{BOT}-{SLOT}`
- **Source:** Read from battle file, line 6 (extract after `record=`)
- **Format:** `Record: {value}`
- **Font:** Arial, 18pt
- **Color:** `#AAAAAA`
- **Position:** Tile X+320, Tile Y+270 (centered)
- **Alignment:** Center

---

## File Paths for Text Sources

### DEKU (via network path or local if synced)
```
\\ubunztu\projects\fouler-play\logs\deku-battle-1.txt
\\ubunztu\projects\fouler-play\logs\deku-battle-2.txt
\\ubunztu\projects\fouler-play\logs\deku-battle-3.txt
```

**Alternative (if local sync):**
```
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-1.txt
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-2.txt
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-3.txt
```

### CHUNG (local on MAGNETON)
```
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-1.txt
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-2.txt
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-3.txt
```

---

## OBS Text Source Configuration

For each dynamic text source:

1. **Properties → Text Settings:**
   - ☑ Read from file
   - Text File: `C:\Users\Ryan\projects\fouler-play\logs\{bot}-battle-{slot}.txt`
   - ☑ Chat log mode (enables auto-refresh)
   - ☐ Log lines (leave unchecked)

2. **Filters (if needed for parsing):**
   - Add **Text Filter** → Regex extraction
   - Example for opponent: `opponent=(.+)`
   - Replace with: `$1`

3. **Transform:**
   - Position: As specified in table
   - Alignment: Centered (for most fields)

---

## Streaming Settings

**Profile:** Create new profile "Fouler Play Streams"

### Output Settings
- **Video Bitrate:** 10,000 Kbps (to handle 6 concurrent overlays)
- **Encoder:** NVIDIA NVENC H.264 (if available) or x264
- **Preset:** Quality (NVENC) or `fast` (x264)
- **Audio Bitrate:** 160 Kbps

### Video Settings
- **Base Resolution:** 1920×1080
- **Output Resolution:** 1920×1080
- **FPS:** 60

### Recording
- **Path:** `D:\battle-recordings\`
- **Format:** MP4
- **Recording Quality:** Same as stream
- **Filename:** `fouler-play-%CCYY-%MM-%DD_%hh-%mm-%ss.mp4`

---

## Optional Enhancements (Phase 2)

### Animated Battle Border
- Add **Image Source** with glowing border GIF
- Visible only when `status=battling`
- Filter: **Show/Hide** based on file content

### Aggregate Stats Panel (y=720-1080)
- **Background:** `#111111` (dark gray)
- **3 columns:**
  - Left: DEKU total stats (aggregate from 3 battles)
  - Center: CHUNG total stats
  - Right: Combined session stats
- **Data sources:** 
  - `C:\Users\Ryan\projects\fouler-play\logs\deku-aggregate.txt`
  - `C:\Users\Ryan\projects\fouler-play\logs\chung-aggregate.txt`
  - `C:\Users\Ryan\projects\fouler-play\logs\combined-stats.txt`

### Win/Loss Streak Indicator
- **Image Source:** Arrow up (green) or down (red)
- **Position:** Next to record
- **Logic:** Parse last battle result, show trend

---

## Testing Checklist

- [ ] All 6 tiles visible and positioned correctly
- [ ] Background colors correct (DEKU blue, CHUNG red)
- [ ] Headers display bot name + team name
- [ ] Dynamic text sources read from files
- [ ] Files update → OBS refreshes within 2 seconds
- [ ] Test with mock data (searching, battling, won, lost states)
- [ ] Screenshot captured showing all 6 tiles populated
- [ ] Scene collection exported to JSON backup

---

## Build Order

1. Create scene collection
2. Create main scene
3. Add 6 background color sources
4. Add 6 border sources
5. Add all static header texts (6 total)
6. Add all dynamic text sources (6 opponents, 6 statuses, 6 ELOs, etc.)
7. Configure file reading for each dynamic source
8. Position all elements
9. Test with mock data
10. Adjust colors/fonts as needed
11. Export scene collection

**Estimated Build Time:** 45-60 minutes
