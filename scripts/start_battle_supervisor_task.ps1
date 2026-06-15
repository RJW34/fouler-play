param(
    [int]$RunCount = 10,
    [int]$MaxConcurrentBattles = 3,
    [int]$QueueTimeoutSeconds = 180,
    [int]$SleepSeconds = 15,
    [int]$MaxCycles = 1,
    [string]$RuntimeLease = "",
    [switch]$AutoImprove,
    [switch]$AllowUnboundedSupervisor,
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
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir ".pids") | Out-Null

function Resolve-RuntimeLeasePath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return "" }
    if ([IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $ProjectDir $Path)
}

$ResolvedRuntimeLease = Resolve-RuntimeLeasePath -Path $RuntimeLease
if (-not [string]::IsNullOrWhiteSpace($ResolvedRuntimeLease)) {
    $env:FOULER_RUNTIME_LEASE_PATH = $ResolvedRuntimeLease
}

$supPidFile = Join-Path $ProjectDir ".pids\devstream_battle_supervisor.pid"

function Read-PidFilePid {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        $raw = (Get-Content -Raw -LiteralPath $Path).Trim()
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        if ($raw.StartsWith("{")) {
            $parsed = $raw | ConvertFrom-Json
            return [int]$parsed.pid
        }
        return [int]$raw
    } catch {
        return $null
    }
}

# Exactly one battle supervisor may run for this repo. Use the supervisor PID
# file instead of broad Win32_Process enumeration; WMI has proven unreliable on
# the Windows runtime and can block the launcher before logs are created.
$existingSupervisorPid = Read-PidFilePid -Path $supPidFile
if ($existingSupervisorPid -and $existingSupervisorPid -ne $PID) {
    $existing = Get-Process -Id $existingSupervisorPid -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-Process -Id $existingSupervisorPid -Force -ErrorAction SilentlyContinue
        Write-Output "[singleton-guard] terminated supervisor PID $existingSupervisorPid from pid file"
        Start-Sleep -Seconds 1
    }
}
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
$AutoImproveFlag = if ($AutoImprove) { "1" } else { "0" }
$env:FOULER_PLAY_ENABLE_AUTO_IMPROVE = $AutoImproveFlag

$supervisorArgs = @(
    (Join-Path $ProjectDir "scripts\devstream_session.py"),
    "supervise",
    "--run-count", "$RunCount",
    "--max-concurrent-battles", "$MaxConcurrentBattles",
    "--queue-timeout-seconds", "$QueueTimeoutSeconds",
    "--sleep-seconds", "$SleepSeconds",
    "--max-cycles", "$MaxCycles"
)
if ($AutoImprove) {
    $supervisorArgs += "--enable-auto-improve"
}
if ($AllowUnboundedSupervisor) {
    $supervisorArgs += "--allow-unbounded-supervisor"
}
if (-not [string]::IsNullOrWhiteSpace($RuntimeLease)) {
    $supervisorArgs += @("--runtime-lease", $RuntimeLease)
}


if ($Foreground) {
    & $Py @supervisorArgs
    exit $LASTEXITCODE
}

$logDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutLog = Join-Path $logDir "jigglypuff-battle-supervisor.log"
$stderrLog = Join-Path $logDir "jigglypuff-battle-supervisor.err.log"

function Rotate-LogFileIfLarge {
    param(
        [string]$Path,
        [int64]$MaxBytes = 10485760,
        [int]$Keep = 6
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $item -or $item.Length -lt $MaxBytes) { return }
    $archive = Join-Path (Split-Path -Parent $Path) "archive"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"
    $name = [IO.Path]::GetFileName($Path)
    Move-Item -LiteralPath $Path -Destination (Join-Path $archive "$stamp-$name") -Force
    Get-ChildItem -LiteralPath $archive -Filter "*-$name" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $Keep |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

Rotate-LogFileIfLarge -Path $stdoutLog
Rotate-LogFileIfLarge -Path $stderrLog

function Quote-BatchArg {
    param([string]$Value)
    '"' + ($Value -replace '"', '""') + '"'
}

$cmdFile = Join-Path $ProjectDir ".pids\start_battle_supervisor.cmd"
$commandLine = @((Quote-BatchArg $Py)) + ($supervisorArgs | ForEach-Object { Quote-BatchArg $_ })
$cmdLines = @(
    "@echo off",
    "cd /d $(Quote-BatchArg $ProjectDir)"
)
if (-not [string]::IsNullOrWhiteSpace($ResolvedRuntimeLease)) {
    $cmdLines += "set ""FOULER_RUNTIME_LEASE_PATH=$ResolvedRuntimeLease"""
}
# The runtime lease proves authorization, not identity authority. devstream_session
# loads .env and rejects a lease whose account disagrees with the configured bot.
$cmdLines += "set ""FOULER_PLAY_ENABLE_AUTO_IMPROVE=$AutoImproveFlag"""
$cmdLines += (($commandLine -join " ") + " 1>>$(Quote-BatchArg $stdoutLog) 2>>$(Quote-BatchArg $stderrLog)")
$cmdLines | Set-Content -LiteralPath $cmdFile -Encoding ASCII

$cmdExe = if ($env:ComSpec) { $env:ComSpec } else { "cmd.exe" }
$launch = Start-Process -FilePath $cmdExe -ArgumentList @("/d", "/c", $cmdFile) -WorkingDirectory $ProjectDir -WindowStyle Hidden -PassThru
if (-not $launch -or -not $launch.Id) {
    Write-Error "Start-Process failed to launch battle supervisor wrapper"
    exit 1
}
Start-Sleep -Seconds 3
exit 0
