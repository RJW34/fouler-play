@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%" || exit /b 1

set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"

:: Load .env
if exist ".env" (
    for /f "usebackq eol=# tokens=* delims==" %%A in (".env") do (
        if not "%%~A"=="" set "%%~A=%%~B"
    )
)

if not defined PS_USERNAME exit /b 1
if not defined PS_PASSWORD exit /b 1
if not defined PS_WEBSOCKET_URI set "PS_WEBSOCKET_URI=wss://sim3.psim.us/showdown/websocket"
if not defined PS_FORMAT set "PS_FORMAT=gen9ou"
if not defined PS_SEARCH_TIME_MS set "PS_SEARCH_TIME_MS=3000"
if not defined BOT_LOG_LEVEL set "BOT_LOG_LEVEL=INFO"
if not defined SAVE_REPLAY set "SAVE_REPLAY=always"
if not defined MAX_MCTS_BATTLES set "MAX_MCTS_BATTLES=1"

:: Autoresearch: 30 battles, 3 concurrent
set "PS_RUN_COUNT=30"
set "CONCURRENT_BATTLES=3"

echo [AUTORESEARCH] Starting 30-battle batch (3 concurrent)
echo [AUTORESEARCH] Account: %PS_USERNAME%
echo [AUTORESEARCH] Format:  %PS_FORMAT%

call py -3 run.py ^
  --websocket-uri "%PS_WEBSOCKET_URI%" ^
  --ps-username "%PS_USERNAME%" ^
  --ps-password "%PS_PASSWORD%" ^
  --bot-mode search_ladder ^
  --pokemon-format "%PS_FORMAT%" ^
  --search-time-ms "%PS_SEARCH_TIME_MS%" ^
  --run-count %PS_RUN_COUNT% ^
  --save-replay "%SAVE_REPLAY%" ^
  --log-level "%BOT_LOG_LEVEL%" ^
  --max-concurrent-battles %CONCURRENT_BATTLES% ^
  --search-parallelism 1 ^
  --max-mcts-battles "%MAX_MCTS_BATTLES%" ^
  --team-names "%TEAM_NAMES%" ^
  --log-to-file

echo [AUTORESEARCH] Batch complete. Running analysis...
call py -3 pipeline.py analyze

echo [AUTORESEARCH] Cycle complete.
