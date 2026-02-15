# BAKUGO Operations Guide — ALL CHUNG (MAGNETON/Windows)

## Configuration Details

| Item | Value |
|------|-------|
| **Install path** | `C:\Users\Ryan\projects\fouler-play\` |
| **Branch** | `master` |
| **Bot account** | ALL CHUNG |
| **Credentials** | `.env` file (PS_USERNAME, PS_PASSWORD) |
| **Logs** | `logs/` dir (rotating, 10MB max, 3 backups) |
| **Start method** | `python launch.py 30 3` (30 battles, 3 concurrent) |
| **Background/unattended** | `FoulerPlayWatchdog` scheduled task (1-min interval) runs `scripts/watchdog.ps1` |
| **Streaming server** | `python streaming/serve_obs_page.py` (port 8777) |
| **OBS WebSocket** | `ws://127.0.0.1:4455` (no auth) |

## Key .env Settings
```
PS_USERNAME=ALL CHUNG
SHOWDOWN_ACCOUNTS=allchung,buginthecode
MAX_CONCURRENT_BATTLES=3
LOSS_TRIGGERED_DRAIN=0
TEAM_NAMES=gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-pivot,gen9/ou/fat-team-3-dondozo
BOT_DISPLAY_NAME=💥 BAKUGO
```

## Quick Commands

```powershell
# Start batch (foreground)
cd C:\Users\Ryan\projects\fouler-play
python launch.py 30 3

# Kill all bot processes
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "run\.py|bot_monitor|launch\.py|serve_obs" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# Check active battles
Get-Content active_battles.json

# Pull latest
git pull origin master

# Restart streaming server
Start-Process python -ArgumentList "streaming/serve_obs_page.py" -WorkingDirectory "C:\Users\Ryan\projects\fouler-play" -WindowStyle Hidden
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Bot not searching | Kill all python, restart `launch.py` |
| Unicode errors in log | Cosmetic only, non-fatal |
| `file_log_handler` missing | Cosmetic, non-fatal |
| OBS showing homepage | Battle URL missing spectator hash — restart serve_obs_page |
| Watchdog not restarting | `Get-ScheduledTask -TaskName FoulerPlayWatchdog` |
