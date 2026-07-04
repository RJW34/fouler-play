# HERMES matchup A/B verdict reporter launcher (2026-07-04, claude)
# Runs scripts/analyze_matchup_ab.py (joins logs/matchup_ab_log.jsonl vs
# battle_stats.json) and appends a one-line summary to
# logs\matchup_ab_verdicts.log so the A/B measurement never goes dark.
# No LLM, no code-gen. Single-shot, exits fast.
#
# 2026-07-04 foreman note: when .env has MATCHUP_MEMORY_ENABLED=0 the live bias
# is OFF (disabled 2026-07-04 by foreman - A/B trending harmful: ON 44.4% n=925
# vs OFF 47.5% n=961, z=-1.36). New arm records stop accruing while disabled, so
# the analyzer's numbers are FROZEN HISTORY, not a live measurement. The verdict
# line is annotated accordingly so future readers are not confused.
$ErrorActionPreference = 'SilentlyContinue'
$repo   = 'D:\Projects\fouler-play'
$python = 'D:\Projects\fouler-play\.venv\Scripts\python.exe'
$script = 'D:\Projects\fouler-play\scripts\analyze_matchup_ab.py'
Set-Location $repo

# Detect the live-bias kill switch straight from .env (same file run.py loads).
$biasDisabled = $false
foreach ($envLine in (Get-Content (Join-Path $repo '.env') -ErrorAction SilentlyContinue)) {
    if ($envLine -match '^\s*MATCHUP_MEMORY_ENABLED\s*=\s*([^#\s]+)') {
        $v = $Matches[1].Trim().ToLower()
        $biasDisabled = ($v -in @('0', 'false', 'no', 'off'))
    }
}

$raw = (& $python $script 2>&1) | Out-String
$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$line = $null
try {
    $j = $raw | ConvertFrom-Json
    if ($j.verdict) {
        $line = "$stamp verdict=$($j.verdict) onN=$($j.biasOn.n) onWR=$($j.biasOn.winRate) offN=$($j.biasOff.n) offWR=$($j.biasOff.winRate) delta=$($j.winRateDelta_onMinusOff) z=$($j.zScore) matched=$($j.matchedDecidedBattles)"
    }
} catch {}
if (-not $line) {
    $flat = ($raw -replace "`r?`n", ' ').Trim()
    if ($flat.Length -gt 400) { $flat = $flat.Substring(0, 400) }
    $line = "$stamp analyzer-error: $flat"
}
if ($biasDisabled) {
    $line = "$line :: NOTE bias DISABLED 2026-07-04 by foreman (trending harmful); numbers above are frozen history, not a live A/B"
}
Add-Content -Path (Join-Path $repo 'logs\matchup_ab_verdicts.log') -Value $line -Encoding utf8
exit 0
