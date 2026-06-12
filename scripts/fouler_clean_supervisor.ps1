# Clean singleton Fouler supervisor (replaces the racy HERMES launcher).
# GUARANTEES exactly one .venv run.py on the account:
#   - kills ANY system-python run.py (those bypass run.py's psutil-based singleton
#     lock -> they are the duplicates that thrash ELO 1300<->1000),
#   - relaunches a .venv client if none,
#   - dedups extra .venv clients (keep oldest = the lock holder).
# Always uses the .venv python so run.py's process_lock singleton actually works.
$ErrorActionPreference = 'SilentlyContinue'
$proj = 'D:\Projects\fouler-play'
$py = Join-Path $proj '.venv\Scripts\python.exe'
$account = 'npctypebeat'
$supLock = Join-Path $proj '.pids\clean_supervisor.lock'
$runtimeLease = Join-Path $proj '.pids\fouler-runtime-lane.lease.json'
$leaseToken = [guid]::NewGuid().ToString('N')
$statusPath = Join-Path $proj 'devstream\truth\clean-supervisor-status.json'
# Eval port used by the improve-window's throwaway local showdown server.
$evalPort = '18765'

# HUNG-DETECTION (ladder-flap safety net) 2026-06-12 ---------------------------
# The ladder bot occasionally HANGS while its run.py process stays ALIVE: it
# stops registering/joining battles, init.log stops being written, and it stops
# completing rated games (ELO goes flat). The pre-existing supervisor only
# restarted DEAD procs, so a hung-but-alive client could sit dead for hours and
# only a manual restart recovered it. This adds a LIVENESS-BY-ACTIVITY check:
# init.log mtime is the authoritative heartbeat. During healthy laddering --
# INCLUDING long gen9ou stall games (20min+) -- the dispatcher logs every
# received message (|inactive| pings, routed battle msgs) and the MCTS logs
# every move sample, so init.log advances every few seconds. A stale init.log
# is therefore a reliable hang signal that does NOT false-positive on a long
# single battle (verified live: init.log mtime advanced within 12s mid-battle).
$initLog = Join-Path $proj 'logs\init.log'
$hangLog = Join-Path $proj 'logs\clean-supervisor-hang.log'
# Stale threshold (seconds of no init.log write while a client is supposed to
# be laddering) before we declare the client hung and restart it. Tunable.
$hangStaleSec = if ($env:FOULER_HANG_STALE_SEC) { [int]$env:FOULER_HANG_STALE_SEC } else { 600 }
$hangStaleSec = [Math]::Max(120, $hangStaleSec)  # floor: never below 2 min
# After (re)starting a client, give run.py time to import/connect/begin writing
# init.log before its activity is judged. Avoids killing a healthy fresh client.
$hangStartupGraceSec = if ($env:FOULER_HANG_STARTUP_GRACE_SEC) { [int]$env:FOULER_HANG_STARTUP_GRACE_SEC } else { 180 }
$hangStartupGraceSec = [Math]::Max(60, $hangStartupGraceSec)

New-Item -ItemType Directory -Force -Path (Join-Path $proj '.pids') | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $statusPath -Parent) | Out-Null

function Write-HangLog {
  param([string]$Message)
  $line = ('{0}  {1}' -f (Get-Date).ToUniversalTime().ToString('o'), $Message)
  try { Add-Content -LiteralPath $hangLog -Value $line -Encoding UTF8 } catch {}
}

function Get-InitLogAgeSeconds {
  # Age (in seconds) of the newest write to init.log. Returns $null if the log
  # does not exist yet (fresh client that hasn't written anything).
  if (-not (Test-Path -LiteralPath $initLog)) { return $null }
  try {
    $fi = Get-Item -LiteralPath $initLog -ErrorAction Stop
    return [int]((Get-Date) - $fi.LastWriteTime).TotalSeconds
  } catch {
    return $null
  }
}

function Stop-LadderClient {
  # Cleanly tear down the live .venv ladder client AND its worker children so the
  # main supervisor loop respawns a fresh one on the next tick. Kills children
  # first (bottom-up) so the launcher does not re-fork during teardown.
  param([array]$VenvClients, [array]$AllClients)
  $killed = 0
  $parentPids = @{}
  foreach ($v in $VenvClients) { $parentPids[[int]$v.ProcessId] = $true }
  # children of the venv launcher(s) (the system-python multiprocessing workers)
  foreach ($c in $AllClients) {
    if (-not $parentPids.ContainsKey([int]$c.ProcessId) -and $parentPids.ContainsKey([int]$c.ParentProcessId)) {
      Stop-Process -Id $c.ProcessId -Force -ErrorAction SilentlyContinue
      $killed++
    }
  }
  foreach ($v in $VenvClients) {
    Stop-Process -Id $v.ProcessId -Force -ErrorAction SilentlyContinue
    $killed++
  }
  return $killed
}

