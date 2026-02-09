@echo off
REM =============================================================================
REM Fouler-Play Deploy Update (Windows Machine)
REM =============================================================================
REM Pulls latest code from master, records the deploy event.
REM =============================================================================

setlocal enabledelayedexpansion

set REPO_DIR=%~dp0..\..
set BRANCH=master
set DEPLOY_LOG=%REPO_DIR%\infrastructure\deploy_log.json
set BATTLE_STATS=%REPO_DIR%\battle_stats.json

echo [%date% %time%] ---- Deploy Update Starting ----

REM Step 1: Record pre-deploy ELO
cd /d "%REPO_DIR%"
for /f %%a in ('git rev-parse HEAD') do set PRE_DEPLOY_COMMIT=%%a
echo [%date% %time%] Pre-deploy commit: %PRE_DEPLOY_COMMIT%

REM Step 2: Pull latest code
echo [%date% %time%] Pulling from %BRANCH%...
git pull origin %BRANCH%
if errorlevel 1 (
    echo [%date% %time%] ERROR: git pull failed. Deploy aborted.
    exit /b 1
)

REM Step 3: Get post-deploy commit hash
for /f %%a in ('git rev-parse HEAD') do set POST_DEPLOY_COMMIT=%%a
echo [%date% %time%] Post-deploy commit: %POST_DEPLOY_COMMIT%

REM Step 4: Log the deploy event to deploy_log.json
echo [%date% %time%] Logging deploy event...
python -c "
import json, os, datetime

deploy_log_path = r'%DEPLOY_LOG%'
battle_stats_path = r'%BATTLE_STATS%'

# Load existing log or create new one
if os.path.exists(deploy_log_path):
    with open(deploy_log_path, 'r') as f:
        try:
            log = json.load(f)
        except json.JSONDecodeError:
            log = []
else:
    log = []

if not isinstance(log, list):
    log = []

# Get current ELO from battle_stats if available
current_elo = None
if os.path.exists(battle_stats_path):
    try:
        with open(battle_stats_path, 'r') as f:
            stats = json.load(f)
        if isinstance(stats, list) and len(stats) > 0:
            last = stats[-1]
            current_elo = last.get('elo', last.get('rating'))
        elif isinstance(stats, dict):
            current_elo = stats.get('elo', stats.get('rating'))
    except Exception:
        pass

# Create deploy entry
entry = {
    'timestamp': datetime.datetime.now().isoformat(),
    'type': 'deploy',
    'pre_commit': '%PRE_DEPLOY_COMMIT%',
    'post_commit': '%POST_DEPLOY_COMMIT%',
    'elo_at_deploy': current_elo
}

log.append(entry)

with open(deploy_log_path, 'w') as f:
    json.dump(log, f, indent=2)

print(f'Deploy logged: {entry[\"post_commit\"][:8]} (ELO: {current_elo})')
"

if errorlevel 1 (
    echo [%date% %time%] WARNING: Failed to log deploy event.
)

echo [%date% %time%] ---- Deploy complete: %POST_DEPLOY_COMMIT:~0,8% ----

endlocal
