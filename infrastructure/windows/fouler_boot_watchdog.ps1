param(
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [int]$RunCount = 0,
    [int]$MaxConcurrentBattles = 3,
    [int]$MaxCycles = 0,
    [int]$QueueTimeoutSeconds = 180,
    [int]$SleepSeconds = 15,
    [string]$RuntimeLease = "",
    [switch]$AutoImprove
)

$ErrorActionPreference = "Stop"

$LogDir = Join-Path $ProjectDir "logs"
$PidDir = Join-Path $ProjectDir ".pids"
$LogPath = Join-Path $LogDir "fouler_boot_watchdog.log"
$LockPath = Join-Path $PidDir "fouler_boot_watchdog.lock"
$SupervisorStopFile = Join-Path $PidDir "supervisor.stop"
$RuntimeScript = Join-Path $ProjectDir "scripts\fouler_jigglypuff_runtime.ps1"
$MissionMonitorScript = Join-Path $ProjectDir "scripts\fouler_mission_monitor.py"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    $PowerShell = "powershell.exe"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $PidDir | Out-Null

function Write-WatchdogLog {
    param([string]$Message)
    $stamp = [DateTimeOffset]::Now.ToString("o")
    Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f $stamp, $Message) -Encoding ASCII
}

function Get-FoulerLadderProcesses {
    $all = @()
    foreach ($name in @("python.exe", "pythonw.exe", "py.exe")) {
        try {
            $found = Get-CimInstance Win32_Process -Filter "Name='$name'" -OperationTimeoutSec 15 -ErrorAction Stop |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine -match "run\.py" -and
                    $_.CommandLine -match "search_ladder" -and
                    $_.CommandLine -match [regex]::Escape($ProjectDir)
                }
            if ($found) { $all += $found }
        } catch {
            Write-WatchdogLog ("process scan failed for {0}: {1}; refusing to launch on this cycle" -f $name, $_.Exception.Message)
            return $null
        }
    }

    $ids = @{}
    foreach ($process in $all) {
        $ids[[int]$process.ProcessId] = $true
    }
    return @($all | Where-Object { -not $ids.ContainsKey([int]$_.ParentProcessId) })
}

function Get-PythonCommand {
    $venv = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venv -PathType Leaf) {
        return @($venv)
    }
    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($py) {
        return @($py.Source, "-3")
    }
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }
    return @("python.exe")
}

function Get-Tail {
    param([string]$Text, [int]$Limit = 2000)
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    if ($Text.Length -le $Limit) { return $Text }
    return $Text.Substring($Text.Length - $Limit)
}

function Test-FoulerStartGate {
    if (-not (Test-Path -LiteralPath $MissionMonitorScript -PathType Leaf)) {
        Write-WatchdogLog ("blocked: mission monitor start gate is missing: {0}" -f $MissionMonitorScript)
        return $false
    }
    $py = Get-PythonCommand
    $exe = $py[0]
    $pyArgs = @()
    if ($py.Count -gt 1) {
        $pyArgs = $py[1..($py.Count - 1)]
    }
    $monitorArgs = @(
        $MissionMonitorScript,
        "--start-gate-only",
        "--run-count", "$RunCount",
        "--max-cycles", "$MaxCycles",
        "--max-concurrent-battles", "$MaxConcurrentBattles"
    )
    Write-WatchdogLog ("checking Fouler mission start gate before launch: runCount={0} maxCycles={1} maxConcurrentBattles={2}" -f $RunCount, $MaxCycles, $MaxConcurrentBattles)
    $output = & $exe @pyArgs @monitorArgs 2>&1
    $exitCode = if ($LASTEXITCODE -is [int]) { $LASTEXITCODE } else { 0 }
    if ($exitCode -ne 0) {
        Write-WatchdogLog ("blocked: Fouler mission start gate rc={0}; tail={1}" -f $exitCode, (Get-Tail -Text (($output | Out-String).Trim())))
        return $false
    }
    Write-WatchdogLog "ok: Fouler mission start gate accepted the bounded launch"
    return $true
}

function Invoke-FoulerRuntimeStart {
    if (-not (Test-Path -LiteralPath $RuntimeScript -PathType Leaf)) {
        Write-WatchdogLog ("blocked: runtime script is missing: {0}" -f $RuntimeScript)
        return 2
    }
    if ($RunCount -le 0 -or $MaxCycles -le 0) {
        Write-WatchdogLog "blocked: RunCount and MaxCycles must be positive before boot watchdog can start Fouler"
        return 2
    }
    if (Test-Path -LiteralPath $SupervisorStopFile -PathType Leaf) {
        Write-WatchdogLog ("blocked: supervisor stop file is present: {0}" -f $SupervisorStopFile)
        return 0
    }
    if (-not (Test-FoulerStartGate)) {
        return 2
    }

    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $RuntimeScript,
        "-Command", "start",
        "-RunCount", "$RunCount",
        "-MaxConcurrentBattles", "$MaxConcurrentBattles",
        "-MaxCycles", "$MaxCycles",
        "-Execute"
    )
    if ($QueueTimeoutSeconds -gt 0) {
        $args += @("-QueueTimeoutSeconds", "$QueueTimeoutSeconds")
    }
    if ($SleepSeconds -gt 0) {
        $args += @("-SleepSeconds", "$SleepSeconds")
    }
    if (-not [string]::IsNullOrWhiteSpace($RuntimeLease)) {
        $args += @("-RuntimeLease", $RuntimeLease)
    }
    if ($AutoImprove) {
        $args += "-AutoImprove"
    }

    Write-WatchdogLog ("starting bounded Fouler runtime via lease gate: runCount={0} maxCycles={1} maxConcurrentBattles={2} autoImprove={3}" -f $RunCount, $MaxCycles, $MaxConcurrentBattles, [bool]$AutoImprove)
    $proc = Start-Process -FilePath $PowerShell -ArgumentList $args -WorkingDirectory $ProjectDir -WindowStyle Hidden -Wait -PassThru
    Write-WatchdogLog ("runtime start command exited rc={0}" -f $proc.ExitCode)
    return $proc.ExitCode
}

$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    Write-WatchdogLog "another boot watchdog invocation holds the lock; exiting"
    exit 0
}

try {
    Set-Location -LiteralPath $ProjectDir
    $top = Get-FoulerLadderProcesses
    if ($null -eq $top) {
        exit 0
    }
    if ($top.Count -gt 0) {
        Write-WatchdogLog ("ok: {0} top-level Fouler ladder client(s) alive: {1}" -f $top.Count, (($top | ForEach-Object { $_.ProcessId }) -join ","))
        exit 0
    }

    $rc = Invoke-FoulerRuntimeStart
    exit $rc
}
finally {
    if ($lockStream) {
        try { $lockStream.Close(); $lockStream.Dispose() } catch {}
    }
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
