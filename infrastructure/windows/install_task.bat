@echo off
REM =============================================================================
REM Install Fouler-Play as a Windows Scheduled Task
REM =============================================================================
REM Installs a task that launches start_one_touch.bat when the current user logs on.
REM =============================================================================

setlocal EnableExtensions

set "TASK_NAME=FoulerPlayOneTouch"
set "SCRIPT_PATH=%~dp0..\..\start_one_touch.bat"
set "TASK_USER=%USERNAME%"

echo Installing Fouler-Play scheduled task...
echo   Task name: %TASK_NAME%
echo   Script: %SCRIPT_PATH%
echo   User: %TASK_USER%
echo.

if not exist "%SCRIPT_PATH%" (
    echo ERROR: Missing launcher script: %SCRIPT_PATH%
    exit /b 1
)

schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "cmd /c \"%SCRIPT_PATH%\"" ^
    /sc onlogon ^
    /ru "%TASK_USER%" ^
    /rl LIMITED ^
    /f

if errorlevel 1 (
    echo ERROR: Failed to create scheduled task.
    exit /b 1
)

echo.
echo Installed successfully.
echo Commands:
echo   schtasks /run /tn "%TASK_NAME%"
echo   schtasks /end /tn "%TASK_NAME%"
echo   schtasks /query /tn "%TASK_NAME%"
echo   schtasks /delete /tn "%TASK_NAME%" /f

endlocal
