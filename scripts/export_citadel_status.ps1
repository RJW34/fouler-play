$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$outputPath = Join-Path $repoRoot 'state\citadel_status.json'

function Read-Json([string] $Path) {
    if (-not (Test-Path $Path)) { return $null }
    try { return Get-Content $Path -Raw | ConvertFrom-Json } catch { return $null }
}

function Get-IsoStamp([string] $Path) {
    if (Test-Path $Path) { return (Get-Item $Path).LastWriteTimeUtc.ToString('o') }
    return ''
}

function New-Signal([string] $Name, [string] $Label, [object] $Value, [string] $Status = 'neutral') {
    return [ordered]@{ name = $Name; label = $Label; value = [string] $Value; status = $Status }
}

function New-Artifact([string] $Label, [string] $RelativePath, [string] $Kind = '') {
    $fullPath = Join-Path $repoRoot $RelativePath
    return [ordered]@{ label = $Label; path = $RelativePath; updated_at = Get-IsoStamp $fullPath; kind = $Kind }
}

$now = (Get-Date).ToUniversalTime().ToString('o')
$streamStatus = Read-Json (Join-Path $repoRoot 'stream_status.json')
$dailyStats = Read-Json (Join-Path $repoRoot 'daily_stats.json')
$battleStats = Read-Json (Join-Path $repoRoot 'battle_stats.json')
$stabilityReport = Read-Json (Join-Path $repoRoot 'stability_report.json')

$battles = @()
if ($battleStats -and $battleStats.battles) { $battles = @($battleStats.battles) }
$totalBattles = $battles.Count
$lastBattleAt = if ($totalBattles -gt 0) { [string] $battles[-1].timestamp } else { '' }
$streamUpdated = if ($streamStatus -and $streamStatus.updated) { [string] $streamStatus.updated } else { '' }
$runtimeUpdated = if ($streamUpdated) { $streamUpdated } elseif ($stabilityReport -and $stabilityReport.generated_at) { [string] $stabilityReport.generated_at } else { $lastBattleAt }

$ageHours = $null
if ($runtimeUpdated) {
    try {
        $ageHours = [math]::Round(((Get-Date).ToUniversalTime() - ([DateTimeOffset]::Parse($runtimeUpdated).UtcDateTime)).TotalHours, 1)
    } catch {
        $ageHours = $null
    }
}

$status = if ($streamStatus -and $streamStatus.status) { 'active' } elseif ($runtimeUpdated) { 'stale' } else { 'idle' }
$health = if ($ageHours -ne $null -and $ageHours -le 72) {
    'warning'
} elseif ($runtimeUpdated) {
    'degraded'
} else {
    'warning'
}
$headline = if ($streamStatus -and $streamStatus.status) {
    "$($streamStatus.status) | $totalBattles recorded battles | next fix: $($streamStatus.next_fix)"
} else {
    'No fresh stream status is available; only historical battle and stability artifacts are present.'
}

$stabilityText = ''
if ($stabilityReport -and $stabilityReport.stability -and $stabilityReport.stability.health) {
    $stabilityText = [string] $stabilityReport.stability.health
}

$signals = @(
    (New-Signal 'runtime_age_hours' 'Runtime age (hours)' ($(if ($ageHours -ne $null) { $ageHours } else { 'missing' })) $(if ($ageHours -ne $null -and $ageHours -le 72) { 'warn' } else { 'bad' })),
    (New-Signal 'stream_status' 'Stream status' ($(if ($streamStatus) { $streamStatus.status } else { 'missing' })) $(if ($streamStatus -and $streamStatus.status -eq 'Searching') { 'good' } else { 'warn' })),
    (New-Signal 'daily_record' 'Today record' ($(if ($dailyStats) { "$($dailyStats.wins)-$($dailyStats.losses)" } else { 'missing' })) 'neutral'),
    (New-Signal 'battle_total' 'Recorded battles' $totalBattles $(if ($totalBattles -gt 0) { 'good' } else { 'warn' })),
    (New-Signal 'stability' 'Stability report' ($(if ($stabilityText) { $stabilityText } else { 'missing' })) $(if ($stabilityText) { 'warn' } else { 'neutral' }))
)

$artifacts = @(
    (New-Artifact 'Stream status' 'stream_status.json' 'state'),
    (New-Artifact 'Battle stats' 'battle_stats.json' 'state'),
    (New-Artifact 'Stability report' 'stability_report.json' 'report'),
    (New-Artifact 'Taskboard' 'TASKBOARD.md' 'documentation')
)

$payload = [ordered]@{
    schema_version = 1
    project_id = 'fouler-play'
    generated_at = $now
    updated_at = $runtimeUpdated
    status = $status
    health = $health
    headline = $headline
    signals = $signals
    artifacts = $artifacts
    details = [ordered]@{
        stream_status = $streamStatus
        daily_stats = $dailyStats
        total_battles = $totalBattles
        last_battle_at = $lastBattleAt
        stability_report = $stabilityReport
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path $outputPath -Parent) | Out-Null
$json = $payload | ConvertTo-Json -Depth 8
Set-Content -Path $outputPath -Value $json -Encoding utf8
$json
