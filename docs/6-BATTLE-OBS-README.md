# 6-Battle OBS Monitor - Implementation Package

**Project:** Fouler Play OBS Scene Redesign
**Target:** MAGNETON (Windows - BAKUGO)
**Status:** Ready for implementation
**Created:** 2026-02-15

---

## 📋 Quick Start

**For BAKUGO (Windows/MAGNETON):**
1. Read `BAKUGO_CHECKLIST.md` — step-by-step build guide
2. Reference `OBS_SCENE_SPEC.md` — technical specifications
3. Use `LAYOUT_VISUAL.txt` — visual mockup
4. Create scene in OBS Studio (45-60 min)
5. Test with data files
6. Post screenshot to Discord

**For DEKU (Integration):**
1. Read `OBS_INTEGRATION.md` — how to update files from bot
2. Use `obs_battle_updater.py` — Python helper class
3. Integrate into Fouler Play battle loop

---

## 📦 Package Contents

| File | Purpose | Audience |
|------|---------|----------|
| `BAKUGO_CHECKLIST.md` | Step-by-step OBS build instructions | BAKUGO |
| `OBS_SCENE_SPEC.md` | Detailed technical specifications | BAKUGO |
| `OBS_INTEGRATION.md` | Bot integration guide | DEKU/Devs |
| `LAYOUT_VISUAL.txt` | ASCII mockup of layout | Both |
| `6-BATTLE-OBS-README.md` | This file (overview) | Both |
| `obs_battle_updater.py` | Python utility for file updates | DEKU/Devs |

---

## 🎯 Design Summary

**Layout:** 3×2 grid (6 tiles), 1920×1080 @ 60fps
- **Top row (Y=0-360):** DEKU battles (blue theme)
  - Tile 1: Stall team
  - Tile 2: Pivot team
  - Tile 3: Dondozo team
- **Bottom row (Y=360-720):** CHUNG battles (red theme)
  - Tile 4: Stall team
  - Tile 5: Pivot team
  - Tile 6: Dondozo team

**Per-Tile Display:**
- Bot name + Team name (header)
- Opponent username (large, centered)
- Battle status badge (searching/battling/won/lost)
- Current ELO (gold text)
- Turn counter (if battling)
- Win-loss record

---

## 🔧 Technical Architecture

### Data Flow
```
Bot (Python) → Battle File (6 .txt files) → OBS Text Sources → Stream/Recording
```

### File Locations
**Windows (MAGNETON):**
```
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-1.txt
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-2.txt
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-3.txt
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-1.txt  (via network or sync)
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-2.txt
C:\Users\Ryan\projects\fouler-play\logs\deku-battle-3.txt
```

**Linux (ubunztu):**
```
/home/ryan/projects/fouler-play/logs/deku-battle-1.txt
/home/ryan/projects/fouler-play/logs/deku-battle-2.txt
/home/ryan/projects/fouler-play/logs/deku-battle-3.txt
```

### File Format
```
opponent=OpponentUsername
team=stall
status=battling
elo=1450
turns=12
record=15-8
```

---

## 🚀 Implementation Status

### ✅ Completed (DEKU side)
- [x] Created 6 template battle data files
- [x] Created test data files (TEST-*.txt)
- [x] Written `obs_battle_updater.py` utility
- [x] Documentation package complete
- [x] Integration guide for bot developers

### ⏳ Pending (BAKUGO side - Windows)
- [ ] Create OBS Scene Collection on MAGNETON
- [ ] Configure 6 battle tiles with text sources
- [ ] Set up file reading and auto-refresh
- [ ] Configure streaming settings (10k kbps, 1080p60)
- [ ] Test with mock data
- [ ] Capture and share screenshot
- [ ] Export scene collection JSON backup

---

## 🧪 Testing

### Quick Test (Manual)
1. Populate all 6 battle files with test data
2. Open OBS, switch to "Multi-Battle Monitor" scene
3. Verify all 6 tiles display correct data
4. Edit a file (change opponent name)
5. Verify OBS updates within 2 seconds

### Automated Test (Python)
```bash
# On ubunztu (DEKU)
cd /home/ryan/projects/fouler-play
python3 obs_battle_updater.py test

# On MAGNETON (CHUNG) - once Python script is copied
cd C:\Users\Ryan\projects\fouler-play
python obs_battle_updater.py test
```

### Demo Mode (Battle Lifecycle)
```bash
python3 obs_battle_updater.py demo
```
Simulates a full battle: searching → battling → turns → win → back to searching

---

## 📊 Streaming Configuration

**Profile:** Fouler Play Streams

