@echo off
REM =============================================================================
REM Install Fouler-Play as a Windows Scheduled Task (BAKUGO)
REM =============================================================================
REM Run once as Administrator: infrastructure\windows\install_task.bat
REM After install: the player loop starts on boot and auto-restarts on failure.
REM =============================================================================

setlocal

set TASK_NAME=FoulerPlayPlayerLoop
set REPO_DIR=%~dp0..\..
set SCRIPT_PATH=%~dp0player_loop.bat

echo Installing Fouler-Play scheduled task...
echo   Task name: %TASK_NAME%
echo   Script: %SCRIPT_PATH%
echo   Repo: %REPO_DIR%
echo.

REM Delete existing task if present
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Create task that runs on logon, restarts on failure
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "cmd /c \"%SCRIPT_PATH%\"" ^
    /sc onlogon ^
    /rl highest ^
    /f

if errorlevel 1 (
    echo ERROR: Failed to create scheduled task. Run as Administrator.
    exit /b 1
)

echo.
echo Installed successfully.
echo.
echo Commands:
echo   schtasks /run /tn "%TASK_NAME%"       -- Start now
echo   schtasks /end /tn "%TASK_NAME%"       -- Stop
echo   schtasks /query /tn "%TASK_NAME%"     -- Check status
echo   schtasks /delete /tn "%TASK_NAME%" /f -- Remove
echo.
echo The task runs on logon with highest privileges.
echo To start immediately: schtasks /run /tn "%TASK_NAME%"

endlocal
