# Installer for HERMES-CommitHogWatchdog (2026-07-04, claude)
# Registers the alert-only commit-growth watchdog added after the 2026-06-29
# commit-exhaustion crash (resident powershell.exe at 42.8-55 GB commit,
# Resource-Exhaustion-Detector Event ID 2004).
#
# Principal: S4U. This box is administered over SSH (network logons only);
# an InteractiveToken task silently never runs here (see the fix note in
# install_battle_supervisor_task.ps1). RunLevel Limited: the watchdog only
# reads process info and writes logs/queue events - it never kills anything.
#
# Triggers: AtStartup with 15-minute repetition (so it survives reboots) PLUS
# a one-time trigger with the same repetition starting now - an AtStartup
# trigger's repetition only begins at the NEXT boot, so without the second
# trigger the watchdog would stay dormant for the rest of the current uptime.
param(
    [string]$TaskName = "HERMES-CommitHogWatchdog",
    [string]$RunAsUser = "ryanj",
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    $PowerShell = "powershell.exe"
}
$Wrapper = Join-Path $ProjectDir "scripts\commit_hog_watchdog.ps1"

function Show-TaskStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Output "task ${TaskName}: MISSING"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Output ("task: {0} state={1}" -f $task.TaskName, $task.State)
    Write-Output ("principal: user={0} logonType={1} runLevel={2}" -f $task.Principal.UserId, $task.Principal.LogonType, $task.Principal.RunLevel)
    foreach ($t in $task.Triggers) {
        Write-Output ("trigger: {0} start={1} repetitionInterval={2} repetitionDuration={3} enabled={4}" -f $t.CimClass.CimClassName, $t.StartBoundary, $t.Repetition.Interval, $t.Repetition.Duration, $t.Enabled)
    }
    if ($info) {
        Write-Output ("lastRunTime={0} lastTaskResult=0x{1:X} nextRunTime={2}" -f $info.LastRunTime, $info.LastTaskResult, $info.NextRunTime)
    }
}

if ($Status) {
    Show-TaskStatus
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "unregistered $TaskName"
    exit 0
}

if (-not (Test-Path -LiteralPath $Wrapper -PathType Leaf)) {
    throw "missing wrapper script: $Wrapper"
}

$action = New-ScheduledTaskAction -Execute $PowerShell -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $Wrapper) -WorkingDirectory $ProjectDir

# Indefinite 15-minute repetition built directly as a CIM repetition pattern:
# [TimeSpan]::MaxValue renders as P99999999DT23H59M59S, which Task Scheduler
# rejects as out of range, and omitting Duration via New-ScheduledTaskTrigger
# is not supported on Windows PowerShell 5.1.
$repetitionClass = Get-CimClass -ClassName MSFT_TaskRepetitionPattern -Namespace Root/Microsoft/Windows/TaskScheduler
function New-FifteenMinuteRepetition {
    New-CimInstance -CimClass $repetitionClass -ClientOnly -Property @{ Interval = "PT15M"; StopAtDurationEnd = $false }
}
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$bootTrigger.Repetition = New-FifteenMinuteRepetition
$currentUptimeTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2))
$currentUptimeTrigger.Repetition = New-FifteenMinuteRepetition
$triggers = @($bootTrigger, $currentUptimeTrigger)

$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description "Alert-only commit-growth watchdog: logs and Discord-queues processes whose commit size exceeds COMMIT_HOG_THRESHOLD_GB (default 4 GB). Never kills anything. Added after the 2026-06-29 commit-exhaustion crash (Resource-Exhaustion-Detector 2004)." -Force | Out-Null

Write-Output "registered $TaskName"
Show-TaskStatus
