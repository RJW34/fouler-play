# Replay Analysis Pipeline - Quick Start Guide

## 🚀 Quick Commands

### Run Analysis Now
```bash
cd /home/ryan/projects/fouler-play
python3 pipeline.py analyze -n 10
```
Analyzes last 10 battles, generates report, posts to Discord.

### View Latest Report
```bash
python3 pipeline.py report
```

### Start Watcher (Daemon Mode)
```bash
python3 pipeline.py watch
```
Monitors battle_stats.json every 60s, triggers analysis when FOULER_BATCH_SIZE battles complete.

---

## ⚙️ Systemd Service Setup

### Install & Enable
```bash
cd /home/ryan/projects/fouler-play
sudo cp replay_analysis/fouler-pipeline.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fouler-pipeline
sudo systemctl start fouler-pipeline
```

### Monitor Service
```bash
# Check status
sudo systemctl status fouler-pipeline

# Watch live logs
journalctl -u fouler-pipeline -f

# Restart service
sudo systemctl restart fouler-pipeline

# Stop service
sudo systemctl stop fouler-pipeline
```

---

## 🧪 Testing with Local Replays

If you have local replay JSON files and want to test the analysis:

```bash
cd /home/ryan/projects/fouler-play
python3 replay_analysis/test_batch_local.py -n 5
```

View output:
```bash
ls -lt replay_analysis/reports/test_local_*.md | head -1 | awk '{print $NF}' | xargs cat
```

---

## 📊 Sample Workflow

```bash
# 1. Play some battles (bot updates battle_stats.json automatically)

# 2. When ready, run analysis
python3 pipeline.py analyze -n 10

# 3. View the generated insights
python3 pipeline.py report

# 4. Implement suggestions in bot logic

# 5. Repeat - measure improvement over time
```

---

## 🔧 Configuration

Create/edit `.env` in project root:
```bash
FOULER_BATCH_SIZE=10                              # Analyze every N battles
DISCORD_WEBHOOK_URL=https://discord.com/api/...   # For notifications
```

---

## 📍 Key Files

- `pipeline.py` - Main orchestrator
- `replay_analysis/batch_analyzer.py` - LLM integration
- `replay_analysis/reports/` - Generated analysis reports
- `battle_stats.json` - Battle history (auto-updated by bot)
- `.pipeline_state` - Tracks analysis progress

---

## 🐛 Troubleshooting

### "No reviews collected. Aborting analysis."
**Cause:** Recent replays expired on Pokemon Showdown (404)  
**Fix:** Replays must be <7 days old OR saved locally as JSON files

**Workaround:**
```bash
# Download replay JSON manually
REPLAY_ID="gen9ou-2539863915"
curl -s "https://replay.pokemonshowdown.com/${REPLAY_ID}.json" > "replay_analysis/${REPLAY_ID}.json"
```

### "SSH/Ollama error"
**Cause:** MAGNETON (192.168.1.181) not reachable or Ollama not running  
**Fix:**
```bash
# Test connection
ssh Ryan@192.168.1.181 "curl -s http://localhost:11434/api/tags | jq"

# If Ollama not running on MAGNETON, start it (on MAGNETON machine)
# Usually auto-starts, but check with: tasklist | findstr ollama
```

### Pipeline not triggering automatically
**Cause:** Systemd service not running or battle count below threshold  
**Check:**
```bash
# Service status
sudo systemctl status fouler-pipeline

# Current battle count
jq '.battles | length' battle_stats.json

# Pipeline state (shows last analyzed count)
cat .pipeline_state
```

---

## 💡 Tips

1. **First run:** Set `FOULER_BATCH_SIZE=5` to get initial insights faster
2. **Production:** Use `FOULER_BATCH_SIZE=10` for balanced analysis frequency
3. **Deep dive:** Run `analyze -n 30` occasionally for comprehensive review
4. **Trend tracking:** Keep all reports in `replay_analysis/reports/` to compare over time

---

## 📖 For More Details

See `PIPELINE_VALIDATION_REPORT.md` for:
- Complete technical validation
- Sample analysis outputs
- Known issues and workarounds
- Future enhancement ideas
