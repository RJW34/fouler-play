# Fouler Play Run Configuration

## Bot Username Convention
- Primary account: **LEBOTJAMESXD001**
- Additional accounts (if needed): LEBOTJAMESXD002, LEBOTJAMESXD003, etc.

## Known Issues
- **MCTS timeout in Random Battle:** Bot takes too long to calculate moves, times out before making decisions
- Needs timeout/iteration limits configured
- Consider testing simpler formats first (constructed teams vs random)

## Running the Bot

### Basic Gen 9 Random Battle (ladder search)
```bash
cd /home/ryan/projects/fouler-play
source venv/bin/activate
python run.py \
  --websocket-uri "wss://sim3.psim.us/showdown/websocket" \
  --ps-username "LEBOTJAMESXD001" \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle \
  --search-time-ms 5000 \
  --run-count 10
```

### Adjustable Parameters
- `--search-time-ms`: Time limit per move calculation (try 5000ms = 5sec for random battles)
- `--search-parallelism`: Number of parallel search threads (default 1)
- `--run-count`: Number of battles to play before stopping
- `--save-replay`: Options: `always`, `never`, `on_win`, `on_loss`

### Test with constructed team (faster MCTS)
```bash
python run.py \
  --websocket-uri "wss://sim3.psim.us/showdown/websocket" \
  --ps-username "LEBOTJAMESXD001" \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --team-name "your-team-name" \
  --search-time-ms 2000 \
  --run-count 5
```

## Next Steps to Fix
1. Tune MCTS parameters for Random Battle complexity
2. Add hard timeout to prevent ladder timer issues
3. Test with simpler formats first (Gen 8, constructed teams)
4. Profile poke-engine MCTS to find bottlenecks
5. Consider async battle handling for multiple simultaneous battles