function Clear-StaleDebris {
  # FLAP ROOT-CAUSE FIX 2026-06-11: stale sentinel/lease/lock files left behind by
  # a force-killed improve-window or a crashed supervisor created handoff ambiguity
  # and (combined with orphaned eval servers starving RAM) made the ladder flap.
  # Drop sentinels the live runtime no longer honours so a fresh supervisor starts
  # from a clean slate. (run.py itself clears its own .bot.pid via the lock path.)
  foreach ($name in @('supervisor.stop','drain.request')) {
    $p = Join-Path $proj ".pids\$name"
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }
  }
}

function Get-ReapedOrphanEvalServers {
  # FLAP ROOT-CAUSE FIX 2026-06-11: the improve-window starts a throwaway
  # pokemon-showdown server (port 18765 + a fan-out of dist/server/*.js workers)
  # for the self-play eval. When that window is force-killed (Task Scheduler
  # ExecutionTimeLimit, or the next window stomping it) the PowerShell `finally`
  # cleanup is bypassed, so those node servers ORPHAN and keep eating RAM. Two
  # such clusters (16 node procs) were observed leaking for hours, starving the
  # box to ~3 GB free -- the conditions under which run.py's startup intermittently
  # failed and the ladder flapped. The live ladder talks to the PUBLIC server
  # (sim3.psim.us), never to :18765, so reaping these eval orphans is always safe
  # AND only happens when NO improve-window is actually running.
  $windowAlive = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -match 'run_improve_window\.ps1' -or $_.CommandLine -match 'run_improve_loop' -or $_.CommandLine -match 'selfplay_eval' })
  if ($windowAlive.Count -gt 0) { return 0 }  # a real eval is in progress; never touch its server
  $reaped = 0
  Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object {
    $_.CommandLine -match 'pokemon-showdown' -and
    ($_.CommandLine -match [regex]::Escape($evalPort) -or $_.CommandLine -match 'dist\\server\\')
  } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    $reaped++
  }
  # also reap any orphaned eval python arms (own users, local server) that escaped cleanup
  Get-CimInstance Win32_Process -Filter "name like 'python%'" | Where-Object {
    $_.CommandLine -match 'selfplay_eval' -or $_.CommandLine -match 'fouler(NEW|OLD)'
  } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    $reaped++
  }
  return $reaped
}

function Test-LivePid {
  param([int]$ProcessId)
  if ($ProcessId -le 0) { return $false }
  return [bool](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue)
}

function Test-CleanSupervisorPid {
  param([string]$PidText)
  if (-not ($PidText -match '^\d+$')) { return $false }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$PidText" -ErrorAction SilentlyContinue
  if (-not $proc -or -not $proc.CommandLine) { return $false }
  return ($proc.CommandLine -match 'fouler_clean_supervisor\.ps1' -and $proc.CommandLine -match [regex]::Escape($proj))
}

function Read-RuntimeLease {
  if (-not (Test-Path -LiteralPath $runtimeLease)) { return $null }
  try {
    return (Get-Content -LiteralPath $runtimeLease -Raw -Encoding UTF8 | ConvertFrom-Json)
  } catch {
    return $null
  }
}

function Test-RuntimeLeaseAvailable {
  $lease = Read-RuntimeLease
  if (-not $lease) {
    if (Test-Path -LiteralPath $runtimeLease) {
      Remove-Item -LiteralPath $runtimeLease -Force -ErrorAction SilentlyContinue
    }
    return $true
  }
  if ($env:FOULER_RUNTIME_LEASE_TOKEN -and $lease.token -eq $env:FOULER_RUNTIME_LEASE_TOKEN -and (Test-LivePid ([int]$lease.pid))) {
    return $true
  }
  if (-not (Test-LivePid ([int]$lease.pid))) {
    Remove-Item -LiteralPath $runtimeLease -Force -ErrorAction SilentlyContinue
    return $true
  }
  return $false
}

