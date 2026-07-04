# BAKUGO Operations Guide — live PS_USERNAME runtime (JIGGLYPUFF/Windows)

> Note: `MAGNETON` is RETIRED (2026-05-13). The live runtime is **JIGGLYPUFF**. Engine
> architecture: see [ARCHITECTURE.md](ARCHITECTURE.md).

## Configuration Details

| Item | Value |
|------|-------|
| **Install path** | `D:\Projects\fouler-play\` on JIGGLYPUFF |
| **Branch** | `fix/clock-countdown-parse-79` (live runtime/docs; a long-lived fork line never merged to `origin/master`). Do NOT assume `master`; confirm with `git status`. |
| **Bot account** | `thepeakmons`; authority is `devstream\truth\runtime-lease.json` + `.env` `PS_USERNAME` (kept in sync). `LEBOTJAMESXD00N`/`npctypebeat` are RETIRED names. |
| **Credentials** | `.env` file (`PS_USERNAME`, `PS_PASSWORD`) |
| **Logs** | `logs/` dir (rotating, 10MB max, 3 backups) |
| **Start method** | `scripts\start_battle_supervisor_task.ps1` via `HERMES-FoulerBattleSupervisor` (current proof window: 30 battles, 1 concurrent) |
| **Background/unattended** | `HERMES-FoulerBattleSupervisor` plus `scripts\devstream_session.py supervise`; verify singleton ownership in `devstream\truth\health.json` |
| **Streaming server** | `python streaming/serve_obs_page.py` (port 8777) |
| **OBS WebSocket** | `ws://127.0.0.1:4455` (no auth) |

## Key .env Settings
```
PS_USERNAME=thepeakmons
SHOWDOWN_ACCOUNTS=thepeakmons
MAX_CONCURRENT_BATTLES=1
LOSS_TRIGGERED_DRAIN=0
TEAM_NAMES=gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-pivot,gen9/ou/fat-team-3-dondozo
BOT_DISPLAY_NAME=💥 BAKUGO
```

## Quick Commands

```powershell
# Start/check supervised runtime
cd D:\Projects\fouler-play
.\.venv\Scripts\python.exe scripts\fouler_mission_monitor.py --write

# Kill all bot processes
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "run\.py|bot_monitor|launch\.py|serve_obs" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# Check active battles and current account proof
Get-Content devstream\truth\health.json
Get-Content devstream\truth\latest-elo-proof.json

# Pull latest deployment/base branch
# (If TASKBOARD.md says docs or migration work is temporarily ahead elsewhere, inspect that branch before assuming master is current.)
git pull origin master

# Restart streaming server
Start-Process python -ArgumentList "streaming/serve_obs_page.py" -WorkingDirectory "D:\Projects\fouler-play" -WindowStyle Hidden
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Bot not searching | Kill stale bot python, then restart `infrastructure\windows\player_loop.bat` |
| Unicode errors in log | Cosmetic only, non-fatal |
| `file_log_handler` missing | Cosmetic, non-fatal |
| OBS showing homepage | Battle URL missing spectator hash — restart serve_obs_page |
| Scheduled task confusion | `Get-ScheduledTask -TaskName FoulerPlayOneTouch -ErrorAction SilentlyContinue` |
