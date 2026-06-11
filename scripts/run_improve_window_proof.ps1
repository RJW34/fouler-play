# run_improve_window_proof.ps1 -- PROVING-RUN variant of run_improve_window.ps1.
#
# WHY THIS EXISTS
#   A real self-play ACCEPT/REVERT verdict has never landed because of THROUGHPUT,
#   not coordination: the default gate needs SELFPLAY_MIN_DECISIVE=30 decisive
#   battles, which at ~0.6 decisive/min cannot complete inside the PT1H window
#   (and the verdict file is only written AFTER all battle blocks finish, so the
#   window kills it with ZERO output every time).
#
#   This wrapper reuses the EXACT coordinated lease-handoff + always-restart
#   pattern of run_improve_window.ps1, but:
#     * sets a REACHABLE decisive floor (SELFPLAY_MIN_DECISIVE, default 12),
#     * runs selfplay_eval.py DIRECTLY on a fixed env-arm A/B candidate
#       (MCTS_BLEND_MAX_SAMPLES=8 NEW vs =1 OLD) -- a real candidate-vs-incumbent
#       comparison -- which ISOLATES the gate from the slow/fragile `claude`
#       propose + autoresearch steps that short-circuit before the gate,
#     * uses a tight turn cap + modest search-time so each battle ends fast,
#   so a REAL verdict completes in ~15-25 min and is provably written.
#
#   It then appends a durable eval_results\improve_ledger.jsonl entry derived
#   from the REAL verdict (outcome accepted_merged if ACCEPT else reverted;
#   proving_run=true, committed=false so it is never mistaken for a live deploy).
#
# SAFETY (non-negotiable, identical to run_improve_window.ps1)
#   The FINALLY block ALWAYS restarts Claude-FoulerPlayer + re-confirms its
#   trigger is ENABLED and a single .venv run.py client returns, on EVERY exit
#   path. Gated on free RAM >= MinFreeGB so cobblemon / the MC bot are not
#   starved. Never pushes. Never edits the production gate defaults.
param(
    [double]$MinFreeGB = 3.5,
    [int]$Battles = 16,
    [int]$MinDecisive = 12,
    [int]$TurnCap = 24,
    [int]$SearchMs = 700,
    [int]$EvalPort = 18765,
    [string]$Repo = "D:\Projects\fouler-play",
    [string]$Showdown = "D:\Projects\pokemon-showdown"
)

$ErrorActionPreference = "Stop"
$proj = $Repo
Set-Location -LiteralPath $proj
$log = Join-Path $proj "logs\improve_window_proof.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log -Parent) | Out-Null
$py = Join-Path $proj ".venv\Scripts\python.exe"
$runtimeLease = Join-Path $proj ".pids\fouler-runtime-lane.lease.json"
$playerTask = "Claude-FoulerPlayer"

function Log($msg) {
    $line = "$(Get-Date -Format o) [proof] $msg"
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
Log "=== improve-window PROOF START (battles=$Battles min_decisive=$MinDecisive turn_cap=$TurnCap search_ms=$SearchMs) ==="

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
        Log "WARNING: lease did not clear after 60s; proceeding anyway."
    } else {
        Log "lease released."
    }

    # --- STEP 2: start a throwaway local showdown server on :$EvalPort -------
    $sdLog = Join-Path $proj "eval_results\selfplay\proof-showdown-$EvalPort.log"
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

    # --- STEP 3: run the gate-proof self-play eval DIRECTLY ------------------
    # Reachable decisive floor via env (does NOT touch the production default of
    # 30 in source); env-arm A/B is a real candidate-vs-incumbent comparison.
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:EVAL_SHOWDOWN_PORT = "$EvalPort"
    $env:SELFPLAY_MIN_DECISIVE = "$MinDecisive"
    $proofLabel = "gate-proof-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Log "running selfplay_eval.py DIRECT: battles=$Battles turn_cap=$TurnCap search_ms=$SearchMs label=$proofLabel SELFPLAY_MIN_DECISIVE=$MinDecisive ..."
    & $py -X utf8 (Join-Path $proj "infrastructure\selfplay_eval.py") `
        --battles $Battles --turn-cap $TurnCap --showdown-port $EvalPort `
        --search-time-ms $SearchMs --per-battle-timeout 150 `
        --teams-from "teams/eval-fast-teams.list" --label $proofLabel `
        --new-env MCTS_BLEND_MAX_SAMPLES=8 --old-env MCTS_BLEND_MAX_SAMPLES=1 `
        2>>"$log.eval.err" | Tee-Object -FilePath "$log.eval.out" | ForEach-Object { Log "  [eval] $_" }
    Log "selfplay_eval exit=$LASTEXITCODE"

    # --- STEP 3b: record a durable ledger entry from the REAL verdict --------
    $proofPath = Join-Path $proj "eval_results\selfplay\$proofLabel.json"
    if (Test-Path $proofPath) {
        Log "verdict file written: $proofPath"
        $v = Get-Content $proofPath -Raw | ConvertFrom-Json
        $decisive = [int]$v.decisive_battles
        $accept = [bool]$v.ACCEPT
        $minD = [int]$v.min_decisive
        Log "VERDICT: NEW $($v.new_wins)/$decisive = $($v.new_win_rate) (LCB $($v.new_wilson_lcb)) min_decisive=$minD ACCEPT=$accept gate_failed_to_run=$($v.gate_failed_to_run)"

        $outcome = if ($accept) { "accepted_merged" } else { "reverted" }
        if ($v.gate_failed_to_run) { $outcome = "gate_failed_to_run" }

        $head = (& git -C $proj rev-parse HEAD).Trim()
        $entry = [ordered]@{
            timestamp        = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ")
            issue            = "gate-proof env-arm A/B (MCTS_BLEND_MAX_SAMPLES 8 vs 1)"
            outcome          = $outcome
            head_before      = $head.Substring(0, 12)
            head_after       = $head.Substring(0, 12)
            verdict_line     = "[PROOF] selfplay verdict: ACCEPT=$accept NEW $($v.new_wins)/$decisive LCB $($v.new_wilson_lcb)"
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
        Log "ledger += $outcome (decisive=$decisive accept=$accept)"
    } else {
        Log "NO verdict file at $proofPath -- gate did NOT complete in time."
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

    # Re-confirm the trigger is ENABLED (a prior hard kill once left it Disabled).
    try {
        $tk = Get-ScheduledTask -TaskName $playerTask -ErrorAction Stop
        $needEnable = @($tk.Triggers | Where-Object { -not $_.Enabled }).Count -gt 0
        if ($needEnable) {
            Log "re-enabling $playerTask trigger(s) (found disabled)."
            foreach ($trg in $tk.Triggers) { $trg.Enabled = $true }
            Set-ScheduledTask -TaskName $playerTask -Trigger $tk.Triggers | Out-Null
        }
    } catch { Log "trigger-enable check threw: $($_.Exception.Message)" }

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
    $tk2 = Get-ScheduledTask -TaskName $playerTask -ErrorAction SilentlyContinue
    $trigEnabled = if ($tk2) { @($tk2.Triggers | Where-Object { $_.Enabled }).Count } else { 0 }
    Log "=== PROOF END (ladder venv clients=$venvCount, task=$($tk2.State), enabled-triggers=$trigEnabled) ==="
}
