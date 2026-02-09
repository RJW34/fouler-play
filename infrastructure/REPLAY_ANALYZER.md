# Replay Analyzer - Automated Loss Pattern Detection

## Overview
Automated system that analyzes loss replays every 10 battles to identify decision-making patterns and improvement opportunities.

## How It Works

### 1. State Tracking
- Tracks last-analyzed battle count in `replay_analyzer_state.json`
- Runs every hour (via cron)
- Only analyzes when 10+ new battles have occurred

### 2. Analysis Process
For each batch of 10 battles:
1. Identifies losses from `battle_stats.json`
2. Loads local replay logs from `logs/` directory
3. Analyzes for patterns:
   - **Bad switches**: Excessive passivity, early faints
   - **Missed KO lines**: Opportunities for damage
   - **Status misplays**: Bad timing on status moves
   - **Setup errors**: Setup vs phazers/Unaware
   - **Hazard issues**: Not setting/removing hazards

### 3. Output
- Posts concise analysis to `#project-fouler-play` via webhook
- Logs full output to `infrastructure/replay_analyzer.log`
- Updates state file with progress

## Files
- **Script**: `infrastructure/replay_analyzer.py`
- **Wrapper**: `infrastructure/run_replay_analyzer.sh`
- **State**: `infrastructure/replay_analyzer_state.json`
- **Logs**: `infrastructure/replay_analyzer.log`

## Setup

### Cron Job
Add to crontab (`crontab -e`):
```cron
# Run replay analyzer every hour
0 * * * * /home/ryan/projects/fouler-play/infrastructure/run_replay_analyzer.sh
```

### Manual Run
```bash
cd /home/ryan/projects/fouler-play
./infrastructure/run_replay_analyzer.sh
```

Or directly:
```bash
/home/ryan/projects/fouler-play/venv/bin/python infrastructure/replay_analyzer.py
```

## Configuration
- **Batch size**: 10 battles (hardcoded)
- **Webhook**: Uses `DISCORD_BATTLES_WEBHOOK_URL` from `.env`
- **Log source**: Local `logs/` directory

## Monitoring
Check logs:
```bash
tail -f /home/ryan/projects/fouler-play/infrastructure/replay_analyzer.log
```

Check state:
```bash
cat /home/ryan/projects/fouler-play/infrastructure/replay_analyzer_state.json
```

## Future Enhancements
- [ ] Adjustable batch size
- [ ] More sophisticated pattern detection (type matchups, move selection)
- [ ] Historical trend tracking
- [ ] Integration with improvement pipeline
- [ ] Per-team analysis and recommendations