function Write-CleanSupervisorStatus {
  param(
    [array]$Clients,
    [array]$Actions,
    [string]$State,
    $InitLogAgeSeconds = $null
  )
  $payload = [ordered]@{
    schemaVersion = 'fouler-play-clean-supervisor/v1'
    checkedAt = (Get-Date).ToUniversalTime().ToString('o')
    supervisorPid = $PID
    state = $State
    runtimeLease = $runtimeLease
    runtimeLeaseToken = $leaseToken
    # Liveness heartbeat: seconds since init.log was last written. $null => log
    # missing. The hang-detector restarts the client when this exceeds the stale
    # threshold while a single .venv client is supposed to be laddering.
    initLogAgeSeconds = $InitLogAgeSeconds
    hangStaleThresholdSeconds = $hangStaleSec
    actions = $Actions
    clients = @($Clients | ForEach-Object {
      [ordered]@{
        pid = $_.ProcessId
        parentPid = $_.ParentProcessId
        executablePath = $_.ExecutablePath
        commandLine = $_.CommandLine
      }
    })
  }
  $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statusPath -Encoding UTF8
}

if (Test-Path $supLock) {
  $opid = (Get-Content $supLock -Raw).Trim()
  if (Test-CleanSupervisorPid $opid) { exit 0 }
  Remove-Item -LiteralPath $supLock -Force -ErrorAction SilentlyContinue
}
$PID | Out-File -FilePath $supLock -Encoding ascii -Force

# We are the sole supervisor now: clear stale debris a dead predecessor left behind.
Clear-StaleDebris

if (-not (Test-RuntimeLeaseAvailable)) {
  Write-CleanSupervisorStatus -Clients @() -Actions @('blocked by live fouler-runtime-lane lease') -State 'blocked-runtime-lease'
  exit 3
}

$leasePayload = [ordered]@{
  schemaVersion = 'fouler-play-runtime-lease/v1'
  name = 'fouler-runtime-lane'
  holder = 'fouler_clean_supervisor'
  pid = $PID
  token = $leaseToken
  cwd = $proj
  argv = @('scripts\fouler_clean_supervisor.ps1')
  createdAt = (Get-Date).ToUniversalTime().ToString('o')
}
try {
  $stream = [System.IO.File]::Open($runtimeLease, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($leasePayload | ConvertTo-Json -Depth 5) + "`n")
    $stream.Write($bytes, 0, $bytes.Length)
  } finally {
    $stream.Dispose()
  }
} catch {
  if (-not (Test-RuntimeLeaseAvailable)) {
    Write-CleanSupervisorStatus -Clients @() -Actions @('blocked by live fouler-runtime-lane lease after create race') -State 'blocked-runtime-lease'
    exit 3
  }
}

$argsList = @('run.py','--websocket-uri','wss://sim3.psim.us/showdown/websocket',
  '--ps-username',$account,'--bot-mode','search_ladder','--pokemon-format','gen9ou',
  '--run-count','999999','--max-concurrent-battles','3','--save-replay','always',
  '--log-to-file','--team-names','gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-pivot,gen9/ou/fat-team-3-dondozo')

# Baseline for hang-detection startup grace: when the supervisor first comes up it
# may ADOPT an already-running client (e.g. after a supervisor-only restart). Give
# that adopted client the same grace as a freshly-launched one before judging its
# init.log activity, so a momentary stale log at supervisor boot is not a false hang.
$script:SupervisorStart = Get-Date

