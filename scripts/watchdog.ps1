# Retired: this watchdog restarted bot_monitor.py outside lease authority.
[Console]::Error.WriteLine(
    "[RETIRED] scripts/watchdog.ps1 is disabled; use the leased battle supervisor task."
)
exit 2
