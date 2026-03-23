@echo off
REM =============================================================================
REM Fouler-Play Player Loop (Windows)
REM =============================================================================
REM Batch loop wrapper. Current default runtime is single-worker unless overridden by env/launcher settings.
REM =============================================================================

setlocal EnableExtensions DisableDelayedExpansion

set "REPO_DIR=%~dp0..\.."
set "BRANCH=master"
set "BATCH_SIZE=%BATCH_SIZE%"
if not defined BATCH_SIZE set "BATCH_SIZE=10"
set "AUTO_PULL=%AUTO_PULL%"
if not defined AUTO_PULL set "AUTO_PULL=0"

echo ==========================================
echo  Fouler-Play Player Loop starting
echo  Repo: %REPO_DIR%
echo  Batch size: %BATCH_SIZE%
echo  Mode: current launcher default worker profile
echo ==========================================

:loop_start
echo.
echo [%date% %time%] --- Cycle start ---

cd /d "%REPO_DIR%"
if /I "%AUTO_PULL%"=="1" (
    echo [%date% %time%] Running deploy update...
    call "%REPO_DIR%\infrastructure\windows\deploy_update.bat"
    if errorlevel 1 (
        echo [%date% %time%] WARNING: deploy update failed. Continuing with local code.
    )
)

set "PS_RUN_COUNT=%BATCH_SIZE%"
call "%REPO_DIR%\start_one_touch.bat"
if errorlevel 1 (
    echo [%date% %time%] WARNING: Bot exited with error. Retrying in 15 seconds...
    timeout /t 15 /nobreak >nul
)

REM Push battle data so other machines can analyze it
echo [%date% %time%] Pushing battle data...
cd /d "%REPO_DIR%"
if exist battle_stats.json (
    git add battle_stats.json
    git diff --cached --quiet battle_stats.json 2>nul
    if errorlevel 1 (
        git commit -m "data: push battle_stats.json after batch"
        git push origin %BRANCH%
        echo [%date% %time%] Battle data pushed.
    ) else (
        echo [%date% %time%] No new battle data to push.
    )
)

REM Run autoresearch to analyze the latest batch
echo [%date% %time%] Running autoresearch...
py -3 "%REPO_DIR%\replay_analysis\autoresearch.py" -n 30 --no-discord 2>nul
if errorlevel 1 (
    echo [%date% %time%] WARNING: Autoresearch failed. Continuing.
)

REM Run the improvement agent — reads autoresearch, writes one fix, tests, pushes
echo [%date% %time%] Running improvement agent...
py -3 "%REPO_DIR%\infrastructure\improve_agent.py"
if errorlevel 1 (
    echo [%date% %time%] WARNING: Improvement agent failed or skipped. Continuing.
)

REM Pull any changes from the improvement agent before next cycle
echo [%date% %time%] Pulling latest code...
cd /d "%REPO_DIR%"
git pull origin %BRANCH% 2>nul

REM Run ELO watchdog after batch
echo [%date% %time%] Running ELO watchdog...
py -3 "%REPO_DIR%\infrastructure\elo_watchdog.py"

echo [%date% %time%] --- Cycle complete ---
goto loop_start

endlocal
