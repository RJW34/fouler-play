@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%" || (
    echo [ERROR] Could not switch to repository directory: %REPO_DIR%
    exit /b 1
)

set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"

echo [CLEANUP] Stopping stale bot python workers...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$targets = Get-CimInstance Win32_Process | Where-Object { " ^
  "  $_.CommandLine -and " ^
  "  $_.CommandLine -match 'fouler-play' -and " ^
  "  $_.Name -match '^(py|python).*\.exe$' -and " ^
  "  ($_.CommandLine -match 'run\.py' -or $_.CommandLine -match 'bot_monitor\.py')" ^
  "}; " ^
  "if ($targets) { foreach ($p in $targets) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Output ('[CLEANUP] Killed PID ' + $p.ProcessId) } catch {} } } else { Write-Output '[CLEANUP] No stale bot python workers found.' }"

if exist ".env" call :load_env ".env"

if not defined PS_USERNAME (
    echo [ERROR] PS_USERNAME is not set. Add it to .env.
    exit /b 1
)
if not defined PS_PASSWORD (
    echo [ERROR] PS_PASSWORD is not set. Add it to .env.
    exit /b 1
)

if not defined PS_WEBSOCKET_URI set "PS_WEBSOCKET_URI=wss://sim3.psim.us/showdown/websocket"
if not defined PS_FORMAT set "PS_FORMAT=gen9ou"
if not defined PS_SEARCH_TIME_MS set "PS_SEARCH_TIME_MS=3000"
if not defined BOT_LOG_LEVEL set "BOT_LOG_LEVEL=INFO"
if not defined SAVE_REPLAY set "SAVE_REPLAY=always"
if not defined MAX_MCTS_BATTLES set "MAX_MCTS_BATTLES=1"
if not defined OBS_SERVER_PORT set "OBS_SERVER_PORT=8777"
if not defined AUTO_START_OBS_SERVER set "AUTO_START_OBS_SERVER=1"

if not defined TEAM_NAMES if not defined TEAM_LIST if not defined TEAM_NAME set "TEAM_NAME=gen9/ou/fat-team-1-stall"

:: =========================================================================
:: Ask user how many battles to play
:: =========================================================================
echo.
echo =============================================
echo   Fouler-Play Battle Launcher
echo   Account: %PS_USERNAME%
echo   Format : %PS_FORMAT%
echo =============================================
echo.
set /p "BATTLE_COUNT=How many battles? (0 = infinite): "
if not defined BATTLE_COUNT set "BATTLE_COUNT=0"
if "!BATTLE_COUNT!"=="0" (
    set "PS_RUN_COUNT=999999"
    echo [START] Running in infinite mode (999999 battles)
) else (
    set "PS_RUN_COUNT=!BATTLE_COUNT!"
    echo [START] Running !BATTLE_COUNT! battle(s)
)
set /p "CONCURRENT_BATTLES=Concurrent battles? (default 1): "
if not defined CONCURRENT_BATTLES set "CONCURRENT_BATTLES=1"
if "!CONCURRENT_BATTLES!"=="" set "CONCURRENT_BATTLES=1"
echo [START] Concurrent battles: !CONCURRENT_BATTLES!
echo.

set "SPECTATOR_FLAG="
if defined SPECTATOR_USERNAME (
    call :is_falsey "%ENABLE_SPECTATOR_INVITES%"
    if errorlevel 1 (
        set SPECTATOR_FLAG=--spectator-username "%SPECTATOR_USERNAME%"
    )
)

set "LOG_TO_FILE_FLAG="
if /I "%BOT_LOG_TO_FILE%"=="1" set "LOG_TO_FILE_FLAG=--log-to-file"

call :is_falsey "%AUTO_START_OBS_SERVER%"
if errorlevel 1 (
    call :ensure_obs_server
) else (
    echo [OBS] Auto-start disabled: AUTO_START_OBS_SERVER=%AUTO_START_OBS_SERVER%.
)

if "!CONCURRENT_BATTLES!"=="1" (
    echo [START] Mode   : single battle worker
) else (
    echo [START] Mode   : !CONCURRENT_BATTLES! concurrent battle workers
)
if defined SPECTATOR_USERNAME (
    if defined SPECTATOR_FLAG (
        echo [SPECTATOR] Inviting spectator account: %SPECTATOR_USERNAME%
    ) else (
        echo [SPECTATOR] Username set "%SPECTATOR_USERNAME%", invites disabled by ENABLE_SPECTATOR_INVITES.
    )
) else (
    echo [SPECTATOR] No spectator account configured.
)

if defined TEAM_NAMES goto run_with_team_names
if defined TEAM_LIST goto run_with_team_list
goto run_with_team_name

:run_with_team_names
call py -3 run.py ^
  --websocket-uri "%PS_WEBSOCKET_URI%" ^
  --ps-username "%PS_USERNAME%" ^
  --ps-password "%PS_PASSWORD%" ^
  --bot-mode search_ladder ^
  --pokemon-format "%PS_FORMAT%" ^
  --search-time-ms "%PS_SEARCH_TIME_MS%" ^
  --run-count "!PS_RUN_COUNT!" ^
  --save-replay "%SAVE_REPLAY%" ^
  --log-level "%BOT_LOG_LEVEL%" ^
  --max-concurrent-battles "!CONCURRENT_BATTLES!" ^
  --search-parallelism 1 ^
  --max-mcts-battles "%MAX_MCTS_BATTLES%" ^
  --team-names "%TEAM_NAMES%" ^
  %LOG_TO_FILE_FLAG% ^
  %SPECTATOR_FLAG%
goto done

:run_with_team_list
call py -3 run.py ^
  --websocket-uri "%PS_WEBSOCKET_URI%" ^
  --ps-username "%PS_USERNAME%" ^
  --ps-password "%PS_PASSWORD%" ^
  --bot-mode search_ladder ^
  --pokemon-format "%PS_FORMAT%" ^
  --search-time-ms "%PS_SEARCH_TIME_MS%" ^
  --run-count "!PS_RUN_COUNT!" ^
  --save-replay "%SAVE_REPLAY%" ^
  --log-level "%BOT_LOG_LEVEL%" ^
  --max-concurrent-battles "!CONCURRENT_BATTLES!" ^
  --search-parallelism 1 ^
  --max-mcts-battles "%MAX_MCTS_BATTLES%" ^
  --team-list "%TEAM_LIST%" ^
  %LOG_TO_FILE_FLAG% ^
  %SPECTATOR_FLAG%
goto done

:run_with_team_name
call py -3 run.py ^
  --websocket-uri "%PS_WEBSOCKET_URI%" ^
  --ps-username "%PS_USERNAME%" ^
  --ps-password "%PS_PASSWORD%" ^
  --bot-mode search_ladder ^
  --pokemon-format "%PS_FORMAT%" ^
  --search-time-ms "%PS_SEARCH_TIME_MS%" ^
  --run-count "!PS_RUN_COUNT!" ^
  --save-replay "%SAVE_REPLAY%" ^
  --log-level "%BOT_LOG_LEVEL%" ^
  --max-concurrent-battles "!CONCURRENT_BATTLES!" ^
  --search-parallelism 1 ^
  --max-mcts-battles "%MAX_MCTS_BATTLES%" ^
  --team-name "%TEAM_NAME%" ^
  %LOG_TO_FILE_FLAG% ^
  %SPECTATOR_FLAG%

goto done

:load_env
for /f "usebackq eol=# tokens=1* delims==" %%A in (%~1) do (
    if not "%%~A"=="" set "%%~A=%%~B"
)
exit /b 0

:is_falsey
set "_flag=%~1"
if /I "%_flag%"=="0" exit /b 0
if /I "%_flag%"=="false" exit /b 0
if /I "%_flag%"=="no" exit /b 0
if /I "%_flag%"=="off" exit /b 0
exit /b 1

:ensure_obs_server
echo [OBS] Ensuring OBS helper server is available on port %OBS_SERVER_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port = [int]$env:OBS_SERVER_PORT; if(-not $port){ $port = 8777 }; " ^
  "$url = ('http://localhost:{0}/obs-debug' -f $port); " ^
  "try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2 | Out-Null; Write-Output '[OBS] Helper server already running.'; exit 0 } catch {} ; " ^
  "$existing = Get-CimInstance Win32_Process | Where-Object { " ^
  "  $_.CommandLine -and $_.Name -match '^(py|python).*\\.exe$' -and " ^
  "  ($_.CommandLine -match 'streaming\\\\serve_obs_page\\.py' -or $_.CommandLine -match 'streaming\\.serve_obs_page')" ^
  "} | Select-Object -First 1; " ^
  "if(-not $existing) { " ^
  "  Start-Process -FilePath py -ArgumentList '-3','-m','streaming.serve_obs_page' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -RedirectStandardOutput 'obs_server.log' -RedirectStandardError 'obs_server.err.log'; " ^
  "  Start-Sleep -Seconds 2 " ^
  "} ; " ^
  "try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 4 | Out-Null; Write-Output '[OBS] Helper server ready.'; exit 0 } catch { Write-Output ('[OBS] WARNING: helper server not reachable: ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo [OBS] WARNING: OBS helper server is not reachable. Scene sources may appear blank.
)
exit /b 0

:done
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo [ERROR] Bot exited with code %EXIT_CODE%.
exit /b %EXIT_CODE%