try {
  while ($true) {
    $actions = @()
    # Reap orphaned improve-window eval servers (RAM-leak that destabilises the
    # ladder) ONLY when no eval is actually running. Safe: live ladder never uses :18765.
    $reaped = Get-ReapedOrphanEvalServers
    if ($reaped -gt 0) { $actions += "reaped $reaped orphaned eval server proc(s)" }
    $clients = @(Get-CimInstance Win32_Process -Filter "name like 'python%'" | Where-Object { $_.CommandLine -match 'run\.py' -and $_.CommandLine -match $account })
    $venv = @($clients | Where-Object { $_.ExecutablePath -match '\.venv' })
    $venvPids = @{}
    foreach ($v in $venv) { $venvPids[[int]$v.ProcessId] = $true }
    $venvChildren = @($clients | Where-Object { $_.ExecutablePath -notmatch '\.venv' -and $venvPids.ContainsKey([int]$_.ParentProcessId) })
    $sys  = @($clients | Where-Object { $_.ExecutablePath -notmatch '\.venv' -and -not $venvPids.ContainsKey([int]$_.ParentProcessId) })
    # 1) kill ALL system-python clients (lock-bypassing duplicates).
    # STARTUP-RACE GUARD 2026-06-11: a freshly-spawned base-python multiprocessing
    # WORKER of a legit .venv client can momentarily appear with its .venv parent not
    # yet catalogued in $venvPids (WMI ordering / parent ExecutablePath still null
    # during init). Killing it would maim the legit client. So before killing a $sys
    # proc, re-resolve its live parent: if the parent is a .venv python from THIS repo,
    # it is a child of the legit client -- spare it. Only genuinely parentless/foreign
    # system-python run.py clients (the real lock-bypassing dups) are killed.
    foreach ($s in $sys) {
      $par = Get-CimInstance Win32_Process -Filter "ProcessId=$($s.ParentProcessId)" -ErrorAction SilentlyContinue
      $parentIsVenvClient = $par -and $par.ExecutablePath -match '\.venv' -and $par.CommandLine -match 'run\.py' -and $par.CommandLine -match $account
      if ($parentIsVenvClient) {
        $actions += "spared base-python child pid=$($s.ProcessId) of live .venv client pid=$($s.ParentProcessId)"
        continue
      }
      Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue
      $actions += "killed system-python duplicate pid=$($s.ProcessId)"
    }
    # 2) ensure exactly one .venv client
    if ($venv.Count -eq 0) {
      # ANTI-FLAP COOLDOWN 2026-06-11: run.py's heavy import/connect startup can take
      # >15s before its .venv process is observable. Without a cooldown the supervisor
      # would relaunch on the very next tick, spawning a 2nd client that the singleton
      # lock then aborts -- visible churn. Wait out a recent launch before starting again.
      $sinceLaunch = if ($script:LastLaunch) { ((Get-Date) - $script:LastLaunch).TotalSeconds } else { 99999 }
      if ($sinceLaunch -ge 30) {
        Start-Process -FilePath $py -ArgumentList $argsList -WorkingDirectory $proj -WindowStyle Hidden
        $script:LastLaunch = Get-Date
        $actions += 'started .venv run.py'
      } else {
        $actions += ("awaiting recent launch (${sinceLaunch}s < 30s); not relaunching")
      }
    } elseif ($venv.Count -gt 1) {
      $venv | Sort-Object CreationDate | Select-Object -Skip 1 | ForEach-Object {
        $extraParentPid = [int]$_.ProcessId
        $venvChildren | Where-Object { [int]$_.ParentProcessId -eq $extraParentPid } | ForEach-Object {
          Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
          $actions += "killed child of extra .venv duplicate pid=$($_.ProcessId)"
        }
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $actions += "killed extra .venv duplicate pid=$($_.ProcessId)"
      }
    }

    # 3) HUNG-DETECTION (ladder-flap safety net) 2026-06-12 ---------------------
    # Only judge activity when exactly one .venv client is laddering (the normal
    # steady state). When count is 0 or >1 the start/dedup logic above is already
    # acting and we let it settle before applying a liveness verdict.
    $state = 'running'
    $initAge = Get-InitLogAgeSeconds
    if ($venv.Count -eq 1) {
      # Grace: skip the verdict if we (re)launched recently OR the supervisor just
      # came up and may have adopted a mid-startup client.
      $sinceLaunch = if ($script:LastLaunch) { ((Get-Date) - $script:LastLaunch).TotalSeconds } else { 99999 }
      $sinceSupStart = ((Get-Date) - $script:SupervisorStart).TotalSeconds
      $inGrace = ($sinceLaunch -lt $hangStartupGraceSec) -or ($sinceSupStart -lt $hangStartupGraceSec)
      if (-not $inGrace) {
        # $initAge -eq $null => init.log missing well past startup grace: also a hang
        # (a healthy client always has a written init.log). Treat null as "very stale".
        $effectiveAge = if ($null -eq $initAge) { $hangStaleSec + 1 } else { $initAge }
        if ($effectiveAge -ge $hangStaleSec) {
          $hungPid = [int]$venv[0].ProcessId
          $msg = "HANG DETECTED: .venv client pid=$hungPid alive but init.log stale ${effectiveAge}s >= ${hangStaleSec}s threshold; restarting client cleanly"
          Write-HangLog $msg
          $actions += $msg
          $killed = Stop-LadderClient -VenvClients $venv -AllClients $clients
          # Arm the cooldown + grace so the next tick relaunches once and does not
          # immediately re-judge the fresh client.
          $script:LastLaunch = Get-Date
          $actions += "hung-restart: killed $killed proc(s); supervisor will respawn next tick"
          Write-HangLog "killed $killed proc(s) for hung client pid=$hungPid"
          $state = 'hung-restart'
        }
      } else {
        $actions += ("hang-check skipped (startup grace: sinceLaunch=$([int]$sinceLaunch)s, sinceSupStart=$([int]$sinceSupStart)s)")
      }
    }

    Write-CleanSupervisorStatus -Clients $clients -Actions $actions -State $state -InitLogAgeSeconds $initAge
    Start-Sleep 15
  }
} finally {
  $lease = Read-RuntimeLease
  if ($lease -and $lease.token -eq $leaseToken -and [int]$lease.pid -eq $PID) {
    Remove-Item -LiteralPath $runtimeLease -Force -ErrorAction SilentlyContinue
  }
}
