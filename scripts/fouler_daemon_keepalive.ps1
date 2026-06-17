# fouler_daemon_keepalive.ps1 - Claude/DEKU 2026-06-16. Relaunch the continuous-ladder
# daemon if it is absent, so a crash / JIGGLY reboot no longer stops laddering permanently
# (the daemon was orphaned under WmiPrvSE with no supervisor). HONORS the stop file so it
# never fights an intentional pause (e.g. an -AutoImprove learning window).
$ErrorActionPreference = 'SilentlyContinue'
$proj = 'D:\Projects\fouler-play'
$daemon = Join-Path $proj 'scripts\fouler_continuous_daemon.ps1'
$stop = Join-Path $proj '.pids\continuous-ladder.stop'
$log = Join-Path $proj 'logs\daemon-keepalive.log'
function say($m) { ("{0} keepalive: {1}" -f (Get-Date -Format o), $m) | Add-Content $log }

if (Test-Path $stop) { say "stop file present -> intentional pause, not relaunching"; exit 0 }
$alive = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" 2>$null | Where-Object { $_.CommandLine -match 'fouler_continuous_daemon' }
if ($alive) { exit 0 }

say "daemon ABSENT -> relaunching"
Start-Process powershell -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $daemon
