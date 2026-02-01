# Fouler Play Heartbeat Diagnostic - Windows Version
# Checks OBS overlay, stream health, bot status from Windows perspective

$SHOWDOWN_USER = "dekubotbygoofy"
$ELO_CACHE = "C:\Users\Ryan\projects\BAKUGO\workspace\fouler-play\elo-cache.txt"

# Check if OBS is running
$OBS_RUNNING = if (Get-Process -Name "obs64" -ErrorAction SilentlyContinue) { "✅" } else { "❌" }

# Check overlay browser source (look for Chrome rendering overlay)
$OVERLAY_BROWSER = if (Get-Process -Name "chrome" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*localhost:3001*" }) { "✅" } else { "❌" }

# Check if stream is active (via Twitch API)
try {
    $response = Invoke-WebRequest -Uri "https://twitch.tv/dekubotbygoofy" -UseBasicParsing -TimeoutSec 5
    $STREAM_LIVE = if ($response.Content -match "isLiveBroadcast") { "✅" } else { "❌" }
} catch {
    $STREAM_LIVE = "⚠️"
}

# Get current elo from Pokemon Showdown ladder
try {
    $ladderData = Invoke-RestMethod -Uri "https://play.pokemonshowdown.com/~~showdown/action.php?act=ladderget&output=json" -TimeoutSec 5
    $userStats = $ladderData | Where-Object { $_.username -eq $SHOWDOWN_USER }
    $CURRENT_ELO = if ($userStats) { $userStats.elo } else { "N/A" }
} catch {
    $CURRENT_ELO = "N/A"
}

# Load previous elo and calculate change
$PREV_ELO = "N/A"
if (Test-Path $ELO_CACHE) {
    $PREV_ELO = Get-Content $ELO_CACHE
}

# Save current elo
$CURRENT_ELO | Out-File -FilePath $ELO_CACHE -NoNewline

# Calculate elo change
$ELO_CHANGE = ""
if ($CURRENT_ELO -ne "N/A" -and $PREV_ELO -ne "N/A") {
    $DIFF = [int]$CURRENT_ELO - [int]$PREV_ELO
    if ($DIFF -gt 0) {
        $ELO_CHANGE = " (+$DIFF)"
    } elseif ($DIFF -lt 0) {
        $ELO_CHANGE = " ($DIFF)"
    }
}

# Output compact status
Write-Output "OBS: $OBS_RUNNING | Overlay: $OVERLAY_BROWSER | Stream: $STREAM_LIVE | Elo: ${CURRENT_ELO}${ELO_CHANGE}"

# If OBS or overlay is down, trigger auto-fix
if ($OBS_RUNNING -eq "❌" -or $OVERLAY_BROWSER -eq "❌") {
    & "C:\Users\Ryan\projects\BAKUGO\workspace\fouler-play\auto-fix.ps1"
}
