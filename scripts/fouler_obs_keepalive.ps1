param(
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$TaskName = "HERMES-FoulerObsServer",
    [int]$Port = 8777,
    [int]$StartupGraceSeconds = 90,
    [int]$HealthProbeAttempts = 6,
    [int]$HealthProbeIntervalSeconds = 8,
    [int]$ClosedPortProbeAttempts = 2
)

$ErrorActionPreference = "Stop"
$PidFile = Join-Path $ProjectDir ".pids\obs_server.pid"
$Schtasks = Join-Path $env:SystemRoot "System32\schtasks.exe"

function Test-LocalPort {
    param([int]$Port)
    $netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
    if (-not (Test-Path -LiteralPath $netstat -PathType Leaf)) { return $false }
    $listeners = @(& $netstat -ano -p tcp 2>$null | Select-String -Pattern "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+\d+\s*$")
    return [bool]($listeners.Count -gt 0)
}

function Test-HealthEndpoint {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 3
    )
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSeconds "http://127.0.0.1:$Port/health"
        return [bool]($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-TaskState {
    param([string]$TaskName)
    try {
        $lines = @(& $Schtasks /Query /TN $TaskName /FO CSV /NH 2>$null)
        if ($LASTEXITCODE -ne 0) { return "Unknown" }
        $line = $lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1
        if (-not $line) { return "Unknown" }
        $row = $line | ConvertFrom-Csv -Header TaskName,NextRunTime,Status
        return [string]$row.Status
    } catch {
        return "Unknown"
    }
}

function Start-ManagedTask {
    param([string]$TaskName)
    & $Schtasks /Run /TN $TaskName *>$null
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks.exe could not start $TaskName (exit $LASTEXITCODE)"
    }
}

function Stop-ManagedTask {
    param([string]$TaskName)
    & $Schtasks /End /TN $TaskName *>$null
}

function Get-ManagedObsServerStatus {
    $status = [ordered]@{
        pidFilePresent = Test-Path -LiteralPath $PidFile -PathType Leaf
        processId = $null
        alive = $false
        ageSeconds = $null
    }
    if (-not $status.pidFilePresent) { return [pscustomobject]$status }
    try {
        $payload = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $processId = [int]$payload.pid
        $status.processId = if ($processId -gt 0) { $processId } else { $null }
        if ($processId -gt 0) {
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            $status.alive = [bool]$process
        }
        $startedAt = [double]$payload.started_at
        if ($startedAt -gt 0) {
            $nowEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
            $status.ageSeconds = [math]::Max(0, [math]::Round($nowEpoch - $startedAt, 1))
        }
    } catch {
        $status.alive = $false
    }
    return [pscustomobject]$status
}

