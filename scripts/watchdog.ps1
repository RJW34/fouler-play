# Fouler Play Watchdog — bot_monitor.py + serve_obs_page.py
# Run as scheduled task every 5 minutes.

$ProjectDir = "C:\Users\Ryan\projects\fouler-play"
$LogFile = "$ProjectDir\watchdog.log"
$MaxLogLines = 500

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -FilePath $LogFile -Encoding utf8
    if (Test-Path $LogFile) {
        $lines = Get-Content $LogFile -Tail $MaxLogLines
        $lines | Set-Content $LogFile -Encoding utf8
    }
}

# === BOT MONITOR ===
$monitor = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*bot_monitor.py*" -and $_.Name -eq "python.exe" }
if ($monitor) {
    $logPath = "$ProjectDir\bot_monitor_stdout.log"
    if (Test-Path $logPath) {
        $age = ((Get-Date) - (Get-Item $logPath).LastWriteTime).TotalMinutes
        if ($age -gt 10) {
            Log "WARNING: bot log stale ($([math]::Round($age,1)) min)"
        }
    }
} else {
    Log "bot_monitor.py not found. Restarting..."
    Remove-Item "$ProjectDir\.pids\*" -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath python -ArgumentList "bot_monitor.py" `
        -WorkingDirectory $ProjectDir -WindowStyle Hidden `
        -RedirectStandardOutput "$ProjectDir\bot_monitor_stdout.log" `
        -RedirectStandardError "$ProjectDir\bot_monitor_stderr.log"
    Start-Sleep 5
    $check = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*bot_monitor.py*" -and $_.Name -eq "python.exe" }
    if ($check) { Log "Bot restarted. PID: $($check.ProcessId)" }
    else { Log "ERROR: Bot restart failed" }
}

# === STREAMING SERVER ===
$stream = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*serve_obs_page.py*" -and $_.Name -eq "python.exe" }
if (-not $stream) {
    Log "serve_obs_page.py not found. Starting..."
    Start-Process -FilePath python -ArgumentList "streaming/serve_obs_page.py" `
        -WorkingDirectory $ProjectDir -WindowStyle Hidden
    Start-Sleep 3
    $check = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*serve_obs_page.py*" -and $_.Name -eq "python.exe" }
    if ($check) { Log "Stream server started. PID: $($check.ProcessId)" }
    else { Log "ERROR: Stream server start failed" }
}
