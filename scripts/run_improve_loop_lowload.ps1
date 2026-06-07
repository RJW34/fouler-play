# run_improve_loop_lowload.ps1 -- fire bounded improve_loop iterations ONLY in a
# genuine low-load window. Gated on RAM headroom so it never competes with the
# live ladder bot / Cobblemon / MC world for memory (the claude propose step +
# self-play gate are heavy and time out under load). Deployed 2026-06-04 with
# the selfplay-gate fix (turn-cap + score-on-cap) so the gate can reach a
# decisive (N>=30) ACCEPT/REVERT verdict.
param(
    [double]$MinFreeGB = 3.0,
    [int]$Battles = 40,
    [int]$CliTimeoutSeconds = 1200,
    [int]$MaxIterations = 3,
    [int]$MaxRuntimeMinutes = 360,
    [int]$TargetElo = 1700,
    [int]$SleepSecondsBetweenIterations = 30
)
$ErrorActionPreference = "Stop"
$proj = "D:\Projects\fouler-play"
Set-Location -LiteralPath $proj
$log = Join-Path $proj "logs\improve_loop_scheduled.log"
$py = Join-Path $proj ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:EVAL_SHOWDOWN_PORT = "18765"
$env:IMPROVE_AGENT_SELFPLAY_BATTLES = "$Battles"
$env:IMPROVE_AGENT_EVAL_GATE = "1"
$env:IMPROVE_AGENT_EVAL_MODE = "selfplay"
$env:IMPROVE_AGENT_CLI_TIMEOUT = "$CliTimeoutSeconds"

$autoImprove = "$env:FOULER_PLAY_ENABLE_AUTO_IMPROVE".Trim().ToLowerInvariant()
if (@("1", "true", "yes", "on") -notcontains $autoImprove) {
    "$(Get-Date -Format o) [lowload] STOP: FOULER_PLAY_ENABLE_AUTO_IMPROVE is not enabled; refusing mutating improve_loop." | Add-Content -LiteralPath $log
    exit 2
}

function Get-FreeGB {
    $os = Get-CimInstance Win32_OperatingSystem
    return [math]::Round($os.FreePhysicalMemory / 1MB, 2)
}

function Get-LearnLoopStatus {
    $json = & $py -X utf8 -c "import json; from infrastructure.improve_loop import loop_status; print(json.dumps(loop_status()))"
    if ($LASTEXITCODE -ne 0 -or -not $json) {
        return $null
    }
    return ($json | ConvertFrom-Json)
}

$started = Get-Date
"$(Get-Date -Format o) [lowload] bounded improve loop start max_iterations=$MaxIterations max_runtime_minutes=$MaxRuntimeMinutes battles=$Battles cli_timeout=$CliTimeoutSeconds target_elo=$TargetElo" | Add-Content -LiteralPath $log

for ($i = 1; $i -le $MaxIterations; $i++) {
    $elapsedMinutes = ((Get-Date) - $started).TotalMinutes
    if ($elapsedMinutes -ge $MaxRuntimeMinutes) {
        "$(Get-Date -Format o) [lowload] STOP: runtime budget exhausted (${elapsedMinutes}m >= $MaxRuntimeMinutes)." | Add-Content -LiteralPath $log
        break
    }

    $freeGB = Get-FreeGB
    "$(Get-Date -Format o) [lowload] iteration $i/$MaxIterations free RAM = $freeGB GB (require >= $MinFreeGB)" | Add-Content -LiteralPath $log
    if ($freeGB -lt $MinFreeGB) {
        "$(Get-Date -Format o) [lowload] STOP: insufficient free RAM; not disrupting live work." | Add-Content -LiteralPath $log
        break
    }

    $status = Get-LearnLoopStatus
    if ($status -and $status.ladder) {
        $currentElo = $status.ladder.current_elo
        $missionTarget = $TargetElo
        if ($status.ladder.target) {
            $missionTarget = [int]$status.ladder.target
        }
        if ($currentElo -and [double]$currentElo -ge $missionTarget) {
            "$(Get-Date -Format o) [lowload] STOP: current ELO $currentElo >= target $missionTarget." | Add-Content -LiteralPath $log
            break
        }
        "$(Get-Date -Format o) [lowload] status: $($status.headline)" | Add-Content -LiteralPath $log
    }

    "$(Get-Date -Format o) [lowload] starting improve_loop --iterations 1 (iteration=$i, battles=$Battles, cli_timeout=$CliTimeoutSeconds)" | Add-Content -LiteralPath $log
    & $py -X utf8 (Join-Path $proj "infrastructure\improve_loop.py") --iterations 1 --num-battles $Battles --enable-auto-improve 1>>$log 2>>"$log.err"
    "$(Get-Date -Format o) [lowload] improve_loop iteration $i exited code $LASTEXITCODE" | Add-Content -LiteralPath $log

    if ($i -lt $MaxIterations -and $SleepSecondsBetweenIterations -gt 0) {
        Start-Sleep -Seconds $SleepSecondsBetweenIterations
    }
}

"$(Get-Date -Format o) [lowload] bounded improve loop finished." | Add-Content -LiteralPath $log
