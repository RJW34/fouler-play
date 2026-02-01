# Fouler Play Run Configuration

## Current Setup (2026-02-01)

### Bot Configuration
- **Account:** LEBOTJAMESXD005
- **Format:** Gen 9 OU (constructed teams)
- **Teams:** Rotating through 3 fat teams (stall, pivot, dondozo)
- **Concurrent battles:** 2 simultaneous games
- **Goal:** Climb to 1700 ELO

### Active Features
- ✅ **2 concurrent battle workers** - Can play 2 games at once
- ⚠️ **Team rotation** - `fat-teams.list` exists but --team-list crashes (ValueError). Using single --team-name for now
- ✅ **Race condition fixed** - Messages properly buffered until battle registration
- ✅ **Discord notifications** - Live battle feed + replay analysis
- ✅ **Automatic loss analysis** - Reviews replays for mistakes

### Team List (fat-teams.list)
```
gen9/ou/fat-team-1-stall
gen9/ou/fat-team-2-pivot
gen9/ou/fat-team-3-dondozo
```

### Running the Bot

**Via monitor (recommended):**
```bash
cd /home/ryan/projects/fouler-play
nohup venv/bin/python -u bot_monitor.py > monitor.log 2>&1 &
```

**Direct run (single team, working):**
```bash
cd /home/ryan/projects/fouler-play
source venv/bin/activate
python -u run.py \
  --websocket-uri "wss://sim3.psim.us/showdown/websocket" \
  --ps-username "LEBOTJAMESXD005" \
  --ps-password "LeBotPassword2026!" \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --team-name gen9/ou/fat-team-1-stall \
  --search-time-ms 3000 \
  --run-count 999999 \
  --save-replay always \
  --log-level INFO
```

**Direct run (team rotation, BROKEN — ValueError bug):**
```bash
# Don't use until team_converter.py bug is fixed
python run.py \
  --team-list fat-teams.list \
  ...
```

## Configuration Files

- **run.py** - `MAX_CONCURRENT_BATTLES = 2` (line 20)
- **bot_monitor.py** - Command line args for bot execution
- **fat-teams.list** - Team rotation list

## Technical Details

### Concurrent Battle Architecture
- 2 battle workers run in parallel
- Each worker:
  1. Acquires semaphore slot (max 2)
  2. Gets next team from rotation
  3. Searches for match
  4. Plays battle to completion
  5. Releases semaphore slot

### Message Buffering (Race Condition Fix)
- Messages for unregistered battles go to `pending_battle_messages[battle_tag]`
- Opponent detection polls buffer without consuming
- When battle registers, all buffered messages flush to battle queue
- Prevents "lost due to inactivity" timeouts

## Next Steps
1. Monitor ELO progression (target: 1700)
2. Tune MCTS parameters if games timeout
3. Analyze loss replays for strategic improvements
4. Consider adding more fat team variants if needed
