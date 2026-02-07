# Spectator System Analysis - Fouler Play v2

**Date:** 2026-02-06  
**Analyst:** DEKU Sub-Agent  
**Status:** ⚠️ Missing Configuration

## 🎯 Mission Summary

Analyzed the spectator system to enable live battle viewing in OBS. The infrastructure is **fully implemented** but **not configured**.

---

## 📋 How the Spectator System Works

### The Problem
Pokemon Showdown blocks iframe embeds with `X-Frame-Options: DENY`, so you can't directly embed battle URLs in OBS browser sources as iframes.

### The Solution (Already Implemented)
1. **Create a dedicated spectator account** on Pokemon Showdown (separate from bot accounts)
2. **Bot invites spectator to every battle** via `/invite {username}` command
3. **Spectator account is logged into a browser** on Windows (BAKUGO's machine)
4. **OBS browser sources navigate to battle URLs** - spectator can view because they're invited
5. **OBS captures the browser showing live battles**

---

## 🔍 Code Implementation Status

### ✅ Already Implemented

**1. Configuration Support** (`config.py:214`)
```python
parser.add_argument(
    "--spectator-username",
    default=None,
    help="Username to automatically invite to battles",
)
```

**2. Battle Invitation** (`fp/run_battle.py:1254-1256`)
```python
if FoulPlayConfig.spectator_username:
    logger.info(f"Inviting spectator: {FoulPlayConfig.spectator_username}")
    await ps_websocket_client.send_message(
        battle.battle_tag, 
        [f"/invite {FoulPlayConfig.spectator_username}"]
    )
```

**3. Monitor Integration** (`bot_monitor.py:40-45`)
```python
spectator_username_env = os.getenv("SPECTATOR_USERNAME", "").strip()
if not spectator_username_env:
    print("[MONITOR] Tip: set SPECTATOR_USERNAME in .env and log OBS into that account")
elif ps_username_env and spectator_username_env.lower() == ps_username_env.lower():
    print("[MONITOR] WARNING: SPECTATOR_USERNAME matches PS_USERNAME.")
```

**4. Stream Server** (`streaming/stream_server.py`)
- WebSocket broadcasting system for real-time battle updates
- JSON API endpoints for battle state (`/battles`, `/state`)
- OBS battle viewer HTML (`streaming/obs_battles.html`)

**5. OBS Controller** (`streaming/obs_controller.py`)
- Updates OBS browser sources with battle URLs
- Manages scene switching and visibility

---

## ❌ What's Missing

### Configuration Gap
**No `SPECTATOR_USERNAME` set in any `.env` file:**

**Current Bot Accounts:**
- DEKU: `BugInTheCode` (in `.env` and `.env.deku`)
- BAKUGO: `LADDERANNIHILATOR` (in `.env.bakugo`)

**Spectator Account Status:** ⚠️ **NOT CONFIGURED**

---

## 🛠️ Implementation Steps

### Step 1: Create Spectator Account
Create a new Pokemon Showdown account (or use an existing one):
- **Username:** Something like `FoulerPlayViewer` or `BattleSpectator`
- **Must be different** from `BugInTheCode` and `LADDERANNIHILATOR`

### Step 2: Configure Environment
Add to `.env` (and `.env.deku`/`.env.bakugo` for consistency):
```bash
SPECTATOR_USERNAME=FoulerPlayViewer
```

### Step 3: BAKUGO Setup (Windows)
1. Open Chrome/browser on BAKUGO's machine
2. Navigate to https://play.pokemonshowdown.com
3. Log in with the spectator account credentials
4. Keep browser logged in (check "Stay logged in")

### Step 4: OBS Configuration
1. Ensure OBS browser sources point to battle URLs
2. Browser sources should use the same Chrome profile that's logged into the spectator account
3. When bot invites spectator, battles will display automatically

### Step 5: Test
1. Start bot with `SPECTATOR_USERNAME` configured
2. Launch a battle
3. Check logs for: `Inviting spectator: FoulerPlayViewer`
4. Verify spectator account receives invite on Showdown
5. Confirm OBS shows the battle

---

## 📊 File Locations

### Configuration Files
- `/home/ryan/projects/fouler-play-v2/.env` - Main config (currently missing `SPECTATOR_USERNAME`)
- `/home/ryan/projects/fouler-play-v2/.env.deku` - DEKU's config
- `/home/ryan/projects/fouler-play-v2/.env.bakugo` - BAKUGO's config

### Core Implementation
- `config.py` - Argument parsing
- `fp/run_battle.py` - Battle invitation logic
- `bot_monitor.py` - Spectator validation
- `streaming/stream_server.py` - Stream server backend
- `streaming/obs_battles.html` - OBS browser source UI
- `streaming/obs_controller.py` - OBS WebSocket control

---

## ⚡ Quick Fix (If You Have the Account)

If a spectator account already exists, this is a **5-minute fix**:

```bash
# Edit .env
echo "SPECTATOR_USERNAME=YourSpectatorUsername" >> .env

# Restart bot monitor
cd /home/ryan/projects/fouler-play-v2
./stop_monitor.sh
./start_monitor.sh

# Check logs
tail -f logs/bot_monitor.log
```

Look for:
```
[MONITOR] Tip: set SPECTATOR_USERNAME in .env...
```
Should disappear after adding the variable.

When a battle starts, you should see:
```
Inviting spectator: YourSpectatorUsername
```

---

## 🎯 Summary

**Status:** ✅ Code is ready, ⚠️ Config is missing  
**Blocker:** Need spectator account credentials  
**Effort:** 5 minutes once account exists  
**Impact:** Enables full OBS battle streaming  

The spectator system is **fully implemented** and waiting for configuration. No code changes needed - just:
1. Create/identify spectator account
2. Add `SPECTATOR_USERNAME=...` to `.env`
3. Log BAKUGO's browser into that account
4. Restart bot

---

## 🔗 Next Steps for BAKUGO

Once `SPECTATOR_USERNAME` is configured on Linux side:

1. **Log into Showdown** in Chrome on Windows with the spectator account
2. **Configure OBS browser sources** to point to battle URLs (via stream server)
3. **Test battle viewing** - spectator should auto-join invited battles
4. **Set up OBS scenes** for multi-battle display

The stream server at `http://192.168.1.40:8777/obs` provides the battle viewer UI.

---

**Analysis Complete** ✅  
**Ready for Configuration** 🚀
