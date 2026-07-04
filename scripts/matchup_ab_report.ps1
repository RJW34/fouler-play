# HERMES matchup A/B verdict reporter launcher (2026-07-04, claude)
# Runs scripts/analyze_matchup_ab.py (joins logs/matchup_ab_log.jsonl vs
# battle_stats.json) and appends a one-line summary to
# logs\matchup_ab_verdicts.log so the A/B measurement never goes dark.
# No LLM, no code-gen. Single-shot, exits fast.
$ErrorActionPreference = 'SilentlyContinue'
$repo   = 'D:\Projects\fouler-play'
$python = 'D:\Projects\fouler-play\.venv\Scripts\python.exe'
$script = 'D:\Projects\fouler-play\scripts\analyze_matchup_ab.py'
Set-Location $repo
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
Add-Content -Path (Join-Path $repo 'logs\matchup_ab_verdicts.log') -Value $line -Encoding utf8
exit 0
