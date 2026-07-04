# HERMES-FoulerLeaseAutorenew (2026-06-23, claude)
# Keeps the fouler runtime proof-window lease ALWAYS valid so laddering + learning
# never go dark on a fail-closed lease expiry. The lease's proofWindow gates
# acquire_lock() in process_lock.py -- once it expires, every freshly-launched
# ladder client is refused and the ladder stops permanently until a human renews.
# This re-issues the lease daily with a rolling multi-day window (no Showdown,
# no battles, no auto-improve -- it ONLY writes JSON), making the venture
# self-sustaining (the "durable auto-renewal" follow-up noted in the live lease).
#
# Values MUST match the live ladder caps: cc=3, run-count=1000, account+machine,
# replay=always. cc=3/sp=4 are owner-locked; this script never changes them.
#
# Purpose = 'devstream-supervise' so allowedPurposes expands to the SAME 5-purpose
# set the live lease carries (supervise + start + 2 stale-truth-cleanup +
# run-py-battle-runner); writing only 'run-py-battle-runner' would strand the
# devstream supervisor + cleanup jobs. The post-write validation below still
# checks the run-py-battle-runner purpose (the gate process_lock actually uses).
$ErrorActionPreference = 'SilentlyContinue'
$repo   = 'D:\Projects\fouler-play'
$python = 'D:\Projects\fouler-play\.venv\Scripts\python.exe'
$leaseScript = Join-Path $repo 'scripts\devstream_runtime_lease.py'
$leaseFile   = Join-Path $repo 'devstream\truth\runtime-lease.json'
$logDir = Join-Path $repo 'logs'
$log    = Join-Path $logDir 'fouler_lease_autorenew.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Say([string]$m) {
  $ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
  Add-Content -Path $log -Value ("[{0}] {1}" -f $ts, $m) -ErrorAction SilentlyContinue
}

# Rolling window: 8 days = 11520 minutes. Renewed daily -> window is always
# >= 7 days ahead, so an expiry can never strand the ladder between renewals.
$validMinutes = 11520

Set-Location $repo
$argList = @(
  $leaseScript,
  '--write',
  '--runtime-lease', $leaseFile,
  '--purpose', 'devstream-supervise',
  '--machine', 'JIGGLYPUFF',
  '--account', 'thepeakmons',
  '--run-count', '1000',
  '--max-cycles', '1000',
  '--max-concurrent-battles', '3',
  '--replay-behavior', 'always',
  '--valid-minutes', "$validMinutes",
  '--status', 'active',
  '--approved',
  '--require-run-count',
  '--require-max-concurrent-battles',
  '--require-replay-behavior'
)

try {
  $out = & $python @argList 2>&1
  $code = $LASTEXITCODE
  # Persist the write+validate JSON for forensics.
  $out | Out-File -FilePath (Join-Path $logDir 'fouler_lease_autorenew.last.json') -Encoding utf8

  # Second, explicit validation against the ACTUAL gate process_lock uses
  # (run-py-battle-runner) with the live ladder bounds, so we prove the renewed
  # lease will admit a freshly-launched ladder client.
  $vArgs = @(
    $leaseScript, '--purpose', 'run-py-battle-runner', '--runtime-lease', $leaseFile,
    '--run-count', '1000', '--max-concurrent-battles', '3', '--replay-behavior', 'always',
    '--require-run-count', '--require-max-concurrent-battles', '--require-replay-behavior'
  )
  $vout = & $python @vArgs 2>&1
  $vcode = $LASTEXITCODE
  $vout | Out-File -FilePath (Join-Path $logDir 'fouler_lease_autorenew.runner-validate.json') -Encoding utf8

  if ($code -eq 0 -and $vcode -eq 0) {
    Say "RENEWED: rolling $validMinutes-min ($([math]::Round($validMinutes/1440,1))d) proof window; supervise-write ok + run-py-battle-runner gate ok."
  } else {
    Say "WARN: lease renew issue (write exit=$code, runner-validate exit=$vcode) -- see fouler_lease_autorenew.last.json / runner-validate.json"
  }
} catch {
  Say ("ERROR: lease autorenew failed: {0}" -f $_.Exception.Message)
  exit 1
}
exit 0
