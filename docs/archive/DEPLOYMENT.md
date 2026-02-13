# Fouler Play v2 - Deployment Guide

## Overview
Fouler Play v2 is a Pokemon Showdown battle bot forked from [pmariglia/foul-play](https://github.com/pmariglia/foul-play). This version includes:
- Multi-battle support (concurrent games)
- Discord integration for battle notifications
- Bot monitoring with automatic restarts
- Turn-by-turn analysis
- Two-bot coexistence support (DEKU + BAKUGO)

## Current Deployment

### Two-Bot Setup
**DEKU (BugInTheCode)**
- Location: `/home/ryan/projects/fouler-play-v2/`
- Account: BugInTheCode
- Config: `.env` (or `.env.deku`)
- PID file: `.bot.pid`
- Logs: `logs/`, `bot_monitor_debug.log`

**BAKUGO (LEBOTJAMESXD006)**
- Location: `/home/ryan/projects/fouler-play/` (old directory)
- Account: LEBOTJAMESXD006
- Config: `.env` (or `.env.bakugo`)
- PID file: `.bot.pid`
- Logs: `logs/`, `bot_monitor_debug.log`

Both bots can run simultaneously without conflicts because:
- They use different usernames on Pokemon Showdown
- They run from separate directories
- They have separate PID files and logs
- process_lock.py scopes process management to its own directory

## Quick Start

### 1. Install Dependencies
```bash
cd /home/ryan/projects/fouler-play-v2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Note: Requires Rust installed to build poke-engine.

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your credentials
nano .env
```

Required variables:
- `PS_USERNAME` - Your Pokemon Showdown username
- `PS_PASSWORD` - Your Pokemon Showdown password
- `TEAM_LIST` - Path to team list file (e.g., `fat-teams.list`)

See `.env.example` for all available options.

### 3. Start the Bot

#### Option A: Direct Run (Manual)
```bash
source venv/bin/activate
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username BugInTheCode \
  --ps-password YOUR_PASSWORD \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --search-time-ms 2000 \
  --max-concurrent-battles 3
```

#### Option B: Using bot_monitor.py (Recommended)
```bash
source venv/bin/activate
./bot_monitor.py
```

The bot monitor:
- Automatically starts the bot with configured settings
- Monitors for crashes and restarts if needed
- Posts battle results to Discord (if webhooks configured)
- Handles graceful shutdown on SIGTERM/SIGINT
- Supports drain mode (finish battles, don't start new ones)

### 4. Stop the Bot

#### Graceful Shutdown (Drain Mode)
```bash
# Create drain request file
touch .pids/drain.request

# Bot will finish current battles and exit
# Monitor logs to see when battles complete
tail -f bot_monitor_debug.log
```

#### Force Stop
```bash
# Find PID
cat .bot.pid

# Kill process
kill <PID>
```

## Monitoring

### Check if Bot is Running
```bash
# Check PID file
cat .bot.pid

# Verify process exists
ps aux | grep run.py | grep search_ladder
```

### View Active Battles
```bash
cat active_battles.json
```

This file shows:
- Currently active battles
- Battle IDs and opponents
- Worker assignments
- Resume pending battles (for restart recovery)

### Check Logs
```bash
# Bot monitor logs (detailed)
tail -f bot_monitor_debug.log

# Battle logs (individual battles)
ls -lh logs/

# Stats file
cat battle_stats.json
```

## Team Management

### Team Structure
```
teams/
├── fat-teams.list         # List of team paths (one per line)
├── gen9/
│   └── ou/
│       ├── fat-team-1-stall
│       ├── fat-team-2-pivot
│       └── fat-team-3-dondozo
├── gen8/
│   └── ou/
│       └── balance
└── gen3/
    └── ou/
        └── sample
```

### Team List Format
The `fat-teams.list` file contains paths to teams (relative to `teams/` directory):
```
gen9/ou/fat-team-1-stall
gen9/ou/fat-team-2-pivot
gen9/ou/fat-team-3-dondozo
```

The bot randomly selects a team from this list for each battle.

### Adding Teams
1. Create team file in appropriate generation directory
2. Add path to `fat-teams.list`
3. No restart needed - bot picks up changes automatically

## Discord Integration

### Webhook Configuration
Set these in `.env`:
```
DISCORD_WEBHOOK_URL=<general updates>
DISCORD_BATTLES_WEBHOOK_URL=<battle results>
DISCORD_FEEDBACK_WEBHOOK_URL=<turn analysis>
```

### Battle Notifications
When `DISCORD_BATTLES_WEBHOOK_URL` is set, bot posts:
- Battle start notifications
- Win/loss results with replay links
- ELO updates
- Batch summaries (every 3 games)

### Bot Identity
Use `BOT_DISPLAY_NAME` to distinguish multiple bots:
```
BOT_DISPLAY_NAME=🪲 DEKU
```

This appears in Discord messages so you know which bot reported what.

## Troubleshooting

### Bot Won't Start
```bash
# Check if another instance is running
cat .bot.pid
ps aux | grep <PID>

# Kill stale process
kill <PID>
rm .bot.pid

# Check logs for errors
tail -50 bot_monitor_debug.log
```

### Bot Keeps Restarting
- Check `bot_monitor_debug.log` for exception traces
- Verify credentials in `.env`
- Ensure Pokemon Showdown is accessible
- Check for rate limiting (too many connections)

### Teams Not Loading
```bash
# Test team loading
source venv/bin/activate
python -c "from teams import load_team; print(load_team('gen9/ou/fat-team-1-stall'))"
```

### Battle Logs Growing Too Large
Battle logs now auto-rotate at 10MB (keeping 3 backups). If you have old 100MB+ logs:
```bash
# Find large logs
find logs/ -size +50M

# Remove old logs (be careful!)
find logs/ -mtime +7 -delete  # Delete logs older than 7 days
```

## Advanced

### Process Lock System
`process_lock.py` prevents duplicate bot instances:
- Checks for existing PID file
- Verifies process is actually a bot (not just any process)
- Kills stale processes from **this directory only**
- Scoped to prevent killing other bots (e.g., BAKUGO in different directory)

### Resume Battle Support
If bot crashes mid-battle, it can resume:
- Active battles are tracked in `active_battles.json`
- On restart, bot re-joins battles marked as "resume_pending"
- Timeout: battles older than 900 seconds are abandoned

### Multi-Bot Coordination
To run multiple bots safely:
1. Use separate directories
2. Different `.env` files with unique usernames
3. Different `BOT_DISPLAY_NAME` values
4. Separate log directories (automatic per directory)
5. process_lock.py will keep them isolated

## Development

### Running Tests
```bash
source venv/bin/activate
python -m pytest tests/ -x -q
```

All 517 tests should pass.

### Making Changes
1. Create feature branch
2. Make changes
3. Run test suite
4. Commit with descriptive message
5. Test in production carefully (monitor ELO)

### Updating Documentation
- This file (DEPLOYMENT.md) - deployment/ops info
- CLAUDE.md - AI agent instructions (if applicable)
- .env.example - environment variable reference
- README.md - general project info

## Support
If you encounter issues not covered here, check:
- Recent commits for similar fixes
- Issue tracker (if public)
- Bot logs for detailed error messages
