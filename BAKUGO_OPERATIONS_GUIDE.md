# BAKUGO Operations Guide — live PS_USERNAME runtime (MAGNETON/Windows)

## Configuration Details

| Item | Value |
|------|-------|
| **Install path** | `C:\Users\Ryan\projects\fouler-play\` |
| **Branch** | `master` for deployment/base; check `TASKBOARD.md` first if a newer docs/coding-agent branch is temporarily ahead |
| **Bot account** | `.env` `PS_USERNAME` (currently `npctypebeat` on 2026-03-10) |
| **Credentials** | `.env` file (`PS_USERNAME`, `PS_PASSWORD`) |
| **Logs** | `logs/` dir (rotating, 10MB max, 3 backups) |
| **Start method** | `infrastructure\windows\player_loop.bat` -> `start_one_touch.bat` (current live process: 30 battles, 1 concurrent) |
| **Background/unattended** | `FoulerPlayOneTouch` scheduled task if installed; otherwise verify the looping `player_loop.bat` cmd.exe is alive |
| **Streaming server** | `python streaming/serve_obs_page.py` (port 8777) |
| **OBS WebSocket** | `ws://127.0.0.1:4455` (no auth) |

## Key .env Settings
```
PS_USERNAME=npctypebeat
SHOWDOWN_ACCOUNTS=npctypebeat
MAX_CONCURRENT_BATTLES=1
LOSS_TRIGGERED_DRAIN=0
TEAM_NAMES=gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-pivot,gen9/ou/fat-team-3-dondozo
BOT_DISPLAY_NAME=💥 BAKUGO
```

## Quick Commands

```powershell
# Start loop/runtime (foreground)
cd C:\Users\Ryan\projects\fouler-play
infrastructure\windows\player_loop.bat

# Kill all bot processes
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "run\.py|bot_monitor|launch\.py|serve_obs" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# Check active battles
Get-Content active_battles.json

# Pull latest deployment/base branch
# (If TASKBOARD.md says docs or migration work is temporarily ahead elsewhere, inspect that branch before assuming master is current.)
git pull origin master

# Restart streaming server
Start-Process python -ArgumentList "streaming/serve_obs_page.py" -WorkingDirectory "C:\Users\Ryan\projects\fouler-play" -WindowStyle Hidden
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Bot not searching | Kill stale bot python, then restart `infrastructure\windows\player_loop.bat` |
| Unicode errors in log | Cosmetic only, non-fatal |
| `file_log_handler` missing | Cosmetic, non-fatal |
| OBS showing homepage | Battle URL missing spectator hash — restart serve_obs_page |
| Scheduled task confusion | `Get-ScheduledTask -TaskName FoulerPlayOneTouch -ErrorAction SilentlyContinue` |
