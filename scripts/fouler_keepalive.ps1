# HERMES-FoulerKeepalive (hardened v3 2026-06-23, claude)
# Goal: maintain EXACTLY ONE top-level run.py search_ladder ladder client on the
# 8-core box, without ever re-over-committing it (the over-commit crisis was
# caused by this guard spawning DUPLICATE clients).
#
# Robust singleton logic (per HERMES directive):
#   * A "ladder client" = a python process whose cmdline contains BOTH 'run.py'
#     AND 'search_ladder'. This matches the venv-launcher shim, the system-python
#     child, AND the MCTS multiprocessing workers (they inherit the cmdline).
#   * A "TOP-LEVEL" client = a matching process whose ParentProcessId is NOT
#     itself a matching process. This excludes:
#       - MCTS search-parallelism worker children (parent = the main run.py)
#       - the system-python child re-exec'd by the .venv launcher shim
#         (parent = the shim, which is itself a matching process)
#     Counting top-level processes is what the previous guard got wrong: it
#     matched only one python path and mis-timed detection right after launch,
#     so it saw "0" while a client was still materializing and launched again,
#     stacking duplicate clients (see logs\fouler_keepalive.log 2026-06-23 08:14).
#   * If 1 top-level -> OK.
#   * If 0 top-level -> launch exactly ONE, then settle+verify (don't re-launch).
#   * If >1 top-level -> KILL the extras (keep the OLDEST = most-established),
#     so a duplicate that slipped in is reduced back to one.
#
# An exclusive keepalive lock file prevents two overlapping task invocations from
# both launching (the documented 20s-apart double-launch). run.py's own
# process_lock (.bot.pid account lock) remains the final safety net.
#
# Caps held at SEARCH_PARALLELISM=4, MAX_CONCURRENT_BATTLES=3 to match the live
# .env and keep CPU headroom on the 8-core box (cc=3, sp=4 are the owner-locked live caps; do NOT change them).

$ErrorActionPreference = 'SilentlyContinue'
$repo    = 'D:\Projects\fouler-play'
$python  = 'D:\Projects\fouler-play\.venv\Scripts\python.exe'
$logDir  = 'D:\Projects\fouler-play\logs'
$kaLog   = Join-Path $logDir 'fouler_keepalive.log'
$lockDir = Join-Path $repo '.pids'
$lockFile = Join-Path $lockDir 'keepalive.lock'

New-Item -ItemType Directory -Force -Path $logDir  | Out-Null
New-Item -ItemType Directory -Force -Path $lockDir | Out-Null

function Write-KA([string]$msg) {
  $ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
  try { Add-Content -Path $kaLog -Value ("[{0}] {1}" -f $ts, $msg) -ErrorAction SilentlyContinue } catch {}
}

# ----- Exclusive keepalive lock (prevents overlapping invocations racing a launch) -----
$lockStream = $null
try {
  $lockStream = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-KA "ANOTHER keepalive invocation holds the lock; exiting to avoid a racing launch."
  exit 0
}

