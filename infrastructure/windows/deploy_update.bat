@echo off
REM =============================================================================
REM Fouler-Play Deploy Update (Windows Machine)
REM =============================================================================
REM Pulls an explicitly approved fast-forward deploy target and records it.
REM This legacy deploy path is disabled by default so stale player loops cannot
REM silently merge over active agent work.
REM =============================================================================

setlocal enabledelayedexpansion

set "REPO_DIR=%~dp0..\.."
set "DEPLOY_LOG=%REPO_DIR%\infrastructure\deploy_log.json"
set "BATTLE_STATS=%REPO_DIR%\battle_stats.json"
set "ENABLE_DEPLOY_UPDATE=%FOULER_PLAY_ENABLE_DEPLOY_UPDATE%"
set "PULL_REMOTE=%FOULER_PLAY_PULL_REMOTE%"
set "PULL_BRANCH=%FOULER_PLAY_PULL_BRANCH%"

if /I not "%ENABLE_DEPLOY_UPDATE%"=="1" (
    echo [%date% %time%] BLOCKED: deploy_update.bat is disabled. Set FOULER_PLAY_ENABLE_DEPLOY_UPDATE=1 with FOULER_PLAY_PULL_REMOTE and FOULER_PLAY_PULL_BRANCH.
    exit /b 2
)
if not defined PULL_REMOTE (
    echo [%date% %time%] BLOCKED: FOULER_PLAY_PULL_REMOTE is required.
    exit /b 2
)
if not defined PULL_BRANCH (
    echo [%date% %time%] BLOCKED: FOULER_PLAY_PULL_BRANCH is required.
    exit /b 2
)
if /I "%PULL_REMOTE%"=="origin" (
    if /I "%PULL_BRANCH%"=="master" (
        if /I not "%FOULER_PLAY_ALLOW_MASTER_PULL%"=="1" (
            echo [%date% %time%] BLOCKED: refusing origin/master from legacy deploy_update.bat. Set FOULER_PLAY_ALLOW_MASTER_PULL=1 only for an intentional deploy window.
            exit /b 2
        )
    )
    if /I "%PULL_BRANCH%"=="main" (
        if /I not "%FOULER_PLAY_ALLOW_MASTER_PULL%"=="1" (
            echo [%date% %time%] BLOCKED: refusing origin/main from legacy deploy_update.bat. Set FOULER_PLAY_ALLOW_MASTER_PULL=1 only for an intentional deploy window.
            exit /b 2
        )
    )
)

echo [%date% %time%] ---- Deploy Update Starting ----

REM Step 1: Record pre-deploy ELO
cd /d "%REPO_DIR%"
for /f %%a in ('git rev-parse HEAD') do set PRE_DEPLOY_COMMIT=%%a
for /f %%a in ('git rev-parse --abbrev-ref HEAD') do set CURRENT_BRANCH=%%a
echo [%date% %time%] Pre-deploy commit: %PRE_DEPLOY_COMMIT%
echo [%date% %time%] Current branch: %CURRENT_BRANCH%

if /I not "%CURRENT_BRANCH%"=="%PULL_BRANCH%" (
    echo [%date% %time%] BLOCKED: current branch "%CURRENT_BRANCH%" does not match FOULER_PLAY_PULL_BRANCH "%PULL_BRANCH%".
    exit /b 2
)

git update-index -q --refresh
set "WORKTREE_DIRTY="
for /f "delims=" %%s in ('git status --porcelain --untracked-files=no') do set "WORKTREE_DIRTY=1"
if defined WORKTREE_DIRTY (
    echo [%date% %time%] BLOCKED: tracked worktree changes are present; refusing legacy deploy pull.
    git status --short
    exit /b 2
)

REM Step 2: Pull latest code by explicit fast-forward only
echo [%date% %time%] Fetching %PULL_REMOTE% %PULL_BRANCH%...
git fetch "%PULL_REMOTE%" "%PULL_BRANCH%"
if errorlevel 1 (
    echo [%date% %time%] ERROR: git fetch failed. Deploy aborted.
    exit /b 1
)
echo [%date% %time%] Fast-forwarding from FETCH_HEAD...
git merge --ff-only "FETCH_HEAD"
if errorlevel 1 (
    echo [%date% %time%] ERROR: fast-forward failed. Deploy aborted.
    exit /b 1
)

REM Step 3: Get post-deploy commit hash
for /f %%a in ('git rev-parse HEAD') do set POST_DEPLOY_COMMIT=%%a
echo [%date% %time%] Post-deploy commit: %POST_DEPLOY_COMMIT%

REM Step 4: Log the deploy event to deploy_log.json
echo [%date% %time%] Logging deploy event...
py -3 -c "
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

REM Step 5: Update build manifest (project-agnostic build tracking)
echo [%date% %time%] Updating build manifest...
py -3 -c "
import sys, json, os
sys.path.insert(0, r'%REPO_DIR%')
try:
    from infrastructure.build_manifest import get_manifest
    m = get_manifest()
    battle_count = 0
    bs_path = r'%BATTLE_STATS%'
    if os.path.exists(bs_path):
        with open(bs_path) as f:
            d = json.load(f)
        battle_count = len(d.get('battles', d) if isinstance(d, dict) else d)
    entry = m.record_deploy(progress_count=battle_count, source='deploy_update.bat')
    print(f'Build manifest: {entry[\"sha\"]} at progress={entry[\"progress_at_deploy\"]}')
except Exception as e:
    print(f'WARNING: build manifest update failed: {e}', file=sys.stderr)
"

echo [%date% %time%] ---- Deploy complete: %POST_DEPLOY_COMMIT:~0,8% ----

endlocal
