# BAKUGO OBS Setup Checklist
## 6-Battle Monitor for Fouler Play

**Target:** MAGNETON (Windows)
**Estimated Time:** 45-60 minutes

---

## ✅ Pre-Flight

- [ ] Read `OBS_SCENE_SPEC.md` (detailed technical spec)
- [ ] Read `OBS_INTEGRATION.md` (how bots update files)
- [ ] Verify OBS Studio is installed on MAGNETON
- [ ] Verify Python 3.x installed (for test script)

---

## 📁 Step 1: Create Data Files (5 min)

### On MAGNETON:
```powershell
# Create logs directory
mkdir C:\Users\Ryan\projects\fouler-play\logs -Force

# Copy test data files from ubunztu (via network or USB)
# OR create manually with test data
```

### Create these 6 files:
```
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-1.txt
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-2.txt
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-3.txt
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-1.txt  (via network path)
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-2.txt
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-3.txt
```

### Test Data for CHUNG files:
```
opponent=TestOpponent1
team=stall
status=battling
elo=1450
turns=12
record=15-8
```

(Repeat for battle-2 and battle-3 with different values)

---

## 🎨 Step 2: Create OBS Scene Collection (10 min)

1. Open OBS Studio
2. **Scene Collection → New**
3. Name: `Fouler Play - 6 Battles`
4. **Scenes → Add**
5. Name: `Multi-Battle Monitor`

---

## 🖼️ Step 3: Build Tile 1 (DEKU-Stall) (10 min)

This is your template. Once tile 1 works, you'll duplicate for tiles 2-6.

### 3.1 Background
- **Sources → Add → Color Source**
- Name: `BG-DEKU-1`
- Color: `#001a33` (dark blue)
- Transform → Edit Transform:
  - Position: X=0, Y=0
  - Size: 640×360

### 3.2 Border
- **Sources → Add → Color Source**
- Name: `Border-DEKU-1`
- Color: `#333333`
- Transform: X=2, Y=2, Size=636×356

### 3.3 Header (Static Text)
- **Sources → Add → Text (FreeType 2)**
- Name: `Header-DEKU-1`
- Text: `DEKU-1 | STALL`
- Font: Arial Black, 24pt, Bold
- Color: `#4da6ff` (light blue)
- Transform: X=10, Y=10

### 3.4 Opponent Name (Dynamic)
- **Sources → Add → Text (FreeType 2)**
- Name: `Opponent-DEKU-1`
- **☑ Read from file**
- File: `C:\Users\Ryan\projects\fouler-play\logs\deku-battle-1.txt`
- **☑ Chat log mode** (enables auto-refresh)
- Font: Arial, 32pt, Bold
- Color: `#FFFFFF`
- Transform: X=320, Y=100, Alignment=Center
- **Add Filter → Text (GDI+) → Find/Replace:**
  - Find: `opponent=`
  - Replace: `` (blank)
  - (This extracts just the opponent name)

### 3.5 Status Badge (Dynamic)
- **Sources → Add → Text (FreeType 2)**
- Name: `Status-DEKU-1`
- **☑ Read from file** (same file)
- **☑ Chat log mode**
- Font: Arial, 20pt, Bold
- Color: `#00FF00` (green — can change later)
- Transform: X=320, Y=150, Center
- **Add Filter → Text Find/Replace:**
  - Pattern: `^.*status=(.+)$` (regex)
  - Replace: `$1`

### 3.6 ELO Display (Dynamic)
- **Sources → Add → Text (FreeType 2)**
- Name: `ELO-DEKU-1`
- **☑ Read from file**
- **☑ Chat log mode**
- Font: Consolas, 24pt
- Color: `#FFD700` (gold)
- Transform: X=320, Y=190, Center
- **Add Filter:** Extract `elo=XXX` → `ELO: XXX`

### 3.7 Turn Counter (Dynamic)
- **Sources → Add → Text (FreeType 2)**
- Name: `Turns-DEKU-1`
- **☑ Read from file**
- **☑ Chat log mode**
- Font: Arial, 18pt
- Color: `#CCCCCC`
- Transform: X=320, Y=230, Center
- **Add Filter:** Extract `turns=XXX` → `Turn: XXX`

### 3.8 Record (Dynamic)
- **Sources → Add → Text (FreeType 2)**
- Name: `Record-DEKU-1`
- **☑ Read from file**
- **☑ Chat log mode**
- Font: Arial, 18pt
- Color: `#AAAAAA`
- Transform: X=320, Y=270, Center
- **Add Filter:** Extract `record=XXX` → `Record: XXX`

---

## 🔁 Step 4: Duplicate for Tiles 2-6 (15 min)

