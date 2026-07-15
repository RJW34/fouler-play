param(
    [switch]$NoRepair,
    [switch]$QueueAlerts,
    [int]$RunCount = 5,
    [int]$MaxCycles = 1
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location -LiteralPath $ProjectDir

$LogDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir "fouler-mission-monitor.log"
$ErrPath = Join-Path $LogDir "fouler-mission-monitor.err.log"

$Py = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) {
    $cmd = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($cmd) {
        $Py = $cmd.Source
    } else {
        $Py = "python.exe"
    }
}

$argsList = @(
    "scripts\fouler_mission_monitor.py",
    "--write",
    "--run-count", "$RunCount",
    "--max-cycles", "$MaxCycles",
    "--max-concurrent-battles", "1",
    "--queue-timeout-seconds", "180",
    "--sleep-seconds", "20",
    "--lease-minutes", "720"
)
if ($QueueAlerts) {
    $argsList += "--queue-alerts"
}

if (-not $NoRepair) {
    # Scheduled monitoring may consume an already-active owner lease, but it
    # must never mint or extend authority.
    $argsList += "--repair-runtime"
}

try {
    $stamp = Get-Date -Format o
    "$stamp mission-monitor start repair=$(-not $NoRepair) queueAlerts=$QueueAlerts runCount=$RunCount maxCycles=$MaxCycles" | Add-Content -LiteralPath $LogPath -Encoding ASCII
    & $Py @argsList 1>> $LogPath 2>> $ErrPath
    $code = $LASTEXITCODE
    "$((Get-Date).ToString('o')) mission-monitor exit=$code" | Add-Content -LiteralPath $LogPath -Encoding ASCII
    exit $code
} catch {
    "$((Get-Date).ToString('o')) mission-monitor exception=$($_.Exception.Message)" | Add-Content -LiteralPath $ErrPath -Encoding ASCII
    exit 1
}
