@echo off
REM ============================================
REM Fouler Play - Standalone "No Claude" Runner
REM ============================================
REM Zero AI agent dependency. Pure heuristic bot.
REM Works on any machine with Python 3.10+
REM ============================================

cd /d "%~dp0"

echo.
echo   FOULER PLAY - Standalone Mode
echo   Pokemon Showdown Gen9 OU Battle Bot
echo   No Claude/AI agent required
echo.

REM --- Prompt for credentials ---
if "%PS_USERNAME%"=="" (
    set /p PS_USERNAME="  Showdown username: "
)
if "%PS_PASSWORD%"=="" (
    set /p PS_PASSWORD="  Showdown password (blank if none): "
)

REM --- Config ---
if "%PS_WEBSOCKET_URI%"=="" set PS_WEBSOCKET_URI=wss://sim3.psim.us/showdown/websocket
if "%PS_FORMAT%"=="" set PS_FORMAT=gen9ou
if "%PS_TEAM%"=="" set PS_TEAM=gen9/ou/fat-team-1-stall
if "%PS_SEARCH_TIME_MS%"=="" set PS_SEARCH_TIME_MS=3000

echo.
echo   Account:    %PS_USERNAME%
echo   Format:     %PS_FORMAT%
echo   Server:     %PS_WEBSOCKET_URI%
echo.

REM --- Check Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

REM --- Setup venv if needed ---
if not exist "venv" (
    echo   Setting up virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo   Installing dependencies...
    pip install -r requirements.txt
    echo   Done!
) else (
    call venv\Scripts\activate.bat
)

REM --- Build password arg ---
set PW_ARG=
if not "%PS_PASSWORD%"=="" set PW_ARG=--ps-password "%PS_PASSWORD%"

REM --- Launch worker 1 ---
echo   Starting battle worker 1...
start /b python -u run.py --websocket-uri "%PS_WEBSOCKET_URI%" --ps-username "%PS_USERNAME%" %PW_ARG% --bot-mode search_ladder --pokemon-format "%PS_FORMAT%" --team-name "%PS_TEAM%" --search-time-ms %PS_SEARCH_TIME_MS% --run-count 999999 --save-replay always --log-level INFO

timeout /t 3 /nobreak >nul

REM --- Launch worker 2 ---
echo   Starting battle worker 2...
start /b python -u run.py --websocket-uri "%PS_WEBSOCKET_URI%" --ps-username "%PS_USERNAME%" %PW_ARG% --bot-mode search_ladder --pokemon-format "%PS_FORMAT%" --team-name "%PS_TEAM%" --search-time-ms %PS_SEARCH_TIME_MS% --run-count 999999 --save-replay always --log-level INFO

echo.
echo   Fouler Play running! 2 workers laddering as %PS_USERNAME%
echo   Press Ctrl+C to stop.
echo.

REM --- Keep window open ---
pause
