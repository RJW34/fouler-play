# Stream Overlay Improvement Plan

## Current Issues
- Plain text overlays via ffmpeg drawtext (boring, static)
- No real-time battle stats or analysis
- No visual engagement (animations, effects)
- Missing key information (turn count, HP bars, type matchups)
- No chat integration or alerts

## Option 1: OBS Studio with Browser Sources (RECOMMENDED)
**Pros:**
- Industry standard for streaming
- Rich browser source support (HTML/CSS/JS overlays)
- Scene management (different layouts for battles, between matches, etc.)
- Plugin ecosystem (alerts, chat, timers, etc.)
- Easy preview and adjustment

**Implementation:**
1. Install OBS Studio
2. Create browser sources for:
   - Battle stats overlay (live HP, turn count, team preview)
   - Win/loss tracker with animations
   - Move prediction display (show MCTS analysis)
   - Chat overlay (Twitch chat integration)
3. Multiple scenes: Starting Soon, Battle View, BRB, End Screen
4. Transitions between scenes

**Files needed:**
- `obs_battle_overlay.html` - Main battle HUD
- `obs_stats_panel.html` - Live stats sidebar
- `obs_predictions.html` - AI move analysis display
- OBS scene collection JSON

## Option 2: Enhanced HTML Overlays (Current + Upgrade)
**Keep current ffmpeg approach but upgrade HTML files**

**New overlays to create:**
- `battle_hud.html` - Live battle stats (HP bars, status, turn count)
- `team_preview.html` - Show both teams before battle
- `move_analysis.html` - Display MCTS predictions in real-time
- `victory_screen.html` - Animated win/loss screen
- `recent_battles.html` - Scrolling recent battle results

**Features:**
- WebSocket connection to bot for live data
- Smooth animations (CSS transitions, anime.js)
- Type effectiveness indicators
- Damage calculations preview
- Turn timer countdown

## Option 3: Python Canvas Overlay (Advanced)
**Direct rendering on top of capture**

Use `pillow` + `cairo` to draw overlays programmatically:
- Draw HP bars directly on Pokemon sprites
- Type matchup indicators
- Move damage predictions
- Battle flow diagram

**Pros:**
- Full control over positioning
- Can analyze screen and overlay contextually
- No browser overhead

**Cons:**
- More complex implementation
- Harder to iterate on design

## Recommended: OBS Studio Setup

### Scene Structure
1. **Starting Soon**
   - Animated logo
   - "Searching for battles..." with timer
   - Recent stats carousel

2. **Battle View (Main)**
   - Chrome window (battles)
   - Top overlay: FOULER PLAY branding + stats
   - Side panel: Team preview + type chart
   - Bottom overlay: Current turn, move analysis
   - Chat overlay (right side)

3. **Between Battles**
   - Victory/defeat animation
   - Battle summary (damage dealt, MVP Pokemon)
   - Next battle countdown

4. **End Stream**
   - Session stats recap
   - Best moments highlights
   - Social links, next stream time

### Data Sources
- HTTP API: `localhost:8777/status` (current stats)
- WebSocket: Real-time battle events
- File monitoring: Parse `bot_monitor.out` for live updates
- Showdown replay API: Fetch battle details

### Visual Improvements
- Animated HP bars (smooth drain/heal)
- Type effectiveness overlays (red/green highlights)
- Move damage predictions (show calculated damage ranges)
- Critical hit/status effect animations
- Pokemon sprites with glow effects
- Turn history timeline
- Win streak counter with flames
- ELO graph over time

## Quick Win: Immediate Improvements (No OBS)

Update `auto_stream_v2.py` to use HTML overlay instead of drawtext:

1. Create enhanced `battle_overlay.html` with:
   - Live HP bars for both Pokemon
   - Turn counter
   - Status effects icons
   - Type matchup indicator
   - Last 3 moves log

2. Composite overlay using ffmpeg's overlay filter:
   ```bash
   ffmpeg -i :0+0,0 -i battle_overlay.html \
     -filter_complex "overlay=0:0" ...
   ```

3. Add WebSocket server to bot for real-time updates

Would you like me to:
A) Set up OBS Studio with professional overlays?
B) Enhance current HTML overlays with animations?
C) Build a hybrid approach (better HTML now, OBS later)?
D) Something else?
