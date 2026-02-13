@echo off
REM =============================================================================
REM Fouler-Play Player Loop (Windows)
REM =============================================================================
REM Single-battle batch loop. Configure credentials and teams in .env.
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
echo  Mode: single battle worker
echo ==========================================

:loop_start
echo.
echo [%date% %time%] --- Cycle start ---

cd /d "%REPO_DIR%"
if /I "%AUTO_PULL%"=="1" (
    echo [%date% %time%] Pulling latest from %BRANCH%...
    git pull origin %BRANCH%
    if errorlevel 1 (
        echo [%date% %time%] WARNING: git pull failed. Continuing with local code.
    )
)

set "PS_RUN_COUNT=%BATCH_SIZE%"
call "%REPO_DIR%\start_one_touch.bat"
if errorlevel 1 (
    echo [%date% %time%] WARNING: Bot exited with error. Retrying in 15 seconds...
    timeout /t 15 /nobreak >nul
)

echo [%date% %time%] --- Cycle complete ---
goto loop_start

endlocal
