# run_improve_window.ps1 -- COORDINATED improve-window wrapper.
#
# WHY THIS EXISTS
#   The live ladder bot (Claude-FoulerPlayer -> fouler_clean_supervisor.ps1)
#   holds the runtime lease (.pids\fouler-runtime-lane.lease.json) continuously,
#   so the learn-from-losses improve loop (run_improve_loop_lowload.ps1) only
#   ever logged "BLOCKED: runtime lease busy" (exit 3) and NEVER produced a
#   verdict. This wrapper opens ONE coordinated window: stop the ladder to
#   release the lease, start a throwaway local pokemon-showdown server (the
#   self-play gate needs one on :18765 or it SKIPS), run ONE bounded improve
#   cycle, then ALWAYS restart the ladder.
#
# SAFETY (non-negotiable)
#   The live ladder is a STREAM SURFACE. The FINALLY block ALWAYS restarts
#   Claude-FoulerPlayer and confirms a .venv run.py client comes back, on every
#   exit path (success, error, timeout, ctrl-c). The improve cycle is bounded
#   (-MaxIterations 1) and the scheduled task wrapping this carries
#   ExecutionTimeLimit ~1h so a hang can never keep the ladder down.
#
# It NEVER pushes (improve_agent commits locally on the current feature branch
# only; --enable-git-push is NOT passed).
param(
    [double]$MinFreeGB = 3.5,
    [int]$Battles = 40,
    [int]$EvalPort = 18765,
    [string]$Repo = "D:\Projects\fouler-play",
    [string]$Showdown = "D:\Projects\pokemon-showdown",
    [switch]$GateProofFallback = $true,
    # Sized so the production decisive floor is REACHABLE inside the task's
    # ExecutionTimeLimit. Measured throughput at TurnCap=18 / SearchMs=700 is
    # ~0.41 decisive/min; 44 battles -> ~38 decisives in ~92 min, which clears
    # MIN_DECISIVE=30 with margin under a PT1H45M task limit. The old defaults
    # (TurnCap 60, SearchMs 700, 36 battles) needed ~107 min and were KILLED by
    # the PT1H limit before writing any verdict -- that was the throughput
    # blocker, not the gate logic.
    [int]$GateProofBattles = 44,
    [int]$GateProofMinDecisive = 30,
    [int]$GateProofTurnCap = 18,
    [int]$GateProofSearchMs = 700,
    [int]$GateProofPerBattleTimeout = 150
)

$ErrorActionPreference = "Stop"
$proj = $Repo
Set-Location -LiteralPath $proj
$log = Join-Path $proj "logs\improve_window.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log -Parent) | Out-Null
$py = Join-Path $proj ".venv\Scripts\python.exe"
$runtimeLease = Join-Path $proj ".pids\fouler-runtime-lane.lease.json"
$playerTask = "Claude-FoulerPlayer"

function Log($msg) {
    $line = "$(Get-Date -Format o) [window] $msg"
    Write-Host $line
    $line | Add-Content -LiteralPath $log
}

function Get-FreeGB {
    $os = Get-CimInstance Win32_OperatingSystem
    return [math]::Round($os.FreePhysicalMemory / 1MB, 2)
}

function Test-LivePid {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    return [bool](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue)
}

function Get-LadderProcs {
    return @(Get-CimInstance Win32_Process -Filter "name like 'python%'" |
        Where-Object { $_.CommandLine -match 'run\.py' -and $_.CommandLine -match 'npctypebeat' })
}

function Get-SupervisorProcs {
    return @(Get-CimInstance Win32_Process -Filter "name='powershell.exe'" |
        Where-Object { $_.CommandLine -match 'fouler_clean_supervisor\.ps1' })
}

function Get-VenvLadderCount {
    return @(Get-LadderProcs | Where-Object { $_.ExecutablePath -match '\.venv' }).Count
}

# --- pre-flight gates --------------------------------------------------------
Log "=== improve-window START ==="

$autoImprove = "$env:FOULER_PLAY_ENABLE_AUTO_IMPROVE".Trim().ToLowerInvariant()
if (@("1", "true", "yes", "on") -notcontains $autoImprove) {
    Log "STOP: FOULER_PLAY_ENABLE_AUTO_IMPROVE not enabled ('$env:FOULER_PLAY_ENABLE_AUTO_IMPROVE'); refusing to open a window."
    exit 2
}

$freeGB = Get-FreeGB
Log "free RAM = $freeGB GB (require >= $MinFreeGB)"
if ($freeGB -lt $MinFreeGB) {
    Log "STOP: insufficient free RAM; not disrupting the live ladder."
    exit 3
}

$sd = $null

