param()
# Fouler-Play Persistent Player Loop (PowerShell)
# Designed to run under NSSM as a Windows service.

$ErrorActionPreference = 'Continue'
$repoDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoDir

$env:PYTHONUTF8 = '1'
if (-not $env:PYTHONIOENCODING) { $env:PYTHONIOENCODING = 'utf-8' }

# Load .env
$envFile = Join-Path $repoDir '.env'
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $parts = $line -split '=', 2
            $key = $parts[0].Trim()
            $val = $parts[1].Trim()
            [Environment]::SetEnvironmentVariable($key, $val, 'Process')
        }
    }
}

# Validate required credentials
if (-not $env:PS_USERNAME) { Write-Error 'PS_USERNAME not set'; exit 1 }
if (-not $env:PS_PASSWORD) { Write-Error 'PS_PASSWORD not set'; exit 1 }

# Defaults
if (-not $env:PS_WEBSOCKET_URI)  { $env:PS_WEBSOCKET_URI = 'wss://sim3.psim.us/showdown/websocket' }
if (-not $env:PS_FORMAT)         { $env:PS_FORMAT = 'gen9ou' }
if (-not $env:PS_SEARCH_TIME_MS) { $env:PS_SEARCH_TIME_MS = '3000' }
if (-not $env:BOT_LOG_LEVEL)     { $env:BOT_LOG_LEVEL = 'INFO' }
if (-not $env:SAVE_REPLAY)       { $env:SAVE_REPLAY = 'always' }
if (-not $env:MAX_MCTS_BATTLES)  { $env:MAX_MCTS_BATTLES = '1' }

$batchSize = 30
$concurrent = 3

Write-Output "=========================================="
Write-Output " Fouler-Play Player Loop (PowerShell)"
Write-Output " Repo: $repoDir"
Write-Output " Batch size: $batchSize"
Write-Output " Concurrent: $concurrent"
Write-Output " Account: $($env:PS_USERNAME)"
Write-Output " Format: $($env:PS_FORMAT)"
Write-Output "=========================================="

while ($true) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Output "[$ts] --- Cycle start ---"

    # Clean up stale bot workers
    $stale = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match 'fouler-play' -and
        $_.Name -match '^(py|python).*\.exe$' -and
        ($_.CommandLine -match 'run\.py' -or $_.CommandLine -match 'bot_monitor\.py')
    }
    if ($stale) {
        foreach ($p in $stale) {
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                  Write-Output "[CLEANUP] Killed PID $($p.ProcessId)" } catch {}
        }
    }

    # Build args
    $teamArg = @()
    if ($env:TEAM_NAMES) {
        $teamArg = @('--team-names', $env:TEAM_NAMES)
    } elseif ($env:TEAM_LIST) {
        $teamArg = @('--team-list', $env:TEAM_LIST)
    } elseif ($env:TEAM_NAME) {
        $teamArg = @('--team-name', $env:TEAM_NAME)
    } else {
        $teamArg = @('--team-name', 'gen9/ou/fat-team-1-stall')
    }

    $pythonExe = 'C:\Python314\python.exe'
    if (-not (Test-Path $pythonExe)) { $pythonExe = 'py' }

    $args = @(
        'run.py',
        '--websocket-uri', $env:PS_WEBSOCKET_URI,
        '--ps-username', $env:PS_USERNAME,
        '--ps-password', $env:PS_PASSWORD,
        '--bot-mode', 'search_ladder',
        '--pokemon-format', $env:PS_FORMAT,
        '--search-time-ms', $env:PS_SEARCH_TIME_MS,
        '--run-count', $batchSize,
        '--save-replay', $env:SAVE_REPLAY,
        '--log-level', $env:BOT_LOG_LEVEL,
        '--max-concurrent-battles', $concurrent,
        '--search-parallelism', '1',
        '--max-mcts-battles', $env:MAX_MCTS_BATTLES
    ) + $teamArg

    if ($env:BOT_LOG_TO_FILE -eq '1') { $args += '--log-to-file' }

    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Output "[$ts] Starting $pythonExe $($args -join ' ')"

    $proc = Start-Process -FilePath $pythonExe -ArgumentList $args -WorkingDirectory $repoDir -NoNewWindow -PassThru -Wait
    $exitCode = $proc.ExitCode

    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    if ($exitCode -ne 0) {
        Write-Output "[$ts] WARNING: Bot exited with code $exitCode. Retrying in 15 seconds..."
        Start-Sleep -Seconds 15
    } else {
        Write-Output "[$ts] --- Cycle complete: $batchSize battles finished ---"
        # Post-batch pipeline: analyze matchups → report → next batch picks up weights
        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Write-Output "[$ts] Running matchup analysis (updates fp/matchup_weights.json)..."
        try {
            & $pythonExe "infrastructure\autoresearch\matchup_analyzer.py" 2>&1 | ForEach-Object { Write-Output "[MATCHUP] $_" }
        } catch { Write-Output "[MATCHUP] Error: $_" }

        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Write-Output "[$ts] Running post-batch reporting..."
        try {
            & $pythonExe "D:\deku-workspace\scripts\fouler-play-pulse.py" --send 2>&1 | ForEach-Object { Write-Output "[PULSE] $_" }
        } catch { Write-Output "[PULSE] Error: $_" }
    }
}
