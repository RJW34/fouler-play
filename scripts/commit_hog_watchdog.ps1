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
# Threshold: 6 GB default (raised from 4 on 2026-07-04: the CamCraft server
# JVM's normal steady state is ~4.7 GB commit with -Xmx4G, which false-alerted
# every 15 minutes). Override with env COMMIT_HOG_THRESHOLD_GB.
# Per-process allowlist: env COMMIT_HOG_ALLOW takes comma-separated "name:gb"
# pairs (e.g. "java:6,obs64:8"); a matching process name (case-insensitive,
# ".exe" suffix tolerated) uses that per-name threshold INSTEAD of the global
# one, so a known-large JVM can be tolerated without raising the global bar
# (a java at 5 GB is fine under "java:6"; a java at 7 GB still alerts).
# Discord damping: the same PID is re-queued to Discord at most every 6 hours
# unless its commit grew >= 25% since the last queued alert (state in
# commit-hog-state.json). The alert LOG line is written on every detection.
# Every run overwrites commit-hog-watchdog.last.json so a clean pass is
# provable (age-honest: the watchdog shows it ran, not just when it alarmed).
#
# Discord enqueue goes through scripts\commit_hog_enqueue.py (a checked-in
# helper, inputs passed via env vars). Never use a `python -c` one-liner here:
# PowerShell 5.1 native-arg quoting mangled the original multi-line -c payload
# into a SyntaxError ('File "<string>", line 4' on 2026-07-04T09:37:31Z).

$ErrorActionPreference = 'Stop'

$repo      = 'D:\Projects\fouler-play'
$alertDir  = 'D:\ops-alerts'
$alertLog  = Join-Path $alertDir 'commit-hog-alerts.log'
$stateFile = Join-Path $alertDir 'commit-hog-state.json'
$lastFile  = Join-Path $alertDir 'commit-hog-watchdog.last.json'
$python    = Join-Path $repo '.venv\Scripts\python.exe'
$enqueuePy = Join-Path $repo 'scripts\commit_hog_enqueue.py'

New-Item -ItemType Directory -Force -Path $alertDir | Out-Null

$thresholdGb = 6.0
if ($env:COMMIT_HOG_THRESHOLD_GB) {
    $parsed = 0.0
    if ([double]::TryParse($env:COMMIT_HOG_THRESHOLD_GB, [ref]$parsed) -and $parsed -gt 0) {
        $thresholdGb = $parsed
    }
}

# ---- per-process allowlist (COMMIT_HOG_ALLOW="name:gb,name:gb") ----
$allowMap = @{}
if ($env:COMMIT_HOG_ALLOW) {
    foreach ($pair in ($env:COMMIT_HOG_ALLOW -split ',')) {
        $trimmed = $pair.Trim()
        if (-not $trimmed) { continue }
        $parts = $trimmed -split ':', 2
        if ($parts.Count -ne 2) { continue }
        $name = $parts[0].Trim()
        if ($name.ToLower().EndsWith('.exe')) { $name = $name.Substring(0, $name.Length - 4) }
        $gb = 0.0
        if ($name -and [double]::TryParse($parts[1].Trim(), [ref]$gb) -and $gb -gt 0) {
            $allowMap[$name.ToLower()] = $gb
        }
    }
}

function Get-EffectiveThresholdGb([string]$procName) {
    $key = ([string]$procName).ToLower()
    if ($allowMap.ContainsKey($key)) { return [double]$allowMap[$key] }
    return $thresholdGb
}

$nowUtc = (Get-Date).ToUniversalTime()
$nowIso = $nowUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')

$hogs = @(Get-Process |
    ForEach-Object {
        $limitGb = Get-EffectiveThresholdGb $_.ProcessName
        if ($_.PagedMemorySize64 -gt [long]($limitGb * 1GB)) {
            [pscustomobject]@{
                name        = $_.ProcessName
                procId      = $_.Id
                commitGb    = [math]::Round($_.PagedMemorySize64 / 1GB, 1)
                thresholdGb = $limitGb
            }
        }
    } |
    Sort-Object commitGb -Descending)

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
    Add-Content -Path $alertLog -Value ('{0} ALERT {1} (pid {2}) commit {3} GB > threshold {4} GB (alert-only; nothing was killed)' -f $nowIso, $hog.name, $hog.procId, $hog.commitGb, $hog.thresholdGb)

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
    $hogText = ($toQueue | ForEach-Object { '{0} (pid {1}): {2} GB commit > {3} GB limit' -f $_.name, $_.procId, $_.commitGb, $_.thresholdGb }) -join '; '
    $content = ('[OPS ALERT] JIGGLYPUFF commit-hog watchdog: {0} process(es) over their commit threshold (default {1} GB): {2}. ' -f $toQueue.Count, $thresholdGb, $hogText) +
        'On 2026-06-29 this box crashed from system commit exhaustion when a resident powershell.exe reached ~43-55 GB commit (Resource-Exhaustion-Detector 2004). ' +
        ('This watchdog is alert-only and never kills; investigate the process. Log: {0}' -f $alertLog)
    # Inputs go through env vars; the python lives in a checked-in helper file
    # (scripts\commit_hog_enqueue.py) so PowerShell never has to quote/escape
    # python source. `python -c` one-liners are banned in this script: PS 5.1
    # does not re-escape embedded double quotes when passing arguments to
    # native executables, which mangled the original multi-line -c payload
    # into a SyntaxError.
    $env:FOULER_OPS_ALERT_CONTENT = $content
    $env:FOULER_REPO = $repo
    try {
        if (-not (Test-Path $enqueuePy)) { throw ('enqueue helper missing: {0}' -f $enqueuePy) }
        $pyOut = @(& $python $enqueuePy 2>&1 | ForEach-Object { [string]$_ })
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
    allowlist      = $allowMap
    hogCount       = $hogs.Count
    hogs           = $hogs
    discordQueued  = ($toQueue.Count -gt 0 -and -not $queueError)
    discordEventId = $eventId
    queueError     = $queueError
    killsPerformed = 0
    note           = 'alert-only watchdog; it never kills processes'
} | ConvertTo-Json -Depth 4 | Set-Content -Path $lastFile -Encoding UTF8

exit 0
