param(
    [switch]$NoRepair
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
    "--queue-alerts",
    "--run-count", "30",
    "--max-cycles", "12",
    "--max-concurrent-battles", "1",
    "--queue-timeout-seconds", "180",
    "--sleep-seconds", "20",
    "--lease-minutes", "720"
)

if (-not $NoRepair) {
    $argsList += @("--repair-runtime", "--renew-lease")
}

try {
    $stamp = Get-Date -Format o
    "$stamp mission-monitor start repair=$(-not $NoRepair)" | Add-Content -LiteralPath $LogPath -Encoding ASCII
    & $Py @argsList 1>> $LogPath 2>> $ErrPath
    $code = $LASTEXITCODE
    "$((Get-Date).ToString('o')) mission-monitor exit=$code" | Add-Content -LiteralPath $LogPath -Encoding ASCII
    exit 0
} catch {
    "$((Get-Date).ToString('o')) mission-monitor exception=$($_.Exception.Message)" | Add-Content -LiteralPath $ErrPath -Encoding ASCII
    exit 1
}
