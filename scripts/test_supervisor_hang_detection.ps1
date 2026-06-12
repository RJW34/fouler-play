# Unit test for the fouler supervisor HUNG-detection decision logic.
# Mirrors the exact predicate used in fouler_clean_supervisor.ps1 so we can
# verify it detects a hang, does not false-positive on an active log, respects
# startup grace, and treats a missing log past grace as a hang -- all WITHOUT
# touching the live bot.

function Test-HangDecision {
  param(
    [int]$VenvCount,
    [object]$InitAge,            # seconds, or $null for missing log
    [double]$SinceLaunch,        # seconds since last (re)launch
    [double]$SinceSupStart,      # seconds since supervisor start
    [int]$HangStaleSec = 600,
    [int]$HangStartupGraceSec = 180
  )
  # --- BEGIN mirror of supervisor predicate ---
  if ($VenvCount -ne 1) { return 'no-verdict' }
  $inGrace = ($SinceLaunch -lt $HangStartupGraceSec) -or ($SinceSupStart -lt $HangStartupGraceSec)
  if ($inGrace) { return 'grace-skip' }
  $effectiveAge = if ($null -eq $InitAge) { $HangStaleSec + 1 } else { $InitAge }
  if ($effectiveAge -ge $HangStaleSec) { return 'HANG-RESTART' }
  return 'healthy'
  # --- END mirror ---
}

$fails = 0
function Assert-Eq($label, $expected, $actual) {
  if ($expected -eq $actual) {
    Write-Output ("  PASS  {0}  => {1}" -f $label, $actual)
  } else {
    Write-Output ("  FAIL  {0}  expected={1} actual={2}" -f $label, $expected, $actual)
    $script:fails++
  }
}

Write-Output "=== HUNG-detection decision tests (threshold=600s, grace=180s) ==="

# 1) Healthy laddering: 1 client, init.log fresh (12s old), well past grace -> healthy
Assert-Eq "healthy active bot (init 12s)" 'healthy' (Test-HangDecision -VenvCount 1 -InitAge 12 -SinceLaunch 5000 -SinceSupStart 5000)

# 2) Long live battle: init.log still advancing (90s old < threshold) -> healthy (NO false positive)
Assert-Eq "long battle, init 90s old" 'healthy' (Test-HangDecision -VenvCount 1 -InitAge 90 -SinceLaunch 5000 -SinceSupStart 5000)

# 3) Edge: init exactly under threshold (599s) -> healthy
Assert-Eq "init 599s (just under)" 'healthy' (Test-HangDecision -VenvCount 1 -InitAge 599 -SinceLaunch 5000 -SinceSupStart 5000)

# 4) HANG: 1 client alive but init.log stale 700s -> restart
Assert-Eq "hung: init 700s stale" 'HANG-RESTART' (Test-HangDecision -VenvCount 1 -InitAge 700 -SinceLaunch 5000 -SinceSupStart 5000)

# 5) HANG edge: init exactly at threshold (600s) -> restart
Assert-Eq "init 600s (at threshold)" 'HANG-RESTART' (Test-HangDecision -VenvCount 1 -InitAge 600 -SinceLaunch 5000 -SinceSupStart 5000)

# 6) Missing init.log past grace -> treated as hang
Assert-Eq "missing log past grace" 'HANG-RESTART' (Test-HangDecision -VenvCount 1 -InitAge $null -SinceLaunch 5000 -SinceSupStart 5000)

# 7) Startup grace by recent launch: stale log but launched 30s ago -> grace-skip (NO restart)
Assert-Eq "stale log but just launched 30s" 'grace-skip' (Test-HangDecision -VenvCount 1 -InitAge 999 -SinceLaunch 30 -SinceSupStart 5000)

# 8) Startup grace by supervisor boot: stale log but supervisor up 20s -> grace-skip
Assert-Eq "stale log but sup booted 20s" 'grace-skip' (Test-HangDecision -VenvCount 1 -InitAge 999 -SinceLaunch 5000 -SinceSupStart 20)

# 9) No client (count 0): start logic owns it -> no verdict
Assert-Eq "zero clients" 'no-verdict' (Test-HangDecision -VenvCount 0 -InitAge 999 -SinceLaunch 5000 -SinceSupStart 5000)

# 10) Two clients (dedup owns it): no verdict
Assert-Eq "two clients" 'no-verdict' (Test-HangDecision -VenvCount 2 -InitAge 999 -SinceLaunch 5000 -SinceSupStart 5000)

# 11) Grace boundary: launch exactly at grace edge (180s) -> NOT in grace -> healthy (fresh log)
Assert-Eq "launch at grace edge 180s, fresh log" 'healthy' (Test-HangDecision -VenvCount 1 -InitAge 10 -SinceLaunch 180 -SinceSupStart 5000)

# 12) Custom shorter threshold honored (300s threshold, init 350s) -> hang
Assert-Eq "custom 300s threshold, init 350s" 'HANG-RESTART' (Test-HangDecision -VenvCount 1 -InitAge 350 -SinceLaunch 5000 -SinceSupStart 5000 -HangStaleSec 300)

Write-Output ""
if ($fails -eq 0) { Write-Output "ALL TESTS PASSED" } else { Write-Output ("FAILURES: {0}" -f $fails); exit 1 }
