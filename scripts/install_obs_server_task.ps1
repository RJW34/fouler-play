param(
    [switch]$Apply,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$SkipHttpProbe,
    [switch]$Uninstall,
    [string]$TaskName = "HERMES-FoulerObsServer"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$BackupRoot = Join-Path $ProjectDir "devstream\backups\obs-server-task"
$LogRoot = Join-Path $ProjectDir "logs"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    $PowerShell = "powershell.exe"
}
$TaskWrapper = "scripts\start_obs_server_task.ps1"
$StdoutLog = Join-Path $LogRoot "jigglypuff-obs-server.log"
$StderrLog = Join-Path $LogRoot "jigglypuff-obs-server.err.log"
$TaskExecute = $PowerShell
$TaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Foreground' -f $TaskWrapper
$PidFile = Join-Path $ProjectDir ".pids\obs_server.pid"

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

function Get-ManagedObsServerProcess {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) { return $null }
    try {
        $payload = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $processId = [int]$payload.pid
        if ($processId -le 0) { return $null }
        return Get-Process -Id $processId -ErrorAction SilentlyContinue
    } catch {
        return $null
    }
}

function Remove-StalePidFile {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) { return }
    if (Get-ManagedObsServerProcess) { return }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-ManagedObsServer {
    $managedProcess = Get-ManagedObsServerProcess
    if ($managedProcess) {
        $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
        if (Test-Path -LiteralPath $taskkill -PathType Leaf) {
            try { & $taskkill /PID $managedProcess.Id /T /F *>$null } catch {}
        }
        Stop-Process -Id $managedProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Test-LocalPort {
    param([int]$Port = 8777)
    $netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
    if (-not (Test-Path -LiteralPath $netstat -PathType Leaf)) { return $false }
    $listeners = @(& $netstat -ano -p tcp 2>$null | Select-String -Pattern "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+\d+\s*$")
    return [bool]($listeners.Count -gt 0)
}

function Test-HealthEndpoint {
    param([int]$Port = 8777)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$Port/health"
        return [bool]($response.StatusCode -eq 200)
    } catch {
        return $false
    }
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

function Get-ObsTaskStatus {
    param([switch]$SkipHttpProbe)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    $managedProcess = Get-ManagedObsServerProcess
    $portOpen = Test-LocalPort -Port 8777
    $healthOk = if ($SkipHttpProbe) {
        $null
    } elseif ($portOpen) {
        Test-HealthEndpoint -Port 8777
    } else {
        $false
    }
    [pscustomobject]@{
        taskName = $TaskName
        taskPresent = [bool]$task
        taskState = if ($task) { [string]$task.State } else { "missing" }
        taskUser = if ($task) { [string]$task.Principal.UserId } else { $null }
        taskLogonType = if ($task) { [string]$task.Principal.LogonType } else { $null }
        lastTaskResult = if ($taskInfo) { $taskInfo.LastTaskResult } else { $null }
        lastRunTime = if ($taskInfo) { $taskInfo.LastRunTime.ToUniversalTime().ToString("o") } else { $null }
        projectDir = $ProjectDir
        powershell = $PowerShell
        wrapper = Join-Path $ProjectDir $TaskWrapper
        execute = $TaskExecute
        arguments = $TaskArguments
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        stderrTail = if (Test-Path -LiteralPath $StderrLog -PathType Leaf) { @(Get-Content -LiteralPath $StderrLog -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { [string]$_ }) } else { @() }
        managedPid = if ($managedProcess) { $managedProcess.Id } else { $null }
        processCount = if ($managedProcess) { 1 } else { 0 }
        port8777Listening = [bool]$portOpen
        healthEndpointOk = if ($SkipHttpProbe) { $null } else { [bool]$healthOk }
        healthProbeSkipped = [bool]$SkipHttpProbe
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    }
}

if ($Status) {
    Get-ObsTaskStatus -SkipHttpProbe:$SkipHttpProbe | ConvertTo-Json -Depth 6
    exit 0
}

if ($Stop -or $Uninstall) {
    Save-TaskBackup -Name $TaskName | Out-Null
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Stop-ManagedObsServer
    if ($Uninstall -and $Apply) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    if ($Uninstall) {
        Get-ObsTaskStatus -SkipHttpProbe:$SkipHttpProbe | ConvertTo-Json -Depth 6
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
        trigger = "AtStartup"
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    } | ConvertTo-Json -Depth 4
    exit 0
}

$backup = Save-TaskBackup -Name $TaskName
Remove-StalePidFile
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$action = New-ScheduledTaskAction -Execute $TaskExecute -Argument $TaskArguments -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "HERMES-managed Fouler Play public OBS surface server on port 8777." -Force | Out-Null

$startAttempt = $null
$startFailed = $false
if ($Start) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Stop-ManagedObsServer
    Remove-StalePidFile
    Rotate-LogFile -Path $StdoutLog | Out-Null
    Rotate-LogFile -Path $StderrLog | Out-Null
    $startedAt = (Get-Date).ToUniversalTime().ToString("o")
    Start-ScheduledTask -TaskName $TaskName
    $healthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        if ((Test-LocalPort -Port 8777) -and (Test-HealthEndpoint -Port 8777)) {
            $healthy = $true
            break
        }
    }
    $startFailed = -not $healthy
    $startAttempt = [ordered]@{
        startedAt = $startedAt
        schedulerOwnedForeground = $true
        healthy = [bool]$healthy
        error = if ($healthy) { $null } else { "scheduled S4U task did not produce a healthy listener on port 8777 within 30 seconds" }
    }
}

$statusPayload = Get-ObsTaskStatus -SkipHttpProbe:$SkipHttpProbe
$statusPayload | Add-Member -NotePropertyName backup -NotePropertyValue $backup
if ($null -ne $startAttempt) {
    $statusPayload | Add-Member -NotePropertyName startAttempt -NotePropertyValue $startAttempt
}
$statusPayload | ConvertTo-Json -Depth 6
if ($startFailed) { exit 1 }
