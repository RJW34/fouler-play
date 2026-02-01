@echo off
REM windows_stop.bat - Stop all Fouler Play processes

title Fouler Play Shutdown

echo ========================================
echo    FOULER PLAY - Shutdown
echo ========================================
echo.

set "PROJECT_DIR=D:\Projects with Claude\BAKUGO\fouler-play"

if not exist "%PROJECT_DIR%" (
    echo ERROR: Project directory not found!
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"

echo Stopping all processes...
echo.

call venv\Scripts\activate.bat
python process_manager.py stop

echo.
echo ========================================
echo   Shutdown complete
echo ========================================
echo.
pause
