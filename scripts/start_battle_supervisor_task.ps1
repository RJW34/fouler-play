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

# --- SINGLETON GUARD ------------------------------------------------------
# Exactly one battle supervisor may run for this repo. Before launching a new
# one, terminate any pre-existing devstream_session.py "supervise" process that
# belongs to THIS project directory. This prevents two supervisors (each of
# which spawns its own bounded run.py batch) from laddering the same Showdown
# account at once -- the duplicate-runner failure mode that abandons battles
# and pins ELO. We match on the repo path so we never touch a supervisor from
# another install, and we exclude our own PID/ancestry.
$selfPid = $PID
$repoNeedle = $ProjectDir.ToLower()
foreach ($p in @(Get-CimInstance Win32_Process -Filter "name like 'python%'" -ErrorAction SilentlyContinue)) {
    $cl = $p.CommandLine
    if (-not $cl) { continue }
    $clLower = $cl.ToLower()
    if ($clLower -match 'devstream_session\.py' -and $clLower -match 'supervise' -and $clLower.Contains($repoNeedle)) {
        if ($p.ProcessId -ne $selfPid) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
                Write-Output "[singleton-guard] terminated pre-existing supervisor PID $($p.ProcessId)"
            } catch {}
        }
    }
}
# Also clear a stale supervisor PID file so the new supervisor owns it cleanly.
$supPidFile = Join-Path $ProjectDir ".pids\devstream_battle_supervisor.pid"
if (Test-Path -LiteralPath $supPidFile) {
    try { Remove-Item -LiteralPath $supPidFile -Force -ErrorAction SilentlyContinue } catch {}
}
Start-Sleep -Seconds 1
# --- END SINGLETON GUARD --------------------------------------------------

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