function Stop-ManagedObsServer {
    $managed = Get-ManagedObsServerStatus
    if ($managed.alive -and $managed.processId) {
        $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
        if (Test-Path -LiteralPath $taskkill -PathType Leaf) {
            try { & $taskkill /PID $managed.processId /T /F *>$null } catch {}
        }
        Stop-Process -Id $managed.processId -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

$statusPath = Join-Path $ProjectDir "devstream\truth\obs-server-keepalive.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $statusPath) | Out-Null
Remove-Item -LiteralPath (Join-Path $ProjectDir "devstream\truth\obs-port-blocked-since.txt") -Force -ErrorAction SilentlyContinue

$beforePort = $false
$beforeState = $false
$probeAttemptsUsed = 0
for ($probeAttempt = 0; $probeAttempt -lt $HealthProbeAttempts; $probeAttempt++) {
    if ($probeAttempt -gt 0) { Start-Sleep -Seconds $HealthProbeIntervalSeconds }
    $probeAttemptsUsed = $probeAttempt + 1
    $beforePort = Test-LocalPort -Port $Port
    $beforeState = if ($beforePort) { Test-HealthEndpoint -Port $Port } else { $false }
    if ($beforePort -and $beforeState) { break }
    if (-not $beforePort -and $probeAttemptsUsed -ge $ClosedPortProbeAttempts) { break }
}

$beforeTaskState = Get-TaskState -TaskName $TaskName
$beforeManaged = Get-ManagedObsServerStatus
$beforeLifecycleHealthy = [bool](
    $beforePort -and
    $beforeState -and
    $beforeTaskState -eq "Running" -and
    $beforeManaged.alive
)
$repairReason = if (-not $beforePort) {
    "port-closed"
} elseif (-not $beforeState) {
    "health-endpoint-failed"
} elseif (-not $beforeManaged.alive) {
    "managed-process-missing"
} elseif ($beforeTaskState -ne "Running") {
    "scheduler-not-owning-process"
} else {
    $null
}
$started = $false
$stoppedStuckTask = $false
$stoppedStuckProcess = $false
$startupGrace = $false
$startError = $null

if (-not $beforeLifecycleHealthy) {
    $withinStartupGrace = (
        $beforeTaskState -eq "Running" -and
        $beforeManaged.alive -and
        $null -ne $beforeManaged.ageSeconds -and
        $beforeManaged.ageSeconds -lt $StartupGraceSeconds
    )
    if ($withinStartupGrace) {
        $startupGrace = $true
        Write-Output ("STARTUP-GRACE: managed OBS server pid {0} is {1:n1}s old; waiting for health before restart." -f $beforeManaged.processId, $beforeManaged.ageSeconds)
    } else {
        try {
            if ($beforeTaskState -eq "Running") {
                Stop-ManagedTask -TaskName $TaskName
                $stoppedStuckTask = $true
                Start-Sleep -Seconds 2
            }
            $managedAfterTaskStop = Get-ManagedObsServerStatus
            $stoppedStuckProcess = [bool]$managedAfterTaskStop.alive
            Stop-ManagedObsServer
            Start-ManagedTask -TaskName $TaskName
            $started = $true
            for ($attempt = 0; $attempt -lt 25; $attempt++) {
                Start-Sleep -Seconds 1
                $afterPortProbe = Test-LocalPort -Port $Port
                $afterHealthProbe = if ($afterPortProbe) { Test-HealthEndpoint -Port $Port } else { $false }
                if ($afterPortProbe -and $afterHealthProbe) { break }
            }
        } catch {
            $startError = $_.Exception.Message
        }
    }
}

$afterPort = Test-LocalPort -Port $Port
$afterState = if ($afterPort) { Test-HealthEndpoint -Port $Port } else { $false }
$afterTaskState = Get-TaskState -TaskName $TaskName
$afterManaged = Get-ManagedObsServerStatus
$afterLifecycleHealthy = [bool](
    $afterPort -and
    $afterState -and
    $afterTaskState -eq "Running" -and
    $afterManaged.alive
)
$ok = $afterLifecycleHealthy
$payload = [ordered]@{
    schemaVersion = "fouler-obs-keepalive/v2"
    checkedAt = (Get-Date).ToUniversalTime().ToString("o")
    taskName = $TaskName
    port = $Port
    startupGraceSeconds = $StartupGraceSeconds
    healthProbeAttempts = $HealthProbeAttempts
    healthProbeIntervalSeconds = $HealthProbeIntervalSeconds
    closedPortProbeAttempts = $ClosedPortProbeAttempts
    probeAttemptsUsed = $probeAttemptsUsed
    before = @{
        portOpen = $beforePort
        healthEndpointOk = $beforeState
        taskState = $beforeTaskState
        managedProcess = $beforeManaged
        lifecycleHealthy = $beforeLifecycleHealthy
    }
    action = @{
        startedTask = $started
        stoppedStuckTask = $stoppedStuckTask
        stoppedStuckProcess = $stoppedStuckProcess
        startupGrace = $startupGrace
        repairReason = $repairReason
        error = $startError
    }
    after = @{
        portOpen = $afterPort
        healthEndpointOk = $afterState
        taskState = $afterTaskState
        managedProcess = $afterManaged
        lifecycleHealthy = $afterLifecycleHealthy
    }
    expectedTransient = [bool]$startupGrace
    ok = [bool]$ok
}

$payload | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $statusPath -Encoding UTF8
if ($startupGrace) { exit 0 }
if (-not $payload.ok) { exit 2 }
exit 0
