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

function Get-RuntimeLeaseAccount {
    param([string]$Path)
    $resolved = Resolve-RuntimeLeasePath -Path $Path
    if ([string]::IsNullOrWhiteSpace($resolved) -or -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        return ""
    }
    try {
        $lease = Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json
    } catch {
        return ""
    }
    $candidates = @(
        $lease.account,
        $lease.psUsername,
        $lease.showdownAccount,
        $lease.battleScope.account,
        $lease.battleScope.psUsername
    )
    foreach ($candidate in $candidates) {
        $value = [string]$candidate
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
    }
    return ""
}

$ResolvedRuntimeLease = Resolve-RuntimeLeasePath -Path $RuntimeLease
$RuntimeLeaseAccount = Get-RuntimeLeaseAccount -Path $RuntimeLease
if (-not [string]::IsNullOrWhiteSpace($ResolvedRuntimeLease)) {
    $env:FOULER_RUNTIME_LEASE_PATH = $ResolvedRuntimeLease
}
if (-not [string]::IsNullOrWhiteSpace($RuntimeLeaseAccount)) {
    $env:PS_USERNAME = $RuntimeLeaseAccount
    $env:SHOWDOWN_USER_ID = $RuntimeLeaseAccount
    $env:SHOWDOWN_ACCOUNTS = $RuntimeLeaseAccount
}

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
    if ($clLower -match 'devstream_session\.py' -and $clLower -match '\bsupervise\b' -and $clLower.Contains($repoNeedle)) {
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
if (-not [string]::IsNullOrWhiteSpace($RuntimeLeaseAccount)) {
    $cmdLines += "set ""PS_USERNAME=$RuntimeLeaseAccount"""
    $cmdLines += "set ""SHOWDOWN_USER_ID=$RuntimeLeaseAccount"""
    $cmdLines += "set ""SHOWDOWN_ACCOUNTS=$RuntimeLeaseAccount"""
}
$cmdLines += "set ""FOULER_PLAY_ENABLE_AUTO_IMPROVE=$AutoImproveFlag"""
$cmdLines += (($commandLine -join " ") + " 1>>$(Quote-BatchArg $stdoutLog) 2>>$(Quote-BatchArg $stderrLog)")
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
