param(
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$TaskName = "HERMES-FoulerObsServer",
    [int]$Port = 8777
)

$ErrorActionPreference = "Stop"

function Test-LocalPort {
    param([int]$Port)
    try {
        $client = New-Object Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $success = $iar.AsyncWaitHandle.WaitOne(1000, $false)
        if ($success) { $client.EndConnect($iar) }
        $client.Close()
        return [bool]$success
    } catch {
        return $false
    }
}

function Test-HealthEndpoint {
    param([int]$Port)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$Port/health"
        return [bool]($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-TaskState {
    param([string]$TaskName)
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        return [string]$task.State
    } catch {
        return "Unknown"
    }
}

function Stop-ObsServerProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "streaming[\\/]serve_obs_page\.py" -and
        $_.Name -match "python|py"
    } | ForEach-Object {
        $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
        if (Test-Path -LiteralPath $taskkill -PathType Leaf) {
            try { & $taskkill /PID $_.ProcessId /T /F *>$null } catch {}
        }
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath (Join-Path $ProjectDir ".pids\obs_server.pid") -Force -ErrorAction SilentlyContinue
}

$statusPath = Join-Path $ProjectDir "devstream\truth\obs-server-keepalive.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $statusPath) | Out-Null

$beforePort = Test-LocalPort -Port $Port
$beforeState = if ($beforePort) { Test-HealthEndpoint -Port $Port } else { $false }
$beforeTaskState = Get-TaskState -TaskName $TaskName
$started = $false
$stoppedStuckTask = $false
$stoppedStuckProcess = $false
$startError = $null

if (-not ($beforePort -and $beforeState)) {
    try {
        if ($beforeTaskState -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName
            $stoppedStuckTask = $true
            Start-Sleep -Seconds 2
        }
        Stop-ObsServerProcesses
        $stoppedStuckProcess = $true
        Start-ScheduledTask -TaskName $TaskName
        $started = $true
        Start-Sleep -Seconds 15
    } catch {
        $startError = $_.Exception.Message
    }
}

$afterPort = Test-LocalPort -Port $Port
$afterState = if ($afterPort) { Test-HealthEndpoint -Port $Port } else { $false }
$afterTaskState = Get-TaskState -TaskName $TaskName
$ok = if ((-not $started) -and $beforePort -and $beforeState) {
    $true
} else {
    [bool]($afterPort -and $afterState)
}
$payload = [ordered]@{
    schemaVersion = "fouler-obs-keepalive/v1"
    checkedAt = (Get-Date).ToUniversalTime().ToString("o")
    taskName = $TaskName
    port = $Port
    before = @{
        portOpen = $beforePort
        healthEndpointOk = $beforeState
        taskState = $beforeTaskState
    }
    action = @{
        startedTask = $started
        stoppedStuckTask = $stoppedStuckTask
        stoppedStuckProcess = $stoppedStuckProcess
        error = $startError
    }
    after = @{
        portOpen = $afterPort
        healthEndpointOk = $afterState
        taskState = $afterTaskState
    }
    ok = [bool]$ok
}

$payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statusPath -Encoding UTF8
if (-not $payload.ok) { exit 2 }
exit 0
