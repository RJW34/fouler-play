# HERMES-CommitHogWatchdog (2026-07-04, claude)
# On 2026-06-29 this box crashed from system commit exhaustion: a resident
# powershell.exe grew to 42.8-55 GB commit charge until Resource-Exhaustion-
# Detector (Event ID 2004) fired and the machine went down. This watchdog
# lists processes whose commit size (PagedMemorySize64 / task-manager
# "Commit size") exceeds a threshold and ALERTS:
#   * appends a dated line per hog to D:\ops-alerts\commit-hog-alerts.log
#   * queues an "ops_alert" event through infrastructure\event_queue_lib
#     (the same queue battle_result events use; drained to Discord by
#     infrastructure\event_poster.py via HERMES-FoulerDiscordEventDrain)
# IT NEVER KILLS ANYTHING. Alert-only by design.
#
# Threshold: 4 GB default; override with env COMMIT_HOG_THRESHOLD_GB.
# Discord damping: the same PID is re-queued to Discord at most every 6 hours
# unless its commit grew >= 25% since the last queued alert (state in
# commit-hog-state.json). The alert LOG line is written on every detection.
# Every run overwrites commit-hog-watchdog.last.json so a clean pass is
# provable (age-honest: the watchdog shows it ran, not just when it alarmed).

$ErrorActionPreference = 'Stop'

$repo      = 'D:\Projects\fouler-play'
$alertDir  = 'D:\ops-alerts'
$alertLog  = Join-Path $alertDir 'commit-hog-alerts.log'
$stateFile = Join-Path $alertDir 'commit-hog-state.json'
$lastFile  = Join-Path $alertDir 'commit-hog-watchdog.last.json'
$python    = Join-Path $repo '.venv\Scripts\python.exe'

New-Item -ItemType Directory -Force -Path $alertDir | Out-Null

$thresholdGb = 4.0
if ($env:COMMIT_HOG_THRESHOLD_GB) {
    $parsed = 0.0
    if ([double]::TryParse($env:COMMIT_HOG_THRESHOLD_GB, [ref]$parsed) -and $parsed -gt 0) {
        $thresholdGb = $parsed
    }
}
$thresholdBytes = [long]($thresholdGb * 1GB)

$nowUtc = (Get-Date).ToUniversalTime()
$nowIso = $nowUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')

$hogs = @(Get-Process |
    Where-Object { $_.PagedMemorySize64 -gt $thresholdBytes } |
    Sort-Object PagedMemorySize64 -Descending |
    ForEach-Object {
        [pscustomobject]@{
            name     = $_.ProcessName
            procId   = $_.Id
            commitGb = [math]::Round($_.PagedMemorySize64 / 1GB, 1)
        }
    })

# ---- load Discord damping state (per name:pid) ----
$state = @{}
if (Test-Path $stateFile) {
    try {
        $rawState = Get-Content -Raw -Path $stateFile | ConvertFrom-Json
        foreach ($prop in $rawState.PSObject.Properties) { $state[$prop.Name] = $prop.Value }
    } catch { $state = @{} }
}

$toQueue = @()
foreach ($hog in $hogs) {
    # Alert log line on EVERY detection (append-only, dated).
    Add-Content -Path $alertLog -Value ('{0} ALERT {1} (pid {2}) commit {3} GB > threshold {4} GB (alert-only; nothing was killed)' -f $nowIso, $hog.name, $hog.procId, $hog.commitGb, $thresholdGb)

    $key = '{0}:{1}' -f $hog.name, $hog.procId
    $queueIt = $true
    $prev = $state[$key]
    if ($prev) {
        try {
            $lastAt = ([datetime]::Parse([string]$prev.lastQueuedUtc)).ToUniversalTime()
            $ageHours = ($nowUtc - $lastAt).TotalHours
            $prevGb = [double]$prev.commitGb
            $grew = ($prevGb -gt 0) -and (($hog.commitGb / $prevGb) -ge 1.25)
            if ($ageHours -lt 6 -and -not $grew) { $queueIt = $false }
        } catch { $queueIt = $true }
    }
    if ($queueIt) {
        $toQueue += $hog
        $state[$key] = @{ lastQueuedUtc = $nowUtc.ToString('o'); commitGb = $hog.commitGb }
    }
}

# Prune state entries for processes that are no longer hogs.
$liveKeys = @($hogs | ForEach-Object { '{0}:{1}' -f $_.name, $_.procId })
foreach ($key in @($state.Keys)) {
    if ($liveKeys -notcontains $key) { $state.Remove($key) }
}

$eventId = $null
$queueError = $null
if ($toQueue.Count -gt 0) {
    $hogText = ($toQueue | ForEach-Object { '{0} (pid {1}): {2} GB commit' -f $_.name, $_.procId, $_.commitGb }) -join '; '
    $content = ('[OPS ALERT] JIGGLYPUFF commit-hog watchdog: {0} process(es) over the {1} GB commit threshold: {2}. ' -f $toQueue.Count, $thresholdGb, $hogText) +
        'On 2026-06-29 this box crashed from system commit exhaustion when a resident powershell.exe reached ~43-55 GB commit (Resource-Exhaustion-Detector 2004). ' +
        ('This watchdog is alert-only and never kills; investigate the process. Log: {0}' -f $alertLog)
    # Inputs go through env vars and the python snippet stays single-line with
    # single quotes only: PowerShell 5.1 does not re-escape embedded double
    # quotes when passing arguments to native executables, which mangles any
    # multi-line/double-quoted -c payload into a SyntaxError.
    $env:FOULER_OPS_ALERT_CONTENT = $content
    $env:FOULER_REPO = $repo
    $pyCode = "import os, sys; sys.path.insert(0, os.environ['FOULER_REPO']); from infrastructure.event_queue_lib import queue_event; print(queue_event('ops_alert', 'project', os.environ['FOULER_OPS_ALERT_CONTENT'], dedup_window_sec=3600) or 'deduped')"
    try {
        $pyOut = @(& $python -c $pyCode 2>&1 | ForEach-Object { [string]$_ })
        if ($LASTEXITCODE -ne 0) {
            $queueError = 'queue_event exited {0}: {1}' -f $LASTEXITCODE, ($pyOut -join ' | ')
        } else {
            $eventId = $pyOut[-1]
        }
    } catch {
        $queueError = $_.Exception.Message
    }
    if ($queueError) {
        Add-Content -Path $alertLog -Value ('{0} WARN Discord queue_event failed: {1}' -f $nowIso, $queueError)
    } else {
        Add-Content -Path $alertLog -Value ('{0} INFO queued Discord ops_alert id={1} for: {2}' -f $nowIso, $eventId, $hogText)
    }
}

# ---- persist damping state + last-run proof ----
$state | ConvertTo-Json -Depth 4 | Set-Content -Path $stateFile -Encoding UTF8
[pscustomobject]@{
    checkedAtUtc   = $nowIso
    thresholdGb    = $thresholdGb
    hogCount       = $hogs.Count
    hogs           = $hogs
    discordQueued  = ($toQueue.Count -gt 0 -and -not $queueError)
    discordEventId = $eventId
    queueError     = $queueError
    killsPerformed = 0
    note           = 'alert-only watchdog; it never kills processes'
} | ConvertTo-Json -Depth 4 | Set-Content -Path $lastFile -Encoding UTF8

exit 0
