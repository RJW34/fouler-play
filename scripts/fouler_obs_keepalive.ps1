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
        $success = $iar.AsyncWaitHandle.WaitOne(3000, $false)
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

function Get-TopLevelObsServerProcesses {
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "streaming[\\/]serve_obs_page\.py" -and
        $_.Name -match "python|py"
    })
    $ids = @{}
    foreach ($p in $all) { $ids[[int]$p.ProcessId] = $true }
    return @($all | Where-Object { -not $ids.ContainsKey([int]$_.ParentProcessId) })
}

function Get-ServerOutputAgeSeconds {
    param([string]$ProjectDir)
    $item = Get-Item -LiteralPath (Join-Path $ProjectDir "logs\jigglypuff-obs-server.log") -ErrorAction SilentlyContinue
    if (-not $item) { return [double]::MaxValue }
    return [double](((Get-Date) - $item.LastWriteTime).TotalSeconds)
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
$blockedSincePath = Join-Path $ProjectDir "devstream\truth\obs-port-blocked-since.txt"

# Wedge-tolerant probe (2026-07-04): the page server's event loop can block for tens of
# seconds (sync work on a loaded box), which a single short connect probe misreads as
# DOWN -- killing a healthy server (observed as PID churn ~every 5 min once this task
# went live under S4U). Require 3 consecutive probe failures ~10s apart before treating
# the server as down; a truly dead server still gets restarted within this same run.
$beforePort = $false
$beforeState = $false
for ($probeAttempt = 0; $probeAttempt -lt 3; $probeAttempt++) {
    if ($probeAttempt -gt 0) { Start-Sleep -Seconds 10 }
    $beforePort = Test-LocalPort -Port $Port
    $beforeState = if ($beforePort) { Test-HealthEndpoint -Port $Port } else { $false }
    if ($beforePort -and $beforeState) { break }
}
$beforeTaskState = Get-TaskState -TaskName $TaskName
$started = $false
$stoppedStuckTask = $false
$stoppedStuckProcess = $false
$startError = $null
$portBlockedButAlive = $false
$portBlockedMinutes = $null
$portBlockedEscalated = $false

if (-not ($beforePort -and $beforeState)) {
    # Victim-hardening (2026-07-04, cross-box interference hunt): "port blocked" is NOT
    # "server dead". Proven today: the 8777 listener socket can die (or the port can be
    # transiently unreachable) while the server PROCESS is alive and its poll loop keeps
    # writing to logs\jigglypuff-obs-server.log. Killing in that state destroys a healthy
    # server (this task's PT1M cadence made it the primary churn engine -- see
    # D:\ClaudeOps\logs\netprobe_wedge_20260704.log / netprobe3_wedge_20260704.log).
    # Gate: process alive + server stdout fresh (<180s) => log LOUDLY, do NOT kill.
    # Escalation: only after >= 15 min of continuous block (dark OBS page, server provably
    # not recovering) does the restart path run. serve_obs_page.py now also self-exits on
    # listener loss, so a truly deaf server becomes "process dead" and restarts promptly.
    $topProcs = @(Get-TopLevelObsServerProcesses)
    $serverOutAgeSec = Get-ServerOutputAgeSeconds -ProjectDir $ProjectDir
    if ($topProcs.Count -ge 1 -and $serverOutAgeSec -lt 180) {
        $now = Get-Date
        $since = $now
        if (Test-Path -LiteralPath $blockedSincePath) {
            try { $since = [datetime]::Parse((Get-Content -LiteralPath $blockedSincePath -First 1)) } catch { $since = $now }
        } else {
            $now.ToString('o') | Set-Content -LiteralPath $blockedSincePath
        }
        $portBlockedMinutes = [math]::Round(($now - $since).TotalMinutes, 1)
        if ($portBlockedMinutes -lt 15) {
            $portBlockedButAlive = $true
            Write-Output ("PORT-BLOCKED-BUT-ALIVE: 8777 port/health down after 3 probes, BUT {0} top-level serve_obs_page proc(s) alive (PIDs {1}) and server stdout fresh ({2:n0}s old). NOT killing a healthy server. Blocked {3} min (escalate at 15)." -f `
                $topProcs.Count, (($topProcs | ForEach-Object { $_.ProcessId }) -join ','), $serverOutAgeSec, $portBlockedMinutes)
        } else {
            $portBlockedEscalated = $true
            Write-Output ("PORT-BLOCKED-ESCALATION: block persisted {0} min with a live process; OBS page dark too long. Allowing one restart." -f $portBlockedMinutes)
            Remove-Item -LiteralPath $blockedSincePath -Force -ErrorAction SilentlyContinue
        }
    } else {
        Remove-Item -LiteralPath $blockedSincePath -Force -ErrorAction SilentlyContinue
    }

    if (-not $portBlockedButAlive) {
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
} else {
    Remove-Item -LiteralPath $blockedSincePath -Force -ErrorAction SilentlyContinue
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
        portBlockedButAlive = $portBlockedButAlive
        portBlockedMinutes = $portBlockedMinutes
        portBlockedEscalated = $portBlockedEscalated
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
if ($portBlockedButAlive) { exit 0 }
if (-not $payload.ok) { exit 2 }
exit 0
