param(
    [switch]$Apply,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Uninstall,
    [string]$TaskName = "HERMES-FoulerObsServer"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$BackupRoot = Join-Path $ProjectDir "devstream\backups\obs-server-task"
$LogRoot = Join-Path $ProjectDir "logs"
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = "python.exe"
}
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    $PowerShell = "powershell.exe"
}
$ScriptArg = "streaming\serve_obs_page.py"
$TaskWrapper = "scripts\start_obs_server_task.ps1"
$StdoutLog = Join-Path $LogRoot "jigglypuff-obs-server.log"
$StderrLog = Join-Path $LogRoot "jigglypuff-obs-server.err.log"
$WrapperStdoutLog = Join-Path $LogRoot "jigglypuff-obs-wrapper.log"
$WrapperStderrLog = Join-Path $LogRoot "jigglypuff-obs-wrapper.err.log"
$TaskExecute = if ($env:ComSpec) { $env:ComSpec } else { "cmd.exe" }
$TaskArguments = '/d /c ""{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" -Foreground 1>>"{2}" 2>>"{3}""' -f $PowerShell, $TaskWrapper, $WrapperStdoutLog, $WrapperStderrLog
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

function Remove-StalePidFile {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) { return }
    try {
        $payload = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
        $pid = [int]$payload.pid
        if ($pid -gt 0 -and (Get-Process -Id $pid -ErrorAction SilentlyContinue)) { return }
    } catch {
        # Malformed pid files are stale for this managed service.
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
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

function Get-ObsServerProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "streaming[\\/]serve_obs_page\.py" -and
        $_.CommandLine -match [regex]::Escape($ProjectDir) -and
        $_.Name -match "python|py"
    } | Select-Object ProcessId,ParentProcessId,Name,CommandLine
}

function Get-ObsTaskStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    $processes = @(Get-ObsServerProcesses)
    $port = @(Get-NetTCPConnection -LocalPort 8777 -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" })
    [pscustomobject]@{
        taskName = $TaskName
        taskPresent = [bool]$task
        taskState = if ($task) { [string]$task.State } else { "missing" }
        lastTaskResult = if ($taskInfo) { $taskInfo.LastTaskResult } else { $null }
        lastRunTime = if ($taskInfo) { $taskInfo.LastRunTime.ToUniversalTime().ToString("o") } else { $null }
        projectDir = $ProjectDir
        python = $Python
        powershell = $PowerShell
        wrapper = Join-Path $ProjectDir $TaskWrapper
        execute = $TaskExecute
        arguments = $TaskArguments
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        wrapperStdoutLog = $WrapperStdoutLog
        wrapperStderrLog = $WrapperStderrLog
        stderrTail = if (Test-Path -LiteralPath $StderrLog -PathType Leaf) { @(Get-Content -LiteralPath $StderrLog -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { [string]$_ }) } else { @() }
        wrapperStderrTail = if (Test-Path -LiteralPath $WrapperStderrLog -PathType Leaf) { @(Get-Content -LiteralPath $WrapperStderrLog -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { [string]$_ }) } else { @() }
        processCount = $processes.Count
        processes = $processes
        port8777Listening = [bool]$port
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    }
}

if ($Status) {
    Get-ObsTaskStatus | ConvertTo-Json -Depth 6
    exit 0
}

if ($Stop -or $Uninstall) {
    Save-TaskBackup -Name $TaskName | Out-Null
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    foreach ($process in @(Get-ObsServerProcesses)) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($Uninstall) {
        if ($Apply) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        }
        Get-ObsTaskStatus | ConvertTo-Json -Depth 6
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
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        wrapperStdoutLog = $WrapperStdoutLog
        wrapperStderrLog = $WrapperStderrLog
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    } | ConvertTo-Json -Depth 4
    exit 0
}

$backup = Save-TaskBackup -Name $TaskName
Remove-StalePidFile
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$action = New-ScheduledTaskAction -Execute $TaskExecute -Argument $TaskArguments -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 30) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "HERMES-managed Fouler Play public OBS surface server on port 8777." -Force | Out-Null

if ($Start) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    foreach ($process in @(Get-ObsServerProcesses)) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Remove-StalePidFile
    Rotate-LogFile -Path $StdoutLog | Out-Null
    Rotate-LogFile -Path $StderrLog | Out-Null
    Rotate-LogFile -Path $WrapperStdoutLog | Out-Null
    Rotate-LogFile -Path $WrapperStderrLog | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 5
}

$statusPayload = Get-ObsTaskStatus
$statusPayload | Add-Member -NotePropertyName backup -NotePropertyValue $backup
$statusPayload | ConvertTo-Json -Depth 6
