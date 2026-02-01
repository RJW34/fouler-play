# Auto-fix for Fouler Play issues - Windows Version
# Handles OBS overlay recovery and stream health

$LOG_FILE = "C:\Users\Ryan\projects\BAKUGO\workspace\fouler-play\auto-fix.log"

"=== Auto-Fix Run $(Get-Date) ===" | Tee-Object -FilePath $LOG_FILE -Append

# Check if OBS is running
if (-not (Get-Process -Name "obs64" -ErrorAction SilentlyContinue)) {
    "OBS not running, attempting to start..." | Tee-Object -FilePath $LOG_FILE -Append
    
    # Try to start OBS (adjust path if needed)
    $obsPath = "C:\Program Files\obs-studio\bin\64bit\obs64.exe"
    if (Test-Path $obsPath) {
        Start-Process $obsPath
        "OBS started" | Tee-Object -FilePath $LOG_FILE -Append
    } else {
        "OBS executable not found at $obsPath" | Tee-Object -FilePath $LOG_FILE -Append
    }
}

# Check if overlay browser source needs refresh
# Find OBS window and send refresh hotkey (if configured)
$obs = Get-Process -Name "obs64" -ErrorAction SilentlyContinue
if ($obs) {
    # Check if overlay browser is stuck (no Chrome process for overlay)
    $overlayBrowser = Get-Process -Name "chrome" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*localhost:3001*" }
    
    if (-not $overlayBrowser) {
        "Overlay browser not detected, may need manual refresh in OBS" | Tee-Object -FilePath $LOG_FILE -Append
        # Could add automation here to refresh browser source via OBS websocket API
    }
}

# Check if bot process is running on Linux side (via SSH or check)
# This would require SSH access to ubunztu from Windows
# For now, just log that bot monitoring is Linux-side responsibility
"Bot monitoring handled by Linux diagnostic (ubunztu)" | Tee-Object -FilePath $LOG_FILE -Append

"=== Auto-Fix Complete ===" | Tee-Object -FilePath $LOG_FILE -Append
"" | Out-File -FilePath $LOG_FILE -Append
