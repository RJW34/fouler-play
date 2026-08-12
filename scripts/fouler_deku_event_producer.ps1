$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\Projects\fouler-play"
$LogDir = Join-Path $ProjectRoot "logs"
$LockPath = Join-Path $LogDir "fouler-deku-event-producer.lock"
$LogPath = Join-Path $LogDir "fouler-deku-event-producer.log"
$ErrPath = Join-Path $LogDir "fouler-deku-event-producer.err.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($LockPath, "OpenOrCreate", "ReadWrite", "None")
} catch {
    Add-Content -LiteralPath $LogPath -Value "$([DateTime]::UtcNow.ToString('o')) skip: prior producer still running"
    exit 0
}

try {
    Set-Location -LiteralPath $ProjectRoot
    $python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        $python = (Get-Command python.exe -ErrorAction Stop).Source
    }

    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $python "infrastructure\event_poster.py" "--once" 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    $output | ForEach-Object { Add-Content -LiteralPath $LogPath -Value ([string]$_) }

    if ($exitCode -ne 0) {
        $deliveryProof = Join-Path $ProjectRoot "devstream\truth\discord-delivery.json"
        if (Test-Path -LiteralPath $deliveryProof) {
            $proof = Get-Content -Raw -LiteralPath $deliveryProof | ConvertFrom-Json
            if ([string]$proof.errorCode -in @(
                "no_pending_events",
                "stale_backlog_archived",
                "stale_battle_result_quarantined",
                "stale_failed_events_archived"
            )) {
                $exitCode = 0
            }
        }
    }
    Add-Content -LiteralPath $LogPath -Value "$([DateTime]::UtcNow.ToString('o')) exit=$exitCode"
    exit $exitCode
} catch {
    Add-Content -LiteralPath $ErrPath -Value "$([DateTime]::UtcNow.ToString('o')) $($_.Exception.GetType().FullName): $($_.Exception.Message)"
    exit 1
} finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
}
