@echo off
REM =============================================================================
REM Fouler-Play Player Loop (Windows Machine)
REM =============================================================================
REM Runs the bot in batches, pushes stats/replays, and deploys code updates.
REM =============================================================================

setlocal enabledelayedexpansion

REM ---------------------------------------------------------------------------
REM Configuration
REM ---------------------------------------------------------------------------
set REPO_DIR=%~dp0..\..
set BRANCH=master
set BATCH_SIZE=10
set DEPLOY_SCRIPT=%~dp0deploy_update.bat
set ELO_WATCHDOG=%REPO_DIR%\infrastructure\elo_watchdog.py

REM ---------------------------------------------------------------------------
REM Main Loop
REM ---------------------------------------------------------------------------
echo ==========================================
echo  Fouler-Play Player Loop starting
echo  Repo: %REPO_DIR%
echo  Branch: %BRANCH%
echo  Batch size: %BATCH_SIZE%
echo ==========================================

:loop_start

echo.
echo [%date% %time%] --- Cycle start ---

REM Step 1: Pull latest code
echo [%date% %time%] Pulling latest from %BRANCH%...
cd /d "%REPO_DIR%"
git pull origin %BRANCH%
if errorlevel 1 (
    echo [%date% %time%] ERROR: git pull failed. Retrying in 60s...
    timeout /t 60 /nobreak >nul
    goto loop_start
)

REM Step 2: Run the bot for a batch of games
echo [%date% %time%] Starting bot for %BATCH_SIZE% games...
cd /d "%REPO_DIR%"
python run.py ^
    --websocket-uri wss://sim3.psim.us/showdown/websocket ^
    --ps-username "ALL CHUNG" ^
    --ps-password ALLCHUNG ^
    --bot-mode search_ladder ^
    --pokemon-format gen9ou ^
    --search-time-ms 3000 ^
    --run-count %BATCH_SIZE% ^
    --save-replay always ^
    --log-level DEBUG ^
    --team-names gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-pivot,gen9/ou/fat-team-3-dondozo ^
    --max-concurrent-battles 3 ^
    --search-parallelism 1 ^
    --max-mcts-battles 3 ^
    --log-to-file ^
    --spectator-username CHUNGMIGHT

if errorlevel 1 (
    echo [%date% %time%] WARNING: Bot exited with error. Continuing loop...
)

REM Step 3: Push battle stats and replays
echo [%date% %time%] Pushing battle stats and replays...
cd /d "%REPO_DIR%"
git add battle_stats.json 2>nul
git add replays\* 2>nul
git add replay_*.json 2>nul

REM Check if there is anything to commit
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data: battle stats and replays from batch (%date% %time%)"
    git push origin %BRANCH%
    if errorlevel 1 (
        echo [%date% %time%] WARNING: git push failed. Will retry next cycle.
    ) else (
        echo [%date% %time%] Stats and replays pushed successfully.
    )
) else (
    echo [%date% %time%] No new stats/replays to push.
)

REM Step 4: Check for new code from the Linux machine
echo [%date% %time%] Checking for new code on %BRANCH%...
cd /d "%REPO_DIR%"
git fetch origin %BRANCH%

REM Compare local HEAD with remote HEAD
for /f %%a in ('git rev-parse HEAD') do set LOCAL_HEAD=%%a
for /f %%a in ('git rev-parse origin/%BRANCH%') do set REMOTE_HEAD=%%a

if not "!LOCAL_HEAD!"=="!REMOTE_HEAD!" (
    echo [%date% %time%] New code detected. Deploying update...
    call "%DEPLOY_SCRIPT%"

    REM Step 5: Run ELO watchdog after deploy
    echo [%date% %time%] Running ELO watchdog...
    python "%ELO_WATCHDOG%" 2>nul
    if errorlevel 1 (
        echo [%date% %time%] WARNING: ELO watchdog reported an issue.
    )
) else (
    echo [%date% %time%] No new code to deploy.
)

echo [%date% %time%] --- Cycle complete ---
echo [%date% %time%] Starting next batch...

goto loop_start

endlocal