| Setting | Value |
|---------|-------|
| Video Bitrate | 10,000 Kbps |
| Encoder | NVIDIA NVENC H.264 (or x264) |
| Resolution | 1920×1080 @ 60fps |
| Recording Path | `D:\battle-recordings\` |
| Recording Format | MP4 |

**Rationale:** 6 concurrent overlays + text updates require higher bitrate than single-battle stream.

---

## 🔗 Integration Points

### Fouler Play Bot Code
```python
from obs_battle_updater import BattleMonitor

# Initialize monitor
monitor = BattleMonitor()  # Auto-detects DEKU/CHUNG

# Battle lifecycle updates
monitor.set_searching(slot=1)
monitor.set_battling(slot=1, opponent="CoolTrainer", turn=1)
monitor.increment_turn(slot=1)
monitor.set_won(slot=1, new_elo=1425, new_record="16-8")
```

See `OBS_INTEGRATION.md` for full API and examples.

---

## 📸 Deliverables

When BAKUGO completes OBS setup, expect:

1. **Screenshot:** `D:\battle-recordings\obs-6battle-layout.png`
   - Shows all 6 tiles populated with test data
   - Visible in Discord #deku-bakugo-sync

2. **Scene Collection Export:** `D:\battle-recordings\fouler-play-6-battles.json`
   - Backup of OBS scene configuration
   - Can be imported if scene needs restoration

3. **Confirmation Message:** Posted in #deku-bakugo-sync
   - "✅ 6-battle OBS scene ready"
   - Includes screenshot attachment

---

## 🆘 Troubleshooting

### Common Issues

**OBS text not updating:**
- Verify "Chat log mode" is enabled on text source
- Check file path is correct (absolute path recommended)
- Ensure file has read permissions
- Try manually editing file to test

**Text shows entire file content:**
- Add "Text Filter" to extract specific line
- Use Find/Replace with regex: `^.*opponent=(.+)$` → `$1`

**Layout doesn't fit screen:**
- Check canvas resolution: Settings → Video → 1920×1080
- Verify tile positions (see OBS_SCENE_SPEC.md)
- Use "Edit Transform" to position precisely

**Files not syncing between machines:**
- Option 1: SMB share from MAGNETON → ubunztu
- Option 2: rsync every 5 seconds
- Option 3: Run all on MAGNETON, network paths for DEKU files

---

## 🎓 Learning Resources

**OBS Text Sources:**
- [OBS Text Source Documentation](https://obsproject.com/wiki/Sources-Guide#text)
- Chat log mode: auto-refreshes file every 1 second

**Text Filters:**
- Find/Replace filter can extract lines from multi-line files
- Regex patterns: `^.*key=(.+)$` → `$1`

**Atomic File Writes:**
- Write to `.tmp` file, then rename (prevents mid-read corruption)
- Python: `temp_path.replace(filepath)` is atomic

---

## 📅 Next Steps

**Immediate (BAKUGO):**
1. Build OBS scene collection (use BAKUGO_CHECKLIST.md)
2. Test with provided data files
3. Post screenshot when complete

**Phase 2 (After OBS is working):**
1. Integrate `obs_battle_updater.py` into Fouler Play bot
2. Add aggregate stats panel (optional)
3. Implement animated borders for active battles
4. Add win/loss streak indicators

**Phase 3 (Long-term):**
1. Automate file sync between ubunztu ↔ MAGNETON
2. Create aggregate stats calculator (combined ELO, session duration)
3. Add overlay graphics (team logos, move animations)

---

## ✅ Success Criteria

This implementation is complete when:

- [ ] OBS scene displays all 6 battle tiles simultaneously
- [ ] Text updates automatically when battle files change
- [ ] Layout is clean and readable at 1920×1080
- [ ] DEKU battles (blue) and CHUNG battles (red) are visually distinct
- [ ] Screenshot shared showing populated test data
- [ ] Scene collection exported as JSON backup
- [ ] Documentation package delivered to both DEKU and BAKUGO

**Definition of Done:** BAKUGO posts "✅ 6-battle OBS scene ready" with screenshot in #deku-bakugo-sync

---

## 📞 Contact

**Questions/Issues:**
- Discord: #deku-bakugo-sync (1467359048650330316)
- Mention: <@1467295290041041177> (BAKUGO)

**Files Location:**
- ubunztu: `/home/ryan/projects/fouler-play/docs/`
- MAGNETON: `C:\Users\Ryan\projects\fouler-play\docs\` (once synced)

---

**Last Updated:** 2026-02-15 00:27 EST
**Prepared by:** DEKU (sub-agent)
**For:** BAKUGO (MAGNETON)
