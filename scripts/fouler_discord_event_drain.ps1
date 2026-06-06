$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\Projects\fouler-play"
$LogDir = Join-Path $ProjectRoot "logs"
$LockPath = Join-Path $LogDir "fouler-discord-event-drain.lock"
$LogPath = Join-Path $LogDir "fouler-discord-event-drain.log"
$ErrPath = Join-Path $LogDir "fouler-discord-event-drain.err.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($LockPath, "OpenOrCreate", "ReadWrite", "None")
} catch {
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogPath -Value "$timestamp skip: prior drain still running"
    exit 0
}

try {
    Set-Location $ProjectRoot
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogPath -Value "$timestamp start"

    $python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        $pythonCmd = Get-Command python -ErrorAction Stop
        $python = $pythonCmd.Source
    }

    $output = & $python "infrastructure\event_poster.py" "--drain" "--max-events" "10" 2>&1
    $exitCode = $LASTEXITCODE
    Add-Content -Path $LogPath -Value $output

    $done = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogPath -Value "$done exit=$exitCode"
    exit $exitCode
} catch {
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $ErrPath -Value "$timestamp $($_.Exception.GetType().FullName): $($_.Exception.Message)"
    exit 1
} finally {
    if ($lockStream -ne $null) {
        $lockStream.Dispose()
    }
}
