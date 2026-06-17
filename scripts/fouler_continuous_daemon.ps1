# Fouler continuous single-cycle ladder daemon (dup-safe) + INTEGRATED LEARNING WINDOW.
# Runs 30-battle single-cycle rounds back-to-back, NEVER overlapping: it only
# launches a new round when no supervisor is running, so run.py's per-account
# lock + the single-WS invariant hold (verified: 1 Showdown WS = single client).
# The MULTI-cycle (--max-cycles>1) path re-execs into a venv<->sys ping-pong
# churn, so we deliberately loop --max-cycles 1 rounds externally instead.
#
# LEARNING (Claude/DEKU 2026-06-16): at the natural pre-round gap (prev round done,
# next not launched -> lease free + WS=0) the daemon runs a bounded improve cycle on a
# cadence. Kill-switch via .pids\learn-mode (off|dryrun|auto, default off). AUTO snapshots
# the dirty tree (git stash create) and restores it on a timeout-kill so a half-applied
# (uncommitted) fix never goes live. Safety nets: elo_watchdog (live, local-reverts a
# regressing deploy), daemon keepalive (recovers a crash), improve_agent's own
# lease+deploy-spacing+offline_eval LCB>0.50 gates.
$ErrorActionPreference = 'SilentlyContinue'
$proj = 'D:\Projects\fouler-play'
Set-Location $proj
$stop = Join-Path $proj '.pids\continuous-ladder.stop'
$log  = Join-Path $proj 'logs\continuous-ladder-daemon.log'
$py   = Join-Path $proj '.venv\Scripts\python.exe'
Remove-Item $stop -Force -ErrorAction SilentlyContinue
("{0} continuous-ladder daemon START (pid {1})" -f (Get-Date -Format o), $PID) | Add-Content $log

function Invoke-LearningWindow {
  # Runs only in the pre-round gap (laddering paused). Returns quickly when not due / off.
  try {
    $modeFile = Join-Path $proj '.pids\learn-mode'
    $mode = if (Test-Path $modeFile) { (Get-Content $modeFile -Raw).Trim().ToLower() } else { 'off' }
    if ($mode -eq 'off' -or [string]::IsNullOrWhiteSpace($mode)) { return }
    $markFile = Join-Path $proj '.pids\last-improve.txt'
    $intervalMin = [int]($env:FOULER_LEARN_INTERVAL_MIN); if ($intervalMin -le 0) { $intervalMin = 240 }
    if (Test-Path $markFile) {
      try { if (((Get-Date) - (Get-Content $markFile -Raw | Get-Date)).TotalMinutes -lt $intervalMin) { return } } catch {}
    }
    $freeGB = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)
    if ($freeGB -lt 3.5) { ("{0} LEARN skip: low RAM ${freeGB}GB" -f (Get-Date -Format o)) | Add-Content $log; return }
    (Get-Date -Format o) | Set-Content $markFile
    ("{0} LEARN window: mode=$mode freeRAM=${freeGB}GB (lease free pre-round)" -f (Get-Date -Format o)) | Add-Content $log
    if ($mode -eq 'dryrun') {
      & $py "$proj\infrastructure\improve_agent.py" --dry-run *> (Join-Path $proj 'logs\daemon-improve.out.txt')
      ("{0} LEARN dryrun done (rc={1})" -f (Get-Date -Format o), $LASTEXITCODE) | Add-Content $log
      return
    }
    # AUTO: snapshot dirty tracked state (non-destructive), bounded real cycle, restore on kill.
    $snap = (& git -C $proj stash create 2>$null | Out-String).Trim()
    & $py "$proj\scripts\devstream_runtime_lease.py" --purpose improve-agent --write --machine JIGGLYPUFF --max-cycles 1 --valid-minutes 45 --approved --runtime-lease 'devstream\truth\runtime-lease.json' 2>&1 | Out-Null
    $ia = Start-Process $py -ArgumentList 'infrastructure\improve_agent.py', '--enable-auto-improve', '--max-cycles', '1', '--runtime-lease', 'devstream\truth\runtime-lease.json' -WorkingDirectory $proj -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $proj 'logs\daemon-improve.out.txt') -RedirectStandardError (Join-Path $proj 'logs\daemon-improve.err.txt')
    if (-not $ia.WaitForExit(2400000)) {
      Stop-Process -Id $ia.Id -Force -ErrorAction SilentlyContinue
      if ($snap) { & git -C $proj checkout $snap -- . 2>$null } else { & git -C $proj checkout -- . 2>$null }
      ("{0} LEARN auto TIMEOUT(40m) -> killed + restored tree (snap=$snap)" -f (Get-Date -Format o)) | Add-Content $log
    } else {
      ("{0} LEARN auto done (rc={1})" -f (Get-Date -Format o), $ia.ExitCode) | Add-Content $log
    }
  } catch { ("{0} LEARN window error: {1}" -f (Get-Date -Format o), $_.Exception.Message) | Add-Content $log }
}

while (-not (Test-Path $stop)) {
  $sup = Get-CimInstance Win32_Process -Filter "name like 'python%'" |
    Where-Object { $_.CommandLine -match 'devstream_session\.py' -and $_.CommandLine -match 'supervise' }
  if (-not $sup) {
    Invoke-LearningWindow   # pre-round gap: lease free + WS=0 (no-op unless mode!=off AND cadence due)
    if (Test-Path $stop) { break }
    & $py "$proj\scripts\devstream_runtime_lease.py" --purpose devstream-supervise --write --machine JIGGLYPUFF --run-count 30 --max-cycles 1 --max-concurrent-battles 1 --account LEBOTJAMESXD00N --replay-behavior always --valid-minutes 240 --approved --runtime-lease 'devstream\truth\runtime-lease.json' 2>&1 | Out-Null
    & "$proj\scripts\start_battle_supervisor_task.ps1" -RunCount 30 -MaxCycles 1 -MaxConcurrentBattles 1 -RuntimeLease 'devstream\truth\runtime-lease.json' 2>&1 | Out-Null
    ("{0} launched single-cycle round (no supervisor was running)" -f (Get-Date -Format o)) | Add-Content $log
    Start-Sleep -Seconds 120
  } else {
    Start-Sleep -Seconds 90
  }
}
("{0} continuous-ladder daemon STOP (stop file present)" -f (Get-Date -Format o)) | Add-Content $log
