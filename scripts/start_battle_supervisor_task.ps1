param(
    [int]$RunCount = 1000000,
    [int]$MaxConcurrentBattles = 3,
    [int]$QueueTimeoutSeconds = 180,
    [int]$SleepSeconds = 15,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location -LiteralPath $ProjectDir

$Py = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) {
    $command = Get-Command "py" -ErrorAction SilentlyContinue
    if ($command) {
        $Py = $command.Source
    } else {
        $Py = "python.exe"
    }
}
if (-not $Foreground) {
    $Pythonw = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
    if (Test-Path -LiteralPath $Pythonw -PathType Leaf) {
        $Py = $Pythonw
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir ".pids") | Out-Null
$stopFile = Join-Path $ProjectDir ".pids\supervisor.stop"
if (Test-Path -LiteralPath $stopFile -PathType Leaf) {
    Remove-Item -LiteralPath $stopFile -Force
}

$env:LOSS_TRIGGERED_DRAIN = "0"
$env:BATTLE_STATS_MAX_ENTRIES = "5000"
$env:BOT_LOG_TO_FILE = "1"

$supervisorArgs = @(
    (Join-Path $ProjectDir "scripts\devstream_session.py"),
    "supervise",
    "--run-count", "$RunCount",
    "--max-concurrent-battles", "$MaxConcurrentBattles",
    "--queue-timeout-seconds", "$QueueTimeoutSeconds",
    "--sleep-seconds", "$SleepSeconds"
)

if ($Foreground) {
    & $Py @supervisorArgs
    exit $LASTEXITCODE
}

$logDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutLog = Join-Path $logDir "jigglypuff-battle-supervisor.log"
$stderrLog = Join-Path $logDir "jigglypuff-battle-supervisor.err.log"

function Quote-BatchArg {
    param([string]$Value)
    '"' + ($Value -replace '"', '""') + '"'
}

$cmdFile = Join-Path $ProjectDir ".pids\start_battle_supervisor.cmd"
$commandLine = @((Quote-BatchArg $Py)) + ($supervisorArgs | ForEach-Object { Quote-BatchArg $_ })
$cmdLines = @(
    "@echo off",
    "cd /d $(Quote-BatchArg $ProjectDir)",
    (($commandLine -join " ") + " 1>>$(Quote-BatchArg $stdoutLog) 2>>$(Quote-BatchArg $stderrLog)")
)
$cmdLines | Set-Content -LiteralPath $cmdFile -Encoding ASCII

$launch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = "cmd.exe /d /c $(Quote-BatchArg $cmdFile)"
    CurrentDirectory = $ProjectDir
}
if ($launch.ReturnValue -ne 0) {
    Write-Error "Win32_Process.Create failed with return value $($launch.ReturnValue)"
    exit 1
}
Start-Sleep -Seconds 3
exit 0
