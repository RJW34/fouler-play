@echo off
REM windows_setup_and_start.bat - Complete setup and startup for BAKUGO
REM This handles dependency installation with Python 3.14 compatibility

title Fouler Play - BAKUGO Setup & Start

set "PROJECT_DIR=D:\Projects with Claude\BAKUGO\fouler-play"

echo ========================================
echo    FOULER PLAY - BAKUGO Deployment
echo ========================================
echo.

if not exist "%PROJECT_DIR%" (
    echo ERROR: Project directory not found!
    echo Expected: %PROJECT_DIR%
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"

REM Check if venv exists, create if not
if not exist "venv\Scripts\activate.bat" (
    echo Creating Python virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create venv
        pause
        exit /b 1
    )
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies with PyO3 forward compatibility
echo.
echo Installing dependencies (this may take a few minutes)...
echo Setting PyO3 forward compatibility for Python 3.14...
set PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo WARNING: Some dependencies may have failed to install
    echo Attempting to continue anyway...
    echo.
)

REM Check if process manager exists
if not exist "process_manager.py" (
    echo WARNING: process_manager.py not found
    echo Bot will start but process tracking may not work
    echo.
)

echo.
echo ========================================
echo   Starting Fouler Play Bot
echo ========================================
echo.

REM Start the bot monitor in background
echo Starting bot monitor...
start /B python -u bot_monitor.py > bot_monitor_output.log 2>&1

timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   Fouler Play is running on BAKUGO!
echo ========================================
echo.
echo Bot is running in the background.
echo.
echo Logs: bot_monitor_output.log
echo.
echo To stop: run windows_stop.bat
echo.
echo Press any key to close this window...
echo (Bot will continue running)
pause >nul
