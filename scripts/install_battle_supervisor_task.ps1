# SAFETY: This installer is not a default launcher. Register or start this persistent
# task only with a current Fouler proof window and runtime lease; normal onboarding
# must leave scheduled tasks disabled and use status/dry-run commands.
param(
    [switch]$Apply,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Uninstall,
    [string]$TaskName = "HERMES-FoulerBattleSupervisor",
    [int]$RunCount = 0,
    [int]$MaxConcurrentBattles = 3,
    [int]$MaxCycles = 0,
    [int]$QueueTimeoutSeconds = 180,
    [int]$SleepSeconds = 15,
    [string]$RuntimeLease = "",
    [switch]$AutoImprove
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$BackupRoot = Join-Path $ProjectDir "devstream\backups\battle-supervisor-task"
$LogRoot = Join-Path $ProjectDir "logs"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    $PowerShell = "powershell.exe"
}
$TaskWrapper = "scripts\start_battle_supervisor_task.ps1"
$StdoutLog = Join-Path $LogRoot "jigglypuff-battle-supervisor.log"
$StderrLog = Join-Path $LogRoot "jigglypuff-battle-supervisor.err.log"
$TaskExecute = if ($env:ComSpec) { $env:ComSpec } else { "cmd.exe" }
$AutoImproveArg = if ($AutoImprove) { " -AutoImprove" } else { "" }
$RuntimeLeaseArg = if ([string]::IsNullOrWhiteSpace($RuntimeLease)) { "" } else { ' -RuntimeLease "{0}"' -f ($RuntimeLease -replace '"', '\"') }
$TaskArguments = '/d /c ""{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" -RunCount {2} -MaxConcurrentBattles {3} -MaxCycles {4} -QueueTimeoutSeconds {5} -SleepSeconds {6}{7}{8}"' -f $PowerShell, $TaskWrapper, $RunCount, $MaxConcurrentBattles, $MaxCycles, $QueueTimeoutSeconds, $SleepSeconds, $RuntimeLeaseArg, $AutoImproveArg
$PidFile = Join-Path $ProjectDir ".pids\devstream_battle_supervisor.pid"
$StopFile = Join-Path $ProjectDir ".pids\supervisor.stop"

function Save-TaskBackup {
    param([string]$Name)
    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($task) {
        $path = Join-Path $BackupRoot "$Name-$stamp.xml"
        Export-ScheduledTask -TaskName $Name | Set-Content -LiteralPath $path -Encoding UTF8
        return $path
    }
    $path = Join-Path $BackupRoot "$Name-$stamp.none.txt"
    "No existing task named $Name at $((Get-Date).ToUniversalTime().ToString('o'))." | Set-Content -LiteralPath $path -Encoding UTF8
    return $path
}

function Rotate-LogFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $archive = Join-Path (Split-Path -Parent $Path) "archive"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"
    $dest = Join-Path $archive "$stamp-$([IO.Path]::GetFileName($Path))"
    Move-Item -LiteralPath $Path -Destination $dest -Force
    return $dest
}

function Get-BattleSupervisorProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "devstream_session\.py" -and
        $_.CommandLine -match "\bsupervise\b" -and
        $_.Name -match "python|py"
    } | Select-Object ProcessId,ParentProcessId,Name,CommandLine
}

function Stop-BattleSupervisorProcesses {
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir ".pids") | Out-Null
    (Get-Date).ToUniversalTime().ToString("o") | Set-Content -LiteralPath $StopFile -Encoding UTF8
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    foreach ($process in @(Get-BattleSupervisorProcesses)) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Get-BattleSupervisorStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    $processes = @(Get-BattleSupervisorProcesses)
    [pscustomobject]@{
        taskName = $TaskName
        taskPresent = [bool]$task
        taskState = if ($task) { [string]$task.State } else { "missing" }
        lastTaskResult = if ($taskInfo) { $taskInfo.LastTaskResult } else { $null }
        lastRunTime = if ($taskInfo) { $taskInfo.LastRunTime.ToUniversalTime().ToString("o") } else { $null }
        projectDir = $ProjectDir
        wrapper = Join-Path $ProjectDir $TaskWrapper
        execute = $TaskExecute
        arguments = $TaskArguments
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        stderrTail = if (Test-Path -LiteralPath $StderrLog -PathType Leaf) { @(Get-Content -LiteralPath $StderrLog -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { [string]$_ }) } else { @() }
        pidFile = $PidFile
        stopFile = $StopFile
        processCount = $processes.Count
        processes = $processes
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    }
}

if ($Status) {
    Get-BattleSupervisorStatus | ConvertTo-Json -Depth 6
    exit 0
}

if ($Stop -or $Uninstall) {
    Save-TaskBackup -Name $TaskName | Out-Null
    Stop-BattleSupervisorProcesses
    if ($Uninstall -and $Apply) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    Get-BattleSupervisorStatus | ConvertTo-Json -Depth 6
    exit 0
}

if (-not $Apply) {
    [pscustomobject]@{
        dryRun = $true
        wouldCreateOrUpdateTask = $TaskName
        execute = $TaskExecute
        arguments = $TaskArguments
        workingDirectory = $ProjectDir
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    } | ConvertTo-Json -Depth 4
    exit 0
}

if ($Start -and ($RunCount -le 0 -or $MaxCycles -le 0)) {
    [pscustomobject]@{
        dryRun = $false
        blocked = $true
        status = "blocked-runtime-bounds"
        blockers = @("starting the persistent battle supervisor requires explicit positive -RunCount and -MaxCycles bounds")
        taskName = $TaskName
    } | ConvertTo-Json -Depth 4
    exit 2
}

$backup = Save-TaskBackup -Name $TaskName
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$action = New-ScheduledTaskAction -Execute $TaskExecute -Argument $TaskArguments -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 30) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "HERMES-managed persistent Fouler Play Showdown battle supervisor." -Force | Out-Null

if ($Start) {
    Stop-BattleSupervisorProcesses
    if (Test-Path -LiteralPath $PidFile -PathType Leaf) { Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $StopFile -PathType Leaf) { Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue }
    Rotate-LogFile -Path $StdoutLog | Out-Null
    Rotate-LogFile -Path $StderrLog | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 8
}

$statusPayload = Get-BattleSupervisorStatus
$statusPayload | Add-Member -NotePropertyName backup -NotePropertyValue $backup
$statusPayload | ConvertTo-Json -Depth 6