try {
  # ----- Enumerate matching ladder processes (python.exe AND pythonw.exe) -----
  function Get-LadderProcs {
    $procs = @()
    foreach ($name in @('python.exe','pythonw.exe')) {
      $found = Get-CimInstance Win32_Process -Filter "Name='$name'" -OperationTimeoutSec 15 -ErrorAction Stop |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match 'run\.py') -and ($_.CommandLine -match 'search_ladder') }
      if ($found) { $procs += $found }
    }
    return $procs
  }

  $top = $null
  try {
    $all = @(Get-LadderProcs)
    $allIds = @{}
    foreach ($p in $all) { $allIds[[int]$p.ProcessId] = $true }
    # Top-level = parent is NOT itself a matching ladder process.
    $top = @($all | Where-Object { -not $allIds.ContainsKey([int]$_.ParentProcessId) })
  } catch {
    # If detection itself fails (WMI hiccup under load), DO NOT blindly launch:
    # run.py's account lock would dedup, but a blind launch still spikes CPU
    # during the racy window. Treat a detect failure as "assume one is alive"
    # and take no action this cycle; the next cycle re-checks.
    Write-KA ("DETECT-FAILED ({0}); taking NO action this cycle (will re-check next run)." -f $_.Exception.Message)
    exit 0
  }

  $count = $top.Count

  if ($count -eq 1) {
    Write-KA ("OK: 1 top-level run.py client alive (PID {0}); no action." -f $top[0].ProcessId)
    exit 0
  }

  if ($count -gt 1) {
    # Duplicate clients present -> keep the OLDEST (most-established ladder
    # session), kill the rest (and their whole process trees, to take down any
    # MCTS workers they spawned).
    $ordered = @($top | Sort-Object { $_.CreationDate })
    $keep = $ordered[0]
    $extras = @($ordered | Select-Object -Skip 1)
    Write-KA ("DUPLICATES: {0} top-level clients; keeping oldest PID {1}, killing {2} extra(s): {3}" -f `
      $count, $keep.ProcessId, $extras.Count, (($extras | ForEach-Object { $_.ProcessId }) -join ','))
    foreach ($e in $extras) {
      try {
        & taskkill.exe /PID $e.ProcessId /T /F | Out-Null
        Write-KA ("  killed extra client tree PID {0}" -f $e.ProcessId)
      } catch {
        Write-KA ("  FAILED to kill PID {0}: {1}" -f $e.ProcessId, $_.Exception.Message)
      }
    }
    exit 0
  }

  # ----- count -eq 0: launch exactly one -----
  # IMPROVE-WINDOW PAUSE (2026-07-04, foreman): honor .pids\continuous-ladder.stop.
  # The improve window (scripts\run_improve_window.ps1) pauses laddering via this
  # stop file so offline evals run on a quiet box; previously only the retired
  # continuous daemon honored it and THIS live keepalive would relaunch a ladder
  # client mid-eval. While the stop file is present (and fresh) we skip the
  # relaunch and log why. STALENESS GUARD: an improve window is bounded (hours);
  # a stop file older than 8h is an orphan (crashed window that never cleaned
  # up) and must not silently kill the lane - delete it and relaunch.
  $stopFile = Join-Path $repo '.pids\continuous-ladder.stop'
  if (Test-Path $stopFile) {
    $stopAgeHours = ((Get-Date) - (Get-Item $stopFile).LastWriteTime).TotalHours
    if ($stopAgeHours -lt 8) {
      Write-KA ("PAUSED: 0 clients but stop file present (age {0:N1}h < 8h) - improve/eval window owns the box; skipping relaunch." -f $stopAgeHours)
      exit 0
    }
    Write-KA ("STALE STOP FILE: age {0:N1}h >= 8h - orphaned pause (crashed improve window?); deleting it and relaunching the ladder." -f $stopAgeHours)
    Remove-Item $stopFile -Force -ErrorAction SilentlyContinue
  }
  # cc stays 3 (mission-fixed). search-parallelism is env-overridable for the
  # oversubscription A/B (2026-06-24): this box is a 4-physical-core i7-7700HQ, so
  # cc=3 x sp=4 = 12 search workers oversubscribes 4 cores -> ~7% of moves drop to
  # the weak 1-ply eval fallback under the move timer. Set FOULER_SEARCH_PARALLELISM=2
  # to test cc=3 x sp=2 (=6 workers, fits 4 cores + HT) and keep whichever yields
  # the lower no-policy-fallback rate at equal/better WR.
  $sp = if ($env:FOULER_SEARCH_PARALLELISM) { $env:FOULER_SEARCH_PARALLELISM } else { '2' }
  Write-KA ("DOWN: 0 top-level run.py search_ladder clients. Launching exactly one (cc=3, parallelism={0}, hidden)." -f $sp)
  $argList = @(
    'run.py',
    '--websocket-uri','wss://sim3.psim.us/showdown/websocket',
    '--ps-username','thepeakmons',
    '--bot-mode','search_ladder',
    '--pokemon-format','gen9ou',
    '--run-count','1000',
    '--max-concurrent-battles','3',
    '--search-parallelism',$sp,
    '--save-replay','always',
    '--log-to-file',
    '--team-names','gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-balance,gen9/ou/fat-team-3-dondozo'
  )
  # Redirect stdout/stderr to files. A hidden process with no console can stall
  # or die on a blocking console write; redirecting to files (as the proven
  # foreground launch does) keeps the ladder client alive and gives us the
  # [LOCK]/exit diagnostics if it ever does exit.
  $launchStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
  $stdoutPath = Join-Path $logDir ("ladder_client_{0}.out.log" -f $launchStamp)
  $stderrPath = Join-Path $logDir ("ladder_client_{0}.err.log" -f $launchStamp)
  # CLEAN-BOOT (2026-06-24, reverted to proven default): the keepalive only
  # launches when 0 clients are alive, i.e. the prior client DIED and any in-flight
  # battles it held are orphaned server-side. Empirically, enabling resume on this
  # path (RESUME_ACTIVE_BATTLES=1) wedged the fresh client at the matchmaking step
  # ("Searching for ranked" then idle, 0 CPU) -- the resume-priming "cancel active
  # ladder search" interacts badly with the fresh search and the worker never
  # re-establishes matchmaking. Restoring RESUME_ACTIVE_BATTLES=0 (which started a
  # battling client reliably) is the right call: a keepalive restart only happens
  # after the prior client already DIED, so its rooms are already orphaned and the
  # forfeit cost is unavoidable either way -- but a wedged client that never
  # ladders is far worse (zero games). NOTE the one retained improvement over the
  # old code: we SNAPSHOT active_battles.json (for OBS/forensics) but no longer
  # need to special-case it. The bounded-resume idea is deferred to a foreground
  # path where it can be validated without risking the autonomous ladder.
  $env:RESUME_ACTIVE_BATTLES = '0'
  try {
    $abFile = Join-Path $repo 'active_battles.json'
    if (Test-Path $abFile) {
      Copy-Item $abFile (Join-Path $logDir ('active_battles.preboot.{0}.json' -f $launchStamp)) -Force -ErrorAction SilentlyContinue
      Set-Content -Path $abFile -Value '{"battles": [], "count": 0, "max_slots": 3, "updated": ""}' -Encoding ASCII -ErrorAction SilentlyContinue
    }
    Write-KA "CLEAN-BOOT: RESUME_ACTIVE_BATTLES=0 + active_battles.json snapshot/cleared for a reliable fresh search (bounded-resume wedged matchmaking; deferred to a validated foreground path)."
  } catch { Write-KA ("CLEAN-BOOT prep warning: {0}" -f $_.Exception.Message) }
  try {
    $proc = Start-Process -FilePath $python -ArgumentList $argList -WorkingDirectory $repo `
      -WindowStyle Hidden -PassThru -ErrorAction Stop `
      -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    Write-KA ("LAUNCHED: pid={0} (cc=3, parallelism={1}; out={2}) -- verifying it materializes before exit" -f $proc.Id, $sp, (Split-Path $stdoutPath -Leaf))
  } catch {
    Write-KA ("LAUNCH-FAILED: {0}" -f $_.Exception.Message)
    exit 1
  }

  # ----- Settle + verify: wait for the launched client to actually appear as a
  # matching ladder process, so the NEXT keepalive run sees it (count>=1) and
  # does not launch a duplicate. We poll up to ~40s. -----
  $appeared = $false
  for ($i = 0; $i -lt 8; $i++) {
    Start-Sleep -Seconds 5
    try {
      $now = @(Get-LadderProcs)
      $nowIds = @{}
      foreach ($p in $now) { $nowIds[[int]$p.ProcessId] = $true }
      $nowTop = @($now | Where-Object { -not $nowIds.ContainsKey([int]$_.ParentProcessId) })
      if ($nowTop.Count -ge 1) {
        $appeared = $true
        Write-KA ("VERIFIED: {0} top-level client(s) present after launch (PIDs {1})." -f `
          $nowTop.Count, (($nowTop | ForEach-Object { $_.ProcessId }) -join ','))
        # If the launch produced MORE than one top-level (e.g. an account-lock
        # race left a loser still alive briefly), reduce to one next cycle; here
        # just record it.
        break
      }
    } catch {}
  }
  if (-not $appeared) {
    Write-KA "WARN: launched client did not appear as a matching ladder process within ~40s (run.py may have exited on the account lock, or is still importing). Next cycle will re-check."
  }
  exit 0
}
finally {
  if ($lockStream) { try { $lockStream.Close(); $lockStream.Dispose() } catch {} }
  try { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue } catch {}
}
