@echo off
REM =============================================================================
REM Fouler-Play Player Loop (Windows)
REM =============================================================================
REM LEGACY FALLBACK ONLY. The current managed runtime is the bounded battle
REM supervisor path:
REM   scripts\devstream_session.py supervise
REM via scripts\fouler_jigglypuff_runtime.ps1 / start_battle_supervisor_task.ps1.
REM
REM This wrapper intentionally loops forever for old manual installs. Do not wire
REM it into HERMES/DEKU/current devstream launch paths.
REM =============================================================================

setlocal EnableExtensions DisableDelayedExpansion

set "REPO_DIR=%~dp0..\.."
set "BATCH_SIZE=%BATCH_SIZE%"
if not defined BATCH_SIZE set "BATCH_SIZE=10"
set "AUTO_PULL=%AUTO_PULL%"
if not defined AUTO_PULL set "AUTO_PULL=0"
set "ENABLE_AUTO_IMPROVE=%FOULER_PLAY_ENABLE_AUTO_IMPROVE%"
if not defined ENABLE_AUTO_IMPROVE set "ENABLE_AUTO_IMPROVE=0"
set "ENABLE_AUTO_PUSH=%FOULER_PLAY_ENABLE_AUTO_PUSH%"
if not defined ENABLE_AUTO_PUSH set "ENABLE_AUTO_PUSH=0"
set "PUSH_REMOTE=%FOULER_PLAY_PUSH_REMOTE%"
if not defined PUSH_REMOTE set "PUSH_REMOTE=%IMPROVE_AGENT_PUSH_REMOTE%"
set "PUSH_BRANCH=%FOULER_PLAY_PUSH_BRANCH%"
if not defined PUSH_BRANCH set "PUSH_BRANCH=%IMPROVE_AGENT_PUSH_BRANCH%"

echo ==========================================
echo  Fouler-Play Player Loop starting
echo  Repo: %REPO_DIR%
echo  Batch size: %BATCH_SIZE%
echo  Mode: LEGACY FALLBACK infinite loop - current supervisor path is devstream_session.py supervise
echo  Auto improve: %ENABLE_AUTO_IMPROVE%
echo  Auto push: %ENABLE_AUTO_PUSH%
echo ==========================================

if /I "%ENABLE_AUTO_PUSH%"=="1" (
    if not defined PUSH_REMOTE (
        echo ERROR: FOULER_PLAY_ENABLE_AUTO_PUSH=1 requires FOULER_PLAY_PUSH_REMOTE or IMPROVE_AGENT_PUSH_REMOTE.
        exit /b 2
    )
    if not defined PUSH_BRANCH (
        echo ERROR: FOULER_PLAY_ENABLE_AUTO_PUSH=1 requires FOULER_PLAY_PUSH_BRANCH or IMPROVE_AGENT_PUSH_BRANCH.
        exit /b 2
    )
    if /I "%PUSH_REMOTE%"=="origin" (
        if /I "%PUSH_BRANCH%"=="master" (
            echo ERROR: refusing unsafe push target origin master.
            exit /b 2
        )
    )
)

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
set "CONCURRENT_BATTLES=3"
echo [%date% %time%] Launching: PS_RUN_COUNT=%PS_RUN_COUNT%, CONCURRENT_BATTLES=%CONCURRENT_BATTLES%
call "%REPO_DIR%\start_one_touch.bat"
if errorlevel 1 (
    echo [%date% %time%] WARNING: Bot exited with error. Retrying in 15 seconds...
    timeout /t 15 /nobreak >nul
)

REM Push battle data only when explicitly enabled.
if /I "%ENABLE_AUTO_PUSH%"=="1" (
    echo [%date% %time%] Pushing battle data to %PUSH_REMOTE% HEAD:%PUSH_BRANCH%...
    cd /d "%REPO_DIR%"
    if exist battle_stats.json (
        git add battle_stats.json
        git diff --cached --quiet battle_stats.json 2>nul
        if errorlevel 1 (
            git commit -m "data: push battle_stats.json after batch"
            git push "%PUSH_REMOTE%" "HEAD:%PUSH_BRANCH%"
            echo [%date% %time%] Battle data pushed.
        ) else (
            echo [%date% %time%] No new battle data to push.
        )
    )
) else (
    echo [%date% %time%] Skipping battle data push; set FOULER_PLAY_ENABLE_AUTO_PUSH=1 with an explicit non-origin-master target to enable.
)

REM Run autoresearch to analyze the latest batch
echo [%date% %time%] Running autoresearch...
py -3 "%REPO_DIR%\replay_analysis\autoresearch.py" -n 30 --no-discord 2>nul
if errorlevel 1 (
    echo [%date% %time%] WARNING: Autoresearch failed. Continuing.
)

REM Run the improvement agent only when explicitly enabled.
if /I "%ENABLE_AUTO_IMPROVE%"=="1" (
    echo [%date% %time%] Running improvement agent...
    if /I "%ENABLE_AUTO_PUSH%"=="1" (
        py -3 "%REPO_DIR%\infrastructure\improve_agent.py" --enable-auto-improve --enable-git-push --push-remote "%PUSH_REMOTE%" --push-branch "%PUSH_BRANCH%"
    ) else (
        py -3 "%REPO_DIR%\infrastructure\improve_agent.py" --enable-auto-improve
    )
    if errorlevel 1 (
        echo [%date% %time%] WARNING: Improvement agent failed or skipped. Continuing.
    )

    REM Pull any changes from the improvement agent before next cycle only with an explicit target.
    if /I "%ENABLE_AUTO_PUSH%"=="1" (
        echo [%date% %time%] Pulling latest code from %PUSH_REMOTE% %PUSH_BRANCH%...
        cd /d "%REPO_DIR%"
        git pull "%PUSH_REMOTE%" "%PUSH_BRANCH%" 2>nul
    ) else (
        echo [%date% %time%] Skipping improvement pull; no explicit push target is enabled.
    )

    REM Run ELO watchdog after an enabled improvement cycle.
    echo [%date% %time%] Running ELO watchdog...
    py -3 "%REPO_DIR%\infrastructure\elo_watchdog.py"
) else (
    echo [%date% %time%] Skipping improvement agent and ELO watchdog; set FOULER_PLAY_ENABLE_AUTO_IMPROVE=1 to enable.
)

echo [%date% %time%] --- Cycle complete ---
goto loop_start

endlocal
