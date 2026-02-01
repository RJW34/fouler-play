@echo off
REM windows_start.bat - 1-Click Fouler Play startup for Windows Desktop
REM Place this on your desktop for easy access

title Fouler Play Startup

echo ========================================
echo    FOULER PLAY - 1-Click Startup
echo ========================================
echo.

REM Change to fouler-play directory (adjust path if needed)
set "PROJECT_DIR=C:\Users\Ryan\projects\fouler-play"

if not exist "%PROJECT_DIR%" (
    echo ERROR: Project directory not found!
    echo Looking for: %PROJECT_DIR%
    echo.
    echo Please update the PROJECT_DIR variable in this batch file.
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"
echo Working directory: %CD%
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    echo Please install Python or add it to PATH
    pause
    exit /b 1
)

REM Check if venv exists
if not exist "venv\Scripts\activate.bat" (
    echo WARNING: Virtual environment not found
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
    
    echo Installing dependencies...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
) else (
    echo Virtual environment found
    call venv\Scripts\activate.bat
)

echo.
echo Checking for existing processes...
python process_manager.py status

echo.
echo Starting Fouler Play...
echo.

REM Start the bot monitor
start /B python -u bot_monitor.py > bot_monitor_output.log 2>&1

REM Wait a moment for startup
timeout /t 3 /nobreak >nul

REM Show status
echo.
python process_manager.py status

echo.
echo ========================================
echo   Fouler Play is now running!
echo ========================================
echo.
echo Commands:
echo   python process_manager.py status  - Show process status
echo   python process_manager.py stop    - Stop all processes
echo.
echo Logs:
echo   type bot_monitor_output.log
echo   Get-Content bot_monitor_output.log -Wait  (PowerShell)
echo.
echo Press any key to close this window...
echo (Bot will continue running in background)
pause >nul
