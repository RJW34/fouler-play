# SAFETY: This registers a boot-capable Fouler watchdog. Use only with an
# explicit current runtime lease and bounded proof-window values. It never
# starts broadcast output and it leaves AutoImprove off unless requested.
param(
    [switch]$Apply,
    [switch]$Start,
    [switch]$Status,
    [switch]$Uninstall,
    [string]$TaskName = "HERMES-FoulerBootWatchdog",
    [int]$RunCount = 0,
    [int]$MaxConcurrentBattles = 3,
    [int]$MaxCycles = 0,
    [int]$QueueTimeoutSeconds = 180,
    [int]$SleepSeconds = 15,
    [int]$RepetitionMinutes = 5,
    [string]$RuntimeLease = "",
    [switch]$AutoImprove
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..\..").Path
$BackupRoot = Join-Path $ProjectDir "devstream\backups\fouler-boot-watchdog-task"
$LogRoot = Join-Path $ProjectDir "logs"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    $PowerShell = "powershell.exe"
}
$WatchdogScript = Join-Path $ProjectDir "infrastructure\windows\fouler_boot_watchdog.ps1"
$RuntimeLeaseArg = if ([string]::IsNullOrWhiteSpace($RuntimeLease)) { "" } else { ' -RuntimeLease "{0}"' -f ($RuntimeLease -replace '"', '\"') }
$AutoImproveArg = if ($AutoImprove) { " -AutoImprove" } else { "" }
$TaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProjectDir "{1}" -RunCount {2} -MaxConcurrentBattles {3} -MaxCycles {4} -QueueTimeoutSeconds {5} -SleepSeconds {6}{7}{8}' -f $WatchdogScript, $ProjectDir, $RunCount, $MaxConcurrentBattles, $MaxCycles, $QueueTimeoutSeconds, $SleepSeconds, $RuntimeLeaseArg, $AutoImproveArg

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

function Get-BootWatchdogStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    [pscustomobject]@{
        taskName = $TaskName
        taskPresent = [bool]$task
        taskState = if ($task) { [string]$task.State } else { "missing" }
        lastTaskResult = if ($info) { $info.LastTaskResult } else { $null }
        lastRunTime = if ($info) { $info.LastRunTime.ToUniversalTime().ToString("o") } else { $null }
        nextRunTime = if ($info) { $info.NextRunTime.ToUniversalTime().ToString("o") } else { $null }
        principalUserId = if ($task) { $task.Principal.UserId } else { $null }
        principalLogonType = if ($task) { [string]$task.Principal.LogonType } else { $null }
        watchdogScript = $WatchdogScript
        action = $TaskArguments
        logPath = Join-Path $LogRoot "fouler_boot_watchdog.log"
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    }
}

if ($Status) {
    Get-BootWatchdogStatus | ConvertTo-Json -Depth 6
    exit 0
}

if ($Uninstall) {
    $backup = Save-TaskBackup -Name $TaskName
    if ($Apply) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    $status = Get-BootWatchdogStatus
    $status | Add-Member -NotePropertyName backup -NotePropertyValue $backup
    $status | ConvertTo-Json -Depth 6
    exit 0
}

if (-not $Apply) {
    [pscustomobject]@{
        dryRun = $true
        wouldCreateOrUpdateTask = $TaskName
        execute = $PowerShell
        arguments = $TaskArguments
        workingDirectory = $ProjectDir
        principal = @{ userId = "SYSTEM"; logonType = "ServiceAccount"; runLevel = "Highest" }
        triggers = @("AtStartup", "Every $RepetitionMinutes minutes")
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    } | ConvertTo-Json -Depth 6
    exit 0
}

if ($RunCount -le 0 -or $MaxCycles -le 0) {
    [pscustomobject]@{
        blocked = $true
        status = "blocked-runtime-bounds"
        blockers = @("starting the Fouler boot watchdog requires explicit positive -RunCount and -MaxCycles")
        taskName = $TaskName
    } | ConvertTo-Json -Depth 4
    exit 2
}

$backup = Save-TaskBackup -Name $TaskName
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$action = New-ScheduledTaskAction -Execute $PowerShell -Argument $TaskArguments -WorkingDirectory $ProjectDir
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$minuteTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $RepetitionMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($startupTrigger, $minuteTrigger) -Settings $settings -Principal $principal -Description "Boot-capable Fouler runtime watchdog; restarts bounded proof windows through the runtime lease gate." -Force | Out-Null

if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 10
}

$statusPayload = Get-BootWatchdogStatus
$statusPayload | Add-Member -NotePropertyName backup -NotePropertyValue $backup
$statusPayload | ConvertTo-Json -Depth 6
