# Fouler Play Autonomous Improvement Pipeline

An automated system that analyzes battle replays, identifies patterns, and provides AI-powered insights to improve the bot's performance.

> **Migration drift warning (2026-03):** this file documents an older Ollama-on-MAGNETON pipeline. Current project intent in `TASKBOARD.md` says batch analysis should use Claude-quality external reasoning rather than local qwen analysis. Treat this document as historical/implementation reference until the pipeline docs are refreshed to match the live workflow.

## Architecture

```
Battle Completion → Watcher → Batch Analyzer → AI Analysis Backend → Report → Discord Notification
```

### Components

1. **pipeline.py** - Main orchestrator
   - `watch` mode: Monitors battle_stats.json for batch completions
   - `analyze` mode: Manual analysis trigger
   - `report` mode: Display latest report

2. **replay_analysis/batch_analyzer.py** - Analysis engine
   - Collects replay data
   - Extracts turn-by-turn reviews
   - Queries the configured AI analysis backend
   - Generates markdown reports

3. **Analysis backend**
   - Historically this was MAGNETON-hosted Ollama
   - Current intent favors stronger external reasoning over local qwen-based analysis
   - Verify the active backend in code/config before changing this pipeline

## Setup

### Prerequisites

- Bot is running and generating battles
- The configured analysis backend is reachable
- Discord delivery path is configured if notifications are enabled
- Confirm current intent in `TASKBOARD.md` before assuming Ollama/MAGNETON is still the production path

### Installation

```bash
# Install python-dotenv for env loading
source venv/bin/activate
pip install python-dotenv

# Test the pipeline
python generate_test_report.py

# Manual analysis run
python pipeline.py analyze -n 10

# View latest report
python pipeline.py report
```

### Running as a Service

```bash
# Install systemd service
sudo cp fouler-pipeline.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fouler-pipeline
sudo systemctl start fouler-pipeline

# Check status
sudo systemctl status fouler-pipeline

# View logs
tail -f logs/pipeline.log
```

## Configuration

Environment variables (in .env):

- `FOULER_BATCH_SIZE` - Number of battles before triggering analysis (default: 10)
- `DISCORD_WEBHOOK_URL` - Webhook for notifications (required)

## Usage

### Automatic Mode (Recommended)

```bash
# Start the watcher daemon
python pipeline.py watch
```

The watcher will:
1. Check battle_stats.json every 60 seconds
2. Trigger analysis after N new battles (default: 10)
3. Generate a report using the configured analysis backend
4. Post summary to Discord #project-fouler-play

### Manual Mode

```bash
# Analyze last 10 battles
python pipeline.py analyze

# Analyze last 20 battles
python pipeline.py analyze -n 20

# View latest report
python pipeline.py report
```

### Testing

```bash
# Test with existing replay data
python test_pipeline.py

# Generate a test report
python generate_test_report.py
```

## Report Structure

Reports are saved to `replay_analysis/reports/batch_NNNN_TIMESTAMP.md`

Each report includes:
- Batch statistics (wins/losses/WR)
- Team performance breakdown
- AI analysis covering:
  - Recurring decision-making mistakes
  - Matchup-specific weaknesses
  - Team composition issues
  - Loss patterns
  - Top 3 actionable improvements (ranked by impact)

## Discord Notifications

Notifications are posted to #project-fouler-play (1466691161363054840) with:
- Batch number and record
- Win rate
- Top 3 issues found
- Link to full report

## Files

- `pipeline.py` - Main orchestrator
- `replay_analysis/batch_analyzer.py` - Analysis engine
- `replay_analysis/reports/` - Generated reports
- `.batch_trigger` - State tracking (last analyzed batch)
- `fouler-pipeline.service` - Systemd service definition
- `test_pipeline.py` - Integration test
- `generate_test_report.py` - Test report generator

## Troubleshooting

### "No reviews collected"
- Replays may be 404 (not uploaded or too old)
- Check that bot is actually completing battles
- Verify replay_id is valid in battle_stats.json

### "Ollama query failed"
- Check MAGNETON is reachable: `ping 192.168.1.181`
- Verify SSH works: `ssh Ryan@192.168.1.181`
- Test Ollama: `ssh Ryan@192.168.1.181 "curl http://localhost:11434/api/version"`
- Check if model is loaded: `ssh Ryan@192.168.1.181 "ollama list"`

### "Discord notification failed"
- Verify DISCORD_WEBHOOK_URL is set in .env
- Test webhook manually with curl
- Check webhook hasn't been deleted/revoked

## Development

To modify the analysis prompt, edit `batch_analyzer.py` in the `build_analysis_prompt()` method.

To change the Ollama model, update `OLLAMA_MODEL` in `batch_analyzer.py`.

To adjust batch size, set `FOULER_BATCH_SIZE` environment variable.

## Performance

- Ollama generation takes 30-120 seconds per batch (depends on prompt size)
- Analysis runs asynchronously - doesn't block battle execution
- Reports are cached locally and don't require re-fetching replays

## Future Enhancements

- Automatic application of improvements (team builder integration)
- Historical trend analysis across batches
- Opponent modeling integration
- Real-time analysis during battles
- Web dashboard for report visualization
