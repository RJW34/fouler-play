# Fouler Play Stream Health Check - Windows Version
# Full diagnostic from Windows perspective: OBS, overlay rendering, stream status

$TWITCH_URL = "https://twitch.tv/dekubotbygoofy"
$SHOWDOWN_USER = "dekubotbygoofy"
$LOG_FILE = "C:\Users\Ryan\projects\BAKUGO\workspace\fouler-play\health-check.log"

"=== Fouler Play Health Check $(Get-Date) ===" | Tee-Object -FilePath $LOG_FILE -Append

# 1. Check OBS process
Write-Host -NoNewline "OBS process: "
if (Get-Process -Name "obs64" -ErrorAction SilentlyContinue) {
    "✅ Running" | Tee-Object -FilePath $LOG_FILE -Append
} else {
    "❌ NOT RUNNING" | Tee-Object -FilePath $LOG_FILE -Append
}

# 2. Check if OBS is streaming
Write-Host -NoNewline "OBS streaming: "
# Check OBS stats file or use OBS WebSocket API
# For now, just check process
$obsStats = Get-Process -Name "obs64" -ErrorAction SilentlyContinue | Select-Object CPU, WorkingSet
if ($obsStats -and $obsStats.CPU -gt 1) {
    "✅ Active (CPU: $([math]::Round($obsStats.CPU, 1))%)" | Tee-Object -FilePath $LOG_FILE -Append
} else {
    "⚠️ Low activity or idle" | Tee-Object -FilePath $LOG_FILE -Append
}

# 3. Check Twitch stream status
Write-Host -NoNewline "Twitch live status: "
try {
    $response = Invoke-WebRequest -Uri $TWITCH_URL -UseBasicParsing -TimeoutSec 10
    if ($response.Content -match "isLiveBroadcast") {
        "✅ LIVE" | Tee-Object -FilePath $LOG_FILE -Append
    } else {
        "❌ OFFLINE" | Tee-Object -FilePath $LOG_FILE -Append
    }
} catch {
    "⚠️ Could not check (network issue?)" | Tee-Object -FilePath $LOG_FILE -Append
}

# 4. Check overlay browser source
Write-Host -NoNewline "Overlay browser: "
$overlayBrowser = Get-Process -Name "chrome" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*localhost:3001*" }
if ($overlayBrowser) {
    "✅ Chrome rendering overlay" | Tee-Object -FilePath $LOG_FILE -Append
} else {
    "❌ No Chrome process for overlay" | Tee-Object -FilePath $LOG_FILE -Append
}

# 5. Take screenshot of OBS window
Write-Host -NoNewline "OBS window capture: "
$screenshotPath = "C:\Users\Ryan\projects\BAKUGO\workspace\fouler-play\obs-snapshot-$(Get-Date -Format 'yyyyMMdd-HHmmss').png"
try {
    # Use Windows built-in screenshot capability
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    
    # Find OBS window
    $obs = Get-Process -Name "obs64" -ErrorAction SilentlyContinue
    if ($obs) {
        # Take full screen capture (would need window-specific logic for just OBS)
        $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
        $bitmap.Save($screenshotPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $graphics.Dispose()
        $bitmap.Dispose()
        
        "✅ Saved to $screenshotPath" | Tee-Object -FilePath $LOG_FILE -Append
    } else {
        "⚠️ OBS not running" | Tee-Object -FilePath $LOG_FILE -Append
    }
} catch {
    "⚠️ Screenshot failed: $_" | Tee-Object -FilePath $LOG_FILE -Append
}

# 6. Get current elo
Write-Host -NoNewline "Current elo: "
try {
    $ladderData = Invoke-RestMethod -Uri "https://play.pokemonshowdown.com/~~showdown/action.php?act=ladderget&output=json" -TimeoutSec 5
    $userStats = $ladderData | Where-Object { $_.username -eq $SHOWDOWN_USER }
    $elo = if ($userStats) { $userStats.elo } else { "N/A" }
    "$elo" | Tee-Object -FilePath $LOG_FILE -Append
} catch {
    "N/A (fetch failed)" | Tee-Object -FilePath $LOG_FILE -Append
}

# 7. Check overlay HTTP endpoint
Write-Host -NoNewline "Overlay endpoint: "
try {
    $overlayResponse = Invoke-WebRequest -Uri "http://192.168.1.40:3001/overlay" -UseBasicParsing -TimeoutSec 5
    if ($overlayResponse.StatusCode -eq 200) {
        "✅ Responding (HTTP 200)" | Tee-Object -FilePath $LOG_FILE -Append
    } else {
        "⚠️ Unexpected status: $($overlayResponse.StatusCode)" | Tee-Object -FilePath $LOG_FILE -Append
    }
} catch {
    "❌ Not responding" | Tee-Object -FilePath $LOG_FILE -Append
}

"=== End Health Check ===" | Tee-Object -FilePath $LOG_FILE -Append
"" | Out-File -FilePath $LOG_FILE -Append
