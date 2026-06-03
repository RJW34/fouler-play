<#
run_selfplay_burst.ps1 -- JIGGLY low-load burst-window runner for the fouler
self-play eval gate (NEW vs OLD).

WHAT IT DOES
  1. RAM-headroom guard: aborts unless >= MinFreeGB is free, so it never
     competes with the live Cobblemon/OBS/ladder work for memory.
  2. Starts a THROWAWAY pokemon-showdown server on a spare port (default 8801,
     NOT the live ladder which uses the public sim server).
  3. Runs infrastructure/selfplay_eval.py: fouler-NEW vs fouler-OLD over -Battles
     games across the 3 fat/stall teams.
  4. Stops the throwaway server.

It NEVER touches the live battle_stats.json (the harness redirects each engine's
stats to throwaway files) and NEVER pushes anything.

USAGE (run on JIGGLY, e.g. via DEKU jigglypuff-ps in a low-load window):
  # Real burst gate (N=50, ~2.5h), OLD = a worktree at the incumbent commit:
  pwsh D:\Projects\fouler-play\infrastructure\run_selfplay_burst.ps1 `
      -Battles 50 -OldCheckout D:\Projects\fouler-play.old -Label burst

  # SMOKE (N=4) proving the gate RANKS NEW vs OLD via env-arm asymmetry
  # (same code, NEW samples more opponent sets than OLD => NEW should win more):
  pwsh D:\Projects\fouler-play\infrastructure\run_selfplay_burst.ps1 `
      -Battles 4 -Label smoke -Smoke
#>
param(
    [int]$Battles = 50,
    [int]$Port = 8801,
    [string]$Label = "burst",
    [string]$Repo = "D:\Projects\fouler-play",
    [string]$Showdown = "D:\Projects\pokemon-showdown",
    [string]$OldCheckout = "",          # empty => same checkout (env-arm A/B)
    [int]$SearchMs = 1200,
    [double]$MinFreeGB = 3.0,
    [switch]$Smoke,                      # env-arm asymmetry to prove ranking
    # FOULER-EVAL-TURNCAP-2026-06-03: eval-only hard turn cap (fewer-fainted
    # scoring) + fast HO eval teams so mirror matches terminate decisively.
    # Default ON for a viable gate; -MaxTurns 0 restores the old behaviour.
    [int]$MaxTurns = 25,
    [string]$TeamsFile = "teams/eval-teams.list"
)

$ErrorActionPreference = "Stop"

# --- 1. RAM-headroom guard ---------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
$freeGB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
Write-Host "[burst] free RAM = $freeGB GB (require >= $MinFreeGB GB)"
if ($freeGB -lt $MinFreeGB) {
    Write-Host "[burst] ABORT: insufficient free RAM; refusing to disrupt live work."
    exit 3
}

# --- 2. Start throwaway showdown on a spare port -----------------------------
$py = Join-Path $Repo ".venv\Scripts\python.exe"
$sdLog = Join-Path $Repo "eval_results\selfplay\showdown-$Port.log"
New-Item -ItemType Directory -Force (Split-Path $sdLog) | Out-Null
Write-Host "[burst] starting throwaway showdown on :$Port ..."
$sd = Start-Process -FilePath "node" `
    -ArgumentList @((Join-Path $Showdown "pokemon-showdown"), "start", "--no-security", "$Port") `
    -WorkingDirectory $Showdown -PassThru -RedirectStandardOutput $sdLog `
    -RedirectStandardError "$sdLog.err" -WindowStyle Hidden
Start-Sleep -Seconds 20

try {
    # --- 3. Run the self-play eval -------------------------------------------
    # FOULER-EVAL-TURNCAP: forward the eval-only turn cap to both engine arms.
    if ($MaxTurns -gt 0) {
        $env:FOULER_MAX_TURNS = "$MaxTurns"
        Write-Host "[burst] eval turn cap FOULER_MAX_TURNS=$MaxTurns (fewer-fainted scoring)"
    } else {
        Remove-Item Env:\FOULER_MAX_TURNS -ErrorAction SilentlyContinue
        Write-Host "[burst] eval turn cap DISABLED (MaxTurns=0)"
    }
    $spArgs = @(
        (Join-Path $Repo "infrastructure\selfplay_eval.py"),
        "--battles", "$Battles",
        "--teams-from", "$TeamsFile",
        "--label", $Label,
        "--showdown-port", "$Port",
        "--search-time-ms", "$SearchMs"
    )
    if ($OldCheckout -ne "") {
        $spArgs += @("--old-checkout", $OldCheckout, "--new-checkout", $Repo)
    }
    if ($Smoke) {
        # SAME code, asymmetric behaviour: NEW searches the real opponent tree
        # harder than OLD. NEW is expected to win more -> proves the harness
        # discriminates / ranks. (Not a promotion-grade signal at N=4.)
        $spArgs += @("--new-env", "MCTS_BLEND_MAX_SAMPLES=8",
                     "--old-env", "MCTS_BLEND_MAX_SAMPLES=1")
    }
    Write-Host "[burst] running: $py $($spArgs -join ' ')"
    & $py @spArgs
    $code = $LASTEXITCODE
    Write-Host "[burst] selfplay_eval exit=$code"
}
finally {
    # --- 4. Stop the throwaway server ----------------------------------------
    if ($sd -and -not $sd.HasExited) {
        Write-Host "[burst] stopping throwaway showdown (pid $($sd.Id)) ..."
        Stop-Process -Id $sd.Id -Force -ErrorAction SilentlyContinue
    }
    Get-Process node -ErrorAction SilentlyContinue |
        Where-Object { $_.Id -eq $sd.Id } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

$verdict = Join-Path $Repo "eval_results\selfplay\$Label.json"
if (Test-Path $verdict) {
    Write-Host "[burst] VERDICT:"
    Get-Content $verdict
} else {
    Write-Host "[burst] WARNING: no verdict file produced ($verdict)"
}
