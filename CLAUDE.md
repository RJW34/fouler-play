# CLAUDE.md - Fouler Play v2 Project Guide

## Current State (2026-02-06)

### Active Bots
- **BugInTheCode (DEKU)** - Running from `/home/ryan/projects/fouler-play-v2/`
- **LEBOTJAMESXD006 (BAKUGO)** - Running from `/home/ryan/projects/fouler-play/`

Both bots play gen9ou on Pokemon Showdown ladder simultaneously.

### Project Status
This is a fork of [pmariglia/foul-play](https://github.com/pmariglia/foul-play) with enhancements:
- ✅ Multi-battle support (3 concurrent battles)
- ✅ Discord webhook integration
- ✅ Bot monitoring and auto-restart
- ✅ Battle statistics tracking
- ✅ Resume battle support after crashes
- ✅ Process lock to prevent duplicate instances
- ✅ Log rotation with size limits
- 🚧 Turn-by-turn analysis (infrastructure exists, needs refinement)
- 🚧 Replay analysis pipeline

### Recent Fixes (This Audit)
1. **Teams directory cleanup** - Removed nested `teams/teams/` structure and circular symlinks
2. **Log rotation** - Added 10MB size limits to prevent 100MB+ log files
3. **Comprehensive .env.example** - Documented all 20+ environment variables
4. **Documentation** - Created DEPLOYMENT.md

---

## Core Architecture

### Entry Points
- `run.py` - Main bot entry point (CLI mode or environment variables)
- `bot_monitor.py` - Wrapper that monitors bot and posts to Discord
- `process_lock.py` - Prevents duplicate bot instances per directory

### Key Modules
- `fp/battle.py` - Battle state management
- `fp/battle_modifier.py` - Parses Pokemon Showdown protocol
- `fp/run_battle.py` - Main battle loop, manages concurrent battles
- `fp/websocket_client.py` - WebSocket connection to Pokemon Showdown
- `fp/search/main.py` - MCTS search for best moves
- `fp/search/endgame.py` - Endgame solver
- `config.py` - Configuration and logging setup
- `teams/load_team.py` - Team loading from files

### Data Flow
1. `bot_monitor.py` spawns `run.py` as subprocess
2. `run.py` connects to Pokemon Showdown via websocket
3. Multiple battle workers handle concurrent games
4. Each battle gets its own log file (with rotation)
5. Battle results written to `battle_stats.json`
6. Discord webhooks post results in real-time
7. `active_battles.json` tracks ongoing battles for crash recovery

---

## Configuration

### Environment Variables
See `.env.example` for complete reference. Key variables:

**Required:**
- `PS_USERNAME` - Pokemon Showdown username
- `PS_PASSWORD` - Pokemon Showdown password
- `TEAM_LIST` - Path to team list file

**Performance:**
- `MAX_CONCURRENT_BATTLES` - How many battles to run simultaneously (default: 3)
- `SEARCH_PARALLELISM` - Worker processes for search (default: 2)
- `PS_SEARCH_TIME_MS` - Milliseconds per move search (default: 2000)

**Discord:**
- `DISCORD_BATTLES_WEBHOOK_URL` - Battle results channel
- `DISCORD_FEEDBACK_WEBHOOK_URL` - Turn analysis channel
- `BOT_DISPLAY_NAME` - Bot identity in Discord messages (e.g., "🪲 DEKU")

### Teams
Teams are stored in `teams/` directory with this structure:
```
teams/
├── fat-teams.list         # List of team paths
└── gen9/ou/
    ├── fat-team-1-stall
    ├── fat-team-2-pivot
    └── fat-team-3-dondozo
```

The `TEAM_LIST` environment variable points to a file containing team paths (one per line).
Bot randomly selects a team from the list for each battle.

---

## Running the Bot

### Development Mode (Manual)
```bash
cd /home/ryan/projects/fouler-play-v2
source venv/bin/activate
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username BugInTheCode \
  --ps-password <password> \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --search-time-ms 2000 \
  --max-concurrent-battles 3
```

### Production Mode (Recommended)
```bash
cd /home/ryan/projects/fouler-play-v2
source venv/bin/activate
./bot_monitor.py
```

The bot monitor handles:
- Auto-restart on crashes
- Discord notifications
- Graceful shutdown
- Drain mode (finish battles, don't start new ones)

### Monitoring
```bash
# Check if running
cat .bot.pid && ps aux | grep $(cat .bot.pid)

# View active battles
cat active_battles.json

# Watch logs
tail -f bot_monitor_debug.log

# Check stats
cat battle_stats.json
```

---

## Development Workflow

### Making Changes
1. **Create branch** - Work on feature branches
2. **Make changes** - Edit code
3. **Run tests** - `python -m pytest tests/ -x -q` (all 517 must pass)
4. **Commit** - Descriptive commit messages
5. **Test in production** - Monitor ELO and logs carefully

### Protected Files (Don't Modify Without Good Reason)
- `run.py` - Main entry point, changes affect both bots
- `config.py` - Core configuration
- Team files in `teams/` - These are battle-tested
- `constants.py` - Game constants

### Code Quality Standards
- All tests must pass before committing
- Use descriptive variable names
- Add comments for complex logic
- Handle exceptions properly (don't silently swallow)
- Use logging instead of print statements
- Follow existing code style

---

## Common Tasks

### Add a New Team
1. Create team file: `teams/gen9/ou/my-new-team`
2. Add to list: `echo "gen9/ou/my-new-team" >> teams/fat-teams.list`
3. Bot picks it up automatically (no restart needed)

### Change Battle Count
Edit `.env`:
```
MAX_CONCURRENT_BATTLES=2  # Run 2 battles at once instead of 3
```
Restart bot for changes to take effect.

### Update Search Time
Edit `.env`:
```
PS_SEARCH_TIME_MS=3000  # Spend 3 seconds per move instead of 2
```
More time = potentially better play, but slower games.

### Drain Mode (Stop Gracefully)
```bash
# Signal bot to finish current battles and exit
touch .pids/drain.request

# Wait for battles to complete
tail -f bot_monitor_debug.log
```

### Force Stop
```bash
kill $(cat .bot.pid)
```

---

## Troubleshooting

### Bot Won't Start
1. Check if already running: `cat .bot.pid && ps aux | grep $(cat .bot.pid)`
2. Remove stale PID: `rm .bot.pid`
3. Check logs: `tail -50 bot_monitor_debug.log`
4. Verify credentials in `.env`

### Bot Keeps Crashing
1. Check logs for exception traces
2. Verify Pokemon Showdown is accessible
3. Check for invalid team files
4. Monitor system resources (CPU/RAM)

### No Discord Messages
1. Verify webhook URLs in `.env`
2. Test webhook manually: `curl -X POST -H "Content-Type: application/json" -d '{"content":"test"}' <WEBHOOK_URL>`
3. Check bot_monitor.py is running (not just run.py)

### Teams Not Loading
```bash
# Test team loading
python -c "from teams import load_team; print(load_team('gen9/ou/fat-team-1-stall'))"
```

---

## Multi-Bot Coexistence

### How DEKU and BAKUGO Coexist
- **Separate directories** - Different working directories
- **Separate configs** - Each has own .env file
- **Separate PIDs** - process_lock.py scopes to directory
- **Separate logs** - Each writes to its own logs/ directory
- **Shared Showdown server** - Both connect to same server with different accounts
- **Shared Discord webhooks** - Can post to same channels (use BOT_DISPLAY_NAME to distinguish)

### Adding Another Bot
1. Create new directory (e.g., `/home/ryan/projects/fouler-play-bot3/`)
2. Clone repo into that directory
3. Create `.env` with unique `PS_USERNAME` and `BOT_DISPLAY_NAME`
4. Run bot_monitor.py from that directory
5. process_lock.py keeps it isolated from other bots

---

## Testing

### Run All Tests
```bash
source venv/bin/activate
python -m pytest tests/ -v
```

### Run Specific Test
```bash
python -m pytest tests/test_battle.py -v
```

### Quick Syntax Check
```bash
python -m py_compile fp/search/main.py
```

### Import Test
```bash
python -c "from fp.search.main import find_best_move; print('OK')"
```

---

## File Structure Reference

```
fouler-play-v2/
├── run.py                    # Main entry point
├── bot_monitor.py            # Bot wrapper with Discord integration
├── process_lock.py           # Prevent duplicate instances
├── config.py                 # Configuration and logging
├── .env                      # Local environment variables (not tracked)
├── .env.example              # Template with all variables documented
├── DEPLOYMENT.md             # Ops/deployment guide
├── CLAUDE.md                 # This file - project guide
├── AUDIT_FINDINGS.md         # Audit results (2026-02-06)
├── active_battles.json       # Currently active battles (runtime)
├── battle_stats.json         # Battle history and stats (runtime)
├── .bot.pid                  # Bot process ID (runtime)
├── .pids/                    # Process management files
├── logs/                     # Battle logs (auto-rotating at 10MB)
├── teams/                    # Team files
│   ├── fat-teams.list        # Team list
│   └── gen*/ou/              # Team files by generation
├── fp/                       # Core bot code
│   ├── battle.py
│   ├── battle_modifier.py
│   ├── run_battle.py
│   ├── websocket_client.py
│   └── search/
│       ├── main.py           # MCTS search
│       ├── endgame.py
│       └── ...
├── tests/                    # Test suite (517 tests)
├── data/                     # Pokedex, moves, abilities
├── streaming/                # OBS/streaming integration (WIP)
├── replay_analysis/          # Replay analysis tools (WIP)
└── venv/                     # Python virtual environment
```

---

## Future Improvements

### High Priority
- [ ] Improve MCTS search heuristics
- [ ] Better endgame detection
- [ ] Smarter switching logic
- [ ] Team matchup analysis

### Medium Priority
- [ ] Replay analysis pipeline
- [ ] Turn-by-turn feedback system
- [ ] ELO tracking and visualization
- [ ] Per-team performance stats

### Low Priority
- [ ] OBS streaming integration
- [ ] Live battle viewer
- [ ] Historical data analysis
- [ ] Meta-game trend tracking

---

## References

- **Upstream fork**: https://github.com/pmariglia/foul-play
- **Pokemon Showdown**: https://pokemonshowdown.com/
- **Poke-engine docs**: https://poke-engine.readthedocs.io/

---

## Notes for AI Assistants

When working on this project:
1. Always run tests after changes
2. Be careful with process_lock.py - it kills processes!
3. Don't modify team files without explicit request
4. Check active_battles.json before stopping bot (avoid forfeits)
5. Monitor ELO after changes - auto-revert if it drops significantly
6. Commit changes incrementally with clear messages
7. Update this file when architecture changes
