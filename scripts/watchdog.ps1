# Fouler Play Watchdog - bot_monitor.py (+ optional serve_obs_page.py)
# Run as scheduled task every 5 minutes.

$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$LogFile = Join-Path $ProjectDir "watchdog.log"
$MaxLogLines = 500
$EnableObsServer = $env:ENABLE_STREAM_HOOKS -eq "1"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -FilePath $LogFile -Encoding utf8
    if (Test-Path $LogFile) {
        $lines = Get-Content $LogFile -Tail $MaxLogLines
        $lines | Set-Content $LogFile -Encoding utf8
    }
}

# === BOT MONITOR ===
$monitor = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*bot_monitor.py*" }
if ($monitor) {
    $logPath = Join-Path $ProjectDir "bot_monitor.out.log"
    if (Test-Path $logPath) {
        $age = ((Get-Date) - (Get-Item $logPath).LastWriteTime).TotalMinutes
        if ($age -gt 10) {
            Log "WARNING: bot monitor output stale ($([math]::Round($age,1)) min)"
        }
    }
}
else {
    Log "bot_monitor.py not found. Restarting..."
    Remove-Item "$ProjectDir\.pids\*" -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath py -ArgumentList "-3", "bot_monitor.py" `
        -WorkingDirectory $ProjectDir -WindowStyle Hidden `
        -RedirectStandardOutput "$ProjectDir\bot_monitor.out.log" `
        -RedirectStandardError "$ProjectDir\bot_monitor.err.log"
    Start-Sleep 5
    $check = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*bot_monitor.py*" }
    if ($check) { Log "Bot monitor restarted." }
    else { Log "ERROR: bot monitor restart failed" }
}

if (-not $EnableObsServer) {
    return
}

# === OBS SERVER (optional) ===
$stream = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*serve_obs_page.py*" }
if (-not $stream) {
    Log "serve_obs_page.py not found. Starting..."
    Start-Process -FilePath py -ArgumentList "-3", "streaming/serve_obs_page.py" `
        -WorkingDirectory $ProjectDir -WindowStyle Hidden `
        -RedirectStandardOutput "$ProjectDir\obs_server.log" `
        -RedirectStandardError "$ProjectDir\obs_server.err.log"
    Start-Sleep 3
    $check = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*serve_obs_page.py*" }
    if ($check) { Log "OBS server started." }
    else { Log "ERROR: OBS server start failed" }
}
