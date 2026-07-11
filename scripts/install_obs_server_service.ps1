param(
    [switch]$Apply,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Uninstall,
    [switch]$DisableLegacyTasks,
    [switch]$SkipHttpProbe,
    [string]$ServiceName = "HERMES-FoulerObsServer",
    [string]$NssmSource = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$BackupRoot = Join-Path $ProjectDir "devstream\backups\obs-server-service"
$StableNssm = "C:\ProgramData\HERMES\bin\nssm.exe"
$Entrypoint = Join-Path $ProjectDir "streaming\run_obs_server_service.py"
$StopTreeHelper = Join-Path $ProjectDir "scripts\stop_obs_server_tree.py"
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PidFile = Join-Path $ProjectDir ".pids\obs_server.pid"
$StdoutLog = Join-Path $ProjectDir "logs\jigglypuff-obs-server.log"
$StderrLog = Join-Path $ProjectDir "logs\jigglypuff-obs-server.err.log"
$LegacyTasks = @("HERMES-FoulerObsKeepAlive", "HERMES-FoulerObsServer")

function Resolve-NssmSource {
    if ($NssmSource -and (Test-Path -LiteralPath $NssmSource -PathType Leaf)) {
        return (Resolve-Path -LiteralPath $NssmSource).Path
    }
    if (Test-Path -LiteralPath $StableNssm -PathType Leaf) { return $StableNssm }
    $command = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($command -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) { return $command.Source }
    throw "nssm.exe was not found; install NSSM or pass -NssmSource"
}

function Invoke-Nssm {
    param([string[]]$Arguments)
    & $StableNssm @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "nssm.exe failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Get-ServiceRecord {
    Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
}

function Get-ServiceProcessId {
    $lines = @(& "$env:SystemRoot\System32\sc.exe" queryex $ServiceName 2>$null)
    $line = $lines | Where-Object { $_ -match "^\s*PID\s*:" } | Select-Object -First 1
    if ($line -and $line -match ":\s*(\d+)") { return [int]$matches[1] }
    return $null
}

function Get-ManagedProcess {
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

function Test-LocalPort {
    param([int]$Port = 8777)
    $listeners = @(& "$env:SystemRoot\System32\netstat.exe" -ano -p tcp 2>$null |
        Select-String -Pattern "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+\d+\s*$")
    return [bool]($listeners.Count -gt 0)
}

function Test-HealthEndpoint {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:8777/health"
        return [bool]($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-LegacyTaskStatus {
    param([string]$Name)
    $lines = @(& "$env:SystemRoot\System32\schtasks.exe" /Query /TN $Name /FO CSV /NH 2>$null)
    if ($LASTEXITCODE -ne 0) { return @{ name = $Name; present = $false; state = "missing" } }
    $line = $lines | Where-Object { $_ } | Select-Object -First 1
    $row = $line | ConvertFrom-Csv -Header TaskName,NextRunTime,Status
    return @{ name = $Name; present = $true; state = [string]$row.Status }
}

function Get-ObsServiceStatus {
    $service = Get-ServiceRecord
    $managed = Get-ManagedProcess
    $portOpen = Test-LocalPort
    $healthOk = if ($SkipHttpProbe) { $null } elseif ($portOpen) { Test-HealthEndpoint } else { $false }
    $serviceRunning = [bool]($service -and [string]$service.Status -eq "Running")
    [pscustomobject]@{
        lifecycleOwner = "windows-service"
        serviceName = $ServiceName
        servicePresent = [bool]$service
        serviceState = if ($service) { [string]$service.Status } else { "missing" }
        serviceStartType = if ($service) { [string]$service.StartType } else { $null }
        serviceProcessId = if ($service) { Get-ServiceProcessId } else { $null }
        nssm = $StableNssm
        projectDir = $ProjectDir
        python = $Python
        entrypoint = $Entrypoint
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        managedPid = if ($managed) { $managed.Id } else { $null }
        processCount = if ($managed) { 1 } else { 0 }
        port8777Listening = [bool]$portOpen
        healthEndpointOk = if ($SkipHttpProbe) { $null } else { [bool]$healthOk }
        healthProbeSkipped = [bool]$SkipHttpProbe
        lifecycleHealthy = [bool]($serviceRunning -and $managed -and $portOpen -and ($SkipHttpProbe -or $healthOk))
        legacyTaskProbeSkipped = [bool]$SkipHttpProbe
        legacyTasks = if ($SkipHttpProbe) { @() } else { @($LegacyTasks | ForEach-Object { Get-LegacyTaskStatus -Name $_ }) }
        rollback = "Stop-Service '$ServiceName'; restore the task XML backups; re-enable HERMES-FoulerObsServer and HERMES-FoulerObsKeepAlive"
    }
}

function Save-RollbackBackup {
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"
    $path = Join-Path $BackupRoot $stamp
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    $service = Get-ServiceRecord
    if ($service) {
        @(& "$env:SystemRoot\System32\sc.exe" qc $ServiceName) | Set-Content -LiteralPath (Join-Path $path "$ServiceName-sc-qc.txt") -Encoding UTF8
        @(& $StableNssm dump $ServiceName) | Set-Content -LiteralPath (Join-Path $path "$ServiceName-nssm-dump.txt") -Encoding UTF8
        & "$env:SystemRoot\System32\reg.exe" export "HKLM\SYSTEM\CurrentControlSet\Services\$ServiceName" (Join-Path $path "$ServiceName.reg") /y | Out-Null
    } else {
        "No existing service named $ServiceName." | Set-Content -LiteralPath (Join-Path $path "$ServiceName.none.txt") -Encoding UTF8
    }
    foreach ($name in $LegacyTasks) {
        $xml = @(& "$env:SystemRoot\System32\schtasks.exe" /Query /TN $name /XML 2>$null)
        if ($LASTEXITCODE -eq 0) {
            [IO.File]::WriteAllLines((Join-Path $path "$name.xml"), [string[]]$xml, [Text.UTF8Encoding]::new($false))
        }
    }
    return $path
}

function Stop-ManagedProcess {
    if ((Test-Path -LiteralPath $Python -PathType Leaf) -and (Test-Path -LiteralPath $StopTreeHelper -PathType Leaf)) {
        & $Python $StopTreeHelper --project-dir $ProjectDir --execute | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "stop_obs_server_tree.py failed with exit code $LASTEXITCODE" }
    } else {
        $managed = Get-ManagedProcess
        if ($managed) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $managed.Id /T /F 2>$null | Out-Null
            Start-Sleep -Seconds 2
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

if ($Status) {
    Get-ObsServiceStatus | ConvertTo-Json -Depth 6
    exit 0
}

if (-not $Apply) {
    [pscustomobject]@{
        dryRun = $true
        serviceName = $ServiceName
        application = $Python
        arguments = "-u `"$Entrypoint`""
        workingDirectory = $ProjectDir
        stableNssm = $StableNssm
        disableLegacyTasks = [bool]$DisableLegacyTasks
        rollback = "Remove the service and restore the backed-up task XML definitions."
    } | ConvertTo-Json -Depth 4
    exit 0
}

$sourceNssm = Resolve-NssmSource
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $StableNssm) | Out-Null
if (-not (Test-Path -LiteralPath $StableNssm -PathType Leaf) -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceNssm).Hash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $StableNssm).Hash) {
    Copy-Item -LiteralPath $sourceNssm -Destination $StableNssm -Force
}
$backup = Save-RollbackBackup

$service = Get-ServiceRecord
if ($Stop -or $Uninstall) {
    if ($service -and [string]$service.Status -ne "Stopped") {
        Stop-Service -Name $ServiceName -Force
        (Get-Service -Name $ServiceName).WaitForStatus("Stopped", (New-TimeSpan -Seconds 30))
    }
    Stop-ManagedProcess
}
if ($Uninstall) {
    if (Get-ServiceRecord) { Invoke-Nssm -Arguments @("remove", $ServiceName, "confirm") }
    Get-ObsServiceStatus | Add-Member -NotePropertyName backup -NotePropertyValue $backup -PassThru | ConvertTo-Json -Depth 6
    exit 0
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "venv Python is missing: $Python" }
if (-not (Test-Path -LiteralPath $Entrypoint -PathType Leaf)) { throw "service entrypoint is missing: $Entrypoint" }
if (-not (Get-ServiceRecord)) { Invoke-Nssm -Arguments @("install", $ServiceName, $Python) }
Invoke-Nssm -Arguments @("set", $ServiceName, "Application", $Python)
Invoke-Nssm -Arguments @("set", $ServiceName, "AppParameters", "-u `"$Entrypoint`"")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppDirectory", $ProjectDir)
Invoke-Nssm -Arguments @("set", $ServiceName, "DisplayName", "HERMES Fouler OBS Server")
Invoke-Nssm -Arguments @("set", $ServiceName, "Description", "HERMES-managed Fouler Play public OBS surface on port 8777")
Invoke-Nssm -Arguments @("set", $ServiceName, "ObjectName", "LocalSystem")
Invoke-Nssm -Arguments @("set", $ServiceName, "Start", "SERVICE_AUTO_START")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppExit", "Default", "Restart")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppRestartDelay", "5000")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppThrottle", "1500")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppNoConsole", "1")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppEnvironmentExtra", "FOULER_OBS_LIFECYCLE_OWNER=windows-service")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppStdout", $StdoutLog)
Invoke-Nssm -Arguments @("set", $ServiceName, "AppStderr", $StderrLog)
Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateFiles", "1")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateOnline", "1")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateSeconds", "86400")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateBytes", "10485760")

if ($DisableLegacyTasks) {
    foreach ($name in $LegacyTasks) {
        & "$env:SystemRoot\System32\schtasks.exe" /Change /TN $name /Disable 2>$null | Out-Null
        & "$env:SystemRoot\System32\schtasks.exe" /End /TN $name 2>$null | Out-Null
    }
    Stop-ManagedProcess
}

if ($Start) {
    $current = Get-ServiceRecord
    if ($current -and [string]$current.Status -ne "Running") { Start-Service -Name $ServiceName }
    (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 30))
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ((Test-LocalPort) -and (Test-HealthEndpoint)) { break }
        Start-Sleep -Seconds 1
    }
}

$statusPayload = Get-ObsServiceStatus
$statusPayload | Add-Member -NotePropertyName backup -NotePropertyValue $backup
$statusPayload | Add-Member -NotePropertyName nssmSha256 -NotePropertyValue (Get-FileHash -Algorithm SHA256 -LiteralPath $StableNssm).Hash
$statusPayload | ConvertTo-Json -Depth 6