### Quick Copy Method:
1. **Select all sources for tile 1** (hold Ctrl, click each)
2. **Right-click → Copy**
3. **Right-click → Paste** (creates duplicates)
4. **Rename each source:**
   - `BG-DEKU-1` → `BG-DEKU-2`
   - `Opponent-DEKU-1` → `Opponent-DEKU-2`
   - (etc.)
5. **Update file paths:**
   - Tile 2: `deku-battle-2.txt`
   - Tile 3: `deku-battle-3.txt`
   - Tile 4: `chung-battle-1.txt` (and change colors to red)
   - Tile 5: `chung-battle-2.txt`
   - Tile 6: `chung-battle-3.txt`
6. **Reposition:**
   - Tile 2 (DEKU-Pivot): X=640, Y=0
   - Tile 3 (DEKU-Dondozo): X=1280, Y=0
   - Tile 4 (CHUNG-Stall): X=0, Y=360
   - Tile 5 (CHUNG-Pivot): X=640, Y=360
   - Tile 6 (CHUNG-Dondozo): X=1280, Y=360

### Color Changes for CHUNG Tiles (4, 5, 6):
- Background: `#330000` (dark red)
- Header color: `#ff6666` (light red)

---

## 🎬 Step 5: Configure Streaming Settings (5 min)

### Profile Settings
- **Settings → Output:**
  - Video Bitrate: `10000` Kbps
  - Encoder: NVIDIA NVENC H.264 (or x264)
  - Preset: Quality

- **Settings → Video:**
  - Base Resolution: 1920×1080
  - Output Resolution: 1920×1080
  - FPS: 60

- **Settings → Advanced → Recording:**
  - Path: `D:\battle-recordings\`
  - Format: MP4
  - Filename: `fouler-play-%CCYY-%MM-%DD_%hh-%mm-%ss.mp4`

---

## 🧪 Step 6: Test with Mock Data (5 min)

### Option A: Use Python Script (if copied to MAGNETON)
```powershell
cd C:\Users\Ryan\projects\fouler-play
python obs_battle_updater.py test
```

### Option B: Manual Edit
1. Open `chung-battle-1.txt` in Notepad
2. Change `opponent=Searching...` to `opponent=TestPlayer123`
3. Change `status=searching` to `status=battling`
4. Save
5. **Verify OBS updates within 2 seconds**

### Check:
- [ ] All 6 tiles visible
- [ ] Text updates when files change
- [ ] Colors correct (DEKU blue, CHUNG red)
- [ ] Layout fits 1920×1080 (no clipping)

---

## 📸 Step 7: Capture Screenshot (2 min)

1. Populate all 6 battle files with test data
2. In OBS, ensure scene is active
3. **Screenshot → Full preview** (or use Snipping Tool)
4. Save to: `D:\battle-recordings\obs-6battle-layout.png`
5. Post screenshot in #deku-bakugo-sync

---

## 💾 Step 8: Export Scene Collection (2 min)

1. **Scene Collection → Export**
2. Save to: `D:\battle-recordings\fouler-play-6-battles.json`
3. (Backup in case scene needs to be restored)

---

## 📋 Deliverables Checklist

- [ ] OBS Scene Collection created: "Fouler Play - 6 Battles"
- [ ] Main scene created: "Multi-Battle Monitor"
- [ ] 6 battle data files exist in `C:\Users\Ryan\projects\fouler-play\logs\`
- [ ] All 6 tiles display correctly with test data
- [ ] Screenshot posted to Discord
- [ ] Scene collection exported as JSON backup
- [ ] Streaming settings configured (10,000 kbps, 1920×1080@60fps)

---

## 🆘 Troubleshooting

**Problem:** Text sources not updating
- ✅ Check "Chat log mode" is enabled
- ✅ Verify file path is correct
- ✅ Check file permissions (readable by OBS user)
- ✅ Try manual file edit to test

**Problem:** Text shows raw file content (all lines)
- ✅ Add "Text Filter" to extract specific line
- ✅ Use regex: `^.*opponent=(.+)$` → `$1`

**Problem:** Layout doesn't fit 1920×1080
- ✅ Check transform positions (use Edit Transform)
- ✅ Verify canvas resolution in Settings → Video

**Problem:** Colors look wrong
- ✅ DEKU = blue (#001a33 bg, #4da6ff text)
- ✅ CHUNG = red (#330000 bg, #ff6666 text)

---

## 🎯 Success Criteria

✅ All 6 battle tiles visible and distinct
✅ Dynamic text updates when files change
✅ Layout fits 1920×1080 cleanly
✅ Screenshot shows populated test data
✅ Scene collection exported for backup

**When complete:** Reply in #deku-bakugo-sync with screenshot and "✅ 6-battle OBS scene ready"
