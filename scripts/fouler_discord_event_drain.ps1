$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\Projects\fouler-play"
$LogDir = Join-Path $ProjectRoot "logs"
$LockPath = Join-Path $LogDir "fouler-discord-event-drain.lock"
$LogPath = Join-Path $LogDir "fouler-discord-event-drain.log"
$ErrPath = Join-Path $LogDir "fouler-discord-event-drain.err.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($LockPath, "OpenOrCreate", "ReadWrite", "None")
} catch {
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogPath -Value "$timestamp skip: prior drain still running"
    exit 0
}

try {
    Set-Location $ProjectRoot
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogPath -Value "$timestamp start"

    $python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        $pythonCmd = Get-Command python -ErrorAction Stop
        $python = $pythonCmd.Source
    }

    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $python "infrastructure\event_poster.py" "--once" 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }

    $normalizedOutput = $output | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            $_.ToString()
        } else {
            [string]$_
        }
    }
    Add-Content -Path $LogPath -Value $normalizedOutput

    $deliveryProof = Join-Path $ProjectRoot "devstream\truth\discord-delivery.json"
    if ($exitCode -ne 0 -and (Test-Path $deliveryProof)) {
        try {
            $proof = Get-Content -Raw -Path $deliveryProof | ConvertFrom-Json
            $code = [string]$proof.errorCode
            if ($code -in @("no_pending_events", "stale_backlog_archived", "stale_battle_result_quarantined")) {
                Add-Content -Path $LogPath -Value "maintenance-ok: $code"
                $exitCode = 0
            }
        } catch {
            Add-Content -Path $ErrPath -Value "$timestamp delivery-proof-read-failed: $($_.Exception.Message)"
        }
    }

    $done = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogPath -Value "$done exit=$exitCode"
    exit $exitCode
} catch {
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $ErrPath -Value "$timestamp $($_.Exception.GetType().FullName): $($_.Exception.Message)"
    exit 1
} finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
}
