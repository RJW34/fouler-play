# SAFETY: This installer manages only the public OBS surface. It never starts battles or streaming.
param(
    [switch]$Apply,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Uninstall,
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
$TaskExecute = $PowerShell
$TaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -TaskName "{1}"' -f $ScriptPath, $ServerTaskName
$Schtasks = Join-Path $env:SystemRoot "System32\schtasks.exe"

function Save-TaskBackup {
    param([string]$Name)
    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"
    $taskXml = @(& $Schtasks /Query /TN $Name /XML 2>$null)
    if ($LASTEXITCODE -eq 0 -and $taskXml.Count -gt 0) {
        $path = Join-Path $BackupRoot "$Name-$stamp.xml"
        $taskXml | Set-Content -LiteralPath $path -Encoding UTF8
        return $path
    }
    $path = Join-Path $BackupRoot "$Name-$stamp.none.txt"
    "No existing task named $Name at $((Get-Date).ToUniversalTime().ToString('o'))." | Set-Content -LiteralPath $path -Encoding UTF8
    return $path
}

function Get-SchtasksTaskRow {
    $csv = @(& $Schtasks /Query /TN $TaskName /V /FO CSV 2>$null)
    if ($LASTEXITCODE -ne 0 -or $csv.Count -lt 2) { return $null }
    return (($csv -join "`n") | ConvertFrom-Csv | Select-Object -First 1)
}

function Get-SchtasksLogonType {
    $taskXml = @(& $Schtasks /Query /TN $TaskName /XML 2>$null)
    if ($LASTEXITCODE -ne 0 -or $taskXml.Count -eq 0) { return $null }
    try {
        [xml]$parsed = $taskXml -join "`n"
        return [string]$parsed.Task.Principals.Principal.LogonType
    } catch {
        return $null
    }
}

function Get-KeepaliveTaskStatus {
    $row = Get-SchtasksTaskRow
    [pscustomobject]@{
        taskName = $TaskName
        taskPresent = [bool]$row
        taskState = if ($row) { [string]$row.Status } else { "missing" }
        taskUser = if ($row) { [string]$row.'Run As User' } else { $null }
        taskLogonType = if ($row) { Get-SchtasksLogonType } else { $null }
        lastTaskResult = if ($row) { [string]$row.'Last Result' } else { $null }
        lastRunTime = if ($row) { [string]$row.'Last Run Time' } else { $null }
        nextRunTime = if ($row) { [string]$row.'Next Run Time' } else { $null }
        action = $TaskArguments
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    }
}

if ($Status) {
    Get-KeepaliveTaskStatus | ConvertTo-Json -Depth 5
    exit 0
}

if ($Stop -or $Uninstall) {
    Save-TaskBackup -Name $TaskName | Out-Null
    & $Schtasks /End /TN $TaskName *>$null
    if ($Uninstall -and $Apply) {
        & $Schtasks /Delete /TN $TaskName /F *>$null
    }
    if ($Uninstall) {
        Get-KeepaliveTaskStatus | ConvertTo-Json -Depth 5
        exit 0
    }
}

if (-not $Apply) {
    [pscustomobject]@{
        dryRun = $true
        wouldCreateOrUpdateTask = $TaskName
        execute = $TaskExecute
        arguments = $TaskArguments
        workingDirectory = $ProjectDir
        principalLogonType = "S4U"
        triggers = @("AtStartup", "EveryMinute")
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    } | ConvertTo-Json -Depth 4
    exit 0
}

$backup = Save-TaskBackup -Name $TaskName
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$action = New-ScheduledTaskAction -Execute $TaskExecute -Argument $TaskArguments -WorkingDirectory $ProjectDir
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$minuteTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($startupTrigger, $minuteTrigger) -Settings $settings -Principal $principal -Description "Keeps the HERMES Fouler OBS surface server reachable on port 8777." -Force | Out-Null

$startProof = $null
$startFailed = $false
if ($Start) {
    $requestedAt = Get-Date
    & $Schtasks /Run /TN $TaskName *>$null
    $runAccepted = [bool]($LASTEXITCODE -eq 0)
    Start-Sleep -Seconds 8
    $row = Get-SchtasksTaskRow
    $lastRunParsed = [datetime]::MinValue
    $lastRunRecent = [bool](
        $row -and
        [datetime]::TryParse([string]$row.'Last Run Time', [ref]$lastRunParsed) -and
        $lastRunParsed -ge $requestedAt.AddMinutes(-1)
    )
    $schedulerRunObserved = [bool]($runAccepted -and $lastRunRecent)
    $schedulerRunCompleted = [bool]($row -and $row.Status -ne "Running")
    $startFailed = -not $schedulerRunObserved
    $startProof = [ordered]@{
        schedulerRunObserved = $schedulerRunObserved
        schedulerRunCompleted = $schedulerRunCompleted
        schtasksRunAccepted = $runAccepted
        fallbackUsed = $false
        lastTaskResult = if ($row) { [string]$row.'Last Result' } else { $null }
    }
}

$statusPayload = Get-KeepaliveTaskStatus
$statusPayload | Add-Member -NotePropertyName backup -NotePropertyValue $backup
if ($null -ne $startProof) {
    $statusPayload | Add-Member -NotePropertyName startProof -NotePropertyValue $startProof
}
$statusPayload | ConvertTo-Json -Depth 5
if ($startFailed) { exit 1 }
