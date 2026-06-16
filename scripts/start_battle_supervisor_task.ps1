param(
    [int]$RunCount = 0,
    [int]$MaxConcurrentBattles = 3,
    [int]$MaxCycles = 0,
    [int]$QueueTimeoutSeconds = 180,
    [int]$SleepSeconds = 15,
    [string]$RuntimeLease = "",
    [switch]$AutoImprove,
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

if ($RunCount -le 0 -or $MaxCycles -le 0) {
    Write-Error "Fouler battle supervisor requires explicit positive -RunCount and -MaxCycles bounds."
    exit 2
}

function Resolve-RuntimeLeasePath {
    param([string]$RuntimeLease)
    if ([string]::IsNullOrWhiteSpace($RuntimeLease)) {
        return (Join-Path $ProjectDir "devstream\truth\runtime-lease.json")
    }
    if ([System.IO.Path]::IsPathRooted($RuntimeLease)) {
        return $RuntimeLease
    }
    return (Join-Path $ProjectDir $RuntimeLease)
}

function Get-RuntimeLeaseAccount {
    param([string]$RuntimeLease)
    $path = Resolve-RuntimeLeasePath -RuntimeLease $RuntimeLease
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return ""
    }
    try {
        $lease = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
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
        $value = "$candidate".Trim()
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value
        }
    }
    return ""
}

function ConvertTo-CmdSetAssignment {
    param([string]$Name, [string]$Value)
    if ([string]::IsNullOrWhiteSpace($Name) -or [string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }
    if ($Value -notmatch '^[A-Za-z0-9_.-]+$') {
        return $null
    }
    return "set $Name=$Value"
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
$env:FOULER_PLAY_ENABLE_AUTO_IMPROVE = if ($AutoImprove) { "1" } else { "0" }

$supervisorArgs = @(
    (Join-Path $ProjectDir "scripts\devstream_session.py"),
    "supervise",
    "--run-count", "$RunCount",
    "--max-concurrent-battles", "$MaxConcurrentBattles",
    "--max-cycles", "$MaxCycles",
    "--queue-timeout-seconds", "$QueueTimeoutSeconds",
    "--sleep-seconds", "$SleepSeconds"
)
if (-not [string]::IsNullOrWhiteSpace($RuntimeLease)) {
    $supervisorArgs += @("--runtime-lease", $RuntimeLease)
}
if ($AutoImprove) {
    $supervisorArgs += "--enable-auto-improve"
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
$leaseAccount = Get-RuntimeLeaseAccount -RuntimeLease $RuntimeLease
if (-not [string]::IsNullOrWhiteSpace($leaseAccount)) {
    foreach ($envName in @("PS_USERNAME", "SHOWDOWN_USER_ID", "SHOWDOWN_ACCOUNTS", "FOULER_ACTIVE_ACCOUNT")) {
        $assignment = ConvertTo-CmdSetAssignment -Name $envName -Value $leaseAccount
        if (-not [string]::IsNullOrWhiteSpace($assignment)) {
            $cmdLines += $assignment
        }
    }
}
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
