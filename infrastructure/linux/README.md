# Fouler Play - Development Infrastructure (Linux)

This directory contains scripts for automated analysis and development workflows on Linux.

## Overview

The replay analysis pipeline pulls battle data from git, analyzes team performance, and generates actionable reports that can be fed to AI assistants for improvement suggestions.

## Components

### 1. `analyze_performance.sh` - One-Shot Analysis

Runs a single analysis cycle:
- Pulls latest `battle_stats.json` from git
- Runs `replay_analysis/team_performance.py`
- Generates timestamped report in `replay_analysis/reports/`
- Creates symlink to latest report

**Usage:**
```bash
./infrastructure/linux/analyze_performance.sh
```

**Output:**
- Full report: `replay_analysis/reports/analysis_YYYYMMDD_HHMMSS.txt`
- Latest report: `replay_analysis/reports/latest_analysis.txt`
- JSON data: `replay_analysis/team_report.json`

### 2. `developer_loop.sh` - Continuous Analysis Loop

Runs continuously, checking for new battle data every 30 minutes (configurable):
- Monitors `battle_stats.json` for new battles
- Runs analysis automatically when new data arrives
- Logs all activity to `infrastructure/linux/developer_loop.log`

**Usage:**
```bash
# Run with defaults (30 min interval)
./infrastructure/linux/developer_loop.sh

# Custom interval (in seconds)
SLEEP_INTERVAL=600 ./infrastructure/linux/developer_loop.sh  # 10 minutes

# Run in background
nohup ./infrastructure/linux/developer_loop.sh &
```

**Environment Variables:**
- `SLEEP_INTERVAL`: Seconds between analysis cycles (default: 1800)
- `BRANCH`: Git branch to track (default: foulest-play)
- `REPO_DIR`: Repository root (auto-detected)

### 3. `team_performance.py` - Core Analysis Script

Located in `replay_analysis/team_performance.py`, this script:
- Reads `battle_stats.json` for match outcomes
- Parses replay JSON files for detailed battle data
- Calculates per-team win rates, matchup statistics, and Pokemon performance
- Generates recommendations based on statistical analysis

**Usage:**
```bash
# Generate full report (text + JSON)
python3 replay_analysis/team_performance.py

# Text only
python3 replay_analysis/team_performance.py --summary

# JSON only
python3 replay_analysis/team_performance.py --json
```

## Data Flow

```
BAKUGO (Windows) → battle_stats.json → Git Push
                                           ↓
DEKU (Linux) → Git Pull → analyze_performance.sh
                                           ↓
                             team_performance.py
                                           ↓
                         Reports (text + JSON)
                                           ↓
                      AI Assistant (Claude Code)
```

## Analysis Output

The analysis report includes:

### Per-Team Metrics
- Overall win rate with 95% confidence intervals
- ELO delta (rating change)
- Windowed win rates (last 10/25/50 games)
- Trend analysis (improving/stable/declining)

### Matchup Analysis
- Loss rate vs. specific opponent Pokemon
- Identifies problematic matchups (>55% loss rate)
- Statistical significance testing

### Pokemon Performance
- Per-Pokemon faint rates
- KO rates
- Games played

### Recommendations
- Identifies #1 weakness per team
- Actionable suggestions for improvement
- Meta-awareness guidance

## Example Workflow

1. **BAKUGO (Windows)** runs the battle bot and pushes `battle_stats.json`
2. **Run one-shot analysis:**
   ```bash
   ./infrastructure/linux/analyze_performance.sh
   ```
3. **Review the report:**
   ```bash
   cat replay_analysis/reports/latest_analysis.txt
   ```
4. **Feed to AI for suggestions:**
   - Copy the report content
   - Provide to Claude Code with context about team files
   - Implement suggested improvements

## Continuous Monitoring

For ongoing development, run the developer loop in the background:

```bash
# Start the loop
nohup ./infrastructure/linux/developer_loop.sh > /dev/null 2>&1 &

# Check logs
tail -f infrastructure/linux/developer_loop.log

# Stop the loop
pkill -f developer_loop.sh
```

The loop will:
- Check for new battles every 30 minutes
- Generate reports automatically when new data arrives
- Track analysis progress in `.last_analysis_count`

## Requirements

- Python 3.7+
- Git repository with `battle_stats.json` tracking
- Replay JSON files in `replay_analysis/losses/`

## Troubleshooting

### "battle_stats.json not found"
- Ensure BAKUGO has pushed the file to git
- Check that you're on the correct branch (`foulest-play`)
- Verify git pull is working

### "No battles found"
- The `battle_stats.json` file exists but contains no battle data
- Wait for BAKUGO to complete some battles

### Analysis script fails
- Check Python dependencies
- Verify replay JSON files are valid
- Review error messages in the report file

## Development

To modify the analysis:
1. Edit `replay_analysis/team_performance.py`
2. Test with: `python3 replay_analysis/team_performance.py --summary`
3. Commit changes to git

The developer loop will automatically use the updated script on the next cycle.
