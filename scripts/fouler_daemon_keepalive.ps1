# Legacy scheduled-task entry point. The old continuous-ladder daemon is no
# longer the authority; bounded HERMES supervisor cycles are. Keep this file so
# the existing scheduled task remains useful, but delegate to the mission
# monitor that refreshes truth, opens tickets, and safely restarts one bounded
# supervisor when rails allow it.
$ErrorActionPreference = 'SilentlyContinue'
$proj = 'D:\Projects\fouler-play'
$monitor = Join-Path $proj 'scripts\fouler_mission_monitor_task.ps1'
$log = Join-Path $proj 'logs\daemon-keepalive.log'
function say($m) { ("{0} keepalive: {1}" -f (Get-Date -Format o), $m) | Add-Content -LiteralPath $log -Encoding ASCII }

if (-not (Test-Path -LiteralPath $monitor -PathType Leaf)) {
    say "mission monitor wrapper missing -> cannot delegate"
    exit 1
}

say "delegating to HERMES Fouler mission monitor"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $monitor
exit 0
