# Fouler Play - Automatic Replay Analysis System

**Status:** ✅ Active and monitoring losses

## What It Does

Automatically analyzes every lost battle to identify improvement opportunities and suggest code fixes.

## Components

### 1. **Replay Analyzer** (`analyzer.py`)
- Fetches replay JSON from Pokemon Showdown
- Parses turn-by-turn battle log
- Detects mistake patterns:
  - Setup moves vs phazers (Whirlwind/Roar/Dragon Tail)
  - Setup moves vs Unaware ability
  - Status moves vs active Substitute
  - Contact moves vs Iron Barbs/Rough Skin/Rocky Helmet
  - Moves used against immunities
  - Wasted coverage (non-STAB doing less damage)
- Generates suggested fixes for each mistake

### 2. **Bot Monitor Integration** (`bot_monitor.py`)
- Automatically triggers analysis when bot loses
- Posts results to Discord:
  - Number of mistakes found
  - Top 3 mistakes with severity
  - Suggested improvements
- Runs asynchronously (doesn't block bot)

### 3. **Weekly Reporter** (`generate_weekly_report.py`)
- Aggregates all losses from the week
- Generates improvement priority list
- Posts summary to Discord
- Run via cron: `0 0 * * 0` (every Sunday)

## Data Storage

```
replay_analysis/
├── losses/          # Individual loss analyses (JSON)
├── reports/         # Weekly summary reports (Markdown)
├── patterns/        # Aggregated pattern data
└── README.md        # This file
```

## How It Works

1. **Bot loses a battle**
2. **Replay URL is captured** from bot output
3. **Analyzer downloads replay** from Showdown
4. **Pattern detection runs** on battle log
5. **Mistakes are categorized** and saved
6. **Discord notification sent** with findings
7. **Weekly report** aggregates trends

## Mistake Categories

| Category | Severity | Example |
|----------|----------|---------|
| `ignored_immunity` | Critical | Ground move vs Levitate |
| `setup_vs_unaware` | Critical | Swords Dance vs Dondozo |
| `setup_vs_phazer` | Major | Dragon Dance when opponent has Roar |
| `status_vs_substitute` | Major | Thunder Wave into Substitute |
| `contact_vs_punish` | Minor | Close Combat vs Rocky Helmet |
| `wasted_coverage` | Minor | Ice Punch doing less than Earthquake |

## Usage

### Manual Analysis
```bash
cd /home/ryan/projects/fouler-play
source venv/bin/activate
python replay_analysis/analyzer.py <replay_url>
```

### Generate Report
```bash
python replay_analysis/generate_weekly_report.py
```

### View All Losses
```bash
ls -lht replay_analysis/losses/
```

## Integration with MCTS

Findings from this system feed directly into heuristic improvements:

1. **Detect pattern** (e.g., "setup vs phazer happening often")
2. **Add/tune penalty** in `fp/search/main.py`
3. **Deploy** and monitor ladder performance
4. **Iterate** based on new loss data

## Current Status

- ✅ Monitor actively capturing losses
- ✅ Analyzer detecting 6 major pattern types
- ⏱️ Waiting for first loss to analyze
- 📊 Weekly reports scheduled for Sundays

## Next Steps

1. Expand pattern detection (weather wars, speed tier mistakes, etc.)
2. Build "suggested code changes" generator
3. A/B test heuristic tuning based on analysis
4. Track improvement metrics over time

---

**Last Updated:** 2026-01-30
**Losses Analyzed:** 0 (just deployed)