try {
    # --- STEP 1: stop the ladder, release the lease --------------------------
    Log "stopping scheduled task $playerTask ..."
    Stop-ScheduledTask -TaskName $playerTask -ErrorAction SilentlyContinue

    Log "killing supervisor + run.py procs that hold the lease ..."
    foreach ($s in (Get-SupervisorProcs)) {
        Log "  stop supervisor pid=$($s.ProcessId)"
        Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue
    }
    foreach ($c in (Get-LadderProcs)) {
        Log "  stop ladder run.py pid=$($c.ProcessId) exe=$($c.ExecutablePath)"
        Stop-Process -Id $c.ProcessId -Force -ErrorAction SilentlyContinue
    }

    # Wait for the lease holder PID to be gone / lease to clear (up to 60s).
    $cleared = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (-not (Test-Path -LiteralPath $runtimeLease)) { $cleared = $true; break }
        $lease = $null
        try { $lease = Get-Content -LiteralPath $runtimeLease -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
        if (-not $lease) { $cleared = $true; break }
        $holderPid = [int]($lease.pid)
        if (-not (Test-LivePid $holderPid)) {
            Log "lease holder pid=$holderPid is DEAD; removing confirmed-stale lease file."
            Remove-Item -LiteralPath $runtimeLease -Force -ErrorAction SilentlyContinue
            $cleared = $true
            break
        }
        Log "  waiting for lease release (holder pid=$holderPid still alive) ..."
    }
    if (-not $cleared) {
        Log "WARNING: lease did not clear after 60s; proceeding anyway (improve_loop will re-check)."
    } else {
        Log "lease released."
    }

    # --- STEP 2: start a throwaway local showdown server on :$EvalPort -------
    $sdLog = Join-Path $proj "eval_results\selfplay\window-showdown-$EvalPort.log"
    New-Item -ItemType Directory -Force -Path (Split-Path $sdLog -Parent) | Out-Null
    Log "starting throwaway showdown on :$EvalPort (source $Showdown) ..."
    $sd = Start-Process -FilePath "node" `
        -ArgumentList @((Join-Path $Showdown "pokemon-showdown"), "start", "--no-security", "$EvalPort") `
        -WorkingDirectory $Showdown -PassThru `
        -RedirectStandardOutput $sdLog -RedirectStandardError "$sdLog.err" -WindowStyle Hidden
    Log "  showdown pid=$($sd.Id); waiting 25s for boot ..."
    Start-Sleep -Seconds 25

    $listening = $false
    for ($i = 0; $i -lt 10; $i++) {
        $c = Get-NetTCPConnection -State Listen -LocalPort $EvalPort -ErrorAction SilentlyContinue
        if ($c) { $listening = $true; break }
        Start-Sleep -Seconds 3
    }
    if ($listening) { Log "showdown is LISTENING on :$EvalPort." }
    else { Log "WARNING: showdown not listening on :$EvalPort after wait; gate may SKIP." }

    # --- STEP 3: run ONE bounded improve cycle -------------------------------
    $env:EVAL_SHOWDOWN_PORT = "$EvalPort"
    Log "running run_improve_loop_lowload.ps1 -Battles $Battles -MaxIterations 1 -MinFreeGB 3.0 ..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $proj "scripts\run_improve_loop_lowload.ps1") `
        -Battles $Battles -MaxIterations 1 -MinFreeGB 3.0
    Log "improve_loop lowload runner exit=$LASTEXITCODE"

    # --- STEP 3b: gate-proof fallback ----------------------------------------
    if ($GateProofFallback) {
        $ledger = Join-Path $proj "eval_results\improve_ledger.jsonl"
        $reachedGate = $false
        if (Test-Path $ledger) {
            $last = Get-Content $ledger -Tail 1
            if ($last -and ($last -match '"selfplay_verdict":\s*\{') -and ($last -notmatch '"selfplay_verdict":\s*null')) {
                $reachedGate = $true
            }
        }
        if (-not $reachedGate) {
            Log "improve_loop did NOT land a self-play verdict; running direct selfplay_eval gate-proof (battles=$GateProofBattles turn_cap=$GateProofTurnCap search_ms=$GateProofSearchMs min_decisive=$GateProofMinDecisive) ..."
            $env:PYTHONUTF8 = "1"
            $env:PYTHONIOENCODING = "utf-8"
            # Reachable production decisive floor for THIS run (env-scoped; does
            # NOT mutate the source default of 30 in selfplay_eval.py).
            $env:SELFPLAY_MIN_DECISIVE = "$GateProofMinDecisive"
            $proofLabel = "window-gateproof-$(Get-Date -Format yyyyMMdd-HHmmss)"
            & $py -X utf8 (Join-Path $proj "infrastructure\selfplay_eval.py") `
                --battles $GateProofBattles --turn-cap $GateProofTurnCap --showdown-port $EvalPort `
                --search-time-ms $GateProofSearchMs --per-battle-timeout $GateProofPerBattleTimeout `
                --teams-from "teams/eval-fast-teams.list" `
                --label $proofLabel `
                --new-env MCTS_BLEND_MAX_SAMPLES=8 --old-env MCTS_BLEND_MAX_SAMPLES=1
            Log "gate-proof selfplay_eval exit=$LASTEXITCODE"
            $proofPath = Join-Path $proj "eval_results\selfplay\$proofLabel.json"
            if (Test-Path $proofPath) {
                Log "gate-proof VERDICT file: $proofPath"
                Get-Content $proofPath -Raw | Add-Content -LiteralPath $log
                # Record a durable ledger entry from the REAL verdict so the
                # window run shows a completed accept/revert outcome (the loop's
                # own path already records when it reaches the gate; the fallback
                # did not, which is why window runs left no ledger verdict).
                try {
                    $v = Get-Content $proofPath -Raw | ConvertFrom-Json
                    $decisive = [int]$v.decisive_battles
                    $accept = [bool]$v.ACCEPT
                    $minD = [int]$v.min_decisive
                    $outcome = if ($accept) { "accepted_merged" } else { "reverted" }
                    if ($v.gate_failed_to_run) { $outcome = "gate_failed_to_run" }
                    $head = (& git -C $proj rev-parse HEAD).Trim()
                    $entry = [ordered]@{
                        timestamp        = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ")
                        issue            = "window gate-proof env-arm A/B (MCTS_BLEND_MAX_SAMPLES 8 vs 1)"
                        outcome          = $outcome
                        head_before      = $head.Substring(0, 12)
                        head_after       = $head.Substring(0, 12)
                        verdict_line     = "[WINDOW-PROOF] selfplay verdict: ACCEPT=$accept NEW $($v.new_wins)/$decisive LCB $($v.new_wilson_lcb)"
                        selfplay_verdict = [ordered]@{
                            label            = $v.label
                            new_wins         = $v.new_wins
                            old_wins         = $v.old_wins
                            decisive_battles = $decisive
                            new_win_rate     = $v.new_win_rate
                            new_wilson_lcb   = $v.new_wilson_lcb
                            ACCEPT           = $accept
                        }
                        decision_source  = "selfplay_lcb_gt_0.50"
                        proving_run      = $true
                        committed        = $false
                        min_decisive     = $minD
                        smoke_battles    = $null
                    }
                    $ledger = Join-Path $proj "eval_results\improve_ledger.jsonl"
                    ($entry | ConvertTo-Json -Compress -Depth 6) | Add-Content -LiteralPath $ledger -Encoding utf8
                    Log "ledger += $outcome (decisive=$decisive accept=$accept min_decisive=$minD)"
                } catch {
                    Log "WARN: failed to append window gate-proof ledger entry: $($_.Exception.Message)"
                }
            } else {
                Log "gate-proof produced NO verdict file ($proofPath)."
            }
        } else {
            Log "improve_loop already landed a self-play verdict; skipping gate-proof fallback."
        }
    }
}
finally {
    # --- ALWAYS: stop showdown + restart the ladder --------------------------
    Log "=== FINALLY: tearing down + restarting ladder ==="
    if ($sd) {
        try {
            $stillUp = Get-Process -Id $sd.Id -ErrorAction SilentlyContinue
            if ($stillUp) {
                Log "stopping throwaway showdown pid=$($sd.Id)"
                Stop-Process -Id $sd.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
    try {
        $c = Get-NetTCPConnection -State Listen -LocalPort $EvalPort -ErrorAction SilentlyContinue
        foreach ($conn in $c) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
            Log "freed eval port $EvalPort (killed pid=$($conn.OwningProcess))"
        }
    } catch {}

    # Restart the live ladder -- NON-NEGOTIABLE.
    Log "restarting scheduled task $playerTask ..."
    try { Start-ScheduledTask -TaskName $playerTask -ErrorAction Stop }
    catch { Log "Start-ScheduledTask threw: $($_.Exception.Message)" }

    $back = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 3
        if ((Get-VenvLadderCount) -ge 1) { $back = $true; break }
    }
    $venvCount = Get-VenvLadderCount
    if ($back) {
        Log "LADDER BACK UP: $venvCount .venv run.py client(s) running."
    } else {
        Log "ladder did NOT return via scheduler; launching supervisor directly as failsafe."
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File",(Join-Path $proj "scripts\fouler_clean_supervisor.ps1")) `
            -WorkingDirectory $proj -WindowStyle Hidden | Out-Null
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Seconds 3
            if ((Get-VenvLadderCount) -ge 1) { break }
        }
        $venvCount = Get-VenvLadderCount
        Log "after failsafe: $venvCount .venv run.py client(s)."
    }
    Log "=== improve-window END (ladder venv clients=$venvCount) ==="
}
