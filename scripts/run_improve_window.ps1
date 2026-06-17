# run_improve_window.ps1 - Claude/DEKU 2026-06-16. RECREATED (tracked this time): the
# original was an UNTRACKED Codex artifact that was lost, so the scheduled task
# Claude-FoulerImproveLoop failed 0x3 (path-not-found) and got disabled -> fouler's
# learning loop went dormant since 6/14.
#
# DEFAULT (no -AutoImprove) = DRY-RUN verification. Ladder-safe: improve_agent.py
# --dry-run needs NO runtime lease and makes NO file change/commit. It analyzes recent
# battles and logs the top issue + target file. This is what the scheduled task runs.
#
# -AutoImprove = real learning cycle (opt-in). Cleanly PAUSE the continuous-ladder daemon
# via its stop file, wait for the round + WS connection to clear, write a bounded runtime
# lease, run improve_agent.py --enable-auto-improve --max-cycles 1 (Claude writes ONE fix
# -> offline_eval LCB>0.50 gate -> commit; the deploy-spacing gate + elo_watchdog protect
# the ladder), then ALWAYS RESUME (relaunch the daemon) even on error.
param(
  [int]$Battles = 40,
  [double]$MinFreeGB = 3.5,
  [switch]$AutoImprove,
  [int]$LeaseMinutes = 30
)
$ErrorActionPreference = 'Continue'
$proj   = 'D:\Projects\fouler-play'
Set-Location $proj
$py     = Join-Path $proj '.venv\Scripts\python.exe'
$log    = Join-Path $proj 'logs\improve_window.log'
$stop   = Join-Path $proj '.pids\continuous-ladder.stop'
$daemon = Join-Path $proj 'scripts\fouler_continuous_daemon.ps1'
function say($m) { $l = ("{0} [window] {1}" -f (Get-Date -Format o), $m); $l | Add-Content $log; Write-Output $l }
function Get-LadderPids { (Get-CimInstance Win32_Process -Filter "Name='python.exe'" 2>$null | Where-Object { $_.CommandLine -match 'run\.py' -and $_.CommandLine -match 'search_ladder' }).ProcessId }
function Get-WsCount($p) { if (-not $p) { return 0 }; (Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object { $_.RemotePort -eq 443 -and $p -contains $_.OwningProcess } | Measure-Object).Count }
function Start-Daemon { Start-Process powershell -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $daemon | Out-Null }

say "=== improve-window START (AutoImprove=$($AutoImprove.IsPresent) Battles=$Battles MinFreeGB=$MinFreeGB) ==="

# RAM gate (proven design: never run under memory pressure -> would risk OOM vs the live ladder)
$freeGB = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)
say "free RAM = $freeGB GB (require >= $MinFreeGB)"
if ($freeGB -lt $MinFreeGB) { say "STOP: insufficient free RAM; not disrupting the live ladder."; exit 0 }

if (-not $AutoImprove) {
  say "DRY-RUN verification (ladder-safe, no lease/commit): analyzing recent battles."
  $out = Join-Path $proj 'logs\improve_window_dryrun.out.txt'
  & $py 'infrastructure\improve_agent.py' '--dry-run' *> $out
  Get-Content $out -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '\[AGENT\] (Top issue|Target file|Evidence|No autoresearch|Deferring|BLOCKED|Current ELO|DRY RUN)' } |
    ForEach-Object { say ("  " + $_.Trim()) }
  say "DRY-RUN done (ladder untouched)."
  exit 0
}

# ---- AUTO-IMPROVE (opt-in) ----
$daemonWasRunning = [bool](Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" 2>$null | Where-Object { $_.CommandLine -match 'fouler_continuous_daemon' })
say "AUTO-IMPROVE: pausing ladder (daemonRunning=$daemonWasRunning) via stop file."
New-Item -ItemType File -Path $stop -Force | Out-Null
$deadline = (Get-Date).AddMinutes(4)
while ((Get-Date) -lt $deadline) { Start-Sleep -Seconds 10; if ((Get-WsCount (Get-LadderPids)) -eq 0) { break } }
$ws = Get-WsCount (Get-LadderPids)
say "ladder WS after pause = $ws"
if ($ws -ne 0) {
  say "ABORT: ladder did not pause in time -> resuming, no improve this window."
  Remove-Item $stop -Force -ErrorAction SilentlyContinue
  if ($daemonWasRunning) { Start-Daemon }
  exit 0
}
try {
  $lease = 'devstream\truth\runtime-lease.json'
  say "writing runtime lease ($LeaseMinutes min) for improve-agent."
  & $py 'scripts\devstream_runtime_lease.py' '--purpose' 'improve-agent' '--write' '--machine' 'JIGGLYPUFF' '--max-cycles' '1' '--valid-minutes' "$LeaseMinutes" '--approved' '--runtime-lease' $lease *>&1 | Add-Content $log
  say "running improve_agent.py --enable-auto-improve --max-cycles 1."
  $autoOut = Join-Path $proj 'logs\improve_window_auto.out.txt'
  & $py 'infrastructure\improve_agent.py' '--enable-auto-improve' '--max-cycles' '1' '--runtime-lease' $lease *> $autoOut
  Get-Content $autoOut -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '\[AGENT\] (Top issue|Target file|ACCEPT|reject|Recorded deploy|Deferring|BLOCKED|committed|No autoresearch)' } |
    ForEach-Object { say ("  " + $_.Trim()) }
  say "improve cycle complete."
}
finally {
  say "RESUME: clearing stop file + relaunching ladder daemon (always, even on error)."
  Remove-Item $stop -Force -ErrorAction SilentlyContinue
  Start-Daemon
}
say "=== improve-window END ==="
exit 0
