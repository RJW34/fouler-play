param(
    [int]$Battles = 30,
    [int]$Concurrent = 3
)

# Load .env
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), 'Process')
        }
    }
}

$username = $env:PS_USERNAME ?? "npctypebeat"
$password = $env:PS_PASSWORD ?? "npctypebeat"
$wsUri = $env:PS_WEBSOCKET_URI ?? "wss://sim3.psim.us/showdown/websocket"
$format = $env:PS_FORMAT ?? "gen9ou"
$teams = $env:TEAM_NAMES ?? "gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-pivot,gen9/ou/fat-team-3-dondozo"
$mcts = $env:MAX_MCTS_BATTLES ?? "3"

Write-Host ""
Write-Host "=== FOULER-PLAY BATCH ===" -ForegroundColor Cyan
Write-Host "  Battles: $Battles ($([math]::Floor($Battles / 3)) per team)"
Write-Host "  Concurrent: $Concurrent"
Write-Host "  Account: $username"
Write-Host "  Format: $format"
Write-Host "  Teams: $teams"
Write-Host ""

$discordWebhook = $env:DISCORD_BATTLES_WEBHOOK_URL

# Run the bot
$process = Start-Process python -ArgumentList @(
    "run.py",
    "--websocket-uri", $wsUri,
    "--ps-username", $username,
    "--ps-password", $password,
    "--bot-mode", "search_ladder",
    "--pokemon-format", $format,
    "--run-count", $Battles,
    "--max-concurrent-battles", $Concurrent,
    "--max-mcts-battles", $mcts,
    "--team-names", $teams,
    "--save-replay", "always",
    "--log-level", "DEBUG",
    "--log-to-file",
    "--search-parallelism", "1"
) -WorkingDirectory $PSScriptRoot -PassThru -NoNewWindow -Wait

$exitCode = $process.ExitCode

# Read battle stats for round summary
$statsFile = Join-Path $PSScriptRoot "battle_stats.json"
$summary = ""

if (Test-Path $statsFile) {
    try {
        $stats = Get-Content $statsFile -Raw | ConvertFrom-Json
        $battles = $stats.battles

        $wins = ($battles | Where-Object { $_.result -eq "win" }).Count
        $losses = ($battles | Where-Object { $_.result -eq "loss" }).Count
        $disconnects = ($battles | Where-Object { $_.result -eq "disconnect" }).Count
        $total = $battles.Count
        $winRate = if ($total -gt 0) { [math]::Round(($wins / $total) * 100, 1) } else { 0 }

        # Per-team breakdown
        $teamStats = $battles | Group-Object -Property team_file | ForEach-Object {
            $tw = ($_.Group | Where-Object { $_.result -eq "win" }).Count
            $tl = ($_.Group | Where-Object { $_.result -eq "loss" }).Count
            $td = ($_.Group | Where-Object { $_.result -eq "disconnect" }).Count
            "$($_.Name): ${tw}W/${tl}L/${td}DC"
        }

        $summary = @"
**ROUND COMPLETE** — $total battles, ${winRate}% win rate
W: $wins | L: $losses | DC: $disconnects
$($teamStats -join " | ")
"@

        Write-Host ""
        Write-Host "=== ROUND COMPLETE ===" -ForegroundColor Green
        Write-Host "  Total: $total battles"
        Write-Host "  Wins: $wins | Losses: $losses | Disconnects: $disconnects"
        Write-Host "  Win rate: ${winRate}%"
        Write-Host "  Per team: $($teamStats -join ', ')"
    } catch {
        $summary = "**ROUND COMPLETE** — batch finished but could not parse stats: $_"
        Write-Host "Failed to parse battle stats: $_" -ForegroundColor Red
    }
} else {
    $summary = "**ROUND COMPLETE** — batch finished but no battle_stats.json found"
}

# Post to Discord via openclaw
if ($summary) {
    Write-Host ""
    Write-Host "Posting summary to Discord..." -ForegroundColor Yellow
    & openclaw message send --channel discord --target 1466691161363054840 --text $summary 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Posted to #fouler-play" -ForegroundColor Green
    } else {
        Write-Host "Discord post failed (gateway may be down)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Batch complete. Exit code: $exitCode" -ForegroundColor Cyan
