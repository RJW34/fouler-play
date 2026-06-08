# SAFETY: This installer is not a default launcher. Register or start this persistent
# task only with a current Fouler proof window and runtime lease; normal onboarding
# must leave scheduled tasks disabled and use status/dry-run commands.
param(
    [switch]$Apply,
    [switch]$Start,
    [string]$TaskName = "HERMES-FoulerObsKeepAlive",
    [string]$ServerTaskName = "HERMES-FoulerObsServer"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$BackupRoot = Join-Path $ProjectDir "devstream\backups\obs-server-keepalive-task"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    $PowerShell = "powershell.exe"
}

$ScriptPath = "scripts\fouler_obs_keepalive.ps1"
$LogRoot = Join-Path $ProjectDir "logs"
$StdoutLog = Join-Path $LogRoot "jigglypuff-obs-keepalive.log"
$StderrLog = Join-Path $LogRoot "jigglypuff-obs-keepalive.err.log"
$TaskExecute = if ($env:ComSpec) { $env:ComSpec } else { "cmd.exe" }
$TaskArguments = '/d /c ""{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" -TaskName "{2}" 1>>"{3}" 2>>"{4}""' -f $PowerShell, $ScriptPath, $ServerTaskName, $StdoutLog, $StderrLog

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

if (-not $Apply) {
    [pscustomobject]@{
        dryRun = $true
        wouldCreateOrUpdateTask = $TaskName
        execute = $TaskExecute
        arguments = $TaskArguments
        workingDirectory = $ProjectDir
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    } | ConvertTo-Json -Depth 4
    exit 0
}

$backup = Save-TaskBackup -Name $TaskName
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$action = New-ScheduledTaskAction -Execute $TaskExecute -Argument $TaskArguments -WorkingDirectory $ProjectDir
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
$minuteTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logonTrigger, $minuteTrigger) -Settings $settings -Principal $principal -Description "Keeps the HERMES Fouler OBS surface server reachable on port 8777." -Force | Out-Null

if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 12
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
[pscustomobject]@{
    taskName = $TaskName
    taskPresent = [bool]$task
    taskState = if ($task) { [string]$task.State } else { "missing" }
    lastTaskResult = if ($info) { $info.LastTaskResult } else { $null }
    lastRunTime = if ($info) { $info.LastRunTime.ToUniversalTime().ToString("o") } else { $null }
    action = $TaskArguments
    backup = $backup
    rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
} | ConvertTo-Json -Depth 5
